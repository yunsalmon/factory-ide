"""End-to-end checks against a running local server. Run with the optional Playwright dependency."""
import json
import atexit
import subprocess
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'artifacts'
ARTIFACTS.mkdir(exist_ok=True)
if len(sys.argv) > 1:
    base = sys.argv[1]
else:
    server = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--port', '0'], stdout=subprocess.PIPE, text=True, cwd=ROOT)
    atexit.register(server.terminate)
    base = server.stdout.readline().strip().split(' → ')[-1]


def wait_valid(page):
    page.wait_for_function('() => window.factoryStudio?.getState().valid === true')


def edit_code(page, source):
    page.evaluate('(source) => document.querySelector(".CodeMirror").CodeMirror.setValue(source)', source)


def go_end(page):
    page.evaluate('() => window.factoryStudio.pause()')
    page.locator('#timeline').evaluate('(el) => {el.value=el.max;el.dispatchEvent(new Event("input"));}')


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(locale="ko-KR", viewport={'width': 1440, 'height': 1080}, device_scale_factor=1)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(base)
    wait_valid(page)
    expect(page.locator('[data-node]')).to_have_count(6)
    expect(page.locator('[data-route]')).to_have_count(10)
    original = page.locator('#code').input_value()
    page.screenshot(path=str(ARTIFACTS / 'studio-initial.png'), full_page=True)
    print('PASS initial layout and demo')

    # Visual changes preserve handwritten functions and survive a browser reload.
    page.locator('[data-node="CUT_A"]').click()
    page.locator('input[name="time"]').fill('11')
    page.get_by_role('button', name='코드에 적용', exact=True).click()
    page.wait_for_function('() => window.factoryStudio.getState().model.machines[0].time === 11')
    updated = page.locator('#code').input_value()
    assert updated[updated.index('\n\ndef choose_candidate'):] == original[original.index('\n\ndef choose_candidate'):]
    page.reload()
    wait_valid(page)
    assert page.evaluate('() => window.factoryStudio.getState().model.machines[0].time') == 11
    print('PASS visual → code and automatic restore')

    # Direct code changes update the graph. Invalid code must never be overwritten.
    edit_code(page, updated.replace("'time': 11", "'time': 7", 1))
    page.wait_for_function('() => window.factoryStudio.getState().valid && window.factoryStudio.getState().model.machines[0].time === 7')
    valid_source = page.locator('#code').input_value()
    edit_code(page, valid_source + '\ninvalid Python ???')
    expect(page.locator('#sync-status')).to_contain_text('코드 오류')
    page.locator('[data-node="CUT_A"]').click()
    page.locator('input[name="time"]').fill('12')
    page.get_by_role('button', name='코드에 적용', exact=True).click()
    expect(page.locator('#form-error')).to_contain_text('코드 오류')
    assert page.locator('#code').input_value().endswith('invalid Python ???')
    page.locator('#close-inspector').click()
    edit_code(page, valid_source)
    wait_valid(page)
    print('PASS code → graph and invalid-source protection')

    # Route editing, including explicit disabling.
    page.locator('[data-route="r5"]').press('Enter')
    expect(page.locator('input[name="id"]')).to_have_value('r5')
    page.locator('input[name="priority"]').fill('4')
    page.locator('input[name="enabled"]').uncheck()
    page.get_by_role('button', name='코드에 적용', exact=True).click()
    page.wait_for_function('() => window.factoryStudio.getState().model.routes.find(r=>r.id==="r5").enabled === false')
    assert "'priority': 4" in page.locator('#code').input_value()
    page.locator('input[name="enabled"]').check()
    page.get_by_role('button', name='코드에 적용', exact=True).click()
    page.wait_for_function('() => window.factoryStudio.getState().model.routes.find(r=>r.id==="r5").enabled === true')
    page.locator('#close-inspector').click()
    print('PASS route rules')

    # Structural editing: processes, parallel/serial-capable machines, and routes.
    page.locator('#add-process').click()
    page.locator('input[name="name"]').fill('추가 공정')
    page.get_by_role('button', name='코드에 적용', exact=True).click()
    page.wait_for_function('() => window.factoryStudio.getState().model.processes.length === 4')
    page.locator('#add-machine').click()
    page.locator('input[name="name"]').fill('추가 머신')
    page.locator('select[name="process"]').select_option('P1')
    page.get_by_role('button', name='코드에 적용', exact=True).click()
    page.wait_for_function('() => window.factoryStudio.getState().model.machines.length === 7')
    page.locator('#close-inspector').click()
    page.locator('#add-route').click()
    page.locator('select[name="from"]').select_option('INPUT')
    page.locator('select[name="to"]').select_option('M1')
    page.get_by_role('button', name='코드에 적용', exact=True).click()
    page.wait_for_function('() => window.factoryStudio.getState().model.routes.length === 11')
    page.locator('#close-inspector').click()
    page.locator('[data-process="P1"]').click()
    page.locator('#delete-item').click()
    expect(page.locator('#form-error')).to_contain_text('머신을 먼저')
    page.locator('[data-machine="M1"]').click()
    page.locator('#delete-item').click()
    page.wait_for_function('() => window.factoryStudio.getState().model.machines.length === 6 && window.factoryStudio.getState().model.routes.length === 10')
    page.locator('[data-process="P1"]').click()
    page.locator('#delete-item').click()
    page.wait_for_function('() => window.factoryStudio.getState().model.processes.length === 3')
    print('PASS structural creation, deletion and dependency validation')

    page.evaluate('() => {document.querySelector("#run-button").click();document.querySelector("#run-button").click();}')
    page.wait_for_function('() => window.factoryStudio.getState().eventCount > 0')
    go_end(page)
    expect(page.locator('#metric-complete')).to_contain_text('30')
    expect(page.locator('#metric-wip')).to_have_text('0')
    page.locator('#toast').wait_for(state='hidden')
    page.screenshot(path=str(ARTIFACTS / 'studio-result.png'), full_page=True)
    # Decision details, lot history, rewind, and decision breakpoint.
    page.locator('tr[data-event]').filter(has=page.locator('.kind.decision')).first.click()
    expect(page.locator('#inspector-content')).to_contain_text('선택 이유')
    page.locator('#track-lot').click()
    expect(page.locator('#inspector-title')).to_have_text('로트 경로 추적')
    expect(page.locator('.route-path.highlight').first).to_be_visible()
    page.screenshot(path=str(ARTIFACTS / 'studio-routing.png'), full_page=True)
    page.locator('#close-inspector').click()
    page.locator('#rewind-button').click()
    expect(page.locator('#metric-complete')).to_contain_text('0')
    page.locator('#break-decision').check()
    page.locator('#play-button').click()
    expect(page.locator('#inspector-title')).to_have_text('의사결정 / 이벤트')
    expect(page.locator('#inspector-content')).to_contain_text('선택 이유')
    assert page.evaluate('() => window.factoryStudio.getState().playing') is False
    page.locator('#close-inspector').click()
    print('PASS SimPy execution, replay, decision inspection and lot tracing')

    page.locator('#settings-button').click()
    page.locator('select[name="mode"]').select_option('push')
    page.get_by_role('button', name='코드에 적용', exact=True).click()
    page.wait_for_function('() => window.factoryStudio.getState().model.mode === "push"')
    page.locator('#break-decision').uncheck()
    page.locator('#run-button').click()
    page.wait_for_function('() => window.factoryStudio.getState().eventCount > 0')
    go_end(page)
    expect(page.locator('#metric-complete')).to_contain_text('30')
    print('PASS Push execution')

    page.locator('[data-tab="utilization"]').click()
    expect(page.locator('.util-row')).to_have_count(6)
    page.locator('[data-tab="lots"]').click()
    expect(page.locator('[data-lot]')).to_have_count(30)
    with page.expect_download() as download:
        page.locator('#save-button').click()
    assert download.value.suggested_filename == 'factory_model.py'
    with page.expect_download() as trace_download:
        page.locator('#export-trace').click()
    trace = json.loads(Path(trace_download.value.path()).read_text())
    assert trace['summary']['completed'] == 30
    assert len(trace['events']) > 0
    print('PASS metrics, Python download and trace export')

    # Imported Python source and real keyboard editing use the same model contract.
    imported = page.locator('#code').input_value().replace("'name': '두 라인의 유연 생산 공장'", "'name': '불러온 공장'", 1)
    page.locator('#file-input').set_input_files({'name': 'import.py', 'mimeType': 'text/x-python', 'buffer': imported.encode()})
    expect(page.locator('#project-name')).to_have_text('불러온 공장')
    code_input = page.locator('.CodeMirror textarea')
    code_input.focus()
    page.keyboard.press('Control+End')
    page.keyboard.press('Enter')
    page.keyboard.type('# browser keyboard check')
    wait_valid(page)
    assert '# browser keyboard check' in page.locator('#code').input_value()
    page.keyboard.press('Control+z')
    assert '# browser keyboard check' not in page.locator('#code').input_value()
    wait_valid(page)
    print('PASS import, keyboard editing and undo')

    # User errors run in a disposable process and leave the IDE usable.
    good = page.locator('#code').input_value()
    edit_code(page, good + '\nraise RuntimeError("test runtime failure")\n')
    wait_valid(page)
    page.locator('#run-button').click()
    expect(page.locator('#trace-content')).to_contain_text('test runtime failure')
    edit_code(page, good + '\nwhile True:\n    pass\n')
    wait_valid(page)
    page.locator('#run-button').click()
    page.wait_for_function('() => !!window.factoryStudio.getState().job')
    page.locator('#run-button').click()
    expect(page.locator('#status')).to_contain_text('중지')
    edit_code(page, good)
    wait_valid(page)
    print('PASS runtime errors and cancellation')

    # Mobile and role-specific views.
    page.locator('[data-view="planner"]').click()
    expect(page.locator('.editor-panel')).not_to_be_visible()
    page.locator('[data-view="developer"]').click()
    expect(page.locator('.graph-panel')).not_to_be_visible()
    page.locator('[data-view="split"]').click()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path=str(ARTIFACTS / 'studio-mobile.png'), full_page=True)
    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
    print('PASS responsive layout and role views')
    assert not errors, errors
    browser.close()
print('All browser checks passed.')
