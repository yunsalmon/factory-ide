"""Run against a local static container or the actual public HTTPS URL.
Usage: python tests/browser_public.py http://127.0.0.1:3084
"""
import hashlib
import json
import sys
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect

base = sys.argv[1].rstrip('/')
with urlopen(base + '/demo.json') as response:
    bundle = json.load(response)
result = bundle['result']
version = bundle['version']
assert hashlib.sha256(bundle['source'].encode()).hexdigest() == version['source_sha256']
assert hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == version['trace_sha256']
assert 0 < result['summary']['completed'] < result['summary']['arrived']
assert any(len(e.get('candidates', [])) > 1 for e in result['events'])
machines = {m['id']:m for m in result['model']['machines']}
routes = {r['id']:r for r in result['model']['routes']}
assert any(routes[a['route_id']]['from'] in machines and a['destination'] in machines and machines[routes[a['route_id']]['from']]['line'] != machines[a['destination']]['line'] for a in result['allocations'])
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for lang in ['ko','en','ja']:
        page = browser.new_page(locale=lang, viewport={'width':1600,'height':1100})
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append(request.url))
        # Old local editor/replay data must never replace the versioned demo.
        page.add_init_script("localStorage.setItem('factory-studio.source.v1', 'raise RuntimeError(123)')")
        page.goto(base + '/?demo=1#replay')
        page.wait_for_function('window.factoryStudio?.getState().eventCount > 0')
        expect(page.locator('html')).to_have_attribute('lang',lang)
        expect(page.locator('#run-button')).to_be_hidden()
        expect(page.locator('.public-notice')).to_be_visible()
        assert page.locator('.CodeMirror').evaluate('(el) => el.CodeMirror.getOption("readOnly")') is True
        assert page.locator('.CodeMirror').evaluate('(el) => el.CodeMirror.getValue()') == bundle['source']
        expect(page.locator('#demo-version')).to_contain_text(version['source_revision'])
        page.locator('#play-button').click()
        page.wait_for_function('window.factoryStudio.getState().cursor > 1')
        page.evaluate('window.factoryStudio.pause()')
        page.locator('[data-tab="results"]').click()
        page.locator('#results-view').select_option('final')
        assert page.locator('#result-table tbody tr').count() > 0
        page.locator('[data-result-select="0"]').click()
        expect(page.locator('#allocation-detail')).to_contain_text(result['allocations'][0]['id'])
        expect(page.locator('.allocation-state')).to_have_count(2)
        with urlopen(base + '/locales.json') as response:
            catalogue = json.load(response)
        expect(page.locator('#allocation-detail')).to_contain_text(catalogue[lang]['message_25'])
        page.locator('#allocation-before').click()
        assert page.evaluate('window.factoryStudio.getState().cursor') == result['allocations'][0]['before_cursor']
        page.locator('#allocation-after').click()
        assert page.evaluate('window.factoryStudio.getState().cursor') == result['allocations'][0]['after_cursor']
        page.locator('#language').select_option('en' if lang != 'en' else 'ja')
        page.locator('[data-tab="allocations"]').click()
        assert not any('/api/' in url for url in requests), requests
        # Verify the actual server rejects arbitrary execution requests, including Origin.
        for endpoint in ['/api/run','/api/parse','/api/bootstrap','/api/jobs/fake']:
            response = page.request.post(base + endpoint, data={'source':"open('/tmp/factory-public-executed','w').write('bad')"}, headers={'Origin':'https://untrusted.invalid'})
            assert response.status in (403,404,405), (endpoint,response.status)
        page.reload()
        page.wait_for_function('window.factoryStudio?.getState().eventCount > 0')
        page.locator('.public-notice a').click()
        expect(page.locator('h1')).to_contain_text('Local installation')
        page.reload()
        expect(page.locator('section[lang="'+lang+'"]')).to_be_visible()
        assert not errors, errors
        page.close()
        print('PASS public browser',lang)
    browser.close()
print('Validated URL:',base)
print('Version:',json.dumps(version))
