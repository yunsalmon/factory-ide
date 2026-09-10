"""Allocation comparison integration test; launches an isolated local server."""
import atexit
import json
from pathlib import Path
import subprocess
import sys
from playwright.sync_api import sync_playwright, expect
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_runtime import simple
from test_allocations import mixed_pull

ROOT = Path(__file__).resolve().parents[1]
server = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--port', '0'], stdout=subprocess.PIPE, text=True, cwd=ROOT)
atexit.register(server.terminate)
base = server.stdout.readline().strip().split(' → ')[-1]
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(base)
    page.wait_for_function('() => window.factoryStudio?.getState().valid')
    for mode in ['pull', 'push', 'declined', 'blocked', 'mixed', 'mixed_declined']:
        m = simple(); m['mode'] = 'push' if mode == 'push' else 'pull'
        m['source']['count'] = 3
        m['source']['interval'] = 3 if mode == 'push' else .01
        if mode == 'blocked': m['routes'][0]['enabled'] = False
        if mode.startswith('mixed'): m = mixed_pull()
        source = 'MODEL = ' + repr(m)
        if mode == 'mixed_declined':
            source += '\ndef choose_candidate(cs, ctx):\n    if ctx.get("machine", {}).get("id") == "N": return None, "N declined"\n    return min(cs, key=lambda c: c["priority"])["id"], "first choice"\n'
        if mode == 'declined': source += '\ndef choose_candidate(cs, ctx):\n    return None, "보류"\n'
        page.evaluate('(s) => document.querySelector(".CodeMirror").CodeMirror.setValue(s)', source)
        page.wait_for_function('() => window.factoryStudio.getState().valid')
        page.locator('#run-button').click()
        page.wait_for_function('() => window.factoryStudio.getState().eventCount > 0')
        page.evaluate('() => window.factoryStudio.pause()')
        page.locator('[data-tab="allocations"]').click()
        with page.expect_download() as download:
            page.locator('#export-trace').click()
        trace = json.loads(Path(download.value.path()).read_text())
        assert trace['schema_version'] == 2
        if mode.startswith('mixed'):
            if mode == 'mixed_declined':
                record = next(e for e in trace['events'] if e.get('reason') == 'N declined')
                assert record['allocation_id'] is None
                page.locator('#allocation-select').select_option(str(record['index']))
                expect(page.locator('#allocation-detail')).to_contain_text('N declined')
                expect(page.locator('#allocation-detail')).to_contain_text('할당 없음')
                assert page.locator('#allocation-select').input_value() == str(record['index'])
                assert page.evaluate('() => window.factoryStudio.getState().cursor') == record['index'] + 1
                page.locator('#allocation-before').click()
                page.locator('#allocation-after').click()
                expect(page.locator('#allocation-detail')).to_contain_text('N declined')
            else:
                allocations = [a for a in trace['allocations'] if a['destination'] == 'N']
                assert len(allocations) == 1
                page.locator('#allocation-select').select_option(str(allocations[0]['decision_index']))
                expect(page.locator('.allocation-state').nth(1)).to_contain_text('예약')
                preference = next(e for e in trace['events'] if e.get('outcome') == 'route_preference')
                page.locator('#allocation-select').select_option(str(preference['index']))
                expect(page.locator('#allocation-detail')).to_contain_text('경로 선택 · 예약 전')
                expect(page.locator('#allocation-detail')).to_contain_text('할당 없음')
            print('PASS allocation comparison', mode)
            continue
        if mode in ['declined', 'blocked']:
            assert trace['allocations'] == []
            record = next(e for e in trace['events'] if e['kind'] in ['decision', 'blocked'])
            page.locator('#allocation-select').select_option(str(record['index']))
            expect(page.locator('#allocation-detail')).to_contain_text('할당 없음')
            expect(page.locator('#allocation-detail')).to_contain_text('보류' if mode == 'declined' else '비활성 경로')
            continue
        allocation = next(a for a in trace['allocations'] if a['lot_id'] == 'LOT-002' and a['destination'] == 'M')
        page.locator('#allocation-select').select_option(str(allocation['decision_index']))
        expect(page.locator('.allocation-state')).to_have_count(2)
        after = page.locator('.allocation-state').nth(1)
        expect(after).to_contain_text('예약' if mode == 'pull' else '처리 중')
        expect(after).to_contain_text('LOT-002')
        expect(page.locator('.state-changed').first).to_be_visible()
        page.locator('#allocation-before').click()
        assert page.evaluate('() => window.factoryStudio.getState().cursor') == allocation['before_cursor']
        page.locator('#allocation-after').click()
        assert page.evaluate('() => window.factoryStudio.getState().cursor') == allocation['after_cursor']
        saved = after.inner_text()
        # Cursor movement follows the allocation attached to the event, then deterministic re-seek.
        page.locator('#timeline').evaluate('(el) => {el.value=el.max;el.dispatchEvent(new Event("input"));}')
        page.locator('#allocation-select').select_option(str(allocation['decision_index']))
        assert after.inner_text() == saved
        page.locator('#rewind-button').click()
        expect(page.locator('#allocation-detail')).to_contain_text('선택 기록이 없습니다')
        print('PASS allocation comparison', mode)
    assert not errors, errors
    browser.close()
print('All allocation browser checks passed.')
