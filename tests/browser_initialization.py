"""Public-app Stop/Reset during stalled initialization, without waiting for downloads."""
import sys,time
from playwright.sync_api import sync_playwright
base=sys.argv[1].rstrip('/')
with sync_playwright() as p:
    browser=p.chromium.launch()
    for language in ['en','ko','ja']:
        page=browser.new_page(locale=language)
        page.add_init_script('''window.testWorkers=[];window.Worker=class {
          constructor(){this.dead=false;testWorkers.push(this)}
          addEventListener(){} postMessage(){} terminate(){this.dead=true}
        }''')
        page.goto(base)
        def wait(expression):
            deadline=time.monotonic()+10
            while not page.evaluate(expression):
                assert time.monotonic()<deadline,expression
                page.wait_for_timeout(30)
        wait('Boolean(window.factoryStudio?.getState().result)')
        assert page.evaluate('editor === null')
        for action in ['stop','reset']:
            page.evaluate('''()=>{const field=document.querySelector('#code');field.value+='\\n# cold connection\\n';field.dispatchEvent(new Event('input',{bubbles:true}))}''')
            wait('S.runtimeState === "loading"')
            assert page.locator('#runtime-status').inner_text()==page.evaluate('tr("public_runtime_loading")')
            assert page.locator('#run-button').inner_text()==page.evaluate('tr("ui_145")')
            page.locator('#run-button' if action=='stop' else '#reset-button').click()
            wait('S.runtimeState === "stopped"')
            assert page.evaluate('testWorkers.every(w=>w.dead) && browserRuntime.pending.size===0')
            if action=='reset':assert page.evaluate('S.valid && S.source===S.example')
            assert page.locator('#run-button').inner_text()==page.evaluate('tr("ui_152")')
        page.evaluate('''()=>{browserRuntime.initTimeout=10;const field=document.querySelector('#code');field.value+='\\n# timeout\\n';field.dispatchEvent(new Event('input',{bubbles:true}))}''')
        wait('S.runtimeState === "init_error"')
        wait('S.parseErrorDetail?.code === "public_init_timeout"')
        assert page.locator('#runtime-status').inner_text()==page.evaluate('tr("public_init_error")')
        print('PASS initialization loading/Stop/Reset/timeout',language)
        page.close()
    browser.close()

# Native Worker entry-script network failure, distinct from a worker that starts
# successfully and later fails while importing runtime modules.
with sync_playwright() as p:
    browser=p.chromium.launch()
    for language in ['en','ko','ja']:
        context=browser.new_context(locale=language)
        context.route('**/browser-worker.js',lambda route:route.abort('failed'))
        page=context.new_page();page.goto(base)
        def wait(expression, timeout=15):
            deadline=time.monotonic()+timeout
            while not page.evaluate(expression):
                assert time.monotonic()<deadline,expression
                page.wait_for_timeout(30)
        wait('Boolean(window.factoryStudio?.getState().result)')
        assert page.evaluate('editor === null')
        page.evaluate('''()=>{const field=document.querySelector('#code');field.value+='\\n# entry load failure\\n';field.dispatchEvent(new Event('input',{bubbles:true}))}''')
        wait('S.runtimeState === "init_error"')
        wait('S.parseErrorDetail?.code === "public_init_error"')
        assert page.locator('#runtime-status').inner_text()==page.evaluate('tr("public_init_error")')
        assert page.evaluate('S.parseError === tr("public_init_error")')
        assert page.evaluate('browserRuntime.worker===null && browserRuntime.pending.size===0')
        context.unroute('**/browser-worker.js')
        page.locator('#run-button').click()
        wait('S.result?.execution?.kind === "browser" && !S.job', 45)
        assert page.evaluate('S.valid && S.runtimeState === "ready"')
        print('PASS native entry failure localization and real-worker retry',language)
        context.close()
    browser.close()
