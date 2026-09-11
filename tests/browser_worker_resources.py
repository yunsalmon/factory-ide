"""Exact public-image gate for the versioned browser Worker resource contract.

Usage: python tests/browser_worker_resources.py URL [OUTPUT_JSON]
"""
import json
from pathlib import Path
import sys
import time

from playwright.sync_api import sync_playwright


BASE = sys.argv[1].rstrip('/')
OUTPUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('artifacts/worker-resources.json')
REPEATS = 5


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def process_rss(browser_cdp):
    total = 0
    for process in browser_cdp.send('SystemInfo.getProcessInfo')['processInfo']:
        try:
            lines = Path(f"/proc/{process['id']}/status").read_text().splitlines()
        except (FileNotFoundError, PermissionError):
            continue
        for line in lines:
            if line.startswith('VmRSS:'):
                total += int(line.split()[1]) * 1024
                break
    return total


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context(locale='en', viewport={'width': 1440, 'height': 1000})
    context.add_init_script(r'''(() => {
      const NativeWorker=window.Worker;
      window.__workerResourceAudit={created:[],live:{interactive:0,experiment:0,other:0},peak:{interactive:0,experiment:0,other:0,total:0}};
      window.Worker=class extends NativeWorker{
        constructor(url,options={}){
          super(url,options);
          const name=options?.name||'';
          const role=name.startsWith('factory-interactive-runtime-')?'interactive':name.startsWith('factory-experiment-runtime-')?'experiment':'other';
          this.__resourceRow={url:new URL(url,location.href).href,name,role,terminated:false};
          __workerResourceAudit.created.push(this.__resourceRow);__workerResourceAudit.live[role]++;
          __workerResourceAudit.peak[role]=Math.max(__workerResourceAudit.peak[role],__workerResourceAudit.live[role]);
          __workerResourceAudit.peak.total=Math.max(__workerResourceAudit.peak.total,Object.values(__workerResourceAudit.live).reduce((a,b)=>a+b,0));
        }
        terminate(){
          if(!this.__resourceRow.terminated){this.__resourceRow.terminated=true;__workerResourceAudit.live[this.__resourceRow.role]--;}
          return super.terminate();
        }
      };
    })();''')
    page = context.new_page()
    page_cdp = context.new_cdp_session(page)
    browser_cdp = browser.new_browser_cdp_session()
    requests = []
    context.on('request', lambda request: requests.append(request.url))

    def worker_targets():
        targets = browser_cdp.send('Target.getTargets')['targetInfos']
        # This fresh browser/context owns no unrelated dedicated Workers. Count
        # every target, including a blank URL while an entry script is loading.
        return [target for target in targets if target['type'] == 'worker']

    def wait_targets(expected, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            targets = worker_targets()
            if len(targets) == expected:
                return targets
            page.wait_for_timeout(50)
        raise AssertionError(f'expected {expected} Worker targets, found {worker_targets()}')

    def memory(label):
        page_cdp.send('HeapProfiler.collectGarbage')
        page.wait_for_timeout(300)
        usage = page_cdp.send('Runtime.getHeapUsage')
        targets = worker_targets()
        return {
            'label': label,
            'used_heap': usage['usedSize'],
            'total_heap': usage['totalSize'],
            'rss_sum': process_rss(browser_cdp),
            'worker_targets': len(targets),
            'worker_urls': [target['url'] for target in targets],
            'ledger': page.evaluate('factoryWorkerResources.snapshot()'),
            'instrumented': page.evaluate('({live:{...__workerResourceAudit.live},peak:{...__workerResourceAudit.peak}})'),
        }

    page.goto(BASE)
    page.wait_for_function('()=>window.factoryStudio?.getState().valid')
    assert page.evaluate('PUBLIC_DEMO') is True
    assert page.evaluate('FACTORY_WORKER_RESOURCE_CONTRACT') == {
        'version': 1,
        'interactive': {'maximum': 1, 'settled': 1, 'workerName': 'factory-interactive-runtime'},
        'experiment': {'maximum': 2, 'settled': 0, 'workerName': 'factory-experiment-runtime'},
        'data': {'maximum': 1, 'settled': 0, 'workerName': 'factory-data-import'},
    }

    # Establish exactly one reusable interactive runtime and measure real warm
    # validation, including the idle-memory acknowledgement before every reply.
    page.evaluate('browserRuntime.parse(S.source)')
    wait_targets(1, 30)
    creation_count = page.evaluate('__workerResourceAudit.created.length')
    idempotence = page.evaluate('''()=>{
      const worker=browserRuntime.worker,ready=browserRuntime.ready;
      const first=browserRuntime.createWorker(),second=browserRuntime.createWorker();
      return {sameWorker:browserRuntime.worker===worker,sameReady:first===ready&&second===ready,
        ledger:factoryWorkerResources.snapshot(),created:__workerResourceAudit.created.length};
    }''')
    assert idempotence == {
        'sameWorker': True,
        'sameReady': True,
        'ledger': {'contractVersion': 1, 'interactive': 1, 'experiment': 0, 'data': 0, 'total': 1},
        'created': creation_count,
    }, idempotence
    wait_targets(1)

    # Real application-level validations may overlap with debounced validation.
    # Every pair must retain its own Pyodide bridge values and cleanup.
    concurrent_pairs = []
    for _ in range(5):
        concurrent_pairs.append(page.evaluate('''async()=>{
          const settled=await Promise.allSettled([applyCode(true),applyCode(true)]);
          return {values:settled.map(row=>row.status==='fulfilled'?row.value:row.reason?.message),
            valid:S.valid,error:S.parseError,state:browserRuntime.state};
        }'''))
    assert all(row == {'values': [True, True], 'valid': True, 'error': '', 'state': 'ready'} for row in concurrent_pairs), concurrent_pairs
    wait_targets(1)

    # A handled Python/source error reclaims its request globals but keeps the
    # count-bounded warm target ready for a successful correction.
    error_reclaims = page.evaluate('browserRuntime.idleReclaims')
    handled_error = page.evaluate('''async()=>{
      let rejected=false;try{await browserRuntime.parse('MODEL = {')}catch{rejected=true}
      const afterError={rejected,state:browserRuntime.state,ledger:factoryWorkerResources.snapshot()};
      const recovered=await browserRuntime.parse(S.source);
      return {afterError,recovered:!!recovered?.model,state:browserRuntime.state};
    }''')
    assert handled_error == {
        'afterError': {'rejected': True, 'state': 'ready', 'ledger': {'contractVersion': 1, 'interactive': 1, 'experiment': 0, 'data': 0, 'total': 1}},
        'recovered': True,
        'state': 'ready',
    }, handled_error
    assert page.evaluate('browserRuntime.idleReclaims') == error_reclaims + 2
    warm = []
    reclaims_before = page.evaluate('browserRuntime.idleReclaims')
    for _ in range(5):
        warm.append(page.evaluate('''async()=>{const start=performance.now();await browserRuntime.parse(S.source);return performance.now()-start}'''))
    warm_p95 = percentile(warm, .95)
    assert warm_p95 <= 500, warm
    assert page.evaluate('browserRuntime.idleReclaims') == reclaims_before + len(warm)
    assert page.evaluate('browserRuntime.lastIdleReclaim.idle_reclaimed && browserRuntime.lastIdleReclaim.globals_cleared===4')
    assert page.evaluate('factoryWorkerResources.snapshot()') == {'contractVersion': 1, 'interactive': 1, 'experiment': 0, 'data': 0, 'total': 1}
    page.evaluate('''async()=>{window.__resourceModel=(await browserRuntime.parse(S.source)).model}''')

    checkpoints = []
    for repeat in range(REPEATS):
        statuses = page.evaluate('''async repeat=>{
          const definition=await experimentDefinition('resource-'+repeat,'',S.source,__resourceModel,[1,2,3,4],2);
          const runner=new ExperimentRunner();window.__resourceRunner=runner;
          const experiment=await runner.run(definition);
          return {statuses:experiment.runs.map(row=>row.status),pool:runner.workers.size,active:runner.active};
        }''', repeat)
        assert statuses == {'statuses': ['success'] * 4, 'pool': 0, 'active': False}, statuses
        wait_targets(1, 15)
        checkpoint = memory(f'repeat_{repeat + 1}')
        assert checkpoint['ledger'] == {'contractVersion': 1, 'interactive': 1, 'experiment': 0, 'data': 0, 'total': 1}
        assert checkpoint['worker_targets'] == 1
        checkpoints.append(checkpoint)

    first, last = checkpoints[0], checkpoints[-1]
    assert last['used_heap'] <= max(first['used_heap'] * 1.20, first['used_heap'] + 1_048_576), checkpoints
    assert last['rss_sum'] <= first['rss_sum'] * 1.20, checkpoints
    peak = page.evaluate('__workerResourceAudit.peak')
    assert peak['interactive'] == 1 and peak['experiment'] == 2 and peak['total'] <= 3, peak

    # Cancellation terminates both task Workers; the same runner can recover.
    cancelled = page.evaluate('''async()=>{
      const source=S.source+'\\ndef processing_time(machine,lot,ctx):\\n    while True: pass\\n';
      const definition=await experimentDefinition('cancel','',source,__resourceModel,[10,11,12,13],2);
      const runner=new ExperimentRunner();window.__resourceRunner=runner;
      const pending=runner.run(definition);
      const deadline=performance.now()+30000;
      while(!(runner.workers.size===2&&[...runner.workers].every(worker=>worker.state==='running'))){if(performance.now()>deadline)throw Error('workers did not run');await new Promise(resolve=>setTimeout(resolve,20));}
      runner.cancel();const experiment=await pending;
      return {statuses:experiment.runs.map(row=>row.status),pool:runner.workers.size,active:runner.active};
    }''')
    assert cancelled == {'statuses': ['cancelled'] * 4, 'pool': 0, 'active': False}, cancelled
    wait_targets(1, 10)
    assert page.evaluate('factoryWorkerResources.snapshot()') == {'contractVersion': 1, 'interactive': 1, 'experiment': 0, 'data': 0, 'total': 1}
    recovered = page.evaluate('''async()=>{
      const definition=await experimentDefinition('recover','',S.source,__resourceModel,[20,21],2);
      const experiment=await __resourceRunner.run(definition);
      return {statuses:experiment.runs.map(row=>row.status),pool:__resourceRunner.workers.size};
    }''')
    assert recovered == {'statuses': ['success', 'success'], 'pool': 0}, recovered
    wait_targets(1, 10)

    # Explicitly stopping the interactive runtime reclaims the final target;
    # the next validation starts one clean replacement and remains warm again.
    assert page.evaluate('browserRuntime.stop()') is True
    wait_targets(0, 10)
    assert page.evaluate('factoryWorkerResources.snapshot().total') == 0
    page.evaluate('browserRuntime.parse(S.source)')
    wait_targets(1, 30)
    assert page.evaluate('factoryWorkerResources.snapshot()') == {'contractVersion': 1, 'interactive': 1, 'experiment': 0, 'data': 0, 'total': 1}

    # A persisted pagehide must invalidate producers which have not created a
    # Worker yet: delayed File.text and the editor's 500 ms validation debounce.
    lifecycle_start = page.evaluate('''()=>{
      selectTab('data');
      window.__lateReadRelease=null;
      window.__lateReadPromise=readDataFile({name:'late.csv',size:32,text:()=>new Promise(resolve=>__lateReadRelease=resolve)});
      const field=document.querySelector('#code');field.value=S.source+'\\n# pending lifecycle validation';field.dispatchEvent(new Event('input',{bubbles:true}));
      const before={dataSerial:DATA.serial,validationGeneration,created:__workerResourceAudit.created.length,
        source:S.source,tab:S.tab,pendingTimer:parseTimer!==null};
      dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));
      return {before,after:{dataSerial:DATA.serial,validationGeneration,created:__workerResourceAudit.created.length,
        source:S.source,tab:S.tab,pendingTimer:parseTimer!==null,busy:DATA.busy,phase:DATA.phase,
        ledger:factoryWorkerResources.snapshot(),live:{...__workerResourceAudit.live}}};
    }''')
    assert lifecycle_start['before']['pendingTimer'], lifecycle_start
    assert lifecycle_start['after']['dataSerial'] == lifecycle_start['before']['dataSerial'] + 1, lifecycle_start
    assert lifecycle_start['after']['validationGeneration'] == lifecycle_start['before']['validationGeneration'] + 1, lifecycle_start
    assert lifecycle_start['after']['source'] == lifecycle_start['before']['source'] and lifecycle_start['after']['tab'] == lifecycle_start['before']['tab'], lifecycle_start
    assert lifecycle_start['after']['pendingTimer'] is False and lifecycle_start['after']['busy'] is False and lifecycle_start['after']['phase'] is None, lifecycle_start
    assert lifecycle_start['after']['ledger']['total'] == 0 and all(value == 0 for value in lifecycle_start['after']['live'].values()), lifecycle_start
    wait_targets(0)
    page.evaluate("async()=>{__lateReadRelease('id,name\\nLATE,late row\\n');await __lateReadPromise}")
    page.wait_for_timeout(750)
    lifecycle_hidden = page.evaluate('''()=>({created:__workerResourceAudit.created.length,dataSerial:DATA.serial,
      dataText:DATA.text,pending:DATA.pending,pendingTimer:parseTimer!==null,ledger:factoryWorkerResources.snapshot()})''')
    assert lifecycle_hidden == {
        'created': lifecycle_start['before']['created'],
        'dataSerial': lifecycle_start['after']['dataSerial'],
        'dataText': None,
        'pending': None,
        'pendingTimer': False,
        'ledger': {'contractVersion': 1, 'interactive': 0, 'experiment': 0, 'data': 0, 'total': 0},
    }, lifecycle_hidden
    wait_targets(0)

    # Simulate BFCache restoration. State remains, while only a new edit/file
    # selection explicitly starts fresh bounded work.
    page.evaluate('''()=>{
      dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));
      const field=document.querySelector('#code');field.value=S.source+'\\n# restored lifecycle validation';field.dispatchEvent(new Event('input',{bubbles:true}));
    }''')
    page.wait_for_function('()=>S.valid&&browserRuntime.state==="ready"', timeout=30000)
    wait_targets(1, 30)
    page.evaluate('''()=>readDataFile(new File(['id,name\\nRECOVERED,recovered row\\n'],'recovered.csv',{type:'text/csv'}))''')
    page.wait_for_function('()=>DATA.message?.code==="data_mapping_ready"&&!DATA.busy&&DATA.worker===null', timeout=10000)
    wait_targets(1)
    lifecycle_recovery = page.evaluate('''()=>({source:S.source,tab:S.tab,dataSerial:DATA.serial,
      mappingReady:DATA.message.code==='data_mapping_ready',ledger:factoryWorkerResources.snapshot(),
      live:{...__workerResourceAudit.live}})''')
    assert lifecycle_recovery['source'].endswith('# restored lifecycle validation') and lifecycle_recovery['tab'] == lifecycle_start['before']['tab'], lifecycle_recovery
    assert lifecycle_recovery['mappingReady'] and lifecycle_recovery['ledger'] == {'contractVersion': 1, 'interactive': 1, 'experiment': 0, 'data': 0, 'total': 1}, lifecycle_recovery

    # Public work stays in Workers at the same origin and never uses an API.
    assert not any('/api/' in url for url in requests), requests
    assert page.evaluate('''async()=>{try{await fetch('https://example.invalid/worker-contract')}catch{return true}return false}''')

    result = {
        'contract': page.evaluate('FACTORY_WORKER_RESOURCE_CONTRACT'),
        'idempotent_create': idempotence,
        'concurrent_apply_pairs': concurrent_pairs,
        'handled_error_recovery': handled_error,
        'pending_pagehide': {'start': lifecycle_start, 'hidden': lifecycle_hidden, 'recovery': lifecycle_recovery},
        'warm_validation_ms': warm,
        'warm_validation_p95_ms': warm_p95,
        'idle_reclaims': page.evaluate('browserRuntime.idleReclaims'),
        'repetitions': checkpoints,
        'heap_growth_ratio': last['used_heap'] / first['used_heap'],
        'rss_growth_ratio': last['rss_sum'] / first['rss_sum'],
        'instrumented_peak': peak,
        'worker_creations': page.evaluate('__workerResourceAudit.created.map(({url,name,role,terminated})=>({url,name,role,terminated}))'),
        'cancel': cancelled,
        'recovery': recovered,
        'final_ledger': page.evaluate('factoryWorkerResources.snapshot()'),
        'final_worker_targets': len(worker_targets()),
        'api_requests': [url for url in requests if '/api/' in url],
        'status': 'PASS',
    }
    # Navigate while all three roles are active. The application pagehide
    # handler must synchronously invalidate startup and stop every owned Worker.
    page.evaluate('''()=>{
      const source=S.source+'\\ndef processing_time(machine,lot,ctx):\\n    while True: pass\\n';
      window.__pagehideRunner=new ExperimentRunner();
      EXPERIMENTS.runner=__pagehideRunner;
      window.__pagehideExperiment=(async()=>{
        const definition=await experimentDefinition('pagehide','',source,__resourceModel,[31,32,33,34],2);
        return __pagehideRunner.run(definition);
      })();
    }''')
    page.wait_for_function('()=>__pagehideRunner.workers.size===2&&[...__pagehideRunner.workers].every(worker=>worker.state==="running")', timeout=60000)
    page.evaluate('''()=>{
      DATA.text='id\\n'+('x'.repeat(300)+'\\n').repeat(19000);DATA.format='csv';DATA.table='machines';runDataWorker(true);
    }''')
    teardown_active = page.evaluate('''()=>({ledger:factoryWorkerResources.snapshot(),runnerActive:__pagehideRunner.active,
      runnerWorkers:__pagehideRunner.workers.size,dataActive:DATA.worker!==null,live:{...__workerResourceAudit.live}})''')
    assert teardown_active['ledger'] == {'contractVersion': 1, 'interactive': 1, 'experiment': 2, 'data': 1, 'total': 4}, teardown_active
    assert teardown_active['runnerActive'] and teardown_active['runnerWorkers'] == 2 and teardown_active['dataActive'], teardown_active
    result['active_before_pagehide'] = teardown_active
    page.evaluate('''()=>addEventListener('pagehide',()=>sessionStorage.setItem('factory-worker-pagehide-audit',JSON.stringify({ledger:factoryWorkerResources.snapshot(),live:__workerResourceAudit.live,runnerWorkers:__pagehideRunner.workers.size,dataActive:DATA.worker!==null})))''')
    page.goto(BASE + '/version.json')
    pagehide = json.loads(page.evaluate("sessionStorage.getItem('factory-worker-pagehide-audit')"))
    wait_targets(0, 10)
    assert pagehide == {'ledger': {'contractVersion': 1, 'interactive': 0, 'experiment': 0, 'data': 0, 'total': 0}, 'live': {'interactive': 0, 'experiment': 0, 'other': 0}, 'runnerWorkers': 0, 'dataActive': False}, pagehide
    result['pagehide'] = pagehide
    result['pagehide_worker_targets'] = len(worker_targets())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2), flush=True)
    context.close()
    browser.close()
