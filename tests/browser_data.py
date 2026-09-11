"""Real Worker, mapped diagnostics, explicit apply, cancellation and privacy in 3 locales."""
import atexit,json,subprocess,sys
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).parents[1]
if len(sys.argv)>1:base=sys.argv[1].rstrip('/')
else:
 server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,text=True);atexit.register(server.terminate)
 base=server.stdout.readline().strip().split(' → ')[-1]
def choose_file(page, file):
 page.locator('#data-file').set_input_files(file)
 if file['name'].endswith('.json'):
  page.wait_for_function('()=>!DATA.busy&&DATA.text!==null')
  assert page.evaluate('DATA.pending') is None
  page.locator('#data-dry-run').click()
with sync_playwright() as p:
 browser=p.chromium.launch()
 for lang,width in [(lang,width) for lang in ['ko','en','ja'] for width in [1440,320]]:
  page=browser.new_page(locale=lang,viewport={'width':width,'height':844});errors=[];requests=[]
  page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append((r.url,r.post_data)))
  page.goto(base);page.wait_for_function('()=>window.factoryStudio?.getState().valid');page.locator('[data-tab="data"]').click()
  expect(page.locator('#data-file')).to_be_visible();assert 'Translation unavailable' not in page.locator('.data-panel').inner_text()
  source=page.evaluate('S.source');model=page.evaluate('S.model');machine=model['machines'][0]['id']
  page.locator('#data-table').select_option('observations')
  csv=f'identifier,kind,time,lot,machine,state\nPRIVATE_RAW_SENTINEL,arrival,0,PRIVATE_LOT,,\nSTATE,state,0,,{machine},idle\n'
  choose_file(page, {'name':'observed.csv','mimeType':'text/csv','buffer':csv.encode()})
  page.wait_for_function('()=>!DATA.busy&&DATA.headers.length>0');page.locator('[data-map-field="id"]').select_option('identifier');page.locator('#data-dry-run').click();page.wait_for_function('()=>!DATA.busy&&DATA.pending?.ok')
  assert page.evaluate('S.source')==source;assert page.evaluate('DATA.observations') is None
  page.locator('#data-confirm').click();page.wait_for_function('()=>DATA.observations?.events.length===2');assert page.evaluate('S.source')==source
  assert not any('PRIVATE_RAW_SENTINEL' in (body or '') for _,body in requests)
  assert 'PRIVATE_RAW_SENTINEL' not in page.evaluate('JSON.stringify(localStorage)')
  assert all(url.startswith(base) for url,_ in requests),requests
  bad=f'id,kind,time,machine,state\nBAD,state,0,MISSING,idle'
  choose_file(page, {'name':'bad.csv','mimeType':'text/csv','buffer':bad.encode()});page.wait_for_function('()=>!DATA.busy&&DATA.headers.length>0');page.locator('#data-dry-run').click();page.wait_for_function('()=>!DATA.busy&&DATA.diagnostics.length>0')
  assert page.locator('#data-errors tbody tr').first.locator('td').nth(1).inner_text()=='2';assert 'Translation unavailable' not in page.locator('.data-panel').inner_text();assert page.evaluate('S.source')==source
  # Numeric event times must not bypass document-origin validation (real Worker).
  for origin in ['2026-02-30T00:00:00Z','not-rfc3339','2026-01-01T00:00:00+24:00']:
   doc={'schema_version':1,'kind':'observations','time_origin':origin,'events':[{'id':'x','kind':'arrival','time':0,'lot':'L'}]}
   choose_file(page, {'name':'origin.json','mimeType':'application/json','buffer':json.dumps(doc).encode()});page.wait_for_function('()=>!DATA.busy&&DATA.diagnostics.length>0')
   assert page.evaluate('DATA.pending') is None;assert page.evaluate('DATA.diagnostics.some(d=>d.field==="time_origin"&&d.row===1)');assert page.evaluate('S.source')==source
   assert 'Translation unavailable' not in page.locator('#data-errors').inner_text()
  doc['time_origin']='2024-02-29T23:59:59.999+23:59'
  choose_file(page, {'name':'valid-origin.json','mimeType':'application/json','buffer':json.dumps(doc).encode()});page.wait_for_function('()=>!DATA.busy&&DATA.pending?.ok');assert page.evaluate('DATA.pending.candidate.time_origin')==doc['time_origin']
  page.locator('#data-table').select_option('processes')
  for text,row in [('id,name\nP1,x\nP2,x,extra',3),('id,name\nP1,"line\ncontinued"\nP2,x,extra',4),('id,name\nP1,"line\ncontinued"x',3)]:
   choose_file(page, {'name':'ragged.csv','mimeType':'text/csv','buffer':text.encode()});page.wait_for_function('()=>!DATA.busy&&DATA.diagnostics.length>0')
   assert page.evaluate('DATA.diagnostics[0].row')==row;assert page.locator('#data-errors tbody tr').first.locator('td').nth(1).inner_text()==str(row)
   assert page.evaluate('DATA.diagnostics[0].args[0]')==row;assert page.evaluate('S.source')==source;assert page.evaluate('DATA.pending') is None
  # Cancel before File.text resolves and terminate an actual running Worker.
  page.evaluate('()=>{readDataFile(new File(["id\\nx"],"cancel.csv"));cancelDataImport()}');page.wait_for_timeout(100);assert page.evaluate('!DATA.busy&&!DATA.pending&&DATA.text===null')
  page.evaluate('()=>{DATA.text="id\\n"+"x\\n".repeat(19000);DATA.format="csv";runDataWorker(true);cancelDataImport()}');page.wait_for_timeout(100);assert page.evaluate('!DATA.busy&&!DATA.pending&&DATA.worker===null')
  # JSON dry run and actual atomic replacement; canonical export survives import.
  model['name']='Imported '+lang;doc={'schema_version':1,'kind':'model','time_unit':'minutes','model':model}
  choose_file(page, {'name':'model.json','mimeType':'application/json','buffer':json.dumps(doc).encode()});page.wait_for_function('()=>!DATA.busy&&DATA.pending?.kind==="model"');assert page.evaluate('S.source')==source
  page.locator('#data-confirm').click();page.wait_for_function('()=>!DATA.busy&&S.model.name.startsWith("Imported")');assert page.evaluate('S.model')==model
  with page.expect_download() as dl:page.locator('#data-export').click()
  assert json.loads(Path(dl.value.path()).read_text())==doc
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),lang
  assert not errors,errors
  if len(sys.argv)>1:assert not any('/api/' in url or body for url,body in requests),requests
  page.locator('#run-button').click();page.wait_for_function('()=>!S.job&&!S.busy&&S.result?.events.length>0')
  page.evaluate('pause()')
  print(f'PASS data {lang} {width}px',flush=True)
  page.close()
 browser.close()
print('Data browser: ko/en/ja mapped import/errors, Worker cancellation, atomic apply/export, local privacy and mobile overflow PASS')
