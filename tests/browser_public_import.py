"""Public import atomicity: real file/Worker/sync plus adversarial async boundaries."""
import csv, io, json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

base=sys.argv[1].rstrip('/')
with sync_playwright() as p:
 browser=p.chromium.launch()
 for lang in ['ko','en','ja']:
  for width in [1440,320]:
   page=browser.new_page(locale=lang,viewport={'width':width,'height':1000})
   errors=[];requests=[]
   page.on('pageerror',lambda e:errors.append(str(e)))
   page.on('request',lambda r:requests.append((r.url,r.method,r.post_data)))
   page.goto(base);page.wait_for_function('()=>S.valid')
   page.locator('#language').select_option(lang);page.locator('[data-tab="data"]').click()
   def snapshot():return page.evaluate('JSON.stringify({source:S.source,model:S.model,result:S.result,revision:S.revision})')
   def prepare():
    model=page.evaluate('S.model');model['name']='Confirmed import '+lang
    doc={'schema_version':1,'kind':'model','model':model}
    before=snapshot()
    page.locator('#data-file').set_input_files({'name':'candidate.json','mimeType':'application/json','buffer':json.dumps(doc).encode()})
    page.wait_for_function('()=>!DATA.busy&&DATA.text!==null')
    expect(page.locator('#data-confirm')).to_be_disabled();assert snapshot()==before
    page.locator('#data-dry-run').click();page.wait_for_function('()=>!DATA.busy&&DATA.pending?.ok')
    assert snapshot()==before
   prepare();before=snapshot();page.locator('#data-cancel').click();assert snapshot()==before
   expect(page.locator('#data-confirm')).to_be_disabled()
   before=snapshot()
   page.evaluate("()=>readDataFile({name:'unreadable.json',size:1,text:async()=>{throw new Error('read failed')}})")
   assert page.evaluate('!DATA.busy&&DATA.phase===null&&DATA.message.code==="data_read_error"')
   assert snapshot()==before
   # Candidate becomes stale before confirmation.
   prepare();page.evaluate('setSource(S.source+"\\n# concurrent edit")');before=snapshot()
   page.locator('#data-confirm').click();assert snapshot()==before;assert page.evaluate('DATA.message.code')=='data_stale'
   for action in ['cancel','stale','failure']:
    prepare();before=snapshot()
    page.evaluate('''()=>{window.originalSync=browserRuntime.sync;window.finishSync=null;
     browserRuntime.sync=async function(...args){const data=await originalSync.apply(this,args);
      await new Promise((resolve,reject)=>{window.finishSync=()=>resolve();window.rejectSync=()=>reject(new Error('probe failure'))});return data}}''')
    page.locator('#data-confirm').click();page.wait_for_function('()=>typeof finishSync==="function"')
    if action=='cancel':page.locator('#data-cancel').click()
    if action=='stale':page.evaluate('setSource(S.source+"\\n# edit during apply")');before=snapshot()
    page.evaluate('rejectSync()' if action=='failure' else 'finishSync()')
    page.wait_for_function('()=>!DATA.busy');page.wait_for_timeout(50)
    assert snapshot()==before,action
    expect(page.locator('#data-confirm')).to_be_disabled()
    page.evaluate('()=>{browserRuntime.sync=originalSync}')
   # Actual mapped CSV model replacement, corrected after a row/field error.
   page.locator('#data-table').select_option('processes')
   processes=page.evaluate('S.model.processes')
   def file_data(bad):
    output=io.StringIO();writer=csv.writer(output);writer.writerow(['identifier','label'])
    for i,row in enumerate(processes):writer.writerow(['' if bad and i==0 else row['id'],'PRIVATE_CSV_NAME' if i==0 else row.get('name',row['id'])])
    return {'name':'processes.csv','mimeType':'text/csv','buffer':output.getvalue().encode()}
   before=snapshot()
   for bad in [True,False]:
    page.locator('#data-file').set_input_files(file_data(bad));page.wait_for_function('()=>!DATA.busy&&DATA.headers.length>0')
    page.locator('[data-map-field="id"]').select_option('identifier');page.locator('[data-map-field="name"]').select_option('label')
    page.locator('#data-dry-run').click();page.wait_for_function('()=>!DATA.busy')
    assert snapshot()==before
    if bad:
     assert page.evaluate('DATA.diagnostics.some(e=>e.row===2&&e.field==="id")')
     expect(page.locator('#data-confirm')).to_be_disabled()
    else:expect(page.locator('#data-confirm')).to_be_enabled()
   page.locator('#data-confirm').click();page.wait_for_function('()=>!DATA.busy&&S.model.processes[0].name==="PRIVATE_CSV_NAME"')
   page.locator('#run-button').click();page.wait_for_function('()=>!S.job&&!S.busy&&S.result?.events.length>0');page.evaluate('pause()')
   assert all(url.startswith(base) and method=='GET' and not body and '/api/' not in url for url,method,body in requests)
   assert not errors,errors
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
   page.locator('[data-tab="data"]').click()
   Path('artifacts').mkdir(exist_ok=True);page.screenshot(path=f'artifacts/public-import-{lang}-{width}.png')
   print(f'PASS public import atomicity/correction/privacy {lang} {width}',flush=True)
   page.close()
 browser.close()
