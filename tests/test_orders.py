import copy
import json
import math
import unittest
from pathlib import Path
from engine import Factory, execute
from inventory import replay_inventory
from model import ModelError, validate, parse, synchronize
from orders import normalize_orders
from test_runtime import simple


def ordered_model():
    model = simple()
    model['duration'] = 30
    model['machines'][0]['time'] = 2
    model['routes'][0]['delay'] = 1
    model['routes'][1]['delay'] = 1
    model['order_risk_window'] = 3
    model['orders'] = [
        dict(id='ORDER_A', product='A', quantity=5, release_time=0, due_time=6, priority=-2,
             customer='고객 日本', reference='REF-1', lots=[dict(id='LOT_A1',quantity=2),dict(id='LOT_A2',quantity=3,release_time=3)]),
        dict(id='ORDER_B', product='B', quantity=4, release_time=40, due_time=50, priority=7),
    ]
    return model


class OrderModelTests(unittest.TestCase):
    def test_raw_values_roundtrip_and_dates(self):
        model=ordered_model();model['time_origin']='2026-09-10T09:00:00+09:00'
        model['orders'][0]['due_date']='2026-09-10T00:06:00Z'
        before=copy.deepcopy(model)
        plan=normalize_orders(model)
        self.assertEqual(plan[0]['due_time'],6)
        self.assertEqual(model,before)
        source='MODEL = '+repr(simple())+'\n# 사용자 코드 kept\nx=1\n'
        changed=synchronize(source,model)
        self.assertEqual(parse(changed)[0],model)
        self.assertTrue(changed.endswith('# 사용자 코드 kept\nx=1\n'))
        del model['orders'][0]['due_time']
        self.assertEqual(normalize_orders(model)[0]['due_time'],6)

    def test_invalid_fields_and_quantity_are_localizable(self):
        changes=[('quantity',0),('quantity',True),('quantity',float('nan')),('quantity',float('inf')),
                 ('release_time',-1),('release_time',False),('priority',float('nan')),
                 ('customer',{}),('reference',[]),('due_time',True),('due_date','tomorrow'),
                 ('lots',[]),('lots',[{'id':'LATE','quantity':5,'release_time':9}]),('lots',[{'id':'DUP','quantity':1},{'id':'DUP','quantity':4}])]
        for field,value in changes:
            model=ordered_model();model['orders'][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ModelError) as error:validate(model)
            self.assertEqual(error.exception.message['code'],'order_invalid')
        model=ordered_model();model['orders'][0]['quantity']=6
        with self.assertRaises(ModelError) as error:validate(model)
        self.assertEqual(error.exception.message['code'],'order_quantity_mismatch')
        for mutation in [lambda m:m['orders'].append(copy.deepcopy(m['orders'][0])),
                         lambda m:m['orders'][0]['lots'][0].update(release_time=-1),
                         lambda m:m.update(time_origin='2026-01-01')]:
            model=ordered_model();mutation(model)
            with self.assertRaises(ModelError):validate(model)
        catalogue=json.loads((Path(__file__).parents[1]/'web/locales.json').read_text())
        for lang in ['ko','en','ja']:
            for key in ['order_invalid','order_quantity_mismatch','order_document_invalid']:
                self.assertTrue(catalogue[lang][key])

    def test_date_requires_timezone_origin_and_agreement(self):
        for origin,date,due in [(None,'2026-09-10T00:06:00Z',6),
                                ('2026-09-10T00:00:00','2026-09-10T00:06:00Z',6),
                                ('2026-09-10T00:00:00Z','2026-09-10T00:06:00',6),
                                ('2026-09-10T00:00:00Z','2026-09-10T00:06:00Z',9)]:
            model=ordered_model();model['orders'][0].update(due_date=date,due_time=due)
            if origin:model['time_origin']=origin
            with self.assertRaises(ModelError):validate(model)
        model=ordered_model();model['orders'][0]['due_time']=-1
        self.assertEqual(normalize_orders(model)[0]['due_time'],-1)


