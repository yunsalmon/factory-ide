"""Reserved-looking family strings remain plain data through forms and files."""
import atexit
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from test_runtime import simple
from model import parse
from engine import Factory
ROOT=Path(__file__).resolve().parents[1]
server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,text=True)
atexit.register(server.terminate)
base=server.stdout.readline().strip().split(' → ')[-1]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(locale='en-US',viewport={'width':1440,'height':1000})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base);page.wait_for_function('() => window.factoryStudio?.getState().valid')
    prototype_keys=page.evaluate('() => [Object.getOwnPropertyNames(Object),Object.getOwnPropertyNames(Object.prototype)]')
    for origin,target in [('constructor','__proto__'),('__proto__','constructor')]:
        model=simple();model['source']['products']=[target];model['product_families']={target:target}
        matrix={origin:{target:2}}
        model['machines'][0].update(initial_family=origin,setup_matrix=matrix)
        payload={'name':'families.py','mimeType':'text/x-python','buffer':('MODEL = '+repr(model)).encode()}
        page.locator('#file-input').set_input_files(payload)
        page.wait_for_function('(origin) => window.factoryStudio.getState().valid && window.factoryStudio.getState().model.machines[0].initial_family === origin', arg=origin)
        page.locator('[data-tab="operations"]').click();page.locator('#ops-configure').click()
        # No-op save of the imported transition must not alter its duration or keys.
        page.locator('#ops-form [type="submit"]').click()
        page.wait_for_function('() => document.querySelector("#toast")?.textContent === "Operational settings saved."')
        expect(page.locator('#ops-error')).to_have_text('')
        # Native JSON retains __proto__; Playwright's object decoder can drop it.
        assert json.loads(page.evaluate('() => JSON.stringify(window.factoryStudio.getState().model.machines[0].setup_matrix)'))==matrix, (page.evaluate('() => JSON.stringify(window.factoryStudio.getState().model.machines[0].setup_matrix)'), matrix)
        # Delete and create the same transition through user inputs: both map
        # levels must treat __proto__/constructor as own keys, not prototypes.
        page.locator('.ops-setup-row .ops-remove').click();page.locator('#ops-add-setup').click()
        inputs=page.locator('.ops-setup-row input')
        inputs.nth(0).fill(origin);inputs.nth(1).fill(target);inputs.nth(2).fill('2')
        page.locator('#ops-form [type="submit"]').click()
        page.wait_for_function('(origin) => !!document.querySelector("#ops-form [type=submit]") && !document.querySelector("#ops-form [type=submit]").disabled && window.factoryStudio.getState().model.machines[0].initial_family === origin', arg=origin)
        assert json.loads(page.evaluate('() => JSON.stringify(window.factoryStudio.getState().model.machines[0].setup_matrix)'))==matrix, (page.evaluate('() => JSON.stringify(window.factoryStudio.getState().model.machines[0].setup_matrix)'), matrix)
        page.locator('#close-inspector').click();page.locator('#ops-resources').click()
        expect(page.locator('.ops-product-family')).to_have_value(target)
        page.locator('#ops-form [type="submit"]').click()
        page.wait_for_function('() => !document.querySelector("#ops-form [type=submit]").disabled')
        page.locator('#close-inspector').click()
        assert page.evaluate('() => [Object.getOwnPropertyNames(Object),Object.getOwnPropertyNames(Object.prototype)]')==prototype_keys
        assert page.evaluate('() => Object.prototype.constructor === Object && Object.getPrototypeOf(Object) === Function.prototype')
        assert page.evaluate('() => Object.getPrototypeOf(opDictionary(JSON.parse(\'{"__proto__":{"polluted":true},"constructor":7}\'))) === null && !Object.hasOwn(Object.prototype,"polluted") && !Object.hasOwn(Object,"polluted")')
        # Python export/import and engine execution retain both keys and setup KPI.
        with page.expect_download() as download:page.locator('#save-button').click()
        source=Path(download.value.path()).read_text();restored=parse(source)[0]
        assert restored['machines'][0]['setup_matrix']==matrix
        assert Factory(restored).run()['operation_metrics']['machines']['M']['setup']==2
        page.locator('#file-input').set_input_files({'name':'roundtrip.py','mimeType':'text/x-python','buffer':source.encode()})
        page.wait_for_function('() => window.factoryStudio.getState().valid')
        page.locator('#run-button').click();page.wait_for_function('() => window.factoryStudio.getState().eventCount > 0')
        page.evaluate('() => window.factoryStudio.pause()')
        with page.expect_download() as trace_download:page.locator('#export-trace').click()
        trace=json.loads(Path(trace_download.value.path()).read_text())
        assert trace['operation_metrics']['machines']['M']['setup']==2
        assert trace['model']['machines'][0]['setup_matrix']==matrix
        print('PASS reserved family roundtrip',origin,'→',target)
    assert not errors,errors
    browser.close()
