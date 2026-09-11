"""Real sandbox seeded repetitions, cancellation/recovery, locale artifacts and bounded UI."""
import atexit,json,subprocess,sys
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_orders import ordered_model
server=None
if len(sys.argv)>1:base=sys.argv[1]
else:
 server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,text=True);atexit.register(server.terminate);base=server.stdout.readline().strip().split(' → ')[-1]
with sync_playwright() as p:
 browser=p.chromium.launch()
 for lang in ['ko','en','ja']:
  page=browser.new_page(locale=lang,viewport={'width':320,'height':844});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto(base);page.wait_for_function('()=>window.factoryStudio?.getState().valid')
  m=ordered_model();m['orders'][1]['release_time']=0;m['orders'][1]['lots']=[{'id':'LAST','quantity':4}]
  source='MODEL = '+repr(m)+'\nimport random\ndef processing_time(machine, lot, ctx):\n    if MODEL["seed"] == 2: raise ValueError("intentional seed failure")\n    return 1 + random.random()\n'
  page.evaluate('(source)=>editor.setValue(source)',source);page.wait_for_function('()=>S.valid&&!S.busy');page.locator('[data-tab="experiments"]').click();page.locator('#ex-seeds').fill('1,2,3');page.locator('#ex-concurrency').select_option('2')
  # Cancel during either actual asynchronous definition digest; no pool may start afterward.
  for blocked_hash in [1,2]:
   page.evaluate('(block)=>{window.exOriginalHash=scenarioHash;window.exHashCalls=0;window.exHashBlocked=false;window.exWorkerCreations=0;window.exOriginalWorker=Worker;window.Worker=class extends exOriginalWorker{constructor(...args){super(...args);exWorkerCreations++}};scenarioHash=async(...args)=>{exHashCalls++;if(exHashCalls===block){exHashBlocked=true;await new Promise(resolve=>window.exReleaseHash=resolve)}return exOriginalHash(...args)}}',blocked_hash)
   page.locator('#ex-run').click();page.wait_for_function('()=>exHashBlocked&&EXPERIMENTS.starting');before_workers=page.evaluate('exWorkerCreations');page.locator('#ex-cancel').click();page.evaluate('exReleaseHash()');page.wait_for_function('()=>!EXPERIMENTS.starting');page.wait_for_timeout(100)
   assert page.evaluate('exWorkerCreations')==before_workers;assert page.evaluate('EXPERIMENTS.runner?.active||false') is False;assert page.evaluate('EXPERIMENTS.items.length')==0;assert page.evaluate('exHashCalls')==blocked_hash
   page.evaluate('()=>{scenarioHash=exOriginalHash;window.Worker=exOriginalWorker}')
  page.evaluate('()=>{window.exTicks=0;window.exLast=performance.now();window.exMaxGap=0;window.exTimer=setInterval(()=>{const now=performance.now();exMaxGap=Math.max(exMaxGap,now-exLast);exLast=now;exTicks++},25)}')
  before_ticks=0
  for i in range(2):
   page.locator('#ex-name').fill('Run '+str(i));page.evaluate('()=>{startExperiment();startExperiment()}');page.wait_for_function('(n)=>EXPERIMENTS.items.length===n&&!EXPERIMENTS.runner.active',arg=i+1,timeout=120000)
   ticks=page.evaluate('exTicks');assert ticks>before_ticks;before_ticks=ticks
  page.evaluate('clearInterval(exTimer)');assert page.evaluate('exMaxGap')<1000
  result=page.evaluate('()=>EXPERIMENTS.items');assert [r['status'] for r in result[0]['runs']]==['success','failed','success'],result
  assert [r.get('trace_hash') for r in result[0]['runs']]==[r.get('trace_hash') for r in result[1]['runs']]
  assert page.evaluate('experimentCompare(...EXPERIMENTS.items).every(r=>r.delta===0||r.delta===null)')
  assert 'Translation unavailable' not in page.locator('.experiments').inner_text();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  with page.expect_download() as download:page.locator('#ex-export').click()
  raw=Path(download.value.path()).read_bytes();before=page.evaluate('JSON.stringify(EXPERIMENTS.items)');tampered=raw.replace(b'"lot": null',b'"lot": 1e309',1);assert tampered!=raw
  page.locator('#ex-import').set_input_files({'name':'tampered.json','mimeType':'application/json','buffer':tampered});expect(page.locator('#ex-error')).to_have_text(page.evaluate('tr("ex_invalid")'));assert page.evaluate('JSON.stringify(EXPERIMENTS.items)')==before
  page.evaluate('EXPERIMENTS.items=[];renderExperiments()');requests=[];page.on('request',lambda r:requests.append(r.url))
  page.locator('#ex-import').set_input_files({'name':'experiments.json','mimeType':'application/json','buffer':raw});page.wait_for_function('()=>EXPERIMENTS.items.length===2');assert page.evaluate('JSON.stringify(EXPERIMENTS.items)')==before
  page.locator('[data-ex-replay="0"]').click();assert not any('/runtime/' in url or '/api/run' in url for url in requests)
  # Terminate both real Workers during long custom processing, then clean recovery.
  slow='MODEL = '+repr(m)+'\ndef processing_time(machine,lot,ctx):\n    while True: pass\n'
  page.evaluate('(source)=>editor.setValue(source)',slow);page.wait_for_function('()=>S.valid&&!S.busy');page.locator('[data-tab="experiments"]').click();page.locator('#ex-run').click();page.wait_for_function('()=>EXPERIMENTS.runner?.workers.size===2&&[...EXPERIMENTS.runner.workers].every(w=>w.state==="running")');page.locator('#ex-cancel').click();page.wait_for_function('()=>!EXPERIMENTS.runner.active',timeout=10000)
  assert page.evaluate('EXPERIMENTS.items.at(-1).runs.every(r=>r.status==="cancelled")')
  page.evaluate('(source)=>editor.setValue(source)',source);page.wait_for_function('()=>S.valid&&!S.busy');page.locator('#ex-seeds').fill('1');page.locator('#ex-run').click();page.wait_for_function('()=>!EXPERIMENTS.runner.active&&EXPERIMENTS.items.at(-1).runs.length===1',timeout=90000);assert page.evaluate('EXPERIMENTS.items.at(-1).runs[0].status')=='success'
  assert len(page.evaluate('EXPERIMENTS.items'))==2;assert not errors,errors
  print('PASS experiments',lang,flush=True);page.close()
 browser.close()
