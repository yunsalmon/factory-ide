import copy
import json
import unittest
from engine import Factory
from test_runtime import simple


def replay(result, cursor):
    state = {'lots': {}, 'machines': {m['id']: {'state': 'idle', 'lot': None} for m in result['model']['machines']}}
    for e in result['events'][:cursor]:
        for key in state:
            state[key].update(copy.deepcopy(e['state_changes'][key]))
    return state


class AllocationTests(unittest.TestCase):
    def test_modes_boundaries_and_links(self):
        for mode in ['pull', 'push']:
            m = simple(); m['mode'] = mode; m['source']['count'] = 3
            m['source']['interval'] = .01
            r = Factory(m).run()
            self.assertEqual(r, json.loads(json.dumps(r)))
            self.assertEqual(r, Factory(m).run())
            self.assertEqual(r['schema_version'], 2)
            self.assertEqual(len({a['id'] for a in r['allocations']}), 6)
            for a in r['allocations']:
                before = replay(r, a['before_cursor'])
                after = replay(r, a['after_cursor'])
                self.assertIsNone(before['lots'][a['lot_id']]['target'])
                self.assertEqual(after['lots'][a['lot_id']]['target'], a['destination'])
                self.assertEqual(after['lots'][a['lot_id']]['state'], 'waiting')
                if mode == 'pull' and a['destination'] == 'M':
                    self.assertEqual(after['machines']['M'], {'state':'reserved', 'lot':a['lot_id']})
                events = [e for e in r['events'] if e['allocation_id'] == a['id']]
                self.assertEqual(events[0]['kind'], 'decision')
                self.assertEqual(events[1]['kind'], 'assigned')
                self.assertIn('move', [e['kind'] for e in events])
                self.assertIn('complete' if a['destination'] == 'OUTPUT' else 'finish', [e['kind'] for e in events])
            times = [r['events'][a['decision_index']]['time'] for a in r['allocations']]
            if mode == 'pull':
                self.assertLess(len(set(times)), len(times))
            frozen = copy.deepcopy(r)
            replay(r, len(r['events']))['lots'].clear()
            self.assertEqual(r, frozen)

    def test_push_same_timestamp_allocations_have_distinct_boundaries(self):
        m = simple(); m['mode'] = 'push'; m['source']['count'] = 3; m['source']['interval'] = 5
        r = Factory(m).run()
        simultaneous = [a for a in r['allocations'] if r['events'][a['decision_index']]['time'] == 5]
        self.assertGreaterEqual(len(simultaneous), 2)
        self.assertEqual(len({a['before_cursor'] for a in simultaneous}), len(simultaneous))
        self.assertEqual(len({a['after_cursor'] for a in simultaneous}), len(simultaneous))
        states = [replay(r, a['after_cursor']) for a in simultaneous]
        self.assertNotEqual(states[0], states[1])
        self.assertEqual(states, [replay(r, a['after_cursor']) for a in simultaneous])

    def test_pull_two_waiting_lots_reserves_only_one(self):
        m = simple(); m['source']['count'] = 3; m['source']['interval'] = .01
        r = Factory(m).run()
        e = next(e for e in r['events'] if e['kind'] == 'decision' and len(e['candidates']) == 2)
        a = next(a for a in r['allocations'] if a['id'] == e['allocation_id'])
        before, after = replay(r, a['before_cursor']), replay(r, a['after_cursor'])
        self.assertEqual(before['machines']['M']['state'], 'idle')
        other = next(c['lot_id'] for c in e['candidates'] if c['id'] != e['chosen'])
        self.assertEqual(before['lots'][other], after['lots'][other])
        self.assertIsNone(after['lots'][other]['target'])
        self.assertEqual(after['machines']['M']['state'], 'reserved')

    def test_push_busy_machine_retains_processing_lot(self):
        m = simple(); m['mode'] = 'push'; m['source']['count'] = 2; m['source']['interval'] = 3
        r = Factory(m).run()
        a = next(a for a in r['allocations'] if a['lot_id'] == 'LOT-002' and a['destination'] == 'M')
        before, after = replay(r, a['before_cursor']), replay(r, a['after_cursor'])
        self.assertEqual(before['machines']['M'], after['machines']['M'])
        self.assertEqual(after['machines']['M'], {'state':'processing', 'lot':'LOT-001'})
        self.assertEqual(after['lots']['LOT-002']['state'], 'waiting')
        self.assertEqual(after['lots']['LOT-002']['target'], 'M')

    def test_declined_and_no_route_do_not_allocate(self):
        for mode in ['pull','push']:
            m = simple(); m['mode'] = mode
            r = Factory(m, lambda cs, ctx: (None, '보류')).run()
            self.assertEqual(r['allocations'], [])
            self.assertFalse(any(e['kind'] in ['assigned','move'] for e in r['events']))
            self.assertIsNone(next(e for e in r['events'] if e['kind']=='decision')['chosen'])
            m['routes'][0]['enabled'] = False
            r = Factory(m).run()
            self.assertEqual(r['allocations'], [])
            self.assertTrue(any(e['kind']=='blocked' for e in r['events']))
