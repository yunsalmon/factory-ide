"""Real-browser checks of prefix projection, result controls, CSV and overlapping lanes."""
import atexit
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
from playwright.sync_api import sync_playwright, expect
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_runtime import simple
from engine import Factory

ROOT = Path(__file__).resolve().parents[1]
server = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--port', '0'], stdout=subprocess.PIPE, text=True, cwd=ROOT)
atexit.register(server.terminate)
base = server.stdout.readline().strip().split(' → ')[-1]
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1600, 'height': 1100})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(base)
    page.wait_for_function('() => window.factoryStudio?.getState().valid')
    for mode in ['push', 'pull', 'blocked', 'revisit']:
        m = simple(); m['mode'] = 'push' if mode == 'push' else 'pull'
        m['source']['count'] = 4; m['source']['interval'] = .1; m['duration'] = 12
        m['processes'].append({'id': 'P2', 'name': '두 번째 공정'})
        m['machines'].append({'id': 'N', 'name': '교차 라인 머신', 'process': 'P2', 'line': 'B', 'time': 10})
        m['routes'][1]['to'] = 'N'
        m['routes'].append({'id': 'ship', 'from': 'N', 'to': 'OUTPUT', 'priority': 0, 'delay': 1, 'product': '*', 'enabled': True})
        if mode == 'blocked': m['routes'][0]['enabled'] = False
        if mode == 'revisit':
            m['source']['count'] = 1
            m['routes'][2]['to'] = 'M'
            m['machines'][1]['time'] = 1
            m['duration'] = 18
        trace = Factory(m).run()
        source = 'MODEL = ' + repr(m)
        page.evaluate('(s) => document.querySelector(".CodeMirror").CodeMirror.setValue(s)', source)
        page.wait_for_function('() => window.factoryStudio.getState().valid')
        page.locator('#run-button').click()
        page.wait_for_function('() => window.factoryStudio.getState().eventCount > 0')
        page.evaluate('() => window.factoryStudio.pause()')
        page.locator('[data-tab="results"]').click()
        page.locator('#results-view').select_option('final')
        expect(page.locator('#result-context')).to_contain_text('실행 최종 결과')
        rows = page.evaluate('(r) => allocationResults(r, 0, true).rows', trace)
        assert len(rows) == page.locator('#result-table tbody tr').count()
        assert len([r for r in rows if r['id']]) == len(trace['allocations'])
        if mode == 'revisit':
            visits = [r for r in rows if r['lot'] == 'LOT-001' and r['destination']['machine'] == 'M']
            assert len(visits) > 1 and len({r['id'] for r in visits}) == len(visits)
        if mode == 'blocked':
            assert all(r['status'] == '막힘' and r['assigned'] is None for r in rows)
        else:
            assert any(r['finish'] is None for r in rows if r['id'])
            # Every assigned destination-machine visit has independent strips (including zero length).
            count = sum((r['assigned'] is not None) + (r['move'] is not None) + (r['start'] is not None) for r in rows if r['id'])
            assert page.locator('[data-result-span]').count() == count
            a = trace['allocations'][0]
            prefix = page.evaluate('([r,c]) => allocationResults(r,c).rows', [trace, a['after_cursor']])
            r = next(r for r in prefix if r['id'] == a['id'])
            assert r['status'] == ('Push 배정 후 대기' if mode == 'push' else 'Pull 예약 후 대기')
            assert all(r[k] is None for k in ['move','arrived','start','finish'])
            # Future decisions at the same timestamp must not become assigned rows.
            before = page.evaluate('([r,c]) => allocationResults(r,c).rows', [trace,a['before_cursor']+1])
            assert not any(r['id'] == a['id'] for r in before)
            page.locator('[data-result-span="0"]').first.click()
            expect(page.locator('#allocation-detail')).to_contain_text(a['id'])
            page.locator('[data-tab="results"]').click()
            page.locator('[data-result-select="0"]').click()
            expect(page.locator('#allocation-detail')).to_contain_text(a['id'])
            expect(page.locator('.route-path.highlight')).to_have_count(1)
            page.locator('[data-tab="results"]').click()
            page.locator('#results-view').select_option('replay')
            expect(page.locator('#result-context')).to_contain_text('현재 재생 시점')
            assert page.locator('#result-table tbody tr').count() == len(prefix)
            with page.expect_download() as replay_download:
                page.locator('#result-csv').click()
            replay_csv = list(csv.DictReader(io.StringIO(Path(replay_download.value.path()).read_text(encoding='utf-8-sig'))))
            assert len(replay_csv) == len(prefix)
            row_csv = next(item for item in replay_csv if item['할당 ID'] == a['id'])
            assert row_csv['보기'] == '현재 재생 시점'
            assert all(row_csv[name] == '' for name in ['이동 시작 (min)', '이동 도착 (min)', '처리 시작 (min)', '처리 종료 (min)'])
            page.locator('#results-view').select_option('final')
        # All control dimensions must match shared projection; search is case insensitive.
        for key, value in [('process','P'), ('line','A'), ('machine','M'), ('lot','LOT-001'), ('status','막힘' if mode == 'blocked' else '처리 완료')]:
            page.locator('#result-reset').click()
            page.locator(f'[data-result-filter="{key}"]').select_option(value)
            expected = page.evaluate('([r,f]) => allocationResults(r,0,true,f).rows.length', [trace,{key:value}])
            assert page.locator('#result-table tbody tr').count() == expected
        page.locator('#result-reset').click()
        page.locator('#result-search').fill('lot-001')
        with page.expect_download() as download:
            page.locator('#result-csv').click()
        exported = list(csv.DictReader(io.StringIO(Path(download.value.path()).read_text(encoding='utf-8-sig'))))
        assert len(exported) == page.locator('#result-table tbody tr').count()
        assert all(r['로트'] == 'LOT-001' and r['보기'] == '실행 최종 결과' and 'lot-001' in r['필터'] for r in exported)
        for r in exported:
            if r['상태'] in ['막힘','미할당']: assert r['할당 시각 (min)'] == ''
        page.locator('#result-reset').click()
        page.locator('#results-view').select_option('replay')
        page.locator('#rewind-button').click()
        assert page.locator('#result-table tbody tr').count() == 0
        print('PASS allocation results', mode)
    assert not errors, errors
    browser.close()
