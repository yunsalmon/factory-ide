import copy
import json
import unittest
from engine import Factory
from model import ModelError, validate
from inventory import replay_inventory
from operation_metrics import operation_metrics
from test_runtime import simple


def parallel():
    m = simple(); m['duration'] = 20
    m['source'].update(count=2, interval=.1, products=['A', 'B'])
    m['machines'][0]['time'] = 4
    m['machines'].append(dict(m['machines'][0], id='N', name='N'))
    m['routes'][0].update(delay=0, product='A')
    m['routes'][1]['delay'] = 0
    m['routes'] += [dict(m['routes'][0], id='inN', to='N', product='B'),
                    dict(m['routes'][1], id='outN', **{'from':'N'})]
    m['resources'] = [dict(id='OP', kind='operator', capacity=1)]
    for machine in m['machines']: machine['resource_requirements'] = {'processing': {'OP': 1}}
    return m


class DisruptionTests(unittest.TestCase):
    def assert_capacity_and_totals(self, result):
        for totals in result['operation_metrics']['machines'].values():
            self.assertAlmostEqual(sum(totals.values()), result['model']['duration'])
        for cursor in range(len(result['events']) + 1):
            state = replay_inventory(result, cursor)
            for resource in state['resources'].values():
                self.assertLessEqual(sum(h['units'] for h in resource['holders']), resource['capacity'])
                self.assertEqual(len({h['id'] for h in resource['holders']}), len(resource['holders']))
                for holder in resource['holders']:
                    if holder['phase'] != 'transport':
                        self.assertNotIn(state['machine_operations'][holder['machine']]['state'], ['down','offshift','maintenance'])
        self.assertEqual(operation_metrics(result, cursor=0, final=False)['horizon'], 0)

    def test_seeded_repairs_independent_reproducible_and_distinct_maintenance(self):
        m = simple(); m['duration'] = 30
        m['machines'][0].update(time=20, failures={'mtbf':3, 'repair_time':1, 'seed':11},
                                maintenance=[dict(start=4, end=6)])
        first = Factory(m).run(); self.assertEqual(first, Factory(m).run())
        windows = first['disruption_plan']['M']
        def consume_routing_randomness(candidates, context):
            context['rng'].random()
            return candidates[0]['id'], 'custom policy'
        self.assertEqual(windows, Factory(m, choose=consume_routing_randomness).run()['disruption_plan']['M'])
        self.assertTrue(any(w['source'] == 'seeded_failure' for w in windows))
        self.assertTrue(any(w['state'] == 'maintenance' for w in windows))
        m['machines'][0]['failures']['seed'] += 1
        self.assertNotEqual(windows, Factory(m).run()['disruption_plan']['M'])
        self.assert_capacity_and_totals(first)

    def test_shift_releases_resources_and_resumes_remaining_work(self):
        m = parallel(); m['machines'][0]['availability'] = [dict(state='offshift',start=1,end=3)]
        result = Factory(m).run()
        starts = {e['machine']:e['time'] for e in result['events'] if e['kind']=='start'}
        finishes = {e['machine']:e['time'] for e in result['events'] if e['kind']=='finish'}
        self.assertEqual(starts, {'M':0,'N':1})
        self.assertEqual(finishes, {'N':5,'M':8})
        self.assertTrue(any(e.get('resource_releases') for e in result['events']))
        self.assertEqual(result['operation_metrics']['machines']['M']['processing'], 4)
        self.assertEqual(result['operation_metrics']['machines']['M']['offshift'], 2)
        self.assertGreater(result['operation_metrics']['machines']['M']['resource_wait'], 0)
        waits=[e for e in result['events'] if e['kind']=='resource_wait']
        self.assertTrue(any(e['resource_request']['machine']=='N' and e['reason']=='resource_contention' for e in waits))
        self.assert_capacity_and_totals(result)

    def test_family_changeover_once_despite_simultaneous_failure_and_shift(self):
        m = simple(); m['duration'] = 40; m['source'].update(count=4, interval=.1, products=['A','B','B','A'])
        m['routes'][0]['delay']=0; m['routes'][1]['delay']=0
        m['machines'][0].update(time=1, initial_family='X', setup_matrix={'X':{'Y':2},'Y':{'X':3}},
                                failures=[dict(start=2,repair_time=2)],
                                availability=[dict(state='offshift',start=2,end=5)],
                                maintenance=[dict(start=4,end=6)])
        m['product_families']={'A':'X','B':'Y'}
        result=Factory(m).run()
        plans=[e for e in result['events'] if e['kind']=='setup_plan']
        self.assertEqual([e['duration'] for e in plans],[0,2,0,3])
        self.assertEqual(len([e for e in result['events'] if e['kind']=='setup_complete']),4)
        totals=result['operation_metrics']['machines']['M']
        self.assertEqual(totals['setup'],5); self.assertEqual(totals['processing'],4)
        self.assertEqual(totals['down'],2); self.assertEqual(totals['maintenance'],2)
        self.assert_capacity_and_totals(result)

    def test_atomic_bundles_and_nonpreemptive_transport(self):
        m=parallel(); m['resources'] += [dict(id='TOOL',kind='tool',capacity=1),dict(id='CART',kind='transport',capacity=1)]
        for machine in m['machines']: machine['resource_requirements']['processing']['TOOL']=1
        for route in m['routes']:
            route['resources']={'CART':1};route['delay']=1
        m['machines'][0]['availability']=[dict(state='offshift',start=.5,end=2)]
        result=Factory(m).run()
        for e in result['events']:
            req=e.get('resource_request')
            if req and req['phase']=='processing': self.assertEqual(set(req['requirements']), {'OP','TOOL'})
        first_arrival=next(e for e in result['events'] if e['kind']=='transport_arrive' and e['machine']=='M')
        self.assertEqual(first_arrival['time'],1)
        self.assertEqual(result['summary']['completed'],2)
        self.assert_capacity_and_totals(result)
        frozen=copy.deepcopy(result);replay_inventory(result)['resources'].clear();self.assertEqual(result,frozen)

    def test_simultaneous_failure_setup_shift_and_resource_release(self):
        m=parallel();m['resources'].append(dict(id='TOOL',kind='tool',capacity=1))
        for machine in m['machines']:
            machine['setup_time']=1
            machine['resource_requirements']['setup']={'OP':1,'TOOL':1}
        m['machines'][0].update(availability=[dict(state='offshift',start=.5,end=2)],
                                failures=[dict(start=.5,repair_time=1)],
                                maintenance=[dict(start=1.5,end=2.5)])
        result=Factory(m).run()
        self.assertEqual(result['summary']['completed'],2)
        for mid in ['M','N']:
            totals=result['operation_metrics']['machines'][mid]
            self.assertEqual(totals['setup'],1)
            self.assertEqual(totals['processing'],4)
        releases=[e for e in result['events'] if e.get('resource_releases') and e['time']==.5]
        self.assertEqual(len(releases),1)
        self.assertEqual(set(releases[0]['resource_releases'][0]['requirements']),{'OP','TOOL'})
        self.assert_capacity_and_totals(result)
        self.assertEqual(result,Factory(m).run())

    def test_empty_form_requirements_preserve_legacy_custom_process(self):
        m=simple();m['machines'][0]['resource_requirements']={'setup':{},'processing':{}}
        def custom(env,machine,lot,context):
            yield env.timeout(1)
        result=Factory(m,process=custom).run()
        self.assertEqual(result['summary']['mean_cycle_time'],4)

    def test_reserved_looking_family_names_preserve_serialized_setup(self):
        for origin, target in [('constructor', '__proto__'), ('__proto__', 'constructor')]:
            with self.subTest(origin=origin, target=target):
                m=simple();m['source']['products']=[target]
                m['product_families']={target:target}
                m['machines'][0].update(initial_family=origin, setup_matrix={origin:{target:2}})
                validate(m)
                restored=json.loads(json.dumps(m))
                self.assertEqual(restored,m)
                result=Factory(restored).run()
                self.assertEqual(result['operation_metrics']['machines']['M']['setup'],2)
                plan=next(e for e in result['events'] if e['kind']=='setup_plan')
                self.assertEqual((plan['previous_family'],plan['family']), (origin,target))

    def test_invalid_resource_and_failure_definitions(self):
        for mutate in [lambda m:m['resources'][0].update(capacity=0),
                       lambda m:m['machines'][0].update(resource_requirements={'processing':{'OP':2}}),
                       lambda m:m['machines'][0].update(failures={'mtbf':0,'repair_time':1}),
                       lambda m:m['machines'][0].update(setup_matrix={'A':{'B':-1}})]:
            m=parallel();mutate(m)
            with self.assertRaises(ModelError):validate(m)
