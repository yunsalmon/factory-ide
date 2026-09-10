import copy
import csv
import io
import json
from pathlib import Path
import unittest
from playwright.sync_api import sync_playwright
from engine import Factory
from test_orders import ordered_model

ROOT=Path(__file__).parents[1]

class OrderProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright=sync_playwright().start();cls.browser=cls.playwright.chromium.launch()
        cls.page=cls.browser.new_page();cls.page.add_script_tag(path=str(ROOT/'web/order-projection.js'))
    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.playwright.stop()
    def project(self,trace,cursor,options=None):
        return self.page.evaluate('([r,c,o])=>orderProjection(r,c,o)',[trace,cursor,options or {}])

    def test_metrics_reconcile_at_every_cursor_and_final(self):
        trace=Factory(ordered_model()).run();prefix={}
        projections=self.page.evaluate('r=>Array.from({length:r.events.length+1},(_,i)=>orderProjection(r,i))',trace)
        for cursor,p in enumerate(projections):
            if cursor:prefix.update(trace['events'][cursor-1]['state_changes']['lots'])
            now=trace['events'][cursor-1]['time'] if cursor else 0
            self.assertEqual(p['time'],now)
            self.assertEqual(p['totals']['quantity'],9)
            self.assertEqual(p['totals']['released'],sum(l['quantity'] for l in prefix.values()))
            completed=[l for l in prefix.values() if l['state']=='completed']
            self.assertEqual(p['totals']['throughput'],sum(l['quantity'] for l in completed))
            self.assertEqual(p['totals']['released']+p['totals']['unreleased'],9)
            self.assertEqual(p['totals']['throughput']+p['totals']['wip'],p['totals']['released'])
            for order in p['rows']:
                actual=[l for l in prefix.values() if l['order_id']==order['id']]
                done=len(actual)==len(order['lots']) and all(l['state']=='completed' for l in actual)
                at=max(l['completed'] for l in actual) if done else now
                self.assertEqual(order['slack'],order['due_time']-at)
                self.assertEqual(order['tardiness'],max(0,at-order['due_time']))
                self.assertTrue(all(i<cursor for lot in order['lots'] for i in lot['history']))
        final=self.project(trace,0,{'finalView':True})
        self.assertEqual(final['time'],30)
        self.assertEqual(final['rows'][1]['slack'],20)
        self.assertEqual(final['rows'][0]['tardiness'],1)
        self.assertEqual(final['totals']['attainment'],0)
        self.assertAlmostEqual(final['totals']['mean_cycle_time'],4)
        self.assertEqual(final['rows'][0]['cycle_time'],7)

    def test_zero_time_no_due_and_future_poison(self):
        model=ordered_model();model['orders'][0]['due_time']=0
        trace=Factory(model).run();p=self.project(trace,1)
        self.assertEqual(p['rows'][0]['lots'][0]['released_at'],0)
        self.assertEqual(p['rows'][0]['slack'],0)
        self.assertEqual(p['rows'][0]['risk'],'at_risk')
        poisoned=copy.deepcopy(trace)
        for e in poisoned['events'][1:]:e['time']=9999;e['lot']={'id':'FUTURE'};e['state_changes']={'lots':{'FUTURE':{'id':'FUTURE'}}}
        poisoned['lots']={'FUTURE':{}}
        self.assertEqual(self.project(poisoned,1),p)
        model=ordered_model();del model['orders'][0]['due_time']
        p=self.project(Factory(model).run(),0,{'finalView':True})
        self.assertIsNone(p['rows'][0]['slack']);self.assertIsNone(p['rows'][0]['tardiness'])
        self.assertIsNone(p['totals']['attainment'])

    def test_location_contract_and_no_false_context_history(self):
        model=ordered_model();model['orders']=[dict(id='A',product='A',quantity=2,
            lots=[dict(id='L1',quantity=1),dict(id='L2',quantity=1)])]
        model['buffers']=[dict(id='IN',at='INPUT',capacity=1)]
        model['machines'][0]['availability']=[dict(start=0,end=10,state='down')]
        model['duration']=2
        trace=Factory(model).run();p=self.project(trace,len(trace['events']))
        self.assertEqual(p['rows'][0]['locations'],{'IN':1,'INPUT (release)':1})
        for lot in p['rows'][0]['lots']:
            expected=[e['index'] for e in trace['events'] if e['affected_lot_id']==lot['id']]
            self.assertEqual(lot['history'],expected)
        shared=self.project(trace,len(trace['events']),{'inventory':{'location_contract':'factory-placement-v1','rows':[{'id':'L1','location':'WIP-IN','node':'INPUT','operation':None,'placement':{'kind':'buffer','id':'IN'},'state':'waiting'}]}})
        self.assertEqual(shared['rows'][0]['lots'][0]['location'],'WIP-IN')

    def test_actual_issue11_adapter_preserves_explicit_buffer_and_external_release(self):
        model=ordered_model();model['orders']=[dict(id='A',product='A',quantity=2,
            lots=[dict(id='L1',quantity=1),dict(id='L2',quantity=1)])]
        model['buffers']=[dict(id='IN',at='INPUT',capacity=1)]
        model['machines'][0]['availability']=[dict(start=0,end=10,state='down')]
        model['duration']=2
        trace=Factory(model).run()
        self.page.add_script_tag(path=str(ROOT/'tests/fixtures/inventory-projection-issue11.js'))
        result=self.page.evaluate("r=>{const inventory=inventoryProjection(r,r.events.length);return {inventory,orders:orderProjection(r,r.events.length,{inventory})}}",trace)
        # The real pre-placement adapter merges the two lots; the planner must not.
        self.assertEqual([row['location'] for row in result['inventory']['rows']],['INPUT','INPUT'])
        order=result['orders']['rows'][0]
        self.assertEqual(order['locations'],{'IN':1,'INPUT (release)':1})
        self.assertEqual([(lot['state'],lot['placement']['kind']) for lot in order['lots']], [('waiting','buffer'),('release_pending','release')])
        self.assertEqual(result['orders']['totals']['released'],2)
        self.assertEqual(order['locations'],self.project(trace,len(trace['events']))['rows'][0]['locations'])

    def test_valid_offset_boundaries_match_python_and_javascript(self):
        for offset in ['+00:00','-00:00','+00:59','-00:59','+23:59','-23:59']:
            model=ordered_model();model['time_origin']='2026-09-10T00:00:00Z'
            model['orders'][0].pop('due_time');model['orders'][0]['due_date']='2026-09-10T00:00:00'+offset
            trace=Factory(model).run();fallback=copy.deepcopy(trace);del fallback['order_plan']
            self.assertEqual(self.project(trace,0),self.project(fallback,0),offset)

    def test_plan_fallback_matches_server_dates_and_csv_filters(self):
        model=ordered_model();model['time_origin']='2026-09-10T09:00:00+09:00'
        del model['orders'][0]['due_time'];model['orders'][0]['due_date']='2026-09-10T00:06:00Z'
        trace=Factory(model).run();fallback=copy.deepcopy(trace);del fallback['order_plan']
        self.assertEqual(self.project(trace,0),self.project(fallback,0))
        result=self.page.evaluate('r=>{const p=orderProjection(r,0,{finalView:true,filters:{search:"ORDER_A"}});return {p,csv:orderCSV(p,[["ID",r=>r.id],["Qty",r=>r.quantity]])}}',trace)
        rows=list(csv.reader(io.StringIO(result['csv'].lstrip('\ufeff'))))
        self.assertEqual(rows,[['ID','Qty'],['ORDER_A','5']])
        self.assertEqual(result['p']['totals']['orders'],1)
        text=self.page.evaluate('()=>orderCSV({rows:[{value:"=formula"}]},[["value",r=>r.value]])')
        self.assertIn("'=formula",text)

if __name__=='__main__':unittest.main()
