import json,subprocess,sys,unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
from engine import Factory
from test_orders import ordered_model
ROOT=Path(__file__).parents[1]
class ExperimentTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port','0'],cwd=ROOT,stdout=subprocess.PIPE,text=True);cls.base=cls.server.stdout.readline().strip().split(' → ')[-1]
  cls.p=sync_playwright().start();cls.b=cls.p.chromium.launch();cls.page=cls.b.new_page();cls.page.goto(cls.base);cls.page.wait_for_function('()=>window.factoryStudio?.getState().valid')
 @classmethod
 def tearDownClass(cls):cls.b.close();cls.p.stop();cls.server.terminate();cls.server.wait();cls.server.stdout.close()
 def test_independent_percentile_interval_oracles(self):
  r=self.page.evaluate('()=>experimentStats([1,2,3,4])');self.assertEqual(r['mean'],2.5);self.assertEqual(r['p50'],2.5);self.assertAlmostEqual(r['p90'],3.7);self.assertAlmostEqual(r['interval'][0],.445739743,places=7);self.assertAlmostEqual(r['interval'][1],4.554260257,places=7)
  self.assertIsNone(self.page.evaluate('experimentStats([5]).interval'));self.assertIsNone(self.page.evaluate('experimentStats([]).mean'));self.assertEqual(self.page.evaluate('experimentStats([0,0]).interval'),[0,0])
 def test_seed_boundaries(self):
  for text in ['','1,1','-1','4294967296','1.2',','.join(map(str,range(31)))]:self.assertEqual(self.page.evaluate('s=>{try{experimentSeeds(s)}catch(e){return e.experimentCode}}',text),'ex_seeds')
  self.assertEqual(self.page.evaluate('experimentSeeds("0,4294967295")'),[0,4294967295])
 def test_scheduler_order_exclusions_and_restore(self):
  r=Factory(ordered_model()).run()
  out=self.page.evaluate('''async r=>{let active=0,max=0;const factory=()=>({runtime:{python:'test'},stop(){},async runSeed(source,seed){active++;max=Math.max(max,active);await new Promise(ok=>setTimeout(ok,seed===1?30:1));active--;if(seed===2)throw Error('deliberate failure');const result=structuredClone(r);result.model.seed=seed;return {source:'seed:'+seed,result};}});const d=await experimentDefinition('fixture','','MODEL',r.model,[1,2,3],2);const runner=new ExperimentRunner({factory});const a=await runner.run(d),b=await runner.run(d);const exported=await experimentExport([a,b]),restored=await experimentRestore(exported);let bad;exported.experiments[0].runs[0].metrics.completed_lots=999;try{await experimentRestore(exported)}catch(e){bad=e.experimentCode}return {max,a,equal:scenarioCanonical(a)===scenarioCanonical(b),restored:scenarioCanonical(restored)===scenarioCanonical([a,b]),stats:experimentAggregate(a),bad};}''',r)
  self.assertEqual(out['max'],2);self.assertEqual([x['status'] for x in out['a']['runs']],['success','failed','success']);self.assertEqual(out['a']['representative']['index'],0);self.assertTrue(out['equal']);self.assertTrue(out['restored']);self.assertEqual(out['bad'],'ex_integrity');self.assertTrue(all(x['excluded']>=1 for x in out['stats']))
 def test_cancel_outstanding_and_recovery(self):
  r=Factory(ordered_model()).run()
  out=self.page.evaluate('''async r=>{let stops=0,starts=0;let slow=true;const factory=()=>({runtime:{},reject:null,stop(){stops++;this.reject?.(Error('stopped'));},async runSeed(source,seed){starts++;if(slow)await new Promise((resolve,reject)=>{this.reject=reject;});const result=structuredClone(r);result.model.seed=seed;return {source:'x',result};}});const runner=new ExperimentRunner({factory});const d=await experimentDefinition('cancel','','x',r.model,[1,2,3,4],2);const pending=runner.run(d);await new Promise(ok=>setTimeout(ok,10));runner.cancel();const a=await pending;slow=false;const b=await runner.run(d);return {a:a.runs.map(r=>r.status),b:b.runs.map(r=>r.status),stops,starts,workers:runner.workers.size};}''',r)
  self.assertEqual(out['a'],['cancelled']*4);self.assertEqual(out['b'],['success']*4);self.assertEqual(out['workers'],0);self.assertEqual(out['starts'],6)
 def test_definition_and_trace_memory_bounds(self):
  r=Factory(ordered_model()).run()
  out=self.page.evaluate('''async r=>{let sourceError;try{await experimentDefinition('large','','x'.repeat(EXP_LIMITS.source+1),r.model,[1],1)}catch(e){sourceError=e.experimentCode}const runner=new ExperimentRunner({factory:()=>({runtime:{},stop(){},async runSeed(){const result=structuredClone(r);result.console='x'.repeat(EXP_LIMITS.trace+1);return {source:'x',result}}})});const exp=await runner.run(await experimentDefinition('large trace','','x',r.model,[r.model.seed],1));return {sourceError,status:exp.runs[0].status,error:exp.runs[0].error,representative:exp.representative,contract:exp.contract};}''',r)
  self.assertEqual(out['sourceError'],'ex_invalid');self.assertEqual(out['status'],'failed');self.assertEqual(out['error'],'ex_limit');self.assertIsNone(out['representative']);self.assertIsNone(out['contract'])
 def test_missing_and_incompatible_never_zero(self):
  r=self.page.evaluate('()=>experimentAggregate({runs:[{status:"success",metrics:{a:null,b:4}},{status:"failed"},{status:"cancelled"}]})');self.assertIsNone(r[0]['mean']);self.assertEqual(r[0]['excluded'],3);self.assertEqual(r[1]['mean'],4);self.assertEqual(r[1]['n'],1)
  self.assertEqual(self.page.evaluate('()=>{try{experimentCompare({contract:{horizon:1},runs:[]},{contract:{horizon:2},runs:[]})}catch(e){return e.experimentCode}}'),'ex_incompatible')
