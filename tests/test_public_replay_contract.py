"""Real engine traces remain restorable under the deferred-inspector contract."""
from pathlib import Path
import unittest
from playwright.sync_api import sync_playwright
from engine import Factory
from test_runtime import simple
from test_operations import line
from test_disruptions import parallel

ROOT = Path(__file__).resolve().parents[1]


class PublicReplayContractTests(unittest.TestCase):
    def test_real_operational_order_and_legacy_traces(self):
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            for file in ['data-core.js', 'scenario-comparison.js']:
                page.add_script_tag(path=str(ROOT / 'web' / file))
            script = (ROOT / 'web/app.js').read_text()
            page.add_script_tag(content=script[script.index('function canRestorePublicReplay('):script.index('\nasync function boot()')])
            models = []
            for mode in ['pull', 'push']:
                for build in [simple, line, parallel]:
                    model = build();model['mode'] = mode
                    models.append(model)
            setup = simple();setup['machines'][0]['setup_time'] = 1;models.append(setup)
            for orders in [[], [dict(id='O',product='A',quantity=1,release_time=10)]]:
                model = simple();model['orders'] = orders
                model['machines'][0]['availability'] = [dict(state='down',start=1,end=3,cause='repair')]
                models.append(model)
            empty = simple();empty['orders'] = [];models.append(empty)
            for index, model in enumerate(models):
                with self.subTest(index=index):
                    trace = Factory(model).run()
                    self.assertTrue(page.evaluate('result=>canRestorePublicReplay({source:"fixture",result},"fixture")', trace))
            browser.close()
