"""Operator forms, translations, replay and KPI reconciliation in Chromium."""
import atexit
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from test_disruptions import parallel
ROOT=Path(__file__).resolve().parents[1]
server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,text=True)
atexit.register(server.terminate)
base=server.stdout.readline().strip().split(' → ')[-1]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(locale='en-US',viewport={'width':1440,'height':1000})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base);page.wait_for_function('() => window.factoryStudio?.getState().valid')
    model=parallel();model['machines'][0]['availability']=[dict(state='offshift',start=1,end=3)]
    page.evaluate('(source)=>document.querySelector(".CodeMirror").CodeMirror.setValue(source)','MODEL = '+repr(model))
    page.wait_for_function('() => window.factoryStudio.getState().valid')
    page.locator('[data-tab="operations"]').click()
    page.locator('#ops-configure').click()
    page.locator('[name="setup_time"]').fill('0.5')
    page.locator('[name="initial_family"]').fill('A')
    page.locator('#ops-add-setup').click()
    row=page.locator('.ops-setup-row').last
    row.locator('input').nth(0).fill('A');row.locator('input').nth(1).fill('B');row.locator('input').nth(2).fill('2')
    page.locator('#ops-add-window').click()
    row=page.locator('.ops-window').last
    row.locator('select').select_option('maintenance');row.locator('input').nth(0).fill('10');row.locator('input').nth(1).fill('11')
    page.locator('#ops-add-window').click()
    row=page.locator('.ops-window').last
    row.locator('select').select_option('failure');row.locator('input').nth(0).fill('12');row.locator('input').nth(1).fill('13')
    page.locator('[name="failure_mode"]').select_option('deterministic')
    page.locator('#language').select_option('ja')
    expect(page.locator('#inspector-title')).to_have_text('シフト・段取り・故障設定')
    expect(page.locator('[name="setup_time"]')).to_have_value('0.5')
    page.locator('#ops-form [type="submit"]').click()
    page.wait_for_function('() => window.factoryStudio.getState().model.machines[0].maintenance?.[0].start === 10')
    page.locator('#close-inspector').click()
    page.locator('#ops-resources').click();page.locator('#ops-add-resource').click()
    row=page.locator('.ops-resource-row').last
    row.locator('input').nth(0).fill('TOOL');row.locator('select').select_option('tool');row.locator('input').nth(1).fill('1')
    page.locator('#ops-form [type="submit"]').click()
    page.wait_for_function('() => window.factoryStudio.getState().model.resources.length === 2')
    page.locator('#close-inspector').click();page.locator('#ops-configure').click()
    page.locator('[name="processing_resource_TOOL"]').fill('1')
    page.locator('#ops-form [type="submit"]').click()
    page.wait_for_function('() => window.factoryStudio.getState().model.machines[0].resource_requirements.processing.TOOL === 1')
    page.locator('#close-inspector').click()
    page.locator('#run-button').click();page.wait_for_function('() => window.factoryStudio.getState().eventCount > 0')
    page.evaluate('() => window.factoryStudio.pause()')
    page.locator('[data-tab="operations"]').click()
    expect(page.locator('#ops-kpis tbody tr')).to_have_count(2)
    expect(page.locator('.ops-total').first).to_have_text('20.0')
    for language,title in [('ko','설비 운영'),('en','Machine operations'),('ja','設備運用')]:
        page.locator('#language').select_option(language)
        expect(page.locator('.ops-toolbar b')).to_have_text(title)
    with page.expect_download() as download:page.locator('#export-trace').click()
    trace=json.loads(Path(download.value.path()).read_text())
    for mid, totals in trace['operation_metrics']['machines'].items():
        assert abs(sum(totals.values())-20)<1e-8
        row=page.locator('#ops-kpis tbody tr').filter(has_text=mid).first
        for state,value in totals.items():
            assert abs(float(row.locator('[data-state="'+state+'"]').inner_text())-round(value,1))<.01
    wait=next(e for e in trace['events'] if e['kind']=='resource_wait')
    page.locator('#ops-scope').select_option('cursor')
    page.locator('#timeline').evaluate('(el,cursor)=>{el.value=cursor;el.dispatchEvent(new Event("input"));}',wait['index']+1)
    expect(page.locator('#ops-resource-state')).to_contain_text(wait['resource_request']['lot'])
    assert page.locator('.ops-span').count()>0
    page.locator('#rewind-button').click();expect(page.locator('#ops-horizon')).to_have_text('0.0')
    assert not errors,errors
    browser.close()
print('PASS operator forms, ko/en/ja, resource replay and KPI reconciliation')
