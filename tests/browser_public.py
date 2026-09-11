"""Run the editable browser sandbox against a static public container or URL.

Usage: python tests/browser_public.py http://127.0.0.1:3084
"""
import hashlib
import csv
import io
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect


base = sys.argv[1].rstrip('/')
with urlopen(base + '/demo.json') as response:
    bundle = json.load(response)
# These assets must be present in the static Docker image, not provided by APIs.
for asset in ['inventory-projection.js', 'inventory-ui.js', 'allocation-results.js',
              'browser-runtime.js', 'browser-worker.js', 'locales.json',
              'order-projection.js', 'order-ui.js', 'operations.js',
              'scenario-comparison.js', 'scenario-ui.js', 'experiment-core.js', 'experiment-ui.js', 'data-core.js', 'data-ui.js', 'data-worker.js',
              'runtime/orders.py', 'runtime/disruptions.py', 'runtime/operation_metrics.py',
              'runtime/engine.py', 'runtime/model.py', 'runtime/messages.py']:
    with urlopen(base + '/' + asset) as response:
        assert response.status == 200 and response.read(), asset

module_texts=[]
for path in ['runtime/engine.py','runtime/model.py','runtime/messages.py','runtime/orders.py',
             'runtime/disruptions.py','runtime/operation_metrics.py','locales.json']:
    with urlopen(base + '/' + path) as response:
        module_texts.append(response.read().decode())
