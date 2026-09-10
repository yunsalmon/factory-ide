import copy
import json
import unittest

from engine import Factory
from inventory import replay_inventory
from model import ModelError, migrate_model, validate, parse, synchronize
from test_runtime import simple, DEMO


def line():
    m = simple()
    m['duration'] = 40
    m['source'].update(count=3, interval=.1)
    m['machines'][0].update(time=1)
    m['machines'].append(dict(id='N', name='N', process='P', line='A', time=3,
                              availability=[dict(state='down', start=0, end=8, cause='maintenance')]))
    m['routes'][0]['delay'] = 0
    m['routes'][1].update(to='N', delay=0)
    m['routes'].append(dict(id='nout', **{'from':'N', 'to':'OUTPUT'}, priority=0, delay=0, product='*', enabled=True))
    m['buffers'] = [dict(id='BETWEEN', at='M', capacity=1, policy='fifo')]
    return m


class ModelOperationTests(unittest.TestCase):
    def test_copy_migration_is_stable_and_source_preserving(self):
        m = simple(); original = copy.deepcopy(m)
        normalized = migrate_model(m)
        self.assertEqual(m, original)
        self.assertEqual(migrate_model(normalized), normalized)
        self.assertEqual([b['id'] for b in normalized['buffers']], ['BUF_INPUT', 'BUF_M', 'BUF_OUTPUT'])
        self.assertTrue(all(b['capacity'] is None for b in normalized['buffers']))
        self.assertEqual(normalized['buffers'][1]['upstream'], ['M'])
        self.assertEqual(normalized['buffers'][1]['downstream'], ['OUTPUT'])
        source = 'MODEL = ' + repr(m) + '\n# preserve me\n'
        self.assertEqual(parse(source)[0], m)
        self.assertTrue(synchronize(source, normalized).endswith('# preserve me\n'))

    def test_rejects_invalid_operational_models(self):
        changes = [
            {'buffers': [dict(id='B', at='M', capacity=0)]},
            {'buffers': [dict(id='B', at='M', capacity=True)]},
            {'buffers': [dict(id='B', at='missing')]},
            {'buffers': [dict(id='B', at='M', policy='lifo')]},
            {'buffers': [dict(id='B', at='M'), dict(id='C', at='M')]},
            {'buffers': [dict(id='B', at='M'), dict(id='B', at='INPUT')]},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ModelError):
                validate(dict(simple(), **change))
        for windows in [[dict(state='down', start=2, end=1)],
                        [dict(state='setup', start=1, end=2)],
                        [dict(state='down', start=1, end=3), dict(state='offshift', start=2, end=4)]]:
            m = simple(); m['machines'][0]['availability'] = windows
            with self.assertRaises(ModelError): validate(m)


