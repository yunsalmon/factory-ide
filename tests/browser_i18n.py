"""Locale workflows, structured messages, live switching and restore."""
import atexit
import json
from pathlib import Path
import subprocess
import sys
from playwright.sync_api import sync_playwright, expect
ROOT = Path(__file__).resolve().parents[1]
server = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--port', '0'], stdout=subprocess.PIPE, text=True, cwd=ROOT)
atexit.register(server.terminate)
base = server.stdout.readline().strip().split(' → ')[-1]
with sync_playwright() as p:
    browser = p.chromium.launch()
    for language in ['ko', 'en', 'ja']:
        context = browser.new_context(locale=language + {'ko': '-KR', 'en': '-US', 'ja': '-JP'}[language], viewport={'width': 1440, 'height': 1080})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(base)
        page.wait_for_function('() => window.factoryStudio?.getState().valid')
        expect(page.locator('html')).to_have_attribute('lang', language)
        page.locator('#help-button').click()
        expect(page.locator('#help')).to_be_visible()
        page.locator('#close-help').click()
        page.locator('[data-node="CUT_A"]').click()
        page.locator('input[name="time"]').fill('11')
        page.locator('#property-form button[type="submit"]').click()
        page.wait_for_function('() => window.factoryStudio.getState().model.machines[0].time === 11')
        page.locator('#close-inspector').click()
        page.locator('#run-button').click()
        page.wait_for_function('() => window.factoryStudio.getState().eventCount > 0 && !window.factoryStudio.getState().job')
        page.evaluate('window.factoryStudio.pause()')
        page.locator('#timeline').evaluate('(el) => { el.value=el.max; el.dispatchEvent(new Event("input")); }')
        source = page.locator('#code').input_value()
        state = page.evaluate('window.factoryStudio.getState()')
        for target in ['en', 'ja', 'ko', language]:
            page.locator('#language').select_option(target)
            now = page.evaluate('window.factoryStudio.getState()')
            assert (now['cursor'], now['eventCount']) == (state['cursor'], state['eventCount'])
            assert page.locator('#code').input_value() == source
            page.locator('[data-tab="results"]').click()
            expect(page.locator('#result-table tbody tr').first).to_be_visible()
            with page.expect_download() as dl:
                page.locator('#result-csv').click()
            assert Path(dl.value.path()).read_text(encoding='utf-8-sig').startswith('"' + page.locator('#result-table th').first.inner_text() + '"')
            page.locator('[data-tab="allocations"]').click()
            reason = page.evaluate('tr("message_25")')
            expect(page.locator('#allocation-detail')).to_contain_text(reason)
            for tab in ['lots', 'utilization', 'console', 'events']:
                page.locator(f'[data-tab="{tab}"]').click()
        page.reload()
        page.wait_for_function('() => window.factoryStudio?.getState().eventCount > 0')
        expect(page.locator('html')).to_have_attribute('lang', language)
        assert page.evaluate('window.factoryStudio.getState().cursor') == state['cursor']
        assert page.locator('#code').input_value() == source
        with page.expect_download() as dl:
            page.locator('#save-button').click()
        assert Path(dl.value.path()).read_text() == source
        # Custom text matching a built-in Korean message is never translated.
        custom = source.replace("message('message_25')", "'경로 우선순위 → 목적지 대기 수 → FIFO'")
        page.evaluate('(s) => document.querySelector(".CodeMirror").CodeMirror.setValue(s)', custom)
        page.wait_for_function('() => window.factoryStudio.getState().valid')
        page.locator('#run-button').click()
        page.wait_for_function('() => window.factoryStudio.getState().eventCount > 0 && !window.factoryStudio.getState().job')
        page.evaluate('window.factoryStudio.pause()')
        page.locator('#timeline').evaluate('(el) => {el.value=el.max;el.dispatchEvent(new Event("input"));}')
        page.locator('[data-tab="allocations"]').click()
        expect(page.locator('#allocation-detail')).to_contain_text('경로 우선순위 → 목적지 대기 수 → FIFO')
        page.evaluate('(s) => document.querySelector(".CodeMirror").CodeMirror.setValue(s)', source.replace("'duration': 180", "'duration': -1"))
        # Syntax diagnostic is structured and translates its wrapper, preserving Python's own message.
        page.evaluate('(s) => document.querySelector(".CodeMirror").CodeMirror.setValue(s)', source + '\ninvalid Python ???')
        page.wait_for_function('() => !window.factoryStudio.getState().valid')
        page.locator('[data-tab="console"]').click()
        expect(page.locator('.console')).to_contain_text({'ko':'행:', 'en':'Line ', 'ja':'行:'}[language])
        assert not errors, errors
        context.close()
        print('PASS', language, 'edit/run/reasons/tabs/CSV/save/switch/restore/custom reason/error')
    page = browser.new_page(locale='fr-FR')
    page.goto(base)
    expect(page.locator('html')).to_have_attribute('lang', 'en')
    assert page.evaluate('tr("missing_key")') == 'Translation unavailable'
    assert page.evaluate('Object.keys(translations.en).every(k => ["ko","ja"].every(l => translations[l][k]))')
    print('PASS unsupported browser locale, missing-key fallback, dictionary parity')
    browser.close()
