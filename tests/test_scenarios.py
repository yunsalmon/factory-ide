"""Scenario snapshots, exact numeric oracles, integrity and compatibility."""
import copy,json,subprocess,sys,unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
from engine import Factory
from test_orders import ordered_model
ROOT=Path(__file__).resolve().parents[1]
class ScenarioTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,text=True)
  cls.base=cls.server.stdout.readline().strip().split(' → ')[-1];cls.p=sync_playwright().start();cls.browser=cls.p.chromium.launch();cls.page=cls.browser.new_page();cls.page.goto(cls.base);cls.page.wait_for_function('()=>window.factoryStudio?.getState().valid')
 @classmethod
 def tearDownClass(cls):cls.browser.close();cls.p.stop();cls.server.terminate();cls.server.wait();cls.server.stdout.close()
 def snapshot(self,m):
  return self.page.evaluate('async x=>scenarioCreate("A","memo",x.source,x.result,{python:"test"})',{'source':'MODEL = '+repr(m),'result':Factory(m).run()})
 def test_identical_hashes_zero_deltas_and_immutable(self):
  m=ordered_model();a=self.snapshot(m);b=self.snapshot(copy.deepcopy(m));self.assertEqual(a['source_hash'],b['source_hash']);self.assertEqual(a['model_hash'],b['model_hash']);self.assertEqual(a['trace_hash'],b['trace_hash'])
  v=self.page.evaluate('([a,b])=>{const before=JSON.stringify([a,b]);const v=scenarioCompare(a,b);if(before!==JSON.stringify([a,b]))throw Error("mutated");return v}',[a,b]);self.assertTrue(all(x['delta'] in [0,None] for x in v['metrics']));self.assertEqual(v['evidence'],[])
  self.assertEqual(self.page.evaluate('async()=>await scenarioHash({b:2,a:1})===await scenarioHash({a:1,b:2})'),True)
 def test_signed_metrics_quantity_and_trace_links(self):
  m=ordered_model();m['machines'][0]['time']=1;a=self.snapshot(m);m['machines'][0]['time']=25;b=self.snapshot(m)
  v=self.page.evaluate('([a,b])=>scenarioCompare(a,b)',[a,b]);rows={x['key']:x for x in v['metrics']};self.assertLess(rows['completed_lots']['delta'],0);self.assertGreater(rows['wip_lots']['delta'],0)
  self.assertEqual(rows['completed_lots']['delta'],b['result']['summary']['completed']-a['result']['summary']['completed'])
  for side,snap in [('baseline',a),('candidate',b)]:
   state={}
   for e in snap['result']['events']:state.update(e['state_changes']['lots'])
   qty=sum(l['quantity'] for l in state.values() if l['state']=='completed');self.assertEqual(rows['completed_quantity'][side],qty)
  self.assertTrue(v['evidence'])
  for row in v['evidence']:
   for side,snap in [('baseline',a),('candidate',b)]:
    if row[side]:self.assertEqual(snap['result']['events'][row[side]['index']]['time'],row[side]['data']['time'])
 def test_source_change_does_not_claim_runtime_change(self):
  r=Factory(ordered_model()).run()
  comparison=self.page.evaluate('async r=>{const make=async source=>{const result=structuredClone(r);result.execution={kind:"browser",runtime:{python:"3.12.7",simpy:"4.1.1"},source_sha256:await scenarioHash(source,true)};return scenarioCreate(source,"",source,result)};return scenarioCompare(await make("baseline"),await make("candidate"));}',r)
  self.assertNotIn('sc_runtime_warning',comparison['warnings'])
 def test_finite_output_capacity_and_cross_line_change(self):
  from test_operations import line
  m=line();m['machines'][1]['line']='B';a=self.snapshot(m)
  m['buffers'].append(dict(id='OUT',at='OUTPUT',capacity=1));b=self.snapshot(m)
  rows={x['key']:x for x in self.page.evaluate('([a,b])=>scenarioCompare(a,b).metrics',[a,b])}
  self.assertLess(rows['completed_lots']['delta'],0)
  expected=sum(e['kind']=='move' and e['lot']['location']=='M' and e['lot']['target']=='N' for e in a['result']['events'])
  self.assertEqual(rows['cross_line']['baseline'],expected)
  self.assertGreater(rows['buffer:OUT']['candidate'],0)
 def test_rejects_horizons_units_schema_and_tampering(self):
  a=self.snapshot(ordered_model())
  for change,code in [({'horizon':99},'sc_horizon'),({'unit':'hours'},'sc_units'),({'schema':1},'sc_schema')]:
   error=self.page.evaluate('([a,change])=>{let b=structuredClone(a);if(change.horizon){b.result.summary.horizon=change.horizon;b.result.model.duration=change.horizon;}if(change.unit)b.result.model.time_unit=change.unit;if(change.schema)b.result.operational_schema_version=change.schema;try{scenarioCompare(a,b)}catch(e){return e.scenarioCode}}',[a,change]);self.assertEqual(error,code)
  restored=self.page.evaluate('async a=>{const doc=scenarioArtifact([a,a],0,1);return await scenarioRestore(JSON.parse(JSON.stringify(doc)))}',a);self.assertEqual(restored['items'],[a,a])
  error=self.page.evaluate('async a=>{const doc=scenarioArtifact([a],0,0);doc.scenarios[0].result.events[0].time+=.01;try{await scenarioRestore(doc)}catch(e){return e.scenarioCode}}',a);self.assertIn(error,['sc_integrity','sc_invalid'])
 def test_missing_not_zero_and_buffer_full_integral(self):
  a=self.snapshot(ordered_model());r=a['result'];r.pop('operation_metrics');r.pop('order_plan');r['model'].pop('orders',None)
  for e in r['events']:
   for lot in e['state_changes']['lots'].values():lot.pop('quantity',None)
  metrics=self.page.evaluate('r=>scenarioMetrics(r)',r);self.assertIsNone(metrics['processing_share']);self.assertIsNone(metrics['completed_quantity']);self.assertIsNone(metrics['late_orders'])
  m=ordered_model();m['buffers']=[dict(id='IN',at='INPUT',capacity=1),dict(id='OUT',at='OUTPUT',capacity=10)];m['machines'][0]['availability']=[dict(state='down',start=0,end=10)];r=Factory(m).run()
  metrics=self.page.evaluate('r=>scenarioMetrics(r)',r);buf=copy.deepcopy(r['initial_state']['buffers']);last=0;full=0
  for e in r['events']:
   if len(buf['IN']['contents'])==1:full+=e['time']-last
   buf.update(e['state_changes']['buffers']);last=e['time']
  if len(buf['IN']['contents'])==1:full+=r['summary']['horizon']-last
  self.assertEqual(metrics['buffer:IN'],full)
 def restored_metrics(self, result):
  # Valid hashes deliberately describe incomplete measurements, not corrupted hashes.
  return self.page.evaluate('async result=>{const a=await scenarioCreate("measurements","","MODEL = {}",result);const restored=await scenarioRestore(JSON.parse(JSON.stringify(scenarioArtifact([a,a],0,1))));return scenarioCompare(...restored.items);}',result)
 def test_missing_buffer_evidence_after_valid_hash_roundtrip(self):
  m=ordered_model();m['buffers']=[dict(id='IN',at='INPUT',capacity=1),dict(id='OUT',at='OUTPUT',capacity=10)];m['machines'][0]['availability']=[dict(state='down',start=0,end=10)]
  trace=Factory(m).run();valid={x['key']:x for x in self.restored_metrics(trace)['metrics']};self.assertEqual(valid['buffer:IN']['baseline'],13)
  for mode in ['contents','null_contents','entire_buffer','single_interval']:
   damaged=copy.deepcopy(trace)
   snapshots=[damaged['initial_state']['buffers']]+[e['state_changes']['buffers'] for e in damaged['events']]
   if mode=='single_interval':snapshots=[next(e['state_changes']['buffers'] for e in damaged['events'] if e['state_changes']['buffers'].get('IN',{}).get('contents'))]
   for snapshot in snapshots:
    if 'IN' not in snapshot:continue
    if mode=='entire_buffer':snapshot.pop('IN')
    elif mode=='null_contents':snapshot['IN']['contents']=None
    else:snapshot['IN'].pop('contents',None)
   rows={x['key']:x for x in self.restored_metrics(damaged)['metrics']}
   for key in ['buffer:IN','buffer_full']:
    self.assertIsNone(rows[key]['baseline'],mode);self.assertIsNone(rows[key]['candidate']);self.assertIsNone(rows[key]['delta'])
 def test_missing_machine_or_state_preserves_expected_denominator(self):
  from test_operations import line
  trace=Factory(line()).run();rows={x['key']:x for x in self.restored_metrics(trace)['metrics']};self.assertAlmostEqual(rows['processing_share']['baseline'],.15)
  for mode in ['machine','processing','idle','down']:
   damaged=copy.deepcopy(trace)
   if mode=='machine':damaged['operation_metrics']['machines'].pop('N')
   else:damaged['operation_metrics']['machines']['N'].pop(mode)
   rows={x['key']:x for x in self.restored_metrics(damaged)['metrics']}
   self.assertIsNone(rows['processing_share']['baseline'],mode);self.assertIsNone(rows['processing_share']['delta'])
   self.assertIsNone(rows['state:N:'+('processing' if mode=='machine' else mode)]['baseline'])
 def test_complete_empty_measurements_are_true_zero(self):
  from test_operations import line
  m=line();m['orders']=[];m['buffers']=[dict(id='IN',at='INPUT',capacity=1)];trace=Factory(m).run()
  rows={x['key']:x for x in self.restored_metrics(trace)['metrics']}
  for key in ['buffer:IN','buffer_full','processing_share']:
   self.assertEqual(rows[key]['baseline'],0);self.assertEqual(rows[key]['candidate'],0);self.assertEqual(rows[key]['delta'],0)
if __name__=='__main__':unittest.main()
