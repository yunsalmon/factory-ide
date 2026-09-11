"""Public startup: exact snapshot restore without Python; observable async progress."""
import json
import sys
from playwright.sync_api import sync_playwright, expect

base = sys.argv[1].rstrip('/')
with sync_playwright() as p:
    browser = p.chromium.launch()
    seed = browser.new_page()
    demo_requests = []
    seed.on('request', lambda request: demo_requests.append(request.url) if request.url.endswith('/demo.json') else None)
    seed.goto(base)
    seed.wait_for_function('()=>window.factoryStudio && S.statusMessage?.code==="public_ready"')
    assert len(demo_requests) == 1, demo_requests
    seed.evaluate('()=>editor.setValue(S.source+"\\n# exact edited snapshot 복원 日本語\\n")')
    seed.wait_for_function('()=>S.valid && S.runtimeState==="ready"', timeout=210000)
    seed.evaluate('()=>{void run()}')
    seed.wait_for_function('()=>!S.job && !S.busy && S.result?.execution?.kind==="browser"')
    seed.evaluate('pause()')
    fixture = seed.evaluate('({source:S.source,result:S.result})')
    seed.close()
    for language in ['en', 'ko', 'ja']:
        for cursor in [0, 1, 15, len(fixture['result']['events'])]:
            context = browser.new_context(locale=language, viewport={'width': 320, 'height': 844})
            saved = dict(fixture, cursor=cursor, tab='results', resultsFinal=True, filters={})
            context.add_init_script('''const saved = %s;
                localStorage.setItem('factory-studio.public.source.v1', saved.source);
                localStorage.setItem('factory-studio.public.replay.v1', JSON.stringify(saved));
                window.createdWorkers=0;
                window.Worker=class {constructor(){createdWorkers++;throw Error('unexpected initialization')}};
            ''' % json.dumps(saved))
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(base)
            page.wait_for_function('()=>window.factoryStudio && S.statusMessage?.code==="public_ready"')
            assert page.evaluate('({source:S.source,result:S.result,cursor:S.cursor,tab:S.tab,resultsFinal:S.resultsFinal,filters:S.resultFilters})') == saved
            assert page.evaluate('createdWorkers===0 && S.runtimeState==="idle" && S.valid')
            expect(page.locator('#startup-progress')).to_be_hidden()
            before = page.evaluate('JSON.stringify(stateAt(S.cursor))')
            page.reload()
            page.wait_for_function('()=>window.factoryStudio && S.statusMessage?.code==="public_ready"')
            assert page.evaluate('JSON.stringify(stateAt(S.cursor))') == before
            assert not errors, errors
            context.close()
        # Hold the payload request; progress must exist while restoration awaits it.
        context = browser.new_context(locale=language, viewport={'width': 320, 'height': 844})
        pending = []
        context.route('**/demo.json', lambda route: pending.append(route))
        page = context.new_page()
        page.goto(base, wait_until='domcontentloaded')
        expect(page.locator('#startup-progress')).to_be_visible()
        expect(page.locator('#startup-progress')).to_have_text(page.evaluate('tr("public_restoring")'))
        assert page.locator('#startup-progress').bounding_box()['y'] < 844
        assert page.evaluate('S.result===null')
        for route in pending:
            route.continue_()
        page.wait_for_function('()=>S.statusMessage?.code==="public_ready"')
        assert page.evaluate('S.cursor===1 && S.source===S.example && S.runtimeState==="idle"')
        expect(page.locator('#startup-progress')).to_be_hidden()
        context.close()
        # Missing/mismatched/malformed replays cannot bypass validation. An empty
        # saved source is also a real edit, not a request to replace it with demo.
        for source, replay in [
            (fixture['source'], None),
            (fixture['source'], dict(fixture, source='wrong source')),
            (fixture['source'], dict(fixture, result=dict(fixture['result'], events=None))),
            ('', None),
        ]:
            context = browser.new_context(locale=language)
            context.add_init_script('''localStorage.setItem('factory-studio.public.source.v1', %s);
              localStorage.setItem('factory-studio.public.replay.v1', %s);
              window.testWorkers=[];window.Worker=class {
                constructor(){this.dead=false;testWorkers.push(this)}
                addEventListener(){} postMessage(){} terminate(){this.dead=true}
              };''' % (json.dumps(source), json.dumps(json.dumps(replay))))
            page = context.new_page()
            page.goto(base)
            page.wait_for_function('()=>window.factoryStudio && S.runtimeState==="loading"')
            expect(page.locator('#startup-progress')).to_be_visible()
            assert page.evaluate('S.result===null && !S.valid')
            assert page.evaluate('S.source') == source
            page.locator('#reset-button').click()
            page.wait_for_function('()=>S.source===S.example && S.valid && S.runtimeState==="stopped"')
            assert page.evaluate('testWorkers.every(w=>w.dead) && S.result.execution.kind==="precomputed"')
            context.close()
        print('PASS startup exact edited replay, cursors, progress, fallback/reset', language, flush=True)
    browser.close()
