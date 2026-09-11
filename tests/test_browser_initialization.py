"""Real Chromium controller with deterministic timers and a simulated slow worker."""
from pathlib import Path
import unittest
from playwright.sync_api import sync_playwright

class InitializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.p.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.evaluate('''() => {
          let now=0, serial=0; const timers=new Map();
          window.setTimeout=(fn,delay)=>{const id=++serial;timers.set(id,{fn,at:now+delay});return id};
          window.clearTimeout=id=>timers.delete(id);
          window.advance=ms=>{now+=ms;for(const [id,t] of [...timers])if(t.at<=now){timers.delete(id);t.fn()}};
          window.fakeWorkers=[];
          window.Worker=class {
            constructor(...args){this.args=args;this.handlers={};this.messages=[];this.dead=false;fakeWorkers.push(this);}
            addEventListener(kind,fn){this.handlers[kind]=fn;}
            postMessage(message){this.messages.push(message);}
            terminate(){this.dead=true;}
            reply(message){this.handlers.message({data:message});}
          };
          window.flush=async()=>{for(let i=0;i<10;i++)await Promise.resolve()};
        }''')
        self.page.add_script_tag(path=str(Path(__file__).resolve().parents[1]/'web/browser-runtime.js'))

    def tearDown(self):
        self.page.close()

    def test_slow_initialization_and_unchanged_execution_deadline(self):
        self.page.evaluate('''async()=>{
          const r=new BrowserPythonRuntime(), p=r.run('MODEL={}').catch(e=>e.code);
          const w=r.worker;
          advance(70000);await flush();
          if(w.dead || r.state!=='loading')throw Error('cold initialization stopped after 60 seconds');
          w.reply({id:w.messages[0].id,type:'ready',runtime:{python:'3.12.7'}});await flush();
          if(r.state!=='running')throw Error('run did not start after initialization');
          advance(7999);await flush();if(w.dead)throw Error('execution ended early');
          advance(1);await flush();
          if(await p!=='browser_timeout' || !w.dead)throw Error('8 second execution bound changed');
        }''')

    def test_initialization_has_distinct_bounded_error(self):
        self.page.evaluate('''async()=>{
          const r=new BrowserPythonRuntime(), p=r.parse('').catch(e=>e.code),w=r.worker;
          advance(179999);await flush();if(w.dead)throw Error('initialization ended early');
          advance(1);await flush();
          if(await p!=='browser_init_timeout'||r.state!=='init_error'||!w.dead||r.pending.size)throw Error('initialization not bounded');
        }''')

    def test_stop_during_loading_cancels_and_can_retry(self):
        self.page.evaluate('''async()=>{
          const r=new BrowserPythonRuntime(),p=r.parse('').catch(e=>e.code),w=r.worker;
          advance(70000);r.stop();await flush();
          if(await p!=='browser_stopped'||!w.dead||r.pending.size)throw Error('stop failed');
          const retry=r.ensureReady(), fresh=r.worker;
          fresh.reply({id:fresh.messages[0].id,type:'ready',runtime:{python:'3.12.7'}});
          await retry;advance(180000);await flush();
          if(r.state!=='ready'||fresh.dead)throw Error('old timeout affected retry');
        }''')

    def test_native_worker_error_during_initialization_is_classified(self):
        self.page.evaluate('''async()=>{
          const r=new BrowserPythonRuntime(),p=r.parse('').catch(e=>e),w=r.worker;
          w.handlers.error({message:''});await flush();const error=await p;
          if(error.code!=='browser_init_error'||r.state!=='init_error'||!w.dead||r.pending.size)
            throw Error('entry-script failure lost initialization classification');
          const retry=r.ensureReady(),fresh=r.worker;
          w.handlers.error({message:'late stale error'});
          if(fresh.dead)throw Error('stale worker killed retry');
          fresh.reply({id:fresh.messages[0].id,type:'ready',runtime:{python:'3.12.7'}});
          await retry;
          if(r.state!=='ready')throw Error('initialization retry failed');
        }''')

    def test_post_ready_native_crash_keeps_execution_semantics(self):
        self.page.evaluate('''async()=>{
          const r=new BrowserPythonRuntime(),ready=r.ensureReady(),w=r.worker;
          w.reply({id:w.messages[0].id,type:'ready',runtime:{python:'3.12.7'}});await ready;
          const p=r.run('').catch(e=>e);await flush();
          w.handlers.error({message:'execution crash'});await flush();const error=await p;
          if(error.code==='browser_init_error'||error.message!=='execution crash'||r.state!=='stopped'||!w.dead)
            throw Error('post-ready crash was misclassified');
        }''')

    def test_model_sync_uses_worker_and_eight_second_deadline(self):
        self.page.evaluate('''async()=>{
          const r=new BrowserPythonRuntime(),ready=r.ensureReady(),w=r.worker;
          w.reply({id:w.messages[0].id,type:'ready',runtime:{python:'3.12.7'}});await ready;
          const model={machines:[{id:'CUT_A',time:11}]};
          const p=r.sync('MODEL = {}',model).catch(e=>e.code);await flush();
          const request=w.messages.at(-1);
          if(request.type!=='sync'||request.model!==model||r.state!=='checking')throw Error('sync transport mismatch');
          advance(7999);await flush();if(w.dead)throw Error('sync timeout early');
          advance(1);if(await p!=='browser_timeout'||!w.dead)throw Error('sync budget changed');
        }''')

    def test_versioned_resource_contract_enforces_roles_and_releases(self):
        result = self.page.evaluate('''async()=>{
          const interactive=new BrowserPythonRuntime();
          const ready=interactive.ensureReady(),iw=interactive.worker;
          iw.reply({id:iw.messages[0].id,type:'ready',runtime:{python:'3.12.7'}});await ready;
          const blocked=new BrowserPythonRuntime();let interactiveError;
          try{await blocked.ensureReady()}catch(error){interactiveError=error.code}
          const a=new BrowserPythonRuntime({role:'experiment'}),b=new BrowserPythonRuntime({role:'experiment'});
          const ar=a.ensureReady(),br=b.ensureReady(),aw=a.worker,bw=b.worker;
          aw.reply({id:aw.messages[0].id,type:'ready',runtime:{}});bw.reply({id:bw.messages[0].id,type:'ready',runtime:{}});await Promise.all([ar,br]);
          const excess=new BrowserPythonRuntime({role:'experiment'});let experimentError;
          try{await excess.ensureReady()}catch(error){experimentError=error.code}
          const peak=factoryWorkerResources.snapshot();a.stop();b.stop();const settled=factoryWorkerResources.snapshot();
          interactive.stop();const stopped=factoryWorkerResources.snapshot();
          return {contract:FACTORY_WORKER_RESOURCE_CONTRACT,interactiveError,experimentError,peak,settled,stopped,names:[iw.args[1].name,aw.args[1].name,bw.args[1].name]};
        }''')
        self.assertEqual(result['contract']['version'], 1)
        self.assertEqual(result['interactiveError'], 'browser_resource_limit')
        self.assertEqual(result['experimentError'], 'browser_resource_limit')
        self.assertEqual(result['peak'], {'contractVersion': 1, 'interactive': 1, 'experiment': 2, 'data': 0, 'total': 3})
        self.assertEqual(result['settled'], {'contractVersion': 1, 'interactive': 1, 'experiment': 0, 'data': 0, 'total': 1})
        self.assertEqual(result['stopped']['total'], 0)
        self.assertTrue(result['names'][0].startswith('factory-interactive-runtime-'))
        self.assertTrue(all(name.startswith('factory-experiment-runtime-') for name in result['names'][1:]))

    def test_repeated_create_is_idempotent_and_stop_releases_the_only_worker(self):
        result = self.page.evaluate('''async()=>{
          const runtime=new BrowserPythonRuntime({role:'experiment'});
          const first=runtime.createWorker(),worker=runtime.worker;
          const second=runtime.createWorker();
          if(first!==second||runtime.worker!==worker)throw Error('createWorker replaced its owner');
          worker.reply({id:worker.messages[0].id,type:'ready',runtime:{python:'3.12.7'}});
          await Promise.all([first,second]);
          const third=runtime.createWorker();
          const sameReady=third===runtime.ready,active=factoryWorkerResources.snapshot();
          const firstStop=runtime.stop(),secondStop=runtime.stop();
          return {sameReady,created:fakeWorkers.length,active,
            firstStop,secondStop,dead:fakeWorkers.map(worker=>worker.dead),settled:factoryWorkerResources.snapshot()};
        }''')
        self.assertTrue(result['sameReady'])
        self.assertEqual(result['created'], 1)
        self.assertEqual(result['active']['experiment'], 1)
        self.assertTrue(result['firstStop'])
        self.assertFalse(result['secondStop'])
        self.assertEqual(result['dead'], [True])
        self.assertEqual(result['settled']['total'], 0)

    def test_idle_reclamation_acknowledgement_is_counted(self):
        result = self.page.evaluate('''async()=>{
          const runtime=new BrowserPythonRuntime(),ready=runtime.ensureReady(),worker=runtime.worker;
          worker.reply({id:worker.messages[0].id,type:'ready',runtime:{python:'3.12.7'}});await ready;
          const parsed=runtime.parse('MODEL={}');await flush();const request=worker.messages.at(-1);
          worker.reply({id:request.id,type:'result',payload:{model:{}},resources:{idle_reclaimed:true,globals_cleared:4,python_objects_collected:7}});
          await parsed;const snapshot=runtime.resourceSnapshot();runtime.stop();return snapshot;
        }''')
        self.assertEqual(result['role'], 'interactive')
        self.assertEqual(result['idleReclaims'], 1)
        self.assertEqual(result['lastIdleReclaim']['globals_cleared'], 4)
        self.assertEqual(result['total'], 1)
