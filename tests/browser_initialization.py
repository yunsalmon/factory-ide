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
        for action in ['stop','reset']:
            page.evaluate('()=>editor.setValue(editor.getValue()+"\\n# cold connection\\n")')
            wait('S.runtimeState === "loading"')
            assert page.locator('#runtime-status').inner_text()==page.evaluate('tr("public_runtime_loading")')
            assert page.locator('#run-button').inner_text()==page.evaluate('tr("ui_145")')
            page.locator('#run-button' if action=='stop' else '#reset-button').click()
            wait('S.runtimeState === "stopped"')
            assert page.evaluate('testWorkers.every(w=>w.dead) && browserRuntime.pending.size===0')
            if action=='reset':assert page.evaluate('S.valid && S.source===S.example')
            assert page.locator('#run-button').inner_text()==page.evaluate('tr("ui_152")')
        page.evaluate('()=>{browserRuntime.initTimeout=10;editor.setValue(editor.getValue()+"\\n# timeout\\n")}')
        wait('S.runtimeState === "init_error"')
        wait('S.parseErrorDetail?.code === "public_init_timeout"')
        assert page.locator('#runtime-status').inner_text()==page.evaluate('tr("public_init_error")')
        print('PASS initialization loading/Stop/Reset/timeout',language)
        page.close()
    browser.close()
