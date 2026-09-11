"""Cross-feature contract using actual orders, finite storage and shared resources."""
import unittest
from engine import Factory
from inventory import replay_inventory
from test_disruptions import parallel
import test_inventory_projection as projection_tests


def combined_model():
    model = parallel()
    model['buffers'] = [dict(id='IN', at='INPUT', capacity=1, policy='fifo')]
    model['orders'] = [dict(id='O'+str(i), product=product, quantity=1,
                           release_time=.5, due_time=5+i, priority=0)
                       for i, product in enumerate(['A','B','A','B'])]
    for machine in model['machines']:
        machine['availability'] = [dict(state='offshift', start=0, end=2)]
        machine['setup_time'] = .25
    return model


class PersonaIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(projection_tests.InventoryProjectionTests.setUpClass.__func__)
    tearDownClass = classmethod(projection_tests.InventoryProjectionTests.tearDownClass.__func__)

    def test_orders_storage_operations_and_due_dates_at_every_cursor(self):
        from pathlib import Path
        self.page.add_script_tag(path=str(Path(__file__).resolve().parents[1]/'web/order-projection.js'))
        trace = Factory(combined_model()).run()
        values = self.page.evaluate('r=>Array.from({length:r.events.length+1},(_,i)=>({w:inventoryProjection(r,i),o:orderProjection(r,i)}))', trace)
        pending = resource_wait = False
        for cursor, value in enumerate(values):
            oracle = replay_inventory(trace, cursor)
            rows = {r['id']:r for r in value['w']['allRows']}
            self.assertEqual(set(rows), set(oracle['lots']))
            self.assertEqual(len(rows), value['w']['allTotals']['count'])
            for lid, lot in oracle['lots'].items():
                row = rows[lid]
                self.assertEqual(row['placement'], lot['placement'])
                if lot['state'] == 'release_pending':
                    pending = True
                    self.assertEqual(row['status'], 'release_pending')
                    self.assertNotEqual(row['kind'], 'input')
                    self.assertNotIn(lid, oracle['buffers']['IN']['contents'])
                resource_wait |= row['status'] == 'resource_wait'
            for order in value['o']['rows']:
                self.assertEqual(order['unreleased_quantity'] + order['released_quantity'], order['quantity'])
                self.assertGreaterEqual(order['tardiness'], 0)
                for lot in order['lots']:
                    at = lot['completed_at'] if lot['completed'] else value['o']['time']
                    self.assertAlmostEqual(lot['tardiness'], max(0, at-lot['due_time']))
        self.assertTrue(pending)
        self.assertTrue(resource_wait)
        self.assertTrue(any(e['lot'] is None for e in trace['events']))
        self.assertEqual(trace['summary']['completed'], 4)
        for durations in trace['operation_metrics']['machines'].values():
            self.assertAlmostEqual(sum(durations.values()), trace['summary']['horizon'])