class OperationRuntimeTests(unittest.TestCase):
    def assert_all_cursors(self, result):
        frozen = copy.deepcopy(result)
        for cursor in range(len(result['events']) + 1):
            state = replay_inventory(result, cursor)
            contents = {}
            for bid, b in state['buffers'].items():
                self.assertEqual(b['occupancy'], len(b['contents']))
                if b['capacity'] is not None:
                    self.assertLessEqual(b['occupancy'], b['capacity'])
                for lid in b['contents']:
                    self.assertNotIn(lid, contents)
                    contents[lid] = bid
            claims = {}
            for mid, machine in state['machines'].items():
                if machine['lot']:
                    self.assertNotIn(machine['lot'], claims)
                    claims[machine['lot']] = mid
            for lid, lot in state['lots'].items():
                place = lot['placement']
                if place['kind'] == 'buffer':
                    self.assertEqual(contents[lid], place['id'])
                else:
                    self.assertNotIn(lid, contents)
                    if place['kind'] == 'machine':
                        self.assertEqual(claims[lid], place['id'])
                    else:
                        self.assertEqual(place['kind'], 'transport')
            self.assertEqual(state['summary']['wip'] + state['summary']['completed'], len(state['lots']))
        state['lots'].clear()
        self.assertEqual(result, frozen)

    def test_full_buffer_blocks_and_releases_same_lot_once(self):
        for mode in ['pull', 'push']:
            m = line(); m['mode'] = mode
            result = Factory(m).run()
            self.assertEqual(result['summary']['completed'], 3)
            blocked = next(e for e in result['events'] if e['kind'] == 'machine_state' and
                           e['machine'] == 'M' and e['transition']['current']['state'] == 'blocked')
            self.assertEqual(blocked['time'], 2)
            self.assertEqual(blocked['affected_lot_id'], 'LOT-002')
            inventory = replay_inventory(result, blocked['index'] + 1)
            self.assertEqual(inventory['lots']['LOT-002']['placement'], dict(kind='machine', id='M'))
            self.assertEqual(inventory['buffers']['BETWEEN']['contents'], ['LOT-001'])
            release = [e for e in result['events'] if e['kind'] == 'buffer_enter' and e['lot']['id'] == 'LOT-002' and e['buffer'] == 'BETWEEN']
            self.assertEqual(len(release), 1)
            self.assertEqual(release[0]['time'], 8)
            starts = [(e['lot']['id'], e['machine']) for e in result['events'] if e['kind'] == 'start']
            self.assertEqual(len(starts), len(set(starts)))
            self.assert_all_cursors(result)
            self.assertFalse(result['operational_diagnostics'])

    def test_starvation_resumes_at_eligible_arrival(self):
        result = Factory(line()).run()
        transitions = [e for e in result['events'] if e['kind'] == 'machine_state' and e['machine'] == 'M']
        starved = next(e for e in transitions if e['transition']['current']['state'] == 'starved')
        self.assertEqual(starved['transition']['current']['cause'], 'no_eligible_input')
        m = simple(); m['source'].update(count=2, interval=10); m['routes'][0]['delay'] = 0
        result = Factory(m).run()
        starved = next(e for e in result['events'] if e['kind'] == 'machine_state' and e['transition']['current']['state'] == 'starved')
        resume = next(e for e in result['events'] if e['kind'] == 'machine_state' and e['time'] == 10 and e['transition']['current']['state'] == 'reserved')
        self.assertEqual(starved['time'], 3)
        self.assertEqual(resume['transition']['previous']['duration'], 7)
        self.assertEqual(result['summary']['completed'], 2)

    def test_setup_and_interruptions_preserve_active_duration(self):
        m = simple(); m['routes'][0]['delay'] = 0; m['routes'][1]['delay'] = 0
        m['machines'][0].update(time=6, setup_time=2, availability=[
            dict(state='down', start=1, end=3, cause='repair'),
            dict(state='offshift', start=5, end=7, cause='shift')])
        result = Factory(m).run()
        spans = [e['transition']['previous'] for e in result['events'] if e['kind'] == 'machine_state']
        self.assertEqual(sum(s['duration'] for s in spans if s['state'] == 'setup'), 2)
        self.assertEqual(sum(s['duration'] for s in spans if s['state'] == 'processing'), 6)
        self.assertEqual(sum(s['duration'] for s in spans if s['state'] == 'down'), 2)
        self.assertEqual(sum(s['duration'] for s in spans if s['state'] == 'offshift'), 2)
        self.assertEqual(next(e['time'] for e in result['events'] if e['kind'] == 'finish'), 12)
        for event in result['events']:
            state = replay_inventory(result, event['index'] + 1)
            if state['machine_operations']['M']['state'] in ['setup', 'down', 'offshift']:
                self.assertNotEqual(state['machines']['M']['state'], 'processing')
                self.assertNotEqual(state['lots']['LOT-001']['state'], 'processing')
        self.assert_all_cursors(result)
        self.assertEqual(result, Factory(m).run())

    def test_queue_policy_and_source_backpressure(self):
        for policy, expected in [('fifo', ['LOT-001', 'LOT-002', 'LOT-003']),
                                 ('priority', ['LOT-002', 'LOT-003', 'LOT-001'])]:
            m = simple(); m['duration'] = 30; m['source'].update(count=3, interval=.1, priorities=[5,1,3])
            m['machines'][0]['availability'] = [dict(state='offshift', start=0, end=1)]
            m['buffers'] = [dict(id='IN', at='INPUT', capacity=3, policy=policy)]
            result = Factory(m).run()
            self.assertEqual([e['lot']['id'] for e in result['events'] if e['kind'] == 'start'], expected)
            self.assert_all_cursors(result)
        m['buffers'][0]['capacity'] = 1
        result = Factory(m).run()
        self.assertEqual(result['summary']['arrived'], 3)
        self.assertEqual(result['summary']['completed'], 3)
        arrivals = [e['time'] for e in result['events'] if e['kind'] == 'arrival']
        self.assertGreaterEqual(arrivals[1], 1)
        self.assert_all_cursors(result)

    def test_deadlock_and_horizon_diagnostics_identify_resources(self):
        m = line(); m['duration'] = 3
        result = Factory(m).run()
        diagnostic = next(d for d in result['operational_diagnostics'] if d.get('machine') == 'M')
        self.assertEqual(diagnostic['code'], 'horizon_wait')
        self.assertIn('BETWEEN', diagnostic['buffers'])
        self.assertIn('LOT-002', diagnostic['lots'])
        m = line(); m['machines'][1]['availability'] = []; m['routes'][1]['enabled'] = False
        result = Factory(m).run()
        diagnostic = next(d for d in result['operational_diagnostics'] if d.get('machine') == 'M')
        self.assertEqual(diagnostic['code'], 'deadlock')
        self.assertIn('BETWEEN', diagnostic['buffers'])
        self.assertIn('LOT-002', diagnostic['lots'])
        self.assert_all_cursors(result)

    def test_adjacent_calendar_boundaries_and_terminal_capacity(self):
        m = simple(); m['source'].update(count=3, interval=.01)
        m['routes'][0]['delay'] = 0; m['routes'][1]['delay'] = 0
        m['machines'][0].update(time=1, availability=[dict(state='down', start=0, end=1),
                                                    dict(state='offshift', start=1, end=2)])
        m['buffers'] = [dict(id='DONE', at='OUTPUT', capacity=1, policy='fifo')]
        result = Factory(m).run()
        self.assertEqual(next(e['time'] for e in result['events'] if e['kind'] == 'start'), 2)
        self.assertEqual(result['summary']['completed'], 1)
        self.assertEqual(len([e for e in result['events'] if e['kind'] == 'buffer_wait']), 2)
        self.assertTrue(any('DONE' in d.get('buffers', []) for d in result['operational_diagnostics']))
        self.assert_all_cursors(result)
        for e in result['events']:
            if e['time'] == 2 and e['kind'] == 'assigned':
                self.assertEqual(replay_inventory(result, e['index'] + 1)['machine_operations']['M']['state'], 'reserved')

    def test_calendar_rejects_unpausable_custom_generator(self):
        m = simple(); m['machines'][0]['availability'] = [dict(state='down', start=1, end=2)]
        def custom(env, machine, lot, context):
            yield env.timeout(1)
        with self.assertRaisesRegex(ModelError, 'Custom process_lot'):
            Factory(m, process=custom)

    def test_legacy_inventory_projection_and_json_immutability(self):
        factory = Factory(simple()); result = factory.run()
        frozen = json.dumps(result, sort_keys=True)
        factory.lots['LOT-001']['priority'] = 99
        factory.buffers['BUF_OUTPUT']['contents'].clear()
        self.assertEqual(json.dumps(result, sort_keys=True), frozen)
        # A historical v2 payload contains only lots/machines deltas.
        old = copy.deepcopy(result)
        old.pop('operational_schema_version'); old.pop('initial_state')
        old['model'].pop('buffers'); old['model'].pop('operational_model_version')
        for e in old['events']:
            e['state_changes'].pop('buffers'); e['state_changes'].pop('machine_operations')
            for lot in e['state_changes']['lots'].values(): lot.pop('placement', None)
        inventory = replay_inventory(old)
        self.assertEqual(inventory['summary'], dict(arrived=1, completed=1, wip=0))
        self.assertEqual(inventory['buffers']['BUF_OUTPUT']['contents'], ['LOT-001'])
        self.assertEqual(replay_inventory(result, 0)['summary']['arrived'], 0)
        with self.assertRaises(ValueError): replay_inventory(result, -1)
        with self.assertRaises(ValueError): replay_inventory(result, len(result['events']) + 1)
