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
          window.Worker=class {
            constructor(){this.handlers={};this.messages=[];this.dead=false;}
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
