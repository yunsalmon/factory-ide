"""Public loader recovery, request ordering, and atomic editor upgrade."""
import sys

from playwright.sync_api import sync_playwright


base = sys.argv[1].rstrip('/') if len(sys.argv) > 1 else 'http://127.0.0.1:8000'


def ready(page):
    page.wait_for_function("() => ['true','error'].includes(document.documentElement.dataset.uiReady) && Boolean(window.factoryStudio?.getState().result)")


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)

    # If both the optimized locale chunk and full-bundle fallback fail, the
    # product reveals usable panels and a localized, keyboard-retryable alert.
    context = browser.new_context(locale='ja', viewport={'width': 320, 'height': 900})
    context.route('**/locales-ja-1.json', lambda route: route.abort('failed'))
    context.route('**/locales.json', lambda route: route.abort('failed'))
    page = context.new_page()
    page.add_init_script("""addEventListener('DOMContentLoaded',()=>{
      window.issue46InitialTraceVisibility=getComputedStyle(document.querySelector('.trace-panel')).contentVisibility;
    })""")
    page.goto(base); ready(page)
    assert page.evaluate('issue46InitialTraceVisibility') == 'hidden'
    assert page.locator('[data-boot-stage]').count() == 0
    assert page.locator('#code').is_visible() and page.locator('#graph-viewport').is_visible()
    assert page.locator('#loader-error').get_attribute('role') == 'alert'
    assert '言語ファイル' in page.locator('#loader-error').inner_text()
    assert page.locator('#loader-error button').is_enabled()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    context.close()

    # A missing optional data UI module must not strand every panel behind the
    # staged-paint guard. The rest of the public editor remains usable.
    context = browser.new_context(locale='en', viewport={'width': 1440, 'height': 900})
    context.route('**/data-ui.js', lambda route: route.abort('failed'))
    page = context.new_page(); page.goto(base); ready(page)
    assert page.locator('[data-boot-stage]').count() == 0
    assert page.locator('#code').is_editable()
    assert page.locator('#loader-error').count() == 1
    assert 'interface' in page.locator('#loader-error').inner_text().lower()
    assert page.locator('[data-tab="data"]').is_disabled()
    context.close()

    # Preflight all editor files before evaluating the core. A failed addon
    # leaves the native textarea intact; clearing the failure permits a retry.
    context = browser.new_context(locale='en', viewport={'width': 320, 'height': 900})
    missing = {'enabled': True}
    def route_python(route):
        route.abort('failed') if missing['enabled'] else route.continue_()
    context.route('**/vendor/codemirror/python.js', route_python)
    page = context.new_page()
    cdp = context.new_cdp_session(page)
    cdp.send('Emulation.setCPUThrottlingRate', {'rate': 4})
    page.add_init_script("window.issue35UpgradeTasks=[];new PerformanceObserver(l=>issue35UpgradeTasks.push(...[...l.getEntries()].map(e=>e.duration))).observe({type:'longtask',buffered:true})")
    page.goto(base); ready(page)
    source = page.locator('#code').input_value()
    page.evaluate("""()=>{const field=document.querySelector('#code');field.focus();field.setSelectionRange(60,90,'backward');issue35UpgradeTasks.length=0}""")
    page.locator('#enhance-editor').click()
    page.wait_for_function("() => !document.querySelector('#enhance-editor').disabled")
    assert page.evaluate('editor === null && typeof CodeMirror === "undefined"')
    assert page.locator('#code').input_value() == source
    assert page.locator('#code').evaluate("field => [field.selectionStart,field.selectionEnd,field.selectionDirection]") == [60, 90, 'backward']
    missing['enabled'] = False
    page.locator('#enhance-editor').click()
    page.wait_for_function('() => Boolean(editor)')
    assert page.evaluate("""()=>({source:editor.getValue(),anchor:editor.indexFromPos(editor.getCursor('anchor')),head:editor.indexFromPos(editor.getCursor('head'))})""") == {'source': source, 'anchor': 90, 'head': 60}
    page.wait_for_timeout(100)
    assert max(page.evaluate('issue35UpgradeTasks') or [0]) < 200
    assert 'Loads the editor modules once' in page.locator('.enhance-cost').inner_text()
    context.close()

    # A forward selection retains its direction as well.
    context = browser.new_context(locale='ko', viewport={'width': 1440, 'height': 900})
    page = context.new_page(); page.goto(base); ready(page)
    source = page.locator('#code').input_value()
    page.evaluate("""()=>{const field=document.querySelector('#code');field.focus();field.setSelectionRange(15,37,'forward')}""")
    page.locator('#enhance-editor').click(); page.wait_for_function('() => Boolean(editor)')
    assert page.evaluate("""()=>[editor.indexFromPos(editor.getCursor('anchor')),editor.indexFromPos(editor.getCursor('head'))]""") == [15, 37]
    assert page.evaluate('editor.getValue()') == source
    context.close()

    # Locale selection is latest-request-wins even when an older download is
    # released after a newer already-loaded locale has rendered.
    held = []
    context = browser.new_context(locale='en', viewport={'width': 320, 'height': 900})
    context.route('**/locales-ja-*.json', lambda route: held.append(route))
    page = context.new_page(); page.goto(base); ready(page)
    page.wait_for_function('() => window.translations.ko && Object.keys(window.translations.ko).length > 0')
    page.locator('#language').select_option('ja')
    page.locator('#language').select_option('ko')
    page.wait_for_function('() => document.documentElement.lang === "ko"')
    for route in held:
        route.continue_()
    page.wait_for_timeout(300)
    assert page.evaluate('document.documentElement.lang') == 'ko'
    assert page.locator('#language').input_value() == 'ko'
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    context.close()

    browser.close()

print('PASS public loader resilience, atomic editor upgrade, locale ordering, and selection direction')
