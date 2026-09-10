"""Planner round trips, cursor metrics, dispatch explanations and responsive locales."""
import atexit
import copy
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_orders import ordered_model
from engine import Factory
server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,text=True)
atexit.register(server.terminate)
base=server.stdout.readline().strip().split(' → ')[-1]
(ROOT/'artifacts').mkdir(exist_ok=True)
model=ordered_model();trace=Factory(model).run()
with sync_playwright() as p:
    browser=p.chromium.launch()
    for mobile in [False,True]:
        for lang in ['ko','en','ja']:
            page=browser.new_page(locale=lang,viewport={'width':320 if mobile else 1500,'height':844 if mobile else 1100})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base);page.wait_for_function('()=>window.factoryStudio?.getState().valid')
            page.locator('[data-tab="orders"]').click()
            expect(page.locator('#order-table')).to_be_visible()
            assert page.locator('#order-table tbody tr').count()==0
            # Import into the actual model via the existing parse/sync API.
            doc={'schema_version':1,'orders':model['orders'],'order_risk_window':3}
            page.locator('#order-import').set_input_files({'name':'orders.json','mimeType':'application/json','buffer':json.dumps(doc).encode()})
            page.wait_for_function('()=>S.model.orders?.length===2')
            assert page.locator('#order-total-unreleased_qty').inner_text()=='9'
            assert page.locator('#order-table tbody tr').count()==2
            with page.expect_download() as download:page.locator('#order-export').click()
            exported=json.loads(Path(download.value.path()).read_text())
            assert exported==doc
            assert page.evaluate('()=>S.model.orders')==model['orders']
            # Inline invalid JSON and quantity validation are localized; neither mutates source.
            source=page.evaluate('S.source')
            page.locator('#order-edit').click()
            page.locator('#order-document').fill('{bad')
            page.locator('.order-editor button[type="submit"]').click()
            expect(page.locator('#order-error')).to_have_text(page.evaluate('tr("order_document_invalid")'))
            bad=copy.deepcopy(doc);bad['orders'][0]['quantity']=99
            page.locator('#order-document').fill(json.dumps(bad))
            page.locator('.order-editor button[type="submit"]').click()
            expect(page.locator('#order-error')).to_have_text(page.evaluate('tr("order_quantity_mismatch",["ORDER_A",99,5])'))
            assert page.evaluate('S.source')==source
            for field in ['due_date','time_origin']:
                bad=copy.deepcopy(doc);bad['time_origin']='2026-09-10T00:00:00Z'
                if field=='due_date':bad['orders'][0]['due_date']='2026-09-10T00:00:00+00:60'
                else:bad['time_origin']='2026-09-10T00:00:00-24:00'
                page.locator('#order-document').fill(json.dumps(bad))
                page.locator('.order-editor button[type="submit"]').click()
                expect(page.locator('#order-error')).to_have_text(page.evaluate('(field)=>tr("order_invalid",[field])','ORDER_A.due_date' if field=='due_date' else 'time_origin'))
                assert page.evaluate('S.source')==source
            page.locator('#order-close').click()
            if lang=='en' and not mobile:
                page.locator('#run-button').click()
                page.wait_for_function('()=>S.result?.order_plan?.length===2 && !S.job')
                actual=page.evaluate('S.result.events.filter(e=>e.kind==="order_release").map(e=>[e.lot.id,e.time])')
                assert actual==[['LOT_A1',0],['LOT_A2',3],['ORDER_B',40]]
                page.evaluate('seek(S.result.events.length);selectTab("orders")')
                page.locator('[data-order-row]').first.click()
                page.locator('#order-detail details').first.locator('summary').click()
                expect(page.locator('#order-detail')).to_contain_text(page.evaluate('tr("order_custom_reason")'))
                expect(page.locator('#order-detail')).not_to_contain_text(page.evaluate('tr("order_default_reason")'))
            # Correct fixture model/trace includes delayed release and incomplete future order.
            page.evaluate('(r)=>{S.model=r.model;S.result=r;seek(0);selectTab("orders")}',trace)
            assert page.locator('#order-total-throughput').inner_text()=='0'
            for cursor in [1,5,20,len(trace['events'])]:
                page.evaluate('(c)=>seek(c)',cursor)
                state={}
                for event in trace['events'][:cursor]:state.update(event['state_changes']['lots'])
                expected=sum(l['quantity'] for l in state.values() if l['state']=='completed')
                assert float(page.locator('#order-total-throughput').inner_text())==expected
            page.locator('#order-view').select_option('final')
            assert page.evaluate('plannerProjection().time')==30
            page.locator('#order-risk').select_option('late')
            assert page.locator('#order-table tbody tr').count()==1
            assert page.locator('#order-total-quantity').inner_text()=='5'
            with page.expect_download() as download:page.locator('#order-csv').click()
            rows=list(csv.reader(io.StringIO(Path(download.value.path()).read_text(encoding='utf-8-sig'))))
            assert len(rows)==2 and rows[1][0]=='ORDER_A' and rows[1][2]=='5'
            assert rows[0][0]==page.evaluate('tr("order_id")')
            assert rows[1][-2]=='30' and rows[1][-3]==str(len(trace['events']))
            assert rows[1][12]=='1'
            page.locator('[data-order-row]').click()
            page.locator('#order-detail details').first.locator('summary').click()
            expect(page.locator('#order-detail')).to_contain_text('LOT_A1')
            expect(page.locator('#order-detail')).to_contain_text(page.evaluate('tr("order_default_reason")'))
            decision=int(page.locator('[data-order-decision]').first.get_attribute('data-order-decision'))
            page.locator('[data-order-lot]').first.click()
            assert page.evaluate('S.lot')=='LOT_A1'
            expect(page.locator('#inspector')).to_be_visible()
            page.evaluate('closeInspector()')
            page.locator('#order-detail details').first.locator('summary').click()
            page.locator('[data-order-decision]').first.click()
            assert page.evaluate('S.comparison')==decision
            assert page.evaluate('S.cursor')==decision+1
            expect(page.locator('#allocation-detail')).to_be_visible()
            page.locator('[data-tab="orders"]').click()
            page.locator('#order-reset').click()
            page.locator('#order-progress').select_option('unreleased')
            assert page.locator('#order-table tbody tr').count()==1
            assert page.locator('#order-total-quantity').inner_text()=='4'
            page.locator('#order-search').fill('ORDER_B')
            page.locator('#language').select_option('ja' if lang!='ja' else 'ko')
            assert page.locator('#order-search').input_value()=='ORDER_B'
            assert page.locator('#order-progress').input_value()=='unreleased'
            page.locator('#language').select_option(lang)
            # Native select focus survives dashboard replacement.
            page.locator('#order-progress').focus();page.keyboard.press('ArrowUp')
            assert page.evaluate('document.activeElement.id')=='order-progress'
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert page.locator('#order-table th:not([scope])').count()==0
            # Before any release, calendar events are visible without phantom lots.
            delayed=ordered_model();delayed['orders']=[dict(id='DELAYED',product='A',quantity=1,release_time=10)]
            delayed['machines'][0]['availability']=[dict(state='down',start=1,end=3,cause='repair')]
            delayed_trace=Factory(delayed).run()
            down=next(e for e in delayed_trace['events'] if e.get('transition',{}).get('current',{}).get('state')=='down')
            page.evaluate('([r,c])=>{S.model=r.model;S.result=r;S.selected=null;S.lot=null;seek(c);selectTab("events")}',[delayed_trace,down['index']+1])
            page.locator(f'[data-event="{down["index"]}"]').click()
            expect(page.locator('#inspector')).to_contain_text(page.evaluate('tr("order_down")'))
            assert page.evaluate('Object.keys(stateAt().lots).length')==0
            page.evaluate('closeInspector()')
            for tab in ['results','allocations','lots','utilization','orders']:
                page.locator(f'[data-tab="{tab}"]').click()
            # Actual issue11 adapter is automatically selected by plannerProjection.
            pending=ordered_model();pending['orders']=[dict(id='PENDING',product='A',quantity=2,lots=[dict(id='L1',quantity=1),dict(id='L2',quantity=1)])]
            pending['buffers']=[dict(id='IN',at='INPUT',capacity=1)];pending['duration']=2
            pending['machines'][0]['availability']=[dict(state='down',start=0,end=10)]
            page.route('**/issue11-adapter-test.js',lambda route:route.fulfill(content_type='text/javascript',body=(ROOT/'tests/fixtures/inventory-projection-issue11.js').read_text()))
            page.add_script_tag(url=base+'/issue11-adapter-test.js')
            page.evaluate('(r)=>{S.model=r.model;S.result=r;PLANNER.filters={};PLANNER.finalView=false;S.selected=null;S.lot=null;seek(r.events.length);selectTab("orders")}',Factory(pending).run())
            assert page.evaluate('plannerProjection().rows[0].locations')=={'IN':1,'INPUT (release)':1}
            assert not errors,errors
            page.screenshot(path=str(ROOT/'artifacts'/f'orders-{lang}-{"mobile" if mobile else "desktop"}.png'),full_page=True)
            print('PASS orders',lang,'mobile' if mobile else 'desktop',flush=True)
            page.close()
    browser.close()
server.terminate()
