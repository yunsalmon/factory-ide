"""Pinned large replay profile. Fixture generation/transport excluded from interactions."""
import atexit,json,sys,subprocess,os,math
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE=Path(__file__).resolve().parents[1]
ROOT=Path(os.environ.get('FACTORY_TEST_ROOT',HERE))
server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,text=True);atexit.register(server.terminate)
base=server.stdout.readline().strip().split(' → ')[-1];rows=[]
out=Path(sys.argv[1] if len(sys.argv)>1 else '/tmp/large-replay.json')
with sync_playwright() as p:
 browser=p.chromium.launch()
 for width,cpu in [(1500,1),(320,4)]:
  page=browser.new_page(viewport={'width':width,'height':1100 if width>320 else 844},locale='en')
  cdp=page.context.new_cdp_session(page);cdp.send('Emulation.setCPUThrottlingRate',{'rate':cpu})
  page.goto(base);page.wait_for_function('()=>S.valid');page.evaluate('()=>{void run()}');page.wait_for_function('()=>!S.job&&S.result?.events.length>0');page.evaluate('pause()')
  page.evaluate('()=>{'+(HERE/'tests/large_replay_fixture.js').read_text()+';window.largeReplayFixture=largeReplayFixture;}')
  prep=page.evaluate('()=>{const t=performance.now();S.result=largeReplayFixture(S.result);const fixture=performance.now()-t;S.model=S.result.model;S.selected=null;S.lot=null;WIP.filters={};WIP.selectedLot=null;const s=performance.now();if(typeof inventoryReplay==="function")inventoryReplay(S.result,0);const index=performance.now()-s;const v=performance.now();seek(0);selectTab("wip");return {fixture,index,install:performance.now()-v}}')
  print(width,'preparation',prep,flush=True)
  page.wait_for_timeout(300)
  page.evaluate('''()=>{window.longTasks=[];new PerformanceObserver(l=>longTasks.push(...l.getEntries().map(e=>({start:e.startTime,duration:e.duration})))).observe({type:'longtask'});window.parts={};
   for(const key of ['stateAt','inventoryProjection','renderInventory','renderGraph','persistReplay']){const f=window[key];window[key]=function(...args){const t=performance.now();try{return f(...args)}finally{(parts[key]??=[]).push(performance.now()-t)}}}}''')
  for action,code in [
   ('cursor','seek([50000,49999,25000,1000,40000,0,50000][n%7])'),
   ('tab','selectTab(n%2?"events":"wip")'),
   ('filter','WIP.filters={status:n%2?"waiting":""};renderInventory()'),
   ('search','WIP.filters={search:n%2?"PERF_9":""};renderInventory()'),
   ('select','WIP.filters={};WIP.selectedLot="PERF_"+(n%1000);renderInventory()')]:
   page.evaluate('()=>{WIP.filters={};seek(50000);selectTab("wip");parts={};longTasks=[]}')
   samples=[]
   for n in range(20):
    samples.append(page.evaluate('''async({code,n})=>{const start=performance.now();eval(code);const sync=performance.now()-start;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));return {sync,total:performance.now()-start}}''',dict(code=code,n=n)))
   record=page.evaluate('({parts,longTasks,nodes:document.querySelectorAll("*").length,rows:document.querySelectorAll("[data-wip-lot]").length,ids:[...document.querySelectorAll("[data-wip-lot]")].map(e=>e.textContent),overflow:document.documentElement.scrollWidth>innerWidth})')
   p95=sorted(x['total'] for x in samples)[math.ceil(.95*len(samples))-1]
   record.update(width=width,cpu=cpu,action=action,samples=samples,p95=p95,browser=browser.version,preparation=prep)
   rows.append(record);out.write_text(json.dumps(rows,indent=2));print(width,action,'p95',p95,flush=True)
  page.close()
 browser.close()
if os.environ.get('FACTORY_PERF_GATE')=='1':
 for r in rows:
  assert r['p95']<=100,(r['width'],r['action'],r['p95'])
  assert not r['overflow']
  assert len(r['ids'])==len(set(r['ids']))
  assert all(s['sync']<200 for s in r['samples'])
  assert all(t['duration']<200 for t in r['longTasks']),(r['action'],r['longTasks'])