class OrderEngineTests(unittest.TestCase):
    def test_scheduled_release_quantity_and_horizon(self):
        model=ordered_model();trace=Factory(model).run()
        released=[e for e in trace['events'] if e['kind']=='order_release']
        self.assertEqual([(e['lot']['id'],e['time']) for e in released],[('LOT_A1',0),('LOT_A2',3)])
        self.assertEqual(sum(e['lot']['quantity'] for e in released),5)
        self.assertEqual(trace['order_plan'][1]['lots'][0]['release_time'],40)
        self.assertNotIn('ORDER_B',replay_inventory(trace)['lots'])
        for e in trace['events']:
            if e['affected_lot_id']:
                lot=e['lot'];self.assertEqual(lot['released_at'],lot['release_time'])
                self.assertEqual(lot['order_id'],'ORDER_A')
        model['duration']=40
        self.assertTrue(any(e['kind']=='order_release' and e['time']==40 for e in Factory(model).run()['events']))

    def test_finite_input_backpressure_keeps_exact_release(self):
        model=ordered_model();model['orders']=[dict(id='A',product='A',quantity=3,release_time=0,
            lots=[dict(id=f'L{i}',quantity=1) for i in range(3)])]
        model['buffers']=[dict(id='IN',at='INPUT',capacity=1,policy='fifo')]
        model['machines'][0]['availability']=[dict(start=0,end=10,state='offshift')]
        model['duration']=2
        trace=Factory(model).run();state=replay_inventory(trace)
        self.assertEqual([e['time'] for e in trace['events'] if e['kind']=='order_release'],[0,0,0])
        self.assertEqual(len(state['lots']),3)
        self.assertEqual(state['buffers']['IN']['occupancy'],1)
        self.assertEqual(sum(l['placement']['kind']=='release' for l in state['lots'].values()),2)
        self.assertTrue(any(d['code']=='order_release_backpressure' for d in trace['operational_diagnostics']))
        model['duration']=25
        trace=Factory(model).run();state=replay_inventory(trace)
        self.assertEqual(sum(l['quantity'] for l in state['lots'].values()),3)
        self.assertTrue(all(l['released_at']==0 for l in state['lots'].values()))
        self.assertTrue(any(l['admitted_at']>=10 for l in state['lots'].values()))
        for cursor in range(len(trace['events'])+1):
            projected=replay_inventory(trace,cursor)
            self.assertLessEqual(projected['buffers']['IN']['occupancy'],1)

    def test_dispatch_copies_raw_order_fields_and_does_not_infer_reason(self):
        model=ordered_model();seen=[]
        def choose(candidates,context):
            chosen=candidates[0];seen.append(copy.deepcopy((chosen,context['orders'])))
            self.assertEqual(chosen['lot_priority'],-2)
            self.assertEqual(chosen['order']['priority'],-2)
            self.assertEqual(chosen['due_slack'],6-context['now'])
            candidates[0]['order']['priority']=999
            context['orders']['ORDER_A']['priority']=999
            if 'machine' in context:context['machine']['time']=999
            return chosen['id'],'My custom reason only'
        factory=Factory(model,choose=choose);trace=factory.run()
        self.assertTrue(seen)
        self.assertEqual(factory.order_headers['ORDER_A']['priority'],-2)
        self.assertEqual(factory.model['machines'][0]['time'],2)
        for e in trace['events']:
            if e['kind']=='decision':
                self.assertEqual(e['reason'],'My custom reason only')
                self.assertEqual(e['selection_policy'],'custom')
                self.assertIsNone(e['order_factors_used'])
        defaults=Factory(model).run()
        self.assertTrue(all(e['order_factors_used'] is False for e in defaults['events'] if e['kind']=='decision'))

    def test_documented_example_runs_and_records_order_rule(self):
        source=(Path(__file__).parents[1]/'examples/orders_demo.py').read_text()
        result=execute(source)
        decisions=[e for e in result['events'] if e['kind']=='decision']
        self.assertTrue(decisions)
        self.assertTrue(all(e['reason_message']['code']=='order_dispatch_reason' for e in decisions))
        self.assertEqual(sum(e['lot']['quantity'] for e in result['events'] if e['kind']=='order_release'),9)

    def test_default_legacy_determinism_and_no_invented_quantities(self):
        model=simple();a=Factory(model).run();b=Factory(model).run()
        self.assertEqual(a,b)
        self.assertEqual(a['order_plan'],[])
        self.assertTrue(all('quantity' not in e['lot'] and 'order_id' not in e['lot'] for e in a['events']))
        self.assertEqual(model,simple())


if __name__=='__main__':unittest.main()
