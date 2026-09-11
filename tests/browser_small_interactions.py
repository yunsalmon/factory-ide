"""Pinned Chromium/CDP gate for issue 35's small-demo interaction budget."""
import json
import math
from pathlib import Path
import sys

from playwright.sync_api import sync_playwright


base = sys.argv[1].rstrip('/')
evidence_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('artifacts/small-interactions.json')
evidence_path.parent.mkdir(parents=True, exist_ok=True)


def percentile(values, quantile):
    return sorted(values)[math.ceil(len(values) * quantile) - 1]


observer = """
window.issue35LongTasks=[];
new PerformanceObserver(list=>issue35LongTasks.push(...[...list.getEntries()].map(e=>({start:e.startTime,duration:e.duration})))).observe({type:'longtask',buffered:true});
"""
install = """() => {
  issue35LongTasks.length=0;
  issue35Calls=[];
  for (const name of ['renderGraph','renderTrace','renderInventory','renderOperations','renderAllocationResults']) {
    const original=window[name];
    window[name]=function(...args){const start=performance.now();try{return original.apply(this,args)}finally{issue35Calls.push({name,ms:performance.now()-start})}};
  }
  window.issue35Action=async(name,index)=>{
    const start=performance.now(),callStart=issue35Calls.length,half=Math.floor(S.result.events.length/2);
    if(name==='cursor') seek(half+(index%3));
    if(name==='wip_cursor') seek(half+(index%3));
    if(name==='wip') selectTab('wip');
    if(name==='filter'){WIP.filters={status:index%2?'waiting':'processing'};renderInventory()}
    if(name==='search'){WIP.filters={search:index%2?'LOT-0':'L'};renderInventory()}
    if(name==='lot'){const rows=wipProjection().rows;WIP.selectedLot=rows[index%Math.max(1,rows.length)]?.id;renderInventory()}
    if(name==='operations') selectTab('operations');
    if(name==='results') selectTab('results');
    const sync=performance.now()-start;
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    return {duration:performance.now()-start,sync,calls:issue35Calls.slice(callStart)};
  };
}"""


records = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in [1440, 320]:
        context = browser.new_context(locale='en', viewport={'width': width, 'height': 900})
        context.add_init_script(observer)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        cdp = context.new_cdp_session(page)
        cdp.send('Emulation.setCPUThrottlingRate', {'rate': 4})
        page.goto(base)
        page.wait_for_function('() => document.documentElement.dataset.uiReady === "true" && Boolean(window.factoryStudio?.getState().result?.events.length)')
        cold_tasks = page.evaluate('issue35LongTasks')
        assert max([row['duration'] for row in cold_tasks] or [0]) < 200, cold_tasks
        before = page.evaluate('JSON.stringify(S.result)')
        page.evaluate('issue35LongTasks.length=0')
        page.locator('#code').focus()
        page.wait_for_timeout(50)
        focus_tasks = page.evaluate('issue35LongTasks')
        assert max([row['duration'] for row in focus_tasks] or [0]) < 200, focus_tasks
        assert page.evaluate('editor === null')
        assert page.locator('#enhance-editor').is_visible()
        page.evaluate(install)
        actions = {}
        for name in ['cursor', 'wip', 'wip_cursor', 'filter', 'search', 'lot', 'operations', 'results']:
            samples = []
            for index in range(20):
                if name == 'wip':
                    page.evaluate('selectTab("events")')
                if name == 'wip_cursor':
                    page.evaluate('selectTab("wip");WIP.filters={};WIP.selectedLot=null;WIP.selectedGroup=null')
                if name == 'operations':
                    page.evaluate('selectTab("results")')
                if name == 'results':
                    page.evaluate('selectTab("operations")')
                samples.append(page.evaluate('([name,index])=>issue35Action(name,index)', [name, index]))
            p95 = percentile([sample['duration'] for sample in samples], .95)
            assert p95 <= 100, (width, name, p95, samples)
            actions[name] = {
                'p95_ms': p95,
                'max_sync_ms': max(sample['sync'] for sample in samples),
                'component_max_ms': {
                    component: max([call['ms'] for sample in samples for call in sample['calls'] if call['name'] == component] or [0])
                    for component in ['renderGraph', 'renderTrace', 'renderInventory', 'renderOperations', 'renderAllocationResults']
                },
            }
        interaction_tasks = page.evaluate('issue35LongTasks')
        assert max([row['duration'] for row in interaction_tasks] or [0]) < 200, interaction_tasks
        assert page.evaluate('JSON.stringify(S.result)') == before
        page.evaluate('selectTab("events")')
        assert page.locator('[data-event]').count() <= 40
        indices = page.locator('[data-event]').evaluate_all('(rows)=>rows.map(row=>Number(row.dataset.event))')
        assert indices and all(index < page.evaluate('S.cursor') for index in indices)
        if page.locator('#event-older').count():
            latest = indices
            page.locator('#event-older').click()
            older = page.locator('[data-event]').evaluate_all('(rows)=>rows.map(row=>Number(row.dataset.event))')
            assert older and max(older) < min(latest)
            page.locator('#event-newer').click()
            assert page.locator('[data-event]').evaluate_all('(rows)=>rows.map(row=>Number(row.dataset.event))') == latest
        page.evaluate('selectTab("wip");WIP.selectedLot=wipProjection().rows[0]?.id;renderInventory()')
        assert page.locator('.wip-highlight').count() > 0
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors, errors
        records.append({'width': width, 'cold_long_tasks_ms': cold_tasks,
                        'editor_focus_long_tasks_ms': focus_tasks,
                        'interaction_long_tasks_ms': interaction_tasks, 'actions': actions})
        context.close()
    for language in ['ko', 'en', 'ja']:
        context = browser.new_context(locale=language, viewport={'width': 320, 'height': 900})
        page = context.new_page(); page.goto(base)
        page.wait_for_function('() => Boolean(window.factoryStudio?.getState().result?.events.length)')
        page.locator('#language').select_option(language)
        page.evaluate('seek(Math.floor(S.result.events.length/2));selectTab("events")')
        assert page.locator('#event-older').get_attribute('aria-label') is None
        assert page.locator('.event-pages').get_attribute('aria-label') == page.evaluate('tr("event_page_label")')
        assert page.locator('[data-event]').count() == 40
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        context.close()
    browser.close()

evidence_path.write_text(json.dumps({'browser': 'playwright chromium 1.62.0', 'cpu_throttle': 4,
                                     'budget_ms': 100, 'long_task_limit_ms': 200,
                                     'records': records}, indent=2))
print(f'PASS issue35 small interactions: {evidence_path}')
