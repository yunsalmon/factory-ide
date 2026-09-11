"""WIP controls, cursor updates, prefix-bounded drilldown, graph and CSV in real browsers."""
import atexit,csv,io,json,subprocess,sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import execute
ROOT=Path(__file__).resolve().parents[1]
trace=execute((ROOT/'examples/public_demo.py').read_text())
server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],stdout=subprocess.PIPE,text=True,cwd=ROOT)
atexit.register(server.terminate)
base=server.stdout.readline().strip().split(' → ')[-1]
(ROOT/'artifacts').mkdir(exist_ok=True)
with sync_playwright() as p:
 browser=p.chromium.launch()
 for mobile in [False,True]:
  for language in ['ko','en','ja']:
   context=browser.new_context(locale=language,**(p.devices['iPhone 13'] if mobile else {'viewport':{'width':1500,'height':1100}}))
   page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(base);page.wait_for_function('()=>window.factoryStudio?.getState().valid')
   page.evaluate('(trace)=>{pause();S.model=trace.model;S.result=trace;seek(0);selectTab("wip")}',trace)
   assert page.locator('#wip-visible').inner_text()=='0'
   for cursor in [1,4,10,30,len(trace['events'])]:
    page.evaluate('(c)=>seek(c)',cursor)
    oracle={}
    for e in trace['events'][:cursor]:oracle.update(e['state_changes']['lots'])
    assert int(page.locator('#wip-total').inner_text())==sum(l['state']!='completed' for l in oracle.values())
    assert page.locator('#wip-lots tbody tr').count()==min(page.evaluate('WIP_PAGE_SIZE'),len(oracle))
   page.locator('#wip-view').select_option('final')
   assert page.evaluate('wipProjection().time')==trace['summary']['horizon']
   for key in ['process','line','location']:
    values=page.locator(f'[data-wip-filter="{key}"] option').evaluate_all('els=>els.map(e=>e.value).filter(Boolean)')
    if values:
     page.locator(f'[data-wip-filter="{key}"]').select_option(values[0])
     assert page.evaluate('(key)=>wipProjection().rows.every(r=>r[key]===WIP.filters[key])',key)
     assert page.locator('#wip-lots tbody tr').count()==min(page.evaluate('WIP_PAGE_SIZE'),int(page.locator('#wip-visible').inner_text()))
     page.locator('#wip-reset').click()
   page.locator('#wip-group').select_option('process')
   page.locator('[data-wip-group]').first.click()
   assert page.locator('.wip-highlight').count()>0
   assert page.locator('#wip-detail details').count()>0
   page.locator('#wip-group').select_option('location')
   page.locator('#wip-search').fill('LOT-001')
   assert page.locator('#wip-lots tbody tr').count()==1
   page.locator('[data-wip-lot]').first.click()
   assert page.locator('#wip-detail').inner_text().count('LOT-001')>0
   assert page.locator('.wip-highlight').count()>0
   with page.expect_download() as event:page.locator('#wip-csv').click()
   rows=list(csv.reader(io.StringIO(Path(event.value.path()).read_text(encoding='utf-8-sig'))))
   assert len(rows)==2 and rows[1][0]=='LOT-001'
   assert rows[1][-3]==str(len(trace['events']))
   assert float(rows[1][-2])==trace['summary']['horizon']
   # Stable filters and retranslated labels survive locale change.
   target='ja' if language!='ja' else 'en'
   page.locator('#language').select_option(target)
   assert page.locator('#wip-search').input_value()=='LOT-001'
   assert page.locator('#wip-visible').inner_text()=='1'
   assert page.locator('[data-i18n="wip_title"]').inner_text()==page.evaluate('tr("wip_title")')
   page.locator('#language').select_option(language)
   page.locator('[data-wip-reason]').click()
   assert page.locator('#allocation-detail').count()==1
   assert page.evaluate('S.comparison') is not None
   page.locator('[data-tab="wip"]').click();page.locator('#wip-reset').click()
   assert page.locator('#wip-view').input_value()=='replay'
   page.evaluate('seek(4)')
   page.locator('[data-wip-lot]').first.click()
   assert page.evaluate('wipProjection().rows[0].history.every(i=>i<4)')
   assert page.locator('#wip-detail li').count()<=4
   page.locator('#wip-reset').click()
   moving=next(e['index']+1 for e in trace['events'] if e['kind']=='move')
   page.evaluate('(c)=>seek(c)',moving)
   page.locator('[data-wip-filter="status"]').select_option('moving')
   page.locator('[data-wip-lot]').first.click()
   assert page.locator('[data-route].wip-highlight').count()>0
   assert page.locator('#wip-visible').inner_text()==str(page.locator('#wip-lots tbody tr').count())
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),page.evaluate('[document.documentElement.scrollWidth,innerWidth]')
   assert not errors,errors
   page.screenshot(path=str(ROOT/'artifacts'/f'wip-{language}-{"mobile" if mobile else "desktop"}.png'),full_page=True)
   print('PASS WIP',language,'mobile' if mobile else 'desktop',flush=True)
   context.close()
 browser.close()
server.terminate()
