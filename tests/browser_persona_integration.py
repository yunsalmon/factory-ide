"""Canonical import feeds all operator projections and immutable scenario restore."""
import atexit,json,subprocess,sys
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from test_persona_integration import combined_model
server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,text=True)
atexit.register(server.terminate);base=server.stdout.readline().strip().split(' → ')[-1]
with sync_playwright() as p:
 browser=p.chromium.launch()
 for lang in ['ko','en','ja']:
  page=browser.new_page(locale=lang,viewport={'width':390,'height':844});errors=[];requests=[]
  page.on('pageerror',lambda error:errors.append(str(error)))
  page.on('request',lambda request:requests.append(request.post_data or ''))
  page.goto(base);page.wait_for_function('()=>window.factoryStudio?.getState().valid')
  model=combined_model();doc=dict(schema_version=1,kind='model',time_unit='minutes',model=model)
  page.locator('[data-tab="data"]').click()
  before=page.evaluate('S.source')
  page.locator('#data-file').set_input_files({'name':'canonical.json','mimeType':'application/json','buffer':json.dumps(doc).encode()})
  page.wait_for_function('()=>DATA.pending?.kind==="model" && !DATA.busy')
  assert page.evaluate('S.source')==before
  page.locator('#data-confirm').click();page.wait_for_function('()=>!DATA.busy && S.model.orders?.length===4')
  assert page.evaluate('S.model')==model
  page.locator('#run-button').click();page.wait_for_function('()=>S.job');page.wait_for_function('()=>!S.job && S.result?.order_schema_version===1')
  result=page.evaluate('S.result')
  assert result['summary']['completed']==4
  page.evaluate('()=>{pause();seek(S.result.events.length)}')
  assert page.evaluate('wipProjection().allTotals.count')==4
  assert page.evaluate('orderProjection(S.result,S.cursor).rows.length')==4
  metrics=page.evaluate('scenarioMetrics(S.result)')
  for machine,states in result['operation_metrics']['machines'].items():
   for state,duration in states.items():assert metrics[f'state:{machine}:{state}']==duration/model['duration']
  assert metrics['buffer_full'] is not None and metrics['tardiness'] is not None
  awaitable='''async()=>{const a=await scenarioCreate('Imported','',S.source,S.result);const b=await scenarioCreate('Copy','',S.source,S.result);
   const doc=scenarioArtifact([a,b],0,1),saved=await scenarioRestore(JSON.parse(JSON.stringify(doc)));
   const comparison=scenarioCompare(...saved.items);if(comparison.metrics.some(m=>m.delta!==0&&m.delta!==null))throw Error('KPI mismatch');
   SCENARIOS.loaded=true;Object.assign(SCENARIOS,saved);scenarioLoadResult(0);return S.result;}'''
  assert page.evaluate(awaitable)==result
  page.locator('[data-tab="data"]').click()
  observed=dict(schema_version=1,kind='observations',events=[dict(id='PRIVATE_INTEGRATION_RAW',kind='arrival',time=0,lot='OBS'),dict(id='end',kind='complete',time=3,lot='OBS')])
  page.locator('#data-file').set_input_files({'name':'observations.json','mimeType':'application/json','buffer':json.dumps(observed).encode()})
  page.wait_for_function('()=>DATA.pending?.kind==="observations" && !DATA.busy');page.locator('#data-confirm').click()
  page.wait_for_function('()=>DATA.observations?.events.length===2 && !DATA.busy')
  assert page.evaluate('simulatedCalibration(S.result,S.result.events.length,true,0,S.result.summary.horizon).throughput.lots')==4
  assert 'PRIVATE_INTEGRATION_RAW' not in page.evaluate('JSON.stringify(localStorage)')
  assert not any('PRIVATE_INTEGRATION_RAW' in body for body in requests)
  # The restored imported scenario is the immutable experiment input. Results
  # use the same complete KPI projection and leave transient observations alone.
  page.locator('[data-tab="experiments"]').click()
  page.locator('#ex-name').fill('Imported scenario')
  page.locator('#ex-seeds').fill('5,6')
  page.locator('#ex-concurrency').select_option('2')
  experiment_requests=[]
  page.on('request',lambda request:experiment_requests.append(request.url))
  page.locator('#ex-run').click()
  page.wait_for_function('()=>EXPERIMENTS.items.length===1&&!EXPERIMENTS.runner.active',timeout=120000)
  exp=page.evaluate('EXPERIMENTS.items[0]')
  assert all(run['status']=='success' for run in exp['runs']),exp
  assert exp['definition']['source']==page.evaluate('S.source')
  assert exp['definition']['model']['orders']==model['orders']
  assert exp['definition']['model']['resources']==model['resources']
  for row in exp['runs']:
   assert set(row['metrics'])==set(metrics)
   assert row['metrics']['completed_lots']==4
   assert row['metrics']['buffer_full'] is not None
   assert all(row['metrics'][key] is not None for key in metrics if key.startswith('state:'))
  assert page.evaluate('scenarioCanonical(scenarioMetrics(EXPERIMENTS.items[0].representative.result))===scenarioCanonical(EXPERIMENTS.items[0].runs[0].metrics)')
  assert page.evaluate('DATA.observations.events[0].id')=='PRIVATE_INTEGRATION_RAW'
  assert not any('/api/run' in url for url in experiment_requests),'experiment executed on server'
  assert not any('PRIVATE_INTEGRATION_RAW' in body for body in requests)
  assert 'PRIVATE_INTEGRATION_RAW' not in page.evaluate('JSON.stringify(localStorage)')
  for tab in ['data','scenarios','orders','wip','operations','results','experiments']:
   page.locator(f'[data-tab="{tab}"]').click()
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),tab
   assert 'Translation unavailable' not in page.locator('#trace-content').inner_text()
  page.reload();page.wait_for_function('()=>window.factoryStudio?.getState().valid')
  assert page.evaluate('DATA.observations===null && DATA.text===null')
  assert not errors,errors
  print('PASS canonical import / KPIs / scenarios / transient calibration',lang)
  page.close()
 browser.close()