factory_hash=hashlib.sha256(json.dumps(module_texts,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
result = bundle['result']
version = bundle['version']
assert hashlib.sha256(bundle['source'].encode()).hexdigest() == version['source_sha256']
assert hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == version['trace_sha256']
assert version['browser_runtime']['pyodide'] == '0.27.7'
assert version['browser_runtime']['python'] == '3.12.7'
assert version['browser_runtime']['simpy'] == '4.1.1'


def edit(page, source):
    page.evaluate('(value) => document.querySelector(".CodeMirror").CodeMirror.setValue(value)', source)


def wait_checked(page):
    wait_for(page, 'window.factoryStudio.getState().valid', 210)


def wait_for(page, expression, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page.evaluate(f'Boolean({expression})'):
            return
        page.wait_for_timeout(50)
    raise AssertionError(f'Timed out waiting for: {expression}')


def run(page):
    page.locator('#run-button').click()
    wait_for(page, 'window.factoryStudio.getState().job', 10)
    wait_for(page, '!window.factoryStudio.getState().job', 30)


def check_wip_pages(page, expected_ids):
    limit = page.evaluate('WIP_PAGE_SIZE')
    first = page.locator('[data-wip-lot]').all_text_contents()
    assert len(first) == min(limit, len(expected_ids))
    assert len(first) == len(set(first))
    if len(expected_ids) > limit:
        page.locator('[data-wip-page="page:1"]').click()
        second = page.locator('[data-wip-lot]').all_text_contents()
        assert 0 < len(second) <= limit and len(second) == len(set(second))
        assert not set(first) & set(second)
        page.locator('[data-wip-page="page:-1"]').click()
        assert page.locator('[data-wip-lot]').all_text_contents() == first
    with page.expect_download() as download:
        page.locator('#wip-csv').click()
    content = Path(download.value.path()).read_text(encoding='utf-8-sig')
    exported = [row[0] for row in list(csv.reader(io.StringIO(content)))[1:]]
    assert len(exported) == len(set(exported)) == len(expected_ids)
    assert set(exported) == set(expected_ids)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for lang in ['ko', 'en', 'ja']:
        context = browser.new_context(locale=lang, viewport={'width': 1600, 'height': 1100})
        page = context.new_page()
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append(request.url))
        page.add_init_script("localStorage.setItem('factory-studio.source.v1', 'raise RuntimeError(123)')")
        page.goto(base + '/?demo=1#replay')
        wait_for(page, 'window.factoryStudio?.getState().eventCount > 0')
        expect(page.locator('html')).to_have_attribute('lang', lang)
        expect(page.locator('#run-button')).to_be_visible()
        expect(page.locator('#reset-button')).to_be_visible()
        assert page.locator('.CodeMirror').evaluate('(el) => el.CodeMirror.getOption("readOnly")') is False
        assert page.locator('.CodeMirror').evaluate('(el) => el.CodeMirror.getValue()') == bundle['source']
        expect(page.locator('#demo-version')).to_contain_text('Pyodide 0.27.7')

        changed = bundle['source'].replace("'time': 7", "'time': 13", 1)
        edit(page, changed)
        wait_checked(page)
        run(page)
        custom = page.evaluate('window.factoryStudio.getState().result')
        assert custom['schema_version'] == 2
        assert custom['model']['machines'][0]['time'] == 13
        assert custom['events'] != result['events']
        assert custom['execution']['kind'] == 'browser'
        assert custom['execution']['runtime'] == {'pyodide': '0.27.7', 'python': '3.12.7', 'simpy': '4.1.1', 'factory_sha256': factory_hash}
        assert custom['execution']['source_sha256'] == hashlib.sha256(changed.encode()).hexdigest()
        assert not any('/api/' in url for url in requests), requests

        # The same worker-produced result feeds every operator dashboard, on
        # desktop and mobile, without injecting a separate server result.
        for width, height in [(1600, 1100), (390, 844)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.evaluate('()=>{pause();seek(S.result.events.length);selectTab("wip")}')
            assert page.evaluate('wipProjection().allTotals.count') == custom['summary']['arrived']
            assert page.evaluate('wipProjection().allTotals.wip') == custom['summary']['arrived'] - custom['summary']['completed']
            check_wip_pages(page, page.evaluate('wipProjection().rows.map(row=>row.id)'))
            page.locator('[data-wip-lot]').first.click()
            page.locator('[data-wip-reason]').click()
            assert page.locator('#allocation-detail').count() == 1
            page.locator('[data-tab="results"]').click()
            assert page.locator('#results-view').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.set_viewport_size({'width': 1600, 'height': 1100})

        # Public static delivery also exercises the 50k-event/1000-lot contract.
        # Construct in-page with yielding; do not transport the fixture object.
        fixture = (Path(__file__).parent / 'large_replay_fixture.js').read_text()
        page.evaluate('()=>{' + fixture + ';window.largeReplayFixture=largeReplayFixture;}')
        page.evaluate('''async()=>{pause();window.publicOriginalResult=S.result;
          const result=await largeReplayFixture(S.result);await prepareInventoryReplay(result);
          S.result=result;S.model=result.model;WIP.filters={};WIP.selectedLot=null;seek(50000);selectTab('wip');}''')
        for width in [1600, 320]:
            page.set_viewport_size({'width': width, 'height': 1100 if width > 320 else 844})
            page.locator('#wip-reset').click()
            check_wip_pages(page, [f'PERF_{i}' for i in range(1000)])
            page.locator('[data-wip-page="page:1"]').click()
            page.locator('[data-wip-lot]').first.click()
            selected = page.evaluate('WIP.selectedLot')
            assert page.locator('.wip-highlight').count() > 0
            page.locator('[data-wip-page="page:-1"]').click()
            assert page.evaluate('WIP.selectedLot') == selected
            assert selected in page.locator('#wip-detail').inner_text()
            page.locator('[data-wip-filter="status"]').select_option('waiting')
            assert page.locator('#wip-visible').inner_text() == '1000'
            page.locator('#wip-search').fill('PERF_999')
            assert page.locator('[data-wip-lot]').all_text_contents() == ['PERF_999']
            check_wip_pages(page, ['PERF_999'])
            page.locator('#wip-reset').click()
            assert page.locator('#wip-visible').inner_text() == '1000'
            assert page.locator('[data-wip-lot]').count() <= page.evaluate('WIP_PAGE_SIZE')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.evaluate('()=>{S.result=publicOriginalResult;S.model=S.result.model;WIP.filters={};WIP.selectedLot=null;seek(0);selectTab("events")}')
        page.set_viewport_size({'width': 1600, 'height': 1100})

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from test_persona_integration import combined_model
        edit(page, 'MODEL = ' + repr(combined_model()))
        wait_checked(page)
        run(page)
        integrated = page.evaluate('S.result')
        assert integrated['order_schema_version'] == 1
        assert integrated['operational_schema_version'] == 2
        assert integrated['summary']['completed'] == 4
        nullable = next(e['index'] for e in integrated['events'] if e['lot'] is None)
        pending = next(e['index'] + 1 for e in integrated['events'] if e['kind'] == 'order_release')
        for width in [1600, 390]:
            page.set_viewport_size({'width': width, 'height': 1000})
            for cursor in [nullable + 1, pending, len(integrated['events'])]:
                for tab in ['events', 'wip', 'orders', 'operations', 'results', 'allocations', 'lots', 'utilization', 'console', 'scenarios', 'data', 'experiments']:
                    page.evaluate('([c,t])=>{seek(c);selectTab(t)}', [cursor, tab])
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.evaluate('(i)=>inspectEvent(i)', nullable)
        page.evaluate('(c)=>{seek(c);selectTab("wip")}', pending)
        assert page.evaluate('wipProjection().rows.some(r=>r.status==="release_pending" && r.kind!=="input")')
        page.set_viewport_size({'width': 1600, 'height': 1100})

        syntax_source = changed + '\ninvalid Python ???'
        syntax_line = len(syntax_source.splitlines())
        edit(page, syntax_source)
        wait_for(page, '!window.factoryStudio.getState().valid')
        page.locator('[data-tab="console"]').click()
        expect(page.locator('.console')).to_contain_text({'ko': '행:', 'en': 'Line ', 'ja': '行:'}[lang])
        page.evaluate('editor.scrollIntoView({line: errorLine, ch: 0}, 60)')
        assert page.evaluate('errorLine') == syntax_line - 1
        expect(page.locator('.code-error-line')).to_have_count(1)

        invalid_model = changed.replace("'duration': 55", "'duration': -1", 1)
        edit(page, invalid_model)
        wait_for(page, '!window.factoryStudio.getState().valid')
        expected_model_error = page.evaluate("tr('message_1', [{code:'message_6'}, .01, 100000])")
        expect(page.locator('.console')).to_contain_text(expected_model_error)

        raw_error = changed + '\nraise RuntimeError("RAW 사용자 日本語 failure")\n'
        edit(page, raw_error)
        wait_checked(page)
        run(page)
        expect(page.locator('.console')).to_contain_text('RAW 사용자 日本語 failure')
        page.evaluate('editor.scrollIntoView({line: errorLine, ch: 0}, 60)')
        assert page.evaluate('errorLine') == len(raw_error.splitlines()) - 1
        expect(page.locator('.code-error-line')).to_have_count(1)

        if lang == 'ko':
            push = changed.replace("'mode': 'pull'", "'mode': 'push'", 1).replace(
                "return chosen['id'], message('message_25')", "return chosen['id'], 'custom chooser raw reason'", 1
            )
            edit(page, push)
            wait_checked(page)
            run(page)
            push_result = page.evaluate('window.factoryStudio.getState().result')
            assert push_result['model']['mode'] == 'push'
            assert any(event.get('reason') == 'custom chooser raw reason' for event in push_result['events'])
            assert push_result['allocations']

            process = changed + "\n\ndef process_lot(env, machine, lot, context):\n    yield env.timeout(machine['time'] + 1)\n"
            edit(page, process)
            wait_checked(page)
            run(page)
            process_result = page.evaluate('window.factoryStudio.getState().result')
            assert process_result['schema_version'] == 2 and process_result['allocations']
            assert all(event.get('duration') is None for event in process_result['events'] if event['kind'] == 'start')

            # The allowlist is a compatibility guide, not the security boundary.
            # Bypass it deliberately: writes remain in WASM VFS and CSP blocks the
            # cross-origin request before it reaches the network.
            host_marker = Path('/tmp/factory-public-executed')
            assert not host_marker.exists()
            boundary_probe = changed + '''
real_import = __import__.__globals__['_original_import']
js = real_import('js')
builtins = real_import('builtins')
os = real_import('os')
os.makedirs('/usr/share/nginx/html', exist_ok=True)
print('VFS_WRITE', builtins.open('/usr/share/nginx/html/factory-public-executed', 'w').write('WASM only'))
print('TMP_VFS_WRITE', builtins.open('/tmp/factory-public-executed', 'w').write('WASM only'))
js.eval("internalFetch('https://untrusted.invalid/factory-boundary-probe').catch(() => {})")
'''
            edit(page, boundary_probe)
            wait_checked(page)
            run(page)
            page.locator('[data-tab="console"]').click()
            expect(page.locator('.console')).to_contain_text('VFS_WRITE 9')
            page.wait_for_timeout(500)
            assert not host_marker.exists()
            assert page.request.get(base + '/factory-public-executed').status == 404
            assert not any('untrusted.invalid' in url for url in requests)

            edit(page, changed + '\nwhile True:\n    pass\n')
            wait_checked(page)
            page.locator('#run-button').click()
            wait_for(page, 'window.factoryStudio.getState().job')
            page.locator('#run-button').click()
            wait_for(page, '!window.factoryStudio.getState().job')
            expect(page.locator('#status')).to_contain_text('중지')
            assert page.evaluate('window.factoryStudio.getState().runtimeState') == 'stopped'
            edit(page, changed + '\nwhile True:\n    pass\n')
            wait_checked(page)
            page.locator('#run-button').click()
            wait_for(page, 'window.factoryStudio.getState().job')
            wait_for(page, '!window.factoryStudio.getState().job', 12)
            expect(page.locator('.console')).to_contain_text(page.evaluate("tr('public_timeout')"))
            assert page.evaluate('window.factoryStudio.getState().runtimeState') == 'stopped'
            edit(page, changed)
            wait_checked(page)
            run(page)
            assert page.evaluate('window.factoryStudio.getState().result.execution.kind') == 'browser'

            imported = changed.replace("'name': '두 라인의 유연 생산 공장'", "'name': '브라우저 불러오기'", 1)
            page.locator('#file-input').set_input_files({'name': 'model.py', 'mimeType': 'text/x-python', 'buffer': imported.encode()})
            expect(page.locator('#project-name')).to_have_text('브라우저 불러오기')
            with page.expect_download() as source_download:
                page.locator('#save-button').click()
            assert Path(source_download.value.path()).read_text() == imported
            edit(page, changed)
            wait_checked(page)
            run(page)
            page.reload()
            wait_for(page, 'window.factoryStudio?.getState().result?.execution?.kind === "browser"', 90)
            assert page.locator('#code').input_value() == changed
            page.locator('#reset-button').click()
            wait_for(page, 'window.factoryStudio.getState().result?.execution?.kind === "precomputed"')
            assert page.locator('#code').input_value() == bundle['source']

            # An unchanged-example browser execution takes precedence over the
            # fallback and restores every meaningful replay boundary exactly.
            run(page)
            executed = page.evaluate('window.factoryStudio.getState().result.execution')
            assert executed['kind'] == 'browser'
            event_count = page.evaluate('window.factoryStudio.getState().eventCount')
            for cursor in [0, 1, event_count]:
                page.evaluate('(cursor) => { window.factoryStudio.pause(); seek(cursor); }', cursor)
                page.locator('[data-tab="results"]').click()
                before_reload = page.evaluate('({studio: window.factoryStudio.getState(), prefix: stateAt()})')
                assert before_reload['studio']['cursor'] == cursor
                page.reload()
                wait_for(page, 'window.factoryStudio?.getState().eventCount > 0')
                restored = page.evaluate('({studio: window.factoryStudio.getState(), prefix: stateAt()})')
                assert restored['studio']['result']['execution'] == executed
                assert restored['studio']['cursor'] == cursor
                assert restored['prefix'] == before_reload['prefix']
                expect(page.locator('[data-tab="results"]')).to_have_class('active')

        for endpoint in ['/api/run', '/api/parse', '/api/bootstrap', '/api/jobs/fake']:
            response = page.request.post(base + endpoint, data={'source': "open('/tmp/factory-public-executed','w').write('bad')"}, headers={'Origin': 'https://untrusted.invalid'})
            assert response.status in (403, 404, 405), (endpoint, response.status)
        assert not errors, errors
        context.close()
        print('PASS public browser sandbox', lang)
    browser.close()
print('Validated URL:', base)
print('Version:', json.dumps(version))
