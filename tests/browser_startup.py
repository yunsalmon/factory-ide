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
    example_source = seed.evaluate('S.example')
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
        malformed = []
        for key, value in [
            ('model', {}), ('model', None), ('model', []),
            ('events', [None]), ('events', [{}]), ('events', []),
            ('allocations', [None]), ('warnings', None),
        ]:
            malformed.append(dict(fixture, result=dict(fixture['result'], **{key: value})))
        for collection, value in [('machines', []), ('machines', [None]), ('routes', {}), ('processes', None)]:
            model = dict(fixture['result']['model'], **{collection: value})
            malformed.append(dict(fixture, result=dict(fixture['result'], model=model)))
        for key in ['model', 'events']:
            result = dict(fixture['result']);del result[key]
            malformed.append(dict(fixture, result=result))
        broken_event = dict(fixture['result']['events'][0], state_changes={'lots': {'broken': None}, 'machines': {}})
        malformed.append(dict(fixture, result=dict(fixture['result'], events=[broken_event])))
        for replacement in [{'lot': None}, {'checks': [None]}]:
            events = [dict(fixture['result']['events'][0], **replacement), *fixture['result']['events'][1:]]
            malformed.append(dict(fixture, result=dict(fixture['result'], events=events)))
        for source, replay in [
            (fixture['source'], None),
            (fixture['source'], dict(fixture, source='wrong source')),
            (fixture['source'], dict(fixture, result=dict(fixture['result'], events=None))),
            ('', None),
            (example_source, dict(source=example_source, result=dict(fixture['result'], model={}))),
            *[(fixture['source'], replay) for replay in malformed],
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
            if replay is not None:
                expect(page.locator('#startup-progress')).to_have_text(page.evaluate('tr("public_replay_invalid")'))
            assert page.evaluate('S.result===null && !S.valid')
            assert page.evaluate('S.source') == source
            page.locator('#reset-button').click()
            page.wait_for_function('()=>S.source===S.example && S.valid && S.runtimeState==="stopped"')
            assert page.evaluate('testWorkers.every(w=>w.dead) && S.result.execution.kind==="precomputed"')
            context.close()
        # A projection can still fail on an unsupported future shape. Adoption
        # must roll back even if the structural checks succeeded.
        context = browser.new_context(locale=language)
        saved = dict(fixture, cursor=15, tab='results')
        context.add_init_script('''const saved=%s;
          localStorage.setItem('factory-studio.public.source.v1',saved.source);
          localStorage.setItem('factory-studio.public.replay.v1',JSON.stringify(saved));
          window.Worker=class {addEventListener(){} postMessage(){} terminate(){}};
        ''' % json.dumps(saved))
        pending = []
        context.route('**/demo.json', lambda route: pending.append(route))
        page = context.new_page();page.goto(base, wait_until='domcontentloaded')
        page.evaluate('()=>{window.projectionAttempted=false;renderAllocationResults=()=>{projectionAttempted=true;throw Error("projection compatibility probe")}}')
        for route in pending:
            route.continue_()
        page.wait_for_function('()=>S.runtimeState==="loading"')
        assert page.evaluate('projectionAttempted && !S.valid && S.result===null && S.cursor===0')
        assert page.evaluate('S.model') == fixture['result']['model']
        assert page.evaluate('S.source') == fixture['source']
        expect(page.locator('#startup-progress')).to_have_text(page.evaluate('tr("public_replay_invalid")'))
        context.close()
        # The non-stalled fallback validates the unchanged source, leaves no
        # invented result, and can then execute normally with a real Worker.
        context = browser.new_context(locale=language)
        saved = dict(fixture, result=dict(fixture['result'], model={}))
        context.add_init_script('''const saved=%s;
          localStorage.setItem('factory-studio.public.source.v1',saved.source);
          localStorage.setItem('factory-studio.public.replay.v1',JSON.stringify(saved));
        ''' % json.dumps(saved))
        page = context.new_page();page.goto(base)
        page.wait_for_function('()=>window.factoryStudio && S.valid && S.runtimeState==="ready" && S.statusMessage?.code==="ui_17"', timeout=210000)
        assert page.evaluate('S.result===null && S.cursor===0')
        assert page.evaluate('S.source') == fixture['source']
        expect(page.locator('#startup-progress')).to_be_hidden()
        page.evaluate('()=>{void run()}')
        page.wait_for_function('()=>!S.job && !S.busy && S.result?.execution?.kind==="browser"')
        page.evaluate('pause()')
        assert page.evaluate('S.result') == fixture['result']
        context.close()
        print('PASS startup exact edited replay, cursors, progress, fallback/reset', language, flush=True)
    browser.close()
