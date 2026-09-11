"""Real browser delivery/integrity/error/recovery contract against static Nginx."""
import sys
from playwright.sync_api import sync_playwright

base=sys.argv[1].rstrip('/')
alias='/vendor/pyodide/0.27.7/pyodide.asm.wasm.js'
with sync_playwright() as p:
    browser=p.chromium.launch()
    for locale in ['en','ko','ja']:
        context=browser.new_context(locale=locale)
        page=context.new_page();page.goto(base)
        page.wait_for_function('()=>window.factoryStudio?.getState().valid')
        # A cache-eligible suffix never grants JavaScript execution permission.
        assert page.evaluate('''path=>new Promise(resolve=>{const s=document.createElement('script');s.src=path;s.onload=()=>resolve(false);s.onerror=()=>resolve(true);document.head.append(s)})''',alias)
        for defect in ['tampered','mime','missing']:
            def intercept(route):
                if defect=='missing':route.fulfill(status=404,content_type='text/plain',body='missing')
                elif defect=='tampered':route.fulfill(content_type='application/wasm',body=b'\x00asm\x01\x00\x00\x00')
                else:
                    response=route.fetch();route.fulfill(response=response,headers={**response.headers,'content-type':'application/javascript'})
            context.route('**'+alias,intercept)
            page.evaluate('void run()')
            page.wait_for_function('()=>S.runtimeState==="init_error"&&!S.busy',timeout=15000)
            assert page.evaluate('browserRuntime.worker===null && browserRuntime.pending.size===0')
            assert page.locator('#runtime-status').inner_text()==page.evaluate('tr("public_init_error")')
            assert page.evaluate('S.parseErrorDetail?.code')=='public_init_error'
            context.unroute('**'+alias,intercept)
        requests=[]
        context.on('request',lambda r:requests.append(r.url))
        page.evaluate('void run()')
        page.wait_for_function('()=>!S.busy&&!S.job&&S.result?.execution?.kind==="browser"',timeout=30000)
        assert page.evaluate('S.result.summary.completed>0')
        assert any(url.endswith(alias) for url in requests)
        assert not any(url.endswith('/pyodide.asm.wasm') for url in requests)
        assert not any('/api/' in url for url in requests)
        print('PASS alias MIME/SRI/404 fail fast, localized diagnostic, fresh real Worker recovery',locale)
        context.close()
    browser.close()
