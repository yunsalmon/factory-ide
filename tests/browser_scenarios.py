"""Actual run/save, signed comparison, export/import and trace restore in three locales."""
import atexit,json,subprocess,sys
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_orders import ordered_model
server=None
if len(sys.argv)>1:base=sys.argv[1]
else:
 server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,text=True);atexit.register(server.terminate);base=server.stdout.readline().strip().split(' → ')[-1]
with sync_playwright() as p:
 browser=p.chromium.launch()
 for mobile in [False,True]:
  for lang in ['ko','en','ja']:
   context=browser.new_context(locale=lang,viewport={'width':320 if mobile else 1500,'height':844 if mobile else 1100},is_mobile=mobile,has_touch=mobile);page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto(base);page.wait_for_function('()=>window.factoryStudio?.getState().valid')
   for name,duration in [('Baseline',1),('Candidate',25)]:
    m=ordered_model();m['machines'][0]['time']=duration;source='MODEL = '+repr(m)
    page.evaluate('(s)=>editor.setValue(s)',source);page.wait_for_function('()=>S.valid&&!S.busy&&!S.job');page.locator('[data-tab="scenarios"]').click();page.locator('#sc-name').fill(name);page.locator('#sc-notes').fill('raw 사용자 日本語');page.locator('#sc-run').click()
    page.wait_for_function('(count)=>SCENARIOS.items.length===count&&!SCENARIOS.busy',arg=1 if name=='Baseline' else 2,timeout=90000)
   assert page.locator('#sc-error').inner_text()==''
   delta=page.locator('[data-sc-metric="completed_lots"] td').nth(2).inner_text();assert '-' in delta,delta
   expect(page.locator('.scenarios')).to_contain_text(page.evaluate('tr("sc_correlation")'))
   assert page.locator('[data-sc-event]').count()>0
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
   with page.expect_download() as download:page.locator('#sc-export').click()
   raw=Path(download.value.path()).read_bytes();doc=json.loads(raw);assert len(doc['scenarios'])==2;assert doc['scenarios'][0]['notes']=='raw 사용자 日本語';assert doc['scenarios'][0]['runtime']['runtime']['python']
   before=page.evaluate('JSON.stringify(scenarioCompare(SCENARIOS.items[0],SCENARIOS.items[1]))');page.evaluate('SCENARIOS.items=[];renderScenarios()')
   requests=[];page.on('request',lambda req:requests.append(req.url));page.locator('#sc-import').set_input_files({'name':'comparison.json','mimeType':'application/json','buffer':raw});page.wait_for_function('()=>SCENARIOS.items.length===2')
   assert page.evaluate('JSON.stringify(scenarioCompare(SCENARIOS.items[0],SCENARIOS.items[1]))')==before
   page.locator('[data-sc-side="candidate"]').first.click();assert page.evaluate('S.result.events.length')==len(doc['scenarios'][1]['result']['events'])
   page.locator('[data-tab="scenarios"]').click();page.locator('#sc-restore-a').click();assert page.evaluate('S.source')==doc['scenarios'][0]['source'];assert page.evaluate('S.cursor')==len(doc['scenarios'][0]['result']['events'])
   assert not any('/api/run' in url or '/runtime/' in url for url in requests),requests
   page.locator('[data-tab="scenarios"]').click();bad=json.loads(raw);bad['scenarios'][0]['source']+='\n# changed'
   page.locator('#sc-import').set_input_files({'name':'bad.json','mimeType':'application/json','buffer':json.dumps(bad).encode()});expect(page.locator('#sc-error')).not_to_be_empty();assert page.evaluate('SCENARIOS.items.length')==2
   page.reload();page.wait_for_function('()=>window.factoryStudio?.getState().valid');page.locator('[data-tab="scenarios"]').click();page.wait_for_function('()=>SCENARIOS.items.length===2');assert page.evaluate('scenarioCompare(SCENARIOS.items[0],SCENARIOS.items[1]).metrics.find(x=>x.key==="completed_lots").delta')<0
   assert errors==[],errors;print('PASS scenarios',lang,'mobile' if mobile else 'desktop',flush=True);context.close()
 browser.close()
if server:server.terminate()
