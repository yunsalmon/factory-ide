"""Independent prefix/state oracle and explicit-buffer adapter contract."""
import copy
import json
from pathlib import Path
import unittest
from playwright.sync_api import sync_playwright
from engine import Factory
from test_runtime import simple

ROOT = Path(__file__).resolve().parents[1]

class InventoryProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch()
        cls.page = cls.browser.new_page()
        cls.page.add_script_tag(path=str(ROOT / 'web/inventory-projection.js'))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()

    def project(self, trace, cursor, options=None):
        # JSON preserves literal __proto__ product keys across Playwright's transport.
        return json.loads(self.page.evaluate('([r,c,o])=>JSON.stringify(inventoryProjection(r,c,o))', [trace,cursor,options or {}]))

    def test_every_cursor_matches_independent_trace_state(self):
        for mode in ['pull', 'push']:
            model = simple(); model['mode'] = mode
            model['source']['count'] = 6; model['source']['interval'] = .2
            trace = Factory(model).run()
            original = copy.deepcopy(trace)
            projected = self.page.evaluate('(r)=>r.events.map((_,i)=>inventoryProjection(r,i+1))', trace)
            state = {}
            for cursor, event in enumerate(trace['events'],1):
                state.update(event['state_changes']['lots'])
                p = projected[cursor-1]
                ids = [r['id'] for r in p['rows']]
                self.assertEqual(len(ids),len(set(ids)))
                self.assertEqual(set(ids),set(state))
                self.assertEqual(p['totals']['wip'],sum(l['state']!='completed' for l in state.values()))
                self.assertEqual(sum(g['wip'] for g in p['groups']),p['totals']['wip'])
                self.assertEqual(p['time'],event['time'])
            self.assertEqual(trace,original)
            self.assertEqual(self.project(trace,0)['rows'],[])

    def fixture(self):
        states = [('I','waiting','INPUT',None),('Q','waiting','M',None),('R','waiting','INPUT','M'),('T','moving','INPUT','M'),('P','processing','M',None),('B','waiting','M',None),('O','completed','OUTPUT',None)]
        events=[]
        for i,(id,state,location,target) in enumerate(states):
            lot=dict(id=id,state=state,location=location,target=target,product='A',created=0,ready_since=0)
            events.append(dict(index=i,time=0,kind='blocked' if id=='B' else 'arrival',lot=lot,state_changes={'lots':{id:lot},'machines':{'M':{'state':'reserved','lot':'R'}}}))
        return dict(model={'machines':[{'id':'M','process':'P1','line':'A'}], 'routes':[]},events=events,summary={'horizon':20})

    def test_all_states_wait_zero_and_no_future(self):
        trace=self.fixture()
        prefix=self.project(trace,7)
        self.assertEqual({r['id']:r['kind'] for r in prefix['rows']},{'I':'input','Q':'queue','R':'reserved','T':'transit','P':'processing','B':'blocked','O':'output'})
        self.assertEqual(prefix['totals']['wip'],6)
        self.assertEqual(prefix['totals']['oldestWait'],0)
        self.assertEqual(prefix['totals']['averageWait'],0)
        final=self.project(trace,0,{'finalView':True})
        self.assertEqual(final['totals']['averageWait'],20)
        self.assertEqual(final['totals']['quantity'],None)
        self.assertTrue(all(r['release'] is None for r in final['rows']))
        # Poison all future data, including final state and future history.
        trace['lots']=[{'id':'FUTURE'}]
        extra=copy.deepcopy(trace['events'][0]);extra.update(index=7,time=99)
        extra['lot']={'id':'FUTURE','state':'waiting','location':'INPUT'}
        extra['state_changes']={'lots':{'FUTURE':extra['lot']}}
        trace['events'].append(extra)
        self.assertEqual(self.project(trace,7),prefix)

    def test_explicit_buffer_capacity_before_filters_and_quantities(self):
        trace=self.fixture();trace['model']['buffers']=[{'id':'BUF','process':'P2','line':'B','capacity':1,'graph_node':'M'}]
        for i in [0,1]:
            lot=trace['events'][i]['lot'];lot.update(location='BUF',quantity=0 if i==0 else 3,released_at=0,location_since=0,wait_since=0,product='A' if i==0 else 'B')
        p=self.project(trace,2,{'filters':{'search':'A'},'finalView':False})
        self.assertEqual(len(p['rows']),1)
        row=p['rows'][0];self.assertEqual((row['process'],row['line'],row['node'],row['release']),('P2','B','M',0))
        self.assertFalse(row['inferred'])
        group=p['groups'][0];self.assertEqual((group['capacity'],group['occupancy'],group['congestion']),(1,2,'capacity'))
        self.assertEqual(group['quantity'],0)
        self.assertEqual(self.project(trace,2)['totals']['quantity'],3)
        for group_by in ['process','line','location']:
            result=self.project(trace,2,{'groupBy':group_by})
            self.assertEqual(sum(g['count'] for g in result['groups']),2)

    def test_block_clears_and_inferred_queue_cue(self):
        trace=self.fixture()
        lot=copy.deepcopy(trace['events'][5]['lot'])
        trace['events'].append(dict(index=7,time=2,kind='ready',lot=lot,state_changes={'lots':{'B':lot}}))
        p=self.project(trace,8,{'filters':{'location':'M','status':'waiting'}})
        self.assertEqual(len(p['rows']),2)
        self.assertEqual(p['groups'][0]['congestion'],'queue')

    def test_product_names_are_data_not_object_keys(self):
        trace=self.fixture()
        trace['events'][0]['lot']['product']='__proto__'
        trace['events'][1]['lot']['product']='constructor'
        p=self.project(trace,2)
        self.assertEqual(p['totals']['mix'],{'__proto__':1,'constructor':1})

    def test_csv_exact_visible_rows_escaping_and_metadata(self):
        trace=self.fixture()
        result=self.page.evaluate('r=>{const p=inventoryProjection(r,7,{filters:{status:"waiting"}});return {p,csv:inventoryCSV(p,[["Lot",r=>r.id],["Product",r=>r.product]],{Cursor:p.cursor})}}',trace)
        import csv,io
        rows=list(csv.reader(io.StringIO(result['csv'].lstrip('\ufeff'))))
        self.assertEqual([r[0] for r in rows[1:]],[r['id'] for r in result['p']['rows']])
        self.assertTrue(all(r[-1]=='7' for r in rows[1:]))
        text=self.page.evaluate('()=>inventoryCSV({rows:[{id:"=1+1",product:"a,\\\"b\\n"}]},[["Lot",r=>r.id],["Product",r=>r.product]])')
        values=list(csv.reader(io.StringIO(text.lstrip('\ufeff'))))[1]
        self.assertEqual(values,["'=1+1",'a,"b\n'])

if __name__=='__main__': unittest.main()
