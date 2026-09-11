"""Real controls, full CSV, bounded DOM, prefix state, and persistence recovery."""
import atexit,csv,io,json,subprocess,sys
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,text=True)
atexit.register(server.terminate)
base=server.stdout.readline().strip().split(' → ')[-1]
with sync_playwright() as p:
 browser=p.chromium.launch()
 for language in ['ko','en','ja']:
  for width in [1500,320]:
   page=browser.new_page(locale=language,viewport={'width':width,'height':844})
   errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(base);page.wait_for_function('()=>S.valid');page.evaluate('()=>{void run()}');page.wait_for_function('()=>!S.job&&S.result?.events.length>0');page.evaluate('pause()')
   # Preserve legacy small-snapshot semantics at zero, midpoint and final cursor.
   for cursor in [0,page.evaluate('Math.floor(S.result.events.length/2)'),page.evaluate('S.result.events.length')]:
    page.evaluate('c=>{seek(c);selectTab("wip")}',cursor)
    saved=page.evaluate('JSON.stringify({source:S.source,result:S.result,cursor:S.cursor})')
    page.reload();page.wait_for_function('()=>S.valid&&S.result');page.evaluate('pause()')
    assert page.evaluate('JSON.stringify({source:S.source,result:S.result,cursor:S.cursor})')==saved
   # Corrupt position data cannot poison a valid immutable snapshot.
   page.evaluate('localStorage.setItem("factory-studio.replay.v1.position","invalid")')
   page.reload();page.wait_for_function('()=>S.valid&&S.result');page.evaluate('pause()')
   if language=='en' and width==1500:
    # Exercise the asynchronous branch below its bounded recovery quota, then
    # supersede another pending job before its first yield has resumed.
    expected=page.evaluate('''()=>{window.smallTrace=S.result;S.result={...S.result,events:Array.from({length:10001},(_,i)=>({index:i,time:i,kind:'ready'}))};S.cursor=123;persistReplay();return {source:S.source,result:S.result}}''')
    page.wait_for_function('()=>JSON.parse(localStorage.getItem("factory-studio.replay.v1")||"null")?.result.events.length===10001')
    saved=page.evaluate('readReplay("factory-studio.replay.v1")')
    assert saved['result']==expected['result'] and saved['source']==expected['source'] and saved['cursor']==123
    assert page.evaluate('''()=>{const key='factory-studio.replay.v1';const saved=readReplay(key);localStorage.setItem(key+'.position',JSON.stringify({_snapshotId:saved._snapshotId,cursor:0,source:'poison',result:{}}));const got=readReplay(key);return got.source===S.source&&got.result.events.length===10001&&got.cursor===0;}''')
    page.evaluate('()=>{S.result={...S.result};persistReplay();S.result=smallTrace;S.cursor=0;persistReplay()}')
    page.wait_for_timeout(150)
    assert page.evaluate('readReplay("factory-studio.replay.v1").result.events.length===smallTrace.events.length')
   page.evaluate('()=>{'+(ROOT/'tests/large_replay_fixture.js').read_text()+';window.largeReplayFixture=largeReplayFixture;}')
   cancelled=page.evaluate('''async()=>{const controller=new AbortController(),before=S.result;let abort=false;
     try{await largeReplayFixture(S.result,{signal:controller.signal,onProgress:()=>controller.abort()});}catch(e){abort=e.name==='AbortError';}
     return abort&&S.result===before;}''')
   assert cancelled
   cancelled=page.evaluate('''async()=>{window.largeCandidate=await largeReplayFixture(S.result);const old=S.result,source=S.source;
     const saved=SCENARIOS.items;SCENARIOS.items=[{source:S.source,result:largeCandidate}];
     const restore=scenarioLoadResult(0);setTimeout(()=>cancelRun(),0);await restore;SCENARIOS.items=saved;
     return S.result===old&&S.source===source&&!inventoryIndexes.has(largeCandidate)&&!S.busy;}''')
   assert cancelled
   assert page.evaluate('''async()=>{S.busy=true;const old=S.result;
     const preparation=prepareReplayForDisplay(largeCandidate);setTimeout(()=>cancelRun(),0);
     try{await preparation;return false;}catch(error){return error.name==='AbortError'&&!S.busy&&S.result===old&&!inventoryIndexes.has(largeCandidate);}}''')
   page.evaluate('async()=>{await prepareReplayForDisplay(largeCandidate);S.result=largeCandidate;S.model=S.result.model;WIP.filters={};seek(50000);selectTab("wip")}')
   limit=page.evaluate('WIP_PAGE_SIZE')
   assert page.locator('#wip-visible').inner_text()=='1000'
   assert page.locator('[data-wip-lot]').count()==limit
   first=page.locator('[data-wip-lot]').all_text_contents()
   page.locator('[data-wip-page="page:1"]').click()
   second=page.locator('[data-wip-lot]').all_text_contents()
   assert not set(first)&set(second)
   assert page.evaluate('document.activeElement.dataset.wipPage')=='page:1'
   page.locator('[data-wip-lot]').first.click()
   selected=page.evaluate('WIP.selectedLot')
   assert page.locator('.wip-highlight').count()>0
   page.locator('[data-wip-page="page:-1"]').click()
   assert page.evaluate('WIP.selectedLot')==selected
   assert selected in page.locator('#wip-detail').inner_text()
   page.locator('[data-wip-group]').first.click()
   assert page.locator('#wip-detail details').count()==limit
   assert page.locator('#wip-detail li').count()==0
   page.locator('#wip-detail summary').first.click()
   expect(page.locator('#wip-detail li')).to_have_count(50)
   page.locator('#wip-search').fill('PERF_999')
   assert page.locator('[data-wip-lot]').all_text_contents()==['PERF_999']
   assert page.evaluate('document.activeElement.id')=='wip-search'
   page.locator('#wip-reset').click()
   with page.expect_download() as event:page.locator('#wip-csv').click()
   content=Path(event.value.path()).read_text(encoding='utf-8-sig')
   assert len([r for r in csv.reader(io.StringIO(content)) if r and r[0].startswith('PERF_')])==1000
   (ROOT/'artifacts').mkdir(exist_ok=True)
   page.screenshot(path=str(ROOT/'artifacts'/f'large-wip-{language}-{width}.png'),full_page=True)
   for cursor in [0,1,999,1000,25000,50000,999,0]:
    page.evaluate('c=>seek(c)',cursor)
    assert int(page.locator('#wip-visible').inner_text())==min(cursor,1000)
    assert page.evaluate('wipProjection().rows.every(r=>r.history.every(i=>i<S.cursor))')
    ids=page.locator('[data-wip-lot]').all_text_contents();assert len(ids)==len(set(ids))<=limit
   assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
   assert not errors,errors
   print('PASS large WIP',language,width,flush=True);page.close()
 browser.close()
