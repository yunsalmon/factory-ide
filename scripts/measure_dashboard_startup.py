"""Single-client public Nginx startup benchmark; no product or CDN mutation.

python scripts/measure_dashboard_startup.py URL --output /tmp/startup.json
Fresh: empty localStorage + empty browser HTTP cache. Restored: an actual edited
browser execution, exact saved snapshot, but empty HTTP cache in a new context.
CDN cache is not purged. Page CDP emulates 20Mbps/20ms desktop or 1.6Mbps/150ms
320px with 4x CPU. Dedicated Worker traffic is NOT network-throttled by page CDP.
"""
import argparse
import hashlib
import json
import math
import time
from pathlib import Path
from playwright.sync_api import sync_playwright


def percentile(values, p):
    values = sorted(values)
    offset = (len(values) - 1) * p
    lo = math.floor(offset)
    return values[lo] + (values[math.ceil(offset)] - values[lo]) * (offset - lo)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 5:
        parser.error('At least five repeats are required')
    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        seed = browser.new_context()
        page = seed.new_page()
        page.goto(args.url)
        page.wait_for_function('()=>window.factoryStudio && S.statusMessage?.code==="public_ready"')
        page.evaluate('()=>editor.setValue(S.source+"\\n# startup benchmark saved browser execution\\n")')
        page.wait_for_function('()=>S.valid && S.runtimeState==="ready"', timeout=210000)
        page.evaluate('()=>{void run()}')
        page.wait_for_function('()=>!S.job && !S.busy && S.result?.execution?.kind==="browser"', timeout=30000)
        page.evaluate('()=>{pause();seek(15);selectTab("results");persistReplay()}')
        storage = seed.storage_state()
        expected = page.evaluate('({source:S.source,result:S.result,cursor:S.cursor,tab:S.tab})')
        seed.close()
        for mobile in (False, True):
            for restored in (False, True):
                for repeat in range(args.repeats):
                    context = browser.new_context(
                        storage_state=storage if restored else None,
                        locale=['en', 'ko', 'ja'][repeat % 3],
                        viewport={'width': 320 if mobile else 1500, 'height': 900})
                    context.add_init_script('''window.startupWorkers=0;
                        window.Worker=new Proxy(Worker,{construct(target,args){
                            startupWorkers++;return Reflect.construct(target,args);
                        }});
                        window.startupProgressMs=null;
                        new MutationObserver(()=>{
                            const node=document.querySelector('#startup-progress');
                            if(node && !node.hidden && startupProgressMs===null)
                                startupProgressMs=performance.now();
                        }).observe(document,{childList:true,subtree:true});''')
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    cdp = context.new_cdp_session(page)
                    cdp.send('Network.enable')
                    cdp.send('Network.clearBrowserCache')
                    cdp.send('Network.emulateNetworkConditions', dict(
                        offline=False, latency=150 if mobile else 20,
                        downloadThroughput=200000 if mobile else 2500000,
                        uploadThroughput=93750 if mobile else 1250000))
                    cdp.send('Emulation.setCPUThrottlingRate', {'rate': 4 if mobile else 1})
                    responses = []
                    cdp.on('Network.responseReceived', lambda event: responses.append({
                        'url': event['response']['url'], 'headers': event['response']['headers'],
                        'fromDiskCache': event['response'].get('fromDiskCache', False)}))
                    start = time.perf_counter()
                    page.goto(args.url, wait_until='domcontentloaded')
                    page.wait_for_function('()=>window.factoryStudio && S.result && S.statusMessage?.code==="public_ready"', timeout=210000)
                    # Include a real tab interaction and the next painted frame.
                    if restored:
                        assert page.evaluate('({source:S.source,result:S.result,cursor:S.cursor,tab:S.tab})') == expected
                    page.locator('[data-tab="wip"]').click()
                    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    elapsed = (time.perf_counter() - start) * 1000
                    assert page.evaluate('S.tab==="wip" && S.valid')
                    assert not errors, errors
                    row = dict(mobile=mobile, restored=restored, repeat=repeat, ms=elapsed,
                        revision=page.evaluate('S.demoVersion.source_revision'),
                        runtime=page.evaluate('S.runtimeState'),
                        workers=page.evaluate('startupWorkers'),
                        progress_ms=page.evaluate('startupProgressMs'), responses=responses,
                        resources=page.evaluate('performance.getEntriesByType("resource").map(r=>({name:r.name,start:r.startTime,duration:r.duration,bytes:r.transferSize}))'))
                    rows.append(row)
                    args.output.write_text(json.dumps(dict(browser=browser.version, rows=rows), indent=2))
                    print(mobile, restored, repeat, round(elapsed), flush=True)
                    context.close()
        browser.close()
    summary = []
    for mobile in (False, True):
        for restored in (False, True):
            values = [r['ms'] for r in rows if r['mobile'] == mobile and r['restored'] == restored]
            p75 = percentile(values, .75)
            summary.append(dict(mobile=mobile, restored=restored, n=len(values),
                p50=percentile(values, .5), p75=p75, p95=percentile(values, .95),
                budget_ms=5000 if mobile else 3000, passed=p75 <= (5000 if mobile else 3000)))
    artifact = json.loads(args.output.read_text())
    artifact.update(summary=summary, fixture_source_sha256=hashlib.sha256(expected['source'].encode()).hexdigest())
    args.output.write_text(json.dumps(artifact, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
