"""Serial, real-Worker cold delivery release gate. Never applies infrastructure changes."""
import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

PROFILES = {
    'broadband': dict(latency=20, downloadThroughput=20_000_000/8, uploadThroughput=10_000_000/8),
    'slow4g': dict(latency=150, downloadThroughput=1_600_000/8, uploadThroughput=750_000/8),
}
HEADERS = {'cf-cache-status', 'age', 'cache-control', 'content-type', 'content-encoding', 'content-length', 'etag', 'vary'}

def percentile(values, p):
    values=sorted(values);at=(len(values)-1)*p;lo=int(at)
    return values[lo]+(values[min(lo+1,len(values)-1)]-values[lo])*(at-lo)

def verdict(rows, profile, require_edge):
    failures=[]
    if len(rows)<5 or any(r.get('error') for r in rows):failures.append('five complete fresh contexts required')
    good=[r for r in rows if not r.get('error')]
    if not good:return failures,{}
    timings={key:{'p50':percentile([r[key] for r in good],.5),'p75':percentile([r[key] for r in good],.75),'p95':percentile([r[key] for r in good],.95)} for key in ['dashboard_ms','edit_ready_ms','feedback_ms','run_ms']}
    ready=timings['edit_ready_ms']['p75'] if profile=='broadband' else max(r['edit_ready_ms'] for r in good)
    if ready>(15000 if profile=='broadband' else 45000):failures.append('cold runtime latency budget')
    if max(r['feedback_ms'] for r in good)>100:failures.append('first feedback exceeds100ms')
    validations=[v for r in good for v in r['validation_ms']]
    timings['validation_p95_ms']=percentile(validations,.95)
    if timings['validation_p95_ms']>500:failures.append('warm validation exceeds500ms p95')
    if timings['run_ms']['p95']>2000:failures.append('warm demo result exceeds2s p95')
    if any(not r['wasm_gets'] or not any(e['url'].endswith(r['delivery']['wasm_path']) for e in r['wasm_gets']) for r in good):failures.append('actual Worker alias GET missing')
    if require_edge and sum(any(e['headers'].get('cf-cache-status')=='HIT' and not e['disk_cache'] for e in r['wasm_gets']) for r in good)<2:failures.append('fewer than two fresh-context Worker GET edge HITs (browser disk cache is not evidence)')
    return failures,timings

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url');parser.add_argument('--revision',required=True)
    parser.add_argument('--profile',choices=PROFILES,default='broadband')
    parser.add_argument('--samples',type=int,default=5)
    parser.add_argument('--require-edge-hit',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.samples<=10:parser.error('samples must be1..10; fewer than5 is diagnostic only and fails the gate')
    if urlparse(args.url).scheme not in ['http','https']:parser.error('HTTP(S) URL required')
    output={'revision':args.revision,'url':args.url,'profile':args.profile,'network':PROFILES[args.profile],
            'method':'fresh browser contexts/cache; serial client; page CDP network emulation; dedicated Worker network observation (worker-target shaping unsupported); no CDN purge or prewarm; no source upload', 'rows':[]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    def save():args.output.write_text(json.dumps(output,indent=2))
    with sync_playwright() as p:
        browser=p.chromium.launch();output['browser']=browser.version
        for index in range(args.samples):
            context=browser.new_context(locale=['en','ko','ja'][index%3]);page=context.new_page();cdp=context.new_cdp_session(page)
            cdp.send('Network.enable');cdp.send('Network.clearBrowserCache')
            cdp.send('Network.emulateNetworkConditions',{'offline':False,**PROFILES[args.profile]})
            if args.profile=='slow4g':cdp.send('Emulation.setCPUThrottlingRate',{'rate':4})
            row={'index':index,'locale':['en','ko','ja'][index%3],'wasm_gets':[],'worker_commands':[]}
            def attached(event):
                sid=event['sessionId']
                for i,command in enumerate([{'method':'Network.enable'}, {'method':'Runtime.runIfWaitingForDebugger'}]):
                    cdp.send('Target.sendMessageToTarget',{'sessionId':sid,'message':json.dumps({'id':i+1,**command})})
            def received(event):
                message=json.loads(event['message'])
                if 'id' in message:row['worker_commands'].append(message)
                if message.get('method')=='Network.responseReceived':
                    r=message['params']['response']
                    if '/pyodide.asm.wasm' in r['url']:
                        row['wasm_gets'].append({'url':r['url'],'status':r['status'],'disk_cache':r.get('fromDiskCache',False),
                           'headers':{k.lower():v for k,v in r['headers'].items() if k.lower() in HEADERS}})
            cdp.on('Target.attachedToTarget',attached);cdp.on('Target.receivedMessageFromTarget',received)
            cdp.send('Target.setAutoAttach',{'autoAttach':True,'waitForDebuggerOnStart':True,'flatten':False})
            try:
                start=time.perf_counter();response=page.goto(args.url,wait_until='domcontentloaded',timeout=60000)
                assert response.status==200
                page.wait_for_function('()=>window.factoryStudio?.getState().valid',timeout=60000)
                row['dashboard_ms']=(time.perf_counter()-start)*1000
                version=page.evaluate('S.demoVersion');assert version['source_revision']==args.revision
                row['delivery']=version['browser_runtime_delivery']
                assert row['delivery']['wasm_sha256']==version['browser_runtime']['artifacts']['pyodide.asm.wasm']
                start=time.perf_counter()
                row['feedback_ms']=page.evaluate('''async()=>{window.deliveryStates=[];const t=performance.now();const e=document.querySelector('#runtime-status');new MutationObserver(()=>deliveryStates.push({ms:performance.now()-t,text:e.textContent})).observe(e,{childList:true,subtree:true,characterData:true});editor.setValue(editor.getValue()+'\\n# delivery measurement');await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));if(!document.querySelector('#dirty-dot').textContent)throw Error('no feedback');return performance.now()-t}''')
                page.wait_for_function('()=>S.runtimeState==="ready"&&S.valid || S.runtimeState==="init_error"',timeout=190000)
                assert page.evaluate('S.runtimeState')=='ready',page.locator('#runtime-status').inner_text()
                row['edit_ready_ms']=(time.perf_counter()-start)*1000
                row['states']=page.evaluate('deliveryStates')
                row['worker_resources']=[w.evaluate('performance.getEntriesByType("resource").map(e=>e.toJSON())') for w in page.workers]
                assert not any('error' in cmd for cmd in row['worker_commands']),row['worker_commands']
                row['validation_ms']=[page.evaluate('async()=>{const t=performance.now();await applyCode(true);return performance.now()-t}') for _ in range(5)]
                row['run_ms']=page.evaluate('async()=>{const t=performance.now();await run();pause();await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));return performance.now()-t}')
                assert page.evaluate('S.result.execution.kind==="browser" && S.result.summary.completed>0')
            except Exception as error:row['error']=str(error)
            output['rows'].append(row);save();context.close()
            print(index,row.get('edit_ready_ms'),row.get('error',''),flush=True)
        browser.close()
    output['failures'],output['timings']=verdict(output['rows'],args.profile,args.require_edge_hit)
    output['verdict']='FAIL' if output['failures'] else 'PASS';save()
    print(output['verdict'],output['failures'])
    return bool(output['failures'])

if __name__=='__main__':raise SystemExit(main())
