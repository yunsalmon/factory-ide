"""Real Worker, mapped diagnostics, explicit apply, cancellation and privacy in 3 locales."""
import atexit,json,subprocess,sys
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).parents[1]
server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,text=True);atexit.register(server.terminate)
base=server.stdout.readline().strip().split(' → ')[-1]
with sync_playwright() as p:
 browser=p.chromium.launch()
 for lang in ['ko','en','ja']:
  page=browser.new_page(locale=lang,viewport={'width':320,'height':844});errors=[];requests=[]
  page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append((r.url,r.post_data)))
  page.goto(base);page.wait_for_function('()=>window.factoryStudio?.getState().valid');page.locator('[data-tab="data"]').click()
  expect(page.locator('#data-file')).to_be_visible();assert 'Translation unavailable' not in page.locator('.data-panel').inner_text()
  source=page.evaluate('S.source');model=page.evaluate('S.model');machine=model['machines'][0]['id']
  page.locator('#data-table').select_option('observations')
  csv=f'identifier,kind,time,lot,machine,state\nPRIVATE_RAW_SENTINEL,arrival,0,PRIVATE_LOT,,\nSTATE,state,0,,{machine},idle\n'
  page.locator('#data-file').set_input_files({'name':'observed.csv','mimeType':'text/csv','buffer':csv.encode()})
  page.wait_for_function('()=>!DATA.busy&&DATA.headers.length>0');page.locator('[data-map-field="id"]').select_option('identifier');page.locator('#data-dry-run').click();page.wait_for_function('()=>!DATA.busy&&DATA.pending?.ok')
  assert page.evaluate('S.source')==source;assert page.evaluate('DATA.observations') is None
  page.locator('#data-confirm').click();page.wait_for_function('()=>DATA.observations?.events.length===2');assert page.evaluate('S.source')==source
  assert not any('PRIVATE_RAW_SENTINEL' in (body or '') for _,body in requests)
  assert 'PRIVATE_RAW_SENTINEL' not in page.evaluate('JSON.stringify(localStorage)')
  assert all(url.startswith(base) for url,_ in requests),requests
  bad=f'id,kind,time,machine,state\nBAD,state,0,MISSING,idle'
  page.locator('#data-file').set_input_files({'name':'bad.csv','mimeType':'text/csv','buffer':bad.encode()});page.wait_for_function('()=>!DATA.busy&&DATA.headers.length>0');page.locator('#data-dry-run').click();page.wait_for_function('()=>!DATA.busy&&DATA.diagnostics.length>0')
  assert page.locator('#data-errors tbody tr').first.locator('td').nth(1).inner_text()=='2';assert 'Translation unavailable' not in page.locator('.data-panel').inner_text();assert page.evaluate('S.source')==source
  # Cancel before File.text resolves and terminate an actual running Worker.
  page.evaluate('()=>{readDataFile(new File(["id\\nx"],"cancel.csv"));cancelDataImport()}');page.wait_for_timeout(100);assert page.evaluate('!DATA.busy&&!DATA.pending&&DATA.text===null')
  page.evaluate('()=>{DATA.text="id\\n"+"x\\n".repeat(19000);DATA.format="csv";runDataWorker(true);cancelDataImport()}');page.wait_for_timeout(100);assert page.evaluate('!DATA.busy&&!DATA.pending&&DATA.worker===null')
  # JSON dry run and actual atomic replacement; canonical export survives import.
  model['name']='Imported '+lang;doc={'schema_version':1,'kind':'model','time_unit':'minutes','model':model}
  page.locator('#data-file').set_input_files({'name':'model.json','mimeType':'application/json','buffer':json.dumps(doc).encode()});page.wait_for_function('()=>!DATA.busy&&DATA.pending?.kind==="model"');assert page.evaluate('S.source')==source
  page.locator('#data-confirm').click();page.wait_for_function('()=>!DATA.busy&&S.model.name.startsWith("Imported")');assert page.evaluate('S.model')==model
  with page.expect_download() as dl:page.locator('#data-export').click()
  assert json.loads(Path(dl.value.path()).read_text())==doc
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),lang
  assert not errors,errors
  page.close()
 browser.close()
print('Data browser: ko/en/ja mapped import/errors, Worker cancellation, atomic apply/export, local privacy and mobile overflow PASS')
