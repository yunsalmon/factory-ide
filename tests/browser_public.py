"""Run the editable browser sandbox against a static public container or URL.

Usage: python tests/browser_public.py http://127.0.0.1:3084
"""
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect


base = sys.argv[1].rstrip('/')
with urlopen(base + '/demo.json') as response:
    bundle = json.load(response)
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
    wait_for(page, 'window.factoryStudio.getState().valid', 90)


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
        assert custom['execution']['runtime'] == {'pyodide': '0.27.7', 'python': '3.12.7', 'simpy': '4.1.1'}
        assert custom['execution']['source_sha256'] == hashlib.sha256(changed.encode()).hexdigest()
        assert not any('/api/' in url for url in requests), requests

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

            for probe, expected in [
                (changed + "\nimport js\n", "Import 'js' is unavailable"),
                (changed + "\nopen('/tmp/factory-public-executed', 'w').write('bad')\n", 'Filesystem access is unavailable'),
            ]:
                edit(page, probe)
                wait_checked(page)
                run(page)
                expect(page.locator('.console')).to_contain_text(expected)
            assert not any('untrusted.invalid' in url for url in requests)

            edit(page, changed + '\nwhile True:\n    pass\n')
            wait_checked(page)
            page.locator('#run-button').click()
            wait_for(page, 'window.factoryStudio.getState().job')
            page.locator('#run-button').click()
            wait_for(page, '!window.factoryStudio.getState().job')
            expect(page.locator('#status')).to_contain_text('중지')
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

        for endpoint in ['/api/run', '/api/parse', '/api/bootstrap', '/api/jobs/fake']:
            response = page.request.post(base + endpoint, data={'source': "open('/tmp/factory-public-executed','w').write('bad')"}, headers={'Origin': 'https://untrusted.invalid'})
            assert response.status in (403, 404, 405), (endpoint, response.status)
        assert not errors, errors
        context.close()
        print('PASS public browser sandbox', lang)
    browser.close()
print('Validated URL:', base)
print('Version:', json.dumps(version))
