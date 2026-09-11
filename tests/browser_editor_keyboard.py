"""Issue30: native focus navigation and explicit edits in both editor modes."""
import atexit
import subprocess
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
if len(sys.argv)>1:
    base=sys.argv[1].rstrip('/')
else:
    server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,text=True)
    atexit.register(server.terminate)
    base=server.stdout.readline().strip().split(' → ')[-1]
ARTIFACTS=ROOT/'artifacts';ARTIFACTS.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser=p.chromium.launch()
    for enhanced in [True,False]:
        for lang in ['ko','en','ja']:
            for width in [1440,320]:
                context=browser.new_context(locale='ko-KR',viewport={'width':width,'height':1000})
                if not enhanced:context.route('**/vendor/codemirror/*.js',lambda route:route.abort())
                page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base);page.wait_for_function('()=>S.valid');page.locator('#language').select_option(lang)
                if enhanced:
                    page.evaluate('loadEnhancedEditor()');page.wait_for_function('()=>Boolean(editor)')
                assert page.evaluate('Boolean(editor)')==enhanced
                field=page.locator('.CodeMirror textarea' if enhanced else '#code')
                expect(field).to_have_attribute('aria-describedby','editor-keyboard-help')
                expect(field).not_to_have_attribute('aria-label','')
                help=page.locator('#editor-keyboard-help');expect(help).to_be_visible()
                assert 'Tab' in help.inner_text() and ']' in help.inner_text()
                # Navigate from an actual preceding control; all following moves use keys.
                original=page.evaluate('({source:S.source,revision:S.revision,saved:localStorage.getItem(PUBLIC_DEMO ? "factory-studio.public.source.v1" : "factory-studio.source.v1")})')
                page.locator('#apply-code').focus();page.keyboard.press('Tab')
                expect(page.locator('#editor-skip-results')).to_be_focused()
                page.keyboard.press('Tab');expect(field).to_be_focused()
                page.keyboard.press('Shift+Tab');expect(page.locator('#editor-skip-results')).to_be_focused()
                page.keyboard.press('Shift+Tab');expect(page.locator('#apply-code')).to_be_focused()
                page.keyboard.press('Tab');page.keyboard.press('Tab');expect(field).to_be_focused()
                page.keyboard.press('Escape');page.keyboard.press('Tab');expect(field).not_to_be_focused()
                page.keyboard.press('Shift+Tab');expect(field).to_be_focused()
                # A selected block must not turn Tab into an indentation/edit command.
                if enhanced:page.evaluate('editor.setSelection({line:0,ch:0},{line:2,ch:0})')
                else:field.evaluate('(e)=>e.setSelectionRange(0,Math.min(20,e.value.length))')
                page.keyboard.press('Tab');expect(field).not_to_be_focused();page.keyboard.press('Shift+Tab');expect(field).to_be_focused()
                page.keyboard.press('Shift+Tab');expect(page.locator('#editor-skip-results')).to_be_focused()
                page.keyboard.press('Enter');expect(page.locator('[data-tab="events"]')).to_be_focused()
                page.keyboard.press('Tab');expect(page.locator('[data-tab="wip"]')).to_be_focused();page.keyboard.press('Enter')
                reached=False
                for _ in range(12):
                    if page.evaluate('document.activeElement.dataset.tab')=='orders':reached=True;break
                    page.keyboard.press('Tab')
                assert reached;page.keyboard.press('Enter')
                assert page.evaluate('S.tab')=='orders'
                assert page.evaluate('({source:S.source,revision:S.revision,saved:localStorage.getItem(PUBLIC_DEMO ? "factory-studio.public.source.v1" : "factory-studio.source.v1")})')==original
                if enhanced:
                    # Completion must not silently turn navigation into an insertion.
                    page.evaluate('editor.setCursor({line:0,ch:0});editor.focus()')
                    page.keyboard.press('Control+Space');expect(page.locator('.CodeMirror-hints')).to_be_visible()
                    page.keyboard.press('Tab');expect(field).not_to_be_focused()
                    expect(page.locator('.CodeMirror-hints')).to_have_count(0)
                    assert page.evaluate('S.source')==original['source']
                # Explicit indentation and removal are real source edits and persist.
                page.evaluate(r'''()=>{const source='\n# keyboard probe\n'+S.source;setSource(source);
                  if(editor){editor.setCursor({line:0,ch:0});editor.focus()}else{const e=document.querySelector('#code');e.focus();e.setSelectionRange(0,0)}}''')
                before=page.evaluate('S.source');page.keyboard.press('Control+]')
                assert page.evaluate('S.source')=='    '+before
                assert page.evaluate('localStorage.getItem(PUBLIC_DEMO ? "factory-studio.public.source.v1" : "factory-studio.source.v1")')=='    '+before
                page.keyboard.press('Control+[');assert page.evaluate('S.source')==before
                # Multi-line explicit edit preserves the selected lines, not replacement.
                if enhanced:page.evaluate('editor.setSelection({line:0,ch:0},{line:2,ch:0})')
                else:field.evaluate('(e)=>e.setSelectionRange(0,e.value.indexOf("\\n",1)+1)')
                page.keyboard.press('Control+]');indented=page.evaluate('S.source')
                assert indented.startswith(('' if enhanced else '    ')+'\n    # keyboard probe\n'), repr(indented[:80])
                page.keyboard.press('Control+[');assert page.evaluate('S.source')==before
                page.wait_for_function('()=>S.valid')
                # One scoped command: no second global Ctrl+Enter (public would cancel).
                page.evaluate('''()=>{window.keyRuns=0;const originalRun=run;run=()=>{keyRuns++;return originalRun()};
                  if(editor){const keys=editor.getOption('extraKeys');keys['Ctrl-Enter']=run;editor.setOption('extraKeys',keys)}}''')
                page.keyboard.press('Control+Enter');page.wait_for_function('()=>!S.job&&S.result?.events.length>0')
                assert page.evaluate('keyRuns')==1
                page.evaluate('pause()');page.reload();page.wait_for_function('()=>S.valid')
                assert page.evaluate('S.source')==before
                page.locator('#editor-skip-results').scroll_into_view_if_needed()
                if lang=='en':page.screenshot(path=str(ARTIFACTS/f'editor-keyboard-{enhanced}-{width}.png'))
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert not errors,errors
                print(f'PASS keyboard {"CodeMirror" if enhanced else "textarea"} {lang} {width}px',flush=True)
                context.close()
    browser.close()
