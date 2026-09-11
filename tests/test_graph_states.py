"""Graph operational projection: independent engine-prefix and legacy oracles."""
import copy
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
from engine import Factory
from test_operations import line
from test_persona_integration import combined_model

ROOT = Path(__file__).resolve().parents[1]

class GraphStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch()
        cls.page = cls.browser.new_page()
        cls.page.add_script_tag(path=str(ROOT / 'web/graph-state.js'))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()

    def project(self, result, cursor, final=False):
        return self.page.evaluate('([r,c,f])=>graphStateProjection(r,c,f)', [result,cursor,final])

    def test_all_engine_boundaries_match_operations_and_inventory(self):
        for model in [line(), combined_model()]:
            trace = Factory(model).run()
            states = copy.deepcopy(trace['initial_state']['machine_operations'])
            buffers = copy.deepcopy(trace['initial_state']['buffers'])
            views = self.page.evaluate('(r)=>Array.from({length:r.events.length+1},(_,i)=>graphStateProjection(r,i))', trace)
            for cursor, view in enumerate(views):
                if cursor:
                    changes = trace['events'][cursor-1]['state_changes']
                    states.update(changes.get('machine_operations', {}))
                    buffers.update(changes.get('buffers', {}))
                for mid, state in states.items():
                    for key in ['state','lot','cause','since']:
                        self.assertEqual(view['machines'][mid][key], state[key], (cursor,mid,key))
                    event = view['machines'][mid]['event']
                    self.assertTrue(event is None or event < cursor)
                self.assertEqual(view['buffers'], buffers)
            final = self.project(trace, 0, True)
            self.assertEqual(final['cursor'], len(trace['events']))
            self.assertEqual(final['time'], model['duration'])
            self.assertEqual(self.project(trace, -5)['cursor'], 0)

    def test_blocked_cause_buffer_and_held_lot_evidence(self):
        trace = Factory(line()).run()
        event = next(e for e in trace['events'] if e.get('transition',{}).get('current',{}).get('state')=='blocked')
        view = self.project(trace, event['index']+1)
        machine = view['machines']['M']
        self.assertEqual((view['time'], machine['state'], machine['lot']), (2,'blocked','LOT-002'))
        self.assertEqual((machine['buffer']['id'],machine['buffer']['capacity'],machine['buffer']['contents']), ('BETWEEN',1,['LOT-001']))
        self.assertEqual(machine['event'], event['index'])
        self.assertEqual(view['machines']['N']['state'], 'down')
        self.assertEqual(view['machines']['N']['cause'], 'maintenance')
        planned = line(); planned['machines'][1].pop('availability'); planned['machines'][1]['maintenance']=[dict(start=0,end=8)]
        planned_trace = Factory(planned).run()
        planned_event = next(e for e in planned_trace['events'] if e.get('transition',{}).get('current',{}).get('state')=='blocked')
        self.assertEqual(self.project(planned_trace,planned_event['index']+1)['machines']['N']['state'],'maintenance')
        self.assertNotEqual(self.project(trace, event['index'])['machines']['M']['state'], 'blocked')
        self.assertEqual(trace['events'][machine['bufferEvent']]['state_changes']['buffers']['BETWEEN']['contents'], ['LOT-001'])

    def test_legacy_unknown_and_prototype_ids(self):
        trace = {'model':{'duration':10,'machines':[{'id':'constructor'}]},'initial_state':{'machines':{'constructor':{'state':'reserved','lot':'L'}}},'events':[]}
        view = self.project(trace,0)
        self.assertEqual(view['machines']['constructor']['state'],'reserved')
        self.assertTrue(view['machines']['constructor']['inferred'])
        trace['initial_state']['machine_operations']={}
        view=self.project(trace,0)
        self.assertEqual(view['machines']['constructor']['state'],'unknown')
        self.assertFalse(view['machines']['constructor']['inferred'])

    def test_projection_mutation_cannot_change_trace(self):
        trace = Factory(line()).run()
        self.assertTrue(self.page.evaluate('''r=>{const before=JSON.stringify(r),v=graphStateProjection(r,r.events.length);
          v.lots['LOT-001'].state='bad';v.buffers.BETWEEN.contents.push('bad');v.machines.M.state='bad';
          return JSON.stringify(r)===before}''',trace))
