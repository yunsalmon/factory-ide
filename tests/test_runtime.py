import copy
from pathlib import Path
import unittest

from engine import Factory, execute
from model import ModelError, parse, synchronize

DEMO = (Path(__file__).resolve().parents[1] / 'examples/demo.py').read_text()


def simple():
    return {'name': 'test', 'mode': 'pull', 'duration': 20, 'seed': 1,
            'source': {'count': 1, 'interval': 1, 'products': ['A']},
            'processes': [{'id': 'P', 'name': '공정'}],
            'machines': [{'id': 'M', 'name': '머신', 'process': 'P', 'line': 'A', 'time': 3}],
            'routes': [{'id': 'in', 'from': 'INPUT', 'to': 'M', 'priority': 0, 'delay': 2, 'product': '*', 'enabled': True},
                       {'id': 'out', 'from': 'M', 'to': 'OUTPUT', 'priority': 0, 'delay': 1, 'product': '*', 'enabled': True}]}


class SyncTests(unittest.TestCase):
    def test_preserves_user_functions_and_comments(self):
        model, _, _ = parse(DEMO)
        model['machines'][0]['time'] = 17
        output = synchronize(DEMO, model)
        self.assertEqual(parse(output)[0], model)
        self.assertEqual(output[output.index('\n\ndef choose_candidate'):], DEMO[DEMO.index('\n\ndef choose_candidate'):])
        self.assertTrue(output.startswith(DEMO[:DEMO.index('MODEL =')]))

    def test_multibyte_offsets_and_suffix(self):
        source = '이름 = "공장"; MODEL = ' + repr(simple()) + '; 꼬리 = "보존"\n'
        m = simple(); m['name'] = '변경'
        output = synchronize(source, m)
        self.assertTrue(output.startswith('이름 = "공장"; MODEL = '))
        self.assertTrue(output.endswith('; 꼬리 = "보존"\n'))
        self.assertEqual(parse(output)[0]['name'], '변경')

    def test_analysis_does_not_execute_python(self):
        source = 'raise RuntimeError("must not run")\nMODEL = ' + repr(simple())
        self.assertEqual(parse(source)[0], simple())

    def test_rejects_computed_or_duplicate_model(self):
        for source in ['MODEL = dict()', 'MODEL = {}\nMODEL = {}', 'MODEL = {']:
            with self.subTest(source=source), self.assertRaises(ModelError):
                parse(source)

    def test_invalid_graph_does_not_serialize(self):
        m = simple(); m['routes'][0]['to'] = 'missing'
        with self.assertRaises(ModelError):
            synchronize(DEMO, m)

    def test_invalid_numeric_values(self):
        for value in [float('nan'), float('inf'), -1, True, '5']:
            m = simple(); m['machines'][0]['time'] = value
            with self.subTest(value=value), self.assertRaises(ModelError):
                Factory(m)


