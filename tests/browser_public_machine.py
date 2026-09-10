"""Graph machine inspector edits run inside the static browser worker, never APIs."""
import sys,time
from playwright.sync_api import sync_playwright,expect
base=sys.argv[1].rstrip('/')
with sync_playwright() as p:
 browser=p.chromium.launch()
 for lang in ['ko','en','ja']:
  page=browser.new_page(locale=lang,viewport={'width':1440,'height':1080});errors=[];requests=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.on('request',lambda r:requests.append(r.url))
  page.goto(base)
  def wait(expression,timeout=45):
   end=time.monotonic()+timeout
   while not page.evaluate(expression):
    assert time.monotonic()<end,expression
    page.wait_for_timeout(30)
  wait('Boolean(window.factoryStudio?.getState().valid)')
  original=page.evaluate('S.source')
  page.locator('[data-node="CUT_A"]').click()
  expect(page.locator('#inspector input[name="time"]')).to_be_visible()
  page.locator('input[name="time"]').fill('11')
  page.locator('#property-form button[type="submit"]').click()
  wait('S.model.machines.find(m=>m.id==="CUT_A").time===11 && !S.busy')
  assert page.evaluate('S.source')!=original
  assert page.evaluate('S.result===null')
  updated=page.evaluate('S.source')
  assert updated[updated.index('\ndef choose_candidate'):]==original[original.index('\ndef choose_candidate'):]
  page.locator('#run-button').click()
  wait('S.result?.execution?.kind==="browser" && !S.job')
  assert page.evaluate('S.result.model.machines.find(m=>m.id==="CUT_A").time')==11
  # WIP selection still highlights the graph; graph inspection remains usable.
  page.evaluate('()=>{pause();seek(S.result.events.findIndex(e=>e.kind==="start")+1);selectTab("wip")}')
  page.locator('[data-wip-lot]').first.click()
  assert page.locator('.wip-highlight').count()>0
  page.locator('[data-node="CUT_A"]').click()
  expect(page.locator('#inspector input[name="time"]')).to_have_value('11')
  # Invalid model synchronization must not corrupt the currently valid source.
  assert page.evaluate('''async()=>{const before=S.source,m=copy(S.model);m.machines[0].time=-1;
   try{await browserRuntime.sync(S.source,m);return false}catch{return S.source===before}}''')
  assert not any('/api/' in url for url in requests),requests
  assert not errors,errors
  print('PASS public CUT_A graph / inspector / time edit / execution / WIP',lang)
  page.close()
 browser.close()
