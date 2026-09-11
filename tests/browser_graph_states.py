"""Issue31: real operational boundaries, keyboard evidence links and scope."""
import atexit
import subprocess
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_operations import line
from test_persona_integration import combined_model

ROOT=Path(__file__).resolve().parents[1]
server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,text=True)
atexit.register(server.terminate)
base=server.stdout.readline().strip().split(' → ')[-1]
ARTIFACTS=ROOT/'artifacts';ARTIFACTS.mkdir(exist_ok=True)


def run(page, model):
    page.evaluate('(s)=>editor.setValue(s)','MODEL = '+repr(model))
    page.locator('#apply-code').click()
    page.wait_for_function('()=>S.valid')
    page.locator('#run-button').click()
    page.wait_for_function('()=>!S.job&&S.result?.events.length>0')
    page.evaluate('pause()')


with sync_playwright() as p:
    browser=p.chromium.launch()
    for lang in ['ko','en','ja']:
        for width in [1440,320]:
            page=browser.new_page(locale='ko-KR',viewport={'width':width,'height':1000})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base);page.wait_for_function('()=>S.valid');page.locator('#language').select_option(lang)
            run(page,line())
            # Compare every actual operation boundary, including same-time events.
            observations=page.evaluate('''()=>{const rows=[];selectTab('events');for(const e of S.result.events){
              if(!Object.keys(e.state_changes.machine_operations||{}).length)continue;
              seek(e.index+1);for(const [id,op] of Object.entries(e.state_changes.machine_operations)){
                const node=[...document.querySelectorAll('[data-node]')].find(n=>n.dataset.node===id);
                rows.push({id,state:op.state,text:node.querySelector('.node-status').textContent,expected:traceStateLabel(op.state),aria:node.getAttribute('aria-label')});}}
              return rows}''')
            assert all(r['text']==r['expected'] and r['expected'] in r['aria'] for r in observations)
            blocked=page.evaluate('S.result.events.find(e=>e.machine==="M"&&e.transition?.current.state==="blocked").index')
            page.evaluate('(i)=>seek(i+1)',blocked)
            node=page.locator('[data-node="M"]');page.locator('#graph-viewport').scroll_into_view_if_needed();page.screenshot(path=str(ARTIFACTS/f'graph-scene-{lang}-{width}.png'));node.focus();page.keyboard.press('Enter')
            expect(page.locator('#graph-operation-detail')).to_be_focused()
            expect(page.locator('#graph-operation-detail .trace-state')).to_have_text(page.evaluate('tr("wip_blocked")'))
            expect(page.locator('#graph-buffer-occupancy')).to_contain_text('BETWEEN')
            expect(page.locator('#graph-buffer-occupancy')).to_contain_text('1')
            expect(page.locator('#graph-operation-detail')).to_contain_text('LOT-001')
            expect(page.locator('#graph-operation-detail')).to_contain_text('LOT-002')
            page.screenshot(path=str(ARTIFACTS/f'graph-operation-{lang}-{width}.png'))
            page.emulate_media(forced_colors='active')
            expect(page.locator('[data-node="M"] .node-status')).to_have_text(page.evaluate('tr("wip_blocked")'))
            if lang=='en' and width==320:page.screenshot(path=str(ARTIFACTS/'graph-forced-colors.png'))
            page.emulate_media(forced_colors='none')
            page.locator('#graph-open-buffer').click()
            assert page.evaluate('S.cursor')==blocked+1
            expect(page.locator('#wip-lots')).to_contain_text('LOT-001')
            expect(page.locator('#wip-lots')).not_to_contain_text('LOT-002')
            page.locator('[data-node="M"]').focus();page.keyboard.press('Space')
            page.locator('#graph-held-lot').click()
            expect(page.locator('#wip-detail')).to_contain_text('LOT-002')
            expect(page.locator('#wip-detail')).to_be_focused()
            page.locator('[data-node="M"]').click()
            state_index=int(page.locator('#graph-state-event').get_attribute('data-graph-event'))
            page.locator('#graph-state-event').click()
            assert page.evaluate('S.cursor')==state_index+1==blocked+1
            expect(page.locator('#inspector-content')).to_contain_text(page.evaluate('tr("wip_blocked")'))
            page.locator('#close-inspector').click();page.locator('[data-node="M"]').click()
            buffer_index=int(page.locator('#graph-buffer-event').get_attribute('data-graph-event'))
            page.locator('#graph-buffer-event').click();assert page.evaluate('S.cursor')==buffer_index+1
            # Final view must not use current replay state; switching back restores it.
            page.locator('#close-inspector').click();page.evaluate('(i)=>seek(i+1)',blocked)
            page.locator('[data-tab="wip"]').click();page.locator('#wip-view').select_option('final')
            assert page.evaluate('graphProjection().cursor')==page.evaluate('S.result.events.length')
            expect(page.locator('[data-node="M"] .node-status')).not_to_have_text(page.evaluate('tr("wip_blocked")'))
            page.locator('#wip-view').select_option('replay')
            expect(page.locator('[data-node="M"] .node-status')).to_have_text(page.evaluate('tr("wip_blocked")'))
            page.locator('[data-tab="operations"]').click();page.locator('#ops-scope').select_option('cursor')
            expect(page.locator('[data-node="M"] .node-status')).to_have_text(page.evaluate('tr("wip_blocked")'))
            page.locator('#ops-scope').select_option('final')
            assert page.evaluate('graphProjection().time')==40
            # True planned-maintenance enum differs from down with cause=maintenance.
            planned=line();planned['machines'][1].pop('availability');planned['machines'][1]['maintenance']=[dict(start=0,end=8)]
            run(page,planned)
            page.evaluate('()=>{selectTab("events");const e=S.result.events.find(e=>e.machine==="M"&&e.transition?.current.state==="blocked");seek(e.index+1)}')
            expect(page.locator('[data-node="N"] .node-status')).to_have_text(page.evaluate('tr("wip_maintenance")'))
            # Added families exercise setup/offshift/resource contention and all enum labels.
            run(page,combined_model())
            seen=page.evaluate('''()=>{selectTab('events');const seen=[];for(const e of S.result.events){for(const [id,op] of Object.entries(e.state_changes.machine_operations||{})){seek(e.index+1);const n=[...document.querySelectorAll('[data-node]')].find(n=>n.dataset.node===id);if(n.querySelector('.node-status').textContent!==traceStateLabel(op.state))throw Error(op.state);seen.push(op.state)}}return seen}''')
            assert {'setup','offshift','resource_wait','processing'} <= set(seen)
            page.evaluate('seek(0)');page.locator('[data-tab="orders"]').click();page.locator('#order-view').select_option('final')
            assert page.evaluate('graphProjection().time')==20
            page.locator('#order-view').select_option('replay');assert page.evaluate('graphProjection().time')==0
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors,errors
            print(f'PASS graph operations {lang} {width}px',flush=True)
            page.close()
    browser.close()
