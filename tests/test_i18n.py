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
