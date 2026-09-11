import json
import unittest
from pathlib import Path
from engine import Factory
from model import parse, ModelError
from messages import descriptor

ROOT = Path(__file__).resolve().parents[1]

class TranslationTests(unittest.TestCase):
    def test_catalogues_have_parity(self):
        catalogues = json.loads((ROOT / 'web/locales.json').read_text())
        for locale in ['ko', 'ja']:
            self.assertEqual(set(catalogues['en']), set(catalogues[locale]))
            self.assertTrue(all(catalogues[locale].values()))

    def test_all_trace_states_and_safe_fallback_are_localized(self):
        catalogues = json.loads((ROOT / 'web/locales.json').read_text())
        state_keys = {
            'wip_waiting', 'wip_reserved', 'wip_moving', 'wip_processing',
            'wip_completed', 'wip_release_pending', 'wip_setup', 'wip_down',
            'wip_maintenance', 'wip_blocked', 'ops_state_starved',
            'wip_offshift', 'wip_resource_wait', 'ops_state_idle',
        }
        allocation_keys = {
            'allocation_physical', 'allocation_block_reason',
            'allocation_state_unknown', 'allocation_state_unknown_detail',
            'allocation_state_missing', 'allocation_placement_buffer',
            'allocation_placement_machine', 'allocation_placement_transport',
            'allocation_placement_release', 'allocation_placement_unknown',
            'allocation_placement_unavailable', 'allocation_reason_unavailable',
        }
        for locale in ['ko', 'en', 'ja']:
            self.assertTrue(state_keys | allocation_keys <= catalogues[locale].keys())
            self.assertTrue(all(catalogues[locale][key].strip() for key in state_keys | allocation_keys))
            self.assertNotIn('undefined', {value.lower() for value in catalogues[locale].values()})

    def test_default_and_custom_reason_identity(self):
        model, _, _ = parse((ROOT / 'examples/demo.py').read_text())
        for hook, structured in [(None, True), (lambda cs, ctx: (cs[0]['id'], '경로 우선순위 → 목적지 대기 수 → FIFO'), False)]:
            result = Factory(model, choose=hook).run()
            decisions = [e for e in result['events'] if e['kind'] == 'decision']
            self.assertTrue(decisions)
            for event in decisions:
                self.assertEqual('reason_message' in event, structured)
                self.assertIsInstance(event['reason'], str)
            json.dumps(result)

    def test_nested_validation_args(self):
        model, _, _ = parse((ROOT / 'examples/demo.py').read_text())
        model['duration'] = -1
        with self.assertRaises(ModelError) as caught:
            Factory(model)
        detail = caught.exception.message
        self.assertEqual(detail['code'], 'message_1')
        self.assertEqual(detail['args'][0]['code'], 'message_6')