class RuntimeTests(unittest.TestCase):
    def test_transport_and_processing_times(self):
        r = Factory(simple()).run()
        self.assertEqual(r['summary']['completed'], 1)
        times = {e['kind']: e['time'] for e in r['events'] if e['kind'] in ['start', 'finish', 'complete']}
        self.assertEqual(times, {'start': 2, 'finish': 5, 'complete': 6})

    def test_horizon_includes_equal_timestamp(self):
        m = simple(); m['duration'] = 6
        self.assertEqual(Factory(m).run()['summary']['completed'], 1)
        m['duration'] = 5.99
        self.assertEqual(Factory(m).run()['summary']['completed'], 0)

    def test_both_modes_finish_demo(self):
        m, _, _ = parse(DEMO)
        for mode in ('pull', 'push'):
            m['mode'] = mode
            with self.subTest(mode=mode):
                r = Factory(m).run()
                self.assertEqual(r['summary']['completed'], 30)
                starts = [(e['lot']['id'], e['machine']) for e in r['events'] if e['kind'] == 'start']
                self.assertEqual(len(starts), 90)
                self.assertEqual(len(starts), len(set(starts)))
                self.assertTrue(any(e['kind'] == 'decision' and len(e['candidates']) > 1 for e in r['events']))

    def test_cross_line_routing_occurs(self):
        r = execute(DEMO)
        crossed = [e for e in r['events'] if e['kind'] == 'move' and e['route'] in ('r5', 'r6')]
        self.assertTrue(crossed)

    def test_product_mismatch_explained(self):
        m = simple(); m['routes'][0]['product'] = 'B'
        r = Factory(m).run()
        self.assertEqual(r['summary']['completed'], 0)
        blocked = next(e for e in r['events'] if e['kind'] == 'blocked')
        self.assertIn('제품 조건 불일치', blocked['checks'][0]['reason'])

    def test_disabled_route_explained(self):
        m = simple(); m['routes'][0]['enabled'] = False
        r = Factory(m).run()
        self.assertTrue(any('연결되지 않은' in w for w in r['warnings']))
        self.assertEqual(next(e for e in r['events'] if e['kind'] == 'blocked')['checks'][0]['reason'], '비활성 경로')

    def test_seeded_custom_processing_reproducible(self):
        source = DEMO.replace("return machine['time']", "return machine['time'] + context['rng'].uniform(0, 2)")
        self.assertEqual(execute(source)['events'], execute(source)['events'])
        self.assertNotEqual(execute(source)['events'], execute(DEMO)['events'])

    def test_custom_policy_selects_requested_candidate(self):
        m = simple(); m['source']['count'] = 4; m['source']['interval'] = .1
        def newest(candidates, context):
            return max(candidates, key=lambda c: c['ready_since'])['id'], '최신 로트 우선'
        r = Factory(m, newest).run()
        starts = [e['lot']['id'] for e in r['events'] if e['kind'] == 'start']
        self.assertEqual(starts[:2], ['LOT-001', 'LOT-004'])

    def test_bad_policy_rejected(self):
        with self.assertRaises(ModelError):
            Factory(simple(), lambda cs, ctx: ('nonexistent', 'invalid')).run()

    def test_invalid_hook_time_rejected(self):
        with self.assertRaises(ModelError):
            Factory(simple(), timing=lambda m, l, c: 0).run()

    def test_custom_simpy_generator(self):
        source = 'MODEL = ' + repr(simple()) + '''
def process_lot(env, machine, lot, context):
    yield env.timeout(2)
    yield env.timeout(4)
'''
        result = execute(source)
        self.assertEqual(result['summary']['mean_cycle_time'], 9)

    def test_custom_resource_shared_between_parallel_machines(self):
        source = DEMO + '''
import simpy
def process_lot(env, machine, lot, context):
    if 'operator' not in context['shared']:
        context['shared']['operator'] = simpy.Resource(env, capacity=1)
    with context['shared']['operator'].request() as request:
        yield request
        yield env.timeout(1)
'''
        result = execute(source)
        intervals = [e['time'] for e in result['events'] if e['kind'] == 'finish']
        self.assertEqual(len(intervals), len(set(intervals)))
        self.assertEqual(result['summary']['completed'], 30)

    def test_push_keeps_exact_route_when_parallel_edges_exist(self):
        m = simple(); m['mode'] = 'push'
        m['routes'].insert(1, dict(m['routes'][0], id='fast', delay=0))
        def fast(cs, ctx):
            c = next((c for c in cs if c['route_id'] == 'fast'), cs[0])
            return c['id'], 'fast edge'
        r = Factory(m, fast).run()
        self.assertEqual(next(e for e in r['events'] if e['kind'] == 'start')['time'], 0)

    def test_serial_machines_in_same_line(self):
        m = simple()
        m['machines'].append(dict(m['machines'][0], id='M2', time=4))
        m['routes'][1]['to'] = 'M2'
        m['routes'].append(dict(m['routes'][1], id='last', **{'from': 'M2', 'to': 'OUTPUT'}))
        r = Factory(m).run()
        self.assertEqual(r['summary']['mean_cycle_time'], 11)

    def test_trace_has_no_duplicate_inflight_lot_or_machine(self):
        r = execute(DEMO)
        inflight, busy = set(), set()
        for e in r['events']:
            lot = e['lot']['id']
            if e['kind'] == 'move':
                self.assertNotIn(lot, inflight); inflight.add(lot)
                if e['machine']:
                    self.assertNotIn(e['machine'], busy); busy.add(e['machine'])
            if e['kind'] == 'finish':
                inflight.remove(lot); busy.remove(e['machine'])
            if e['kind'] == 'complete':
                inflight.remove(lot)
        self.assertFalse(inflight)
        self.assertFalse(busy)


if __name__ == '__main__':
    unittest.main()
