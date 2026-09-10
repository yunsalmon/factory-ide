"""Browser-executed pure import/calibration contracts with independent oracles."""
import copy,json,unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
from test_orders import ordered_model
from engine import Factory
ROOT=Path(__file__).parents[1]
class DataTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.p=sync_playwright().start();cls.b=cls.p.chromium.launch();cls.page=cls.b.new_page();cls.page.add_script_tag(path=str(ROOT/'web/data-core.js'))
 @classmethod
 def tearDownClass(cls):cls.b.close();cls.p.stop()
 def call(self,request):return self.page.evaluate('r=>{try{return prepareDataImport(r)}catch(e){return {ok:false,code:e.code,row:e.row,field:e.field}}}',request)
 def model(self):
  m=ordered_model();m['lines']=[{'id':m['machines'][0]['line'],'name':'Line, 日本'}];m['products']=[{'id':'A'},{'id':'B'}];m['buffers']=[{'id':'BUF','at':'INPUT','capacity':None,'policy':'fifo'}];m['orders'][1]['due_time']=None;m['orders'][1]['due_date']=None;m['machines'][0]['name']='';m['orders'][0]['customer']='';m['custom']={'preserved':[1,'한글']};return m
 def test_canonical_and_all_table_csv_roundtrip(self):
  m=self.model();r=self.call(dict(text=json.dumps(dict(schema_version=1,kind='model',time_unit='minutes',model=m)),format='json',baseModel=m));self.assertTrue(r['ok'],r);self.assertEqual(r['candidate'],m)
  for table in ['processes','lines','machines','buffers','routes','products','orders']:
   csv=self.page.evaluate('([t,r])=>exportDataCSV(t,r)',[table,m[table]])
   r=self.call(dict(text=csv,format='csv',table=table,baseModel=m));self.assertTrue(r['ok'],r);self.assertEqual(r['candidate'],m,table)
 def test_mapping_units_and_atomic_row_diagnostics(self):
  m=ordered_model();before=copy.deepcopy(m)
  r=self.call(dict(text='identifier,event,when,resource\nx,state,120,M1\n',format='csv',table='observations',baseModel=m,mapping={'id':'identifier','kind':'event','time':'when','machine':'resource'},options={'unit':'seconds'}))
  self.assertFalse(r['ok']);self.assertEqual(r['diagnostics'][0]['row'],2)
  for text,code,row in [('id,kind,time,machine,state\nx,state,0,BAD,idle','data_reference',2),('id,kind,time,machine,state\nx,state,0,M1,idle\nx,state,2,M1,idle','data_duplicate',3),('id,kind,time,machine,state,time_unit\nx,state,0,M1,idle,fortnight','data_bad_unit',2),('id,kind,time,machine,state\nx,state,2026-09-10T00:00:00+00:60,M1,idle','data_bad_timezone',2)]:
   r=self.call(dict(text=text,format='csv',table='observations',baseModel=m,options={'origin':'2026-09-10T00:00:00Z'}));self.assertFalse(r['ok'],r);self.assertTrue(any(d['code']==code and d['row']==row for d in r['diagnostics']),r)
  self.assertEqual(m,before)
 def test_dates_units_and_limits(self):
  m=ordered_model()
  for stamp,expected in [('2026-09-10T09:02:00+09:00',2),('2026-09-10T00:02:00',2),('120',2)]:
   r=self.call(dict(text=f'id,kind,time,lot\nx,arrival,{stamp},PRIVATE',format='csv',table='observations',baseModel=m,options={'origin':'2026-09-10T00:00:00Z','timezone':'Z','unit':'seconds'}));self.assertTrue(r['ok'],r);self.assertEqual(r['candidate']['events'][0]['time'],expected)
  for text in ['id\n'+'x\n'*20001,'id\n'+'x'*65537,','.join('h'+str(i) for i in range(129))]:
   self.assertEqual(self.call(dict(text=text,format='csv',table='observations',baseModel=m))['code'],'data_limit')
 def test_numeric_observations_validate_document_origin(self):
  m=ordered_model()
  for origin in ['2026-02-30T00:00:00Z','not-rfc3339','2026-01-01T00:00:00+24:00','2026-01-01T00:00:00+00:60','1900-02-29T00:00:00Z','2026-01-01T24:00:00Z','2026-01-01T00:00:00','',None,0]:
   doc=dict(schema_version=1,kind='observations',time_origin=origin,events=[dict(id='x',kind='arrival',time=0,lot='L')])
   r=self.call(dict(text=json.dumps(doc),format='json',baseModel=m));self.assertFalse(r['ok'],origin);self.assertTrue(any(d['field']=='time_origin' and d['row']==1 for d in r['diagnostics']),r)
  for origin in ['2000-02-29T23:59:59.999Z','2024-02-29T00:00:00+23:59','2026-12-31T23:59:59-23:59','0001-01-01T00:00:00Z']:
   doc=dict(schema_version=1,kind='observations',time_origin=origin,events=[dict(id='x',kind='arrival',time=0,lot='L')])
   r=self.call(dict(text=json.dumps(doc),format='json',baseModel=m));self.assertTrue(r['ok'],r);self.assertEqual(r['candidate']['time_origin'],origin);self.assertEqual(r['candidate']['events'][0]['time'],0)
  r=self.call(dict(text='id,kind,time,lot\nx,arrival,0,L',format='csv',table='observations',baseModel=m,options={'origin':'2026-02-30T00:00:00Z'}));self.assertFalse(r['ok']);self.assertEqual(r['diagnostics'][0]['field'],'time_origin')
 def test_parser_errors_preserve_physical_source_rows(self):
  for text,row in [('id,name\nP1,x\nP2,x,extra',3),('id,name\nP1,"line\ncontinued"\nP2,x,extra',4),('id,name\nP1,"line\ncontinued"x',3),('id,name\nP1,"line\nunfinished',3),('id,name\rP1,"line\rcontinued"\rP2,x,extra',4)]:
   r=self.call(dict(text=text,format='csv',table='processes',baseModel=ordered_model()));self.assertEqual(r['code'],'data_bad_csv');self.assertEqual(r['row'],row);self.assertEqual(r['field'],'row')
 def test_json_units_and_physical_row_identity(self):
  m=self.model();r=self.call(dict(text=json.dumps(dict(schema_version=1,kind='model',time_unit='seconds',model=m)),format='json',baseModel=m));self.assertTrue(r['ok'],r);self.assertEqual(r['candidate']['duration'],.5);self.assertAlmostEqual(r['candidate']['machines'][0]['time'],2/60)
  m['machines']=[None,dict(m['machines'][0],process='MISSING')]
  r=self.call(dict(text=json.dumps(dict(schema_version=1,kind='model',model=m)),format='json',baseModel=self.model()));self.assertFalse(r['ok']);self.assertTrue(any(d['table']=='machines' and d['row']==2 and d['code']=='data_reference' for d in r['diagnostics']),r)
 def test_hand_computed_calibration_and_missingness(self):
  ev=[]
  for lot,arrival,start,finish,complete,qty in [('L1',0,2,4,5,2),('L2',1,5,8,9,3)]:
   for kind,t in [('arrival',arrival),('start',start),('finish',finish),('complete',complete)]:ev.append(dict(kind=kind,time=t,lot=lot,quantity=qty))
  ev += [dict(kind='state',time=t,machine='M1',state=s) for t,s in [(0,'idle'),(2,'processing'),(4,'idle'),(5,'processing'),(8,'idle'),(12,'idle')]]
  metric=self.page.evaluate('e=>calibrationMetrics(e,{machines:[{id:"M1"}]},0,10)',ev)
  self.assertEqual(metric['throughput']['quantity'],5);self.assertEqual(metric['throughput']['lots_per_hour'],12);self.assertEqual(metric['cycle']['mean'],6.5);self.assertAlmostEqual(metric['cycle']['p90'],7.7);self.assertEqual(metric['wait']['mean'],3);self.assertEqual(metric['utilization'][0]['known'],10);self.assertEqual(metric['utilization'][0]['value'],.5)
  clipped=self.page.evaluate('e=>calibrationMetrics(e,{machines:[{id:"M1"}]},2,6)',ev);self.assertEqual(clipped['utilization'][0]['known'],4);self.assertEqual(clipped['utilization'][0]['processing'],3)
  ev.append(dict(kind='complete',time=7,lot='UNKNOWN'))
  metric=self.page.evaluate('e=>calibrationMetrics(e,{machines:[{id:"M1"}]},0,10)',ev)
  self.assertIsNone(metric['throughput']['quantity']);self.assertEqual(metric['coverage']['cycle_missing'],1)
  metric=self.page.evaluate('()=>calibrationMetrics([{kind:"state",time:0,machine:"M1",state:"processing"}],{machines:[{id:"M1"}]},0,10)')
  self.assertEqual(metric['utilization'][0]['coverage'],0);self.assertIsNone(metric['utilization'][0]['value'])
 def test_simulation_final_and_cursor_no_future(self):
  trace=Factory(ordered_model()).run()
  result=self.page.evaluate('r=>({final:simulatedCalibration(r,0,true,0,30),zero:simulatedCalibration(r,0,false,0,30),prefix:r.events.map((e,i)=>simulatedCalibration(r,i+1,false,0,30))})',trace)
  complete=[e for e in trace['events'] if e['kind']=='complete']
  self.assertEqual(result['final']['throughput']['lots'],len(complete));self.assertEqual(result['final']['throughput']['quantity'],sum(e['lot']['quantity'] for e in complete));self.assertEqual(result['zero']['throughput']['lots'],0)
  for i,metric in enumerate(result['prefix']):self.assertEqual(metric['throughput']['lots'],sum(e['kind']=='complete' for e in trace['events'][:i+1]))
