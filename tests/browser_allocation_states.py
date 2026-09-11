"""Issue 32: complete localized allocation states at the real replay boundary."""
import atexit
import json
from pathlib import Path
import subprocess
import sys

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / 'tests/fixtures/allocation-blocked-issue32.json').read_text())['model']
server = subprocess.Popen(
    [sys.executable, str(ROOT / 'server.py'), '--port', '0'],
    cwd=ROOT, stdout=subprocess.PIPE, text=True,
)
atexit.register(server.terminate)
base = server.stdout.readline().strip().split(' → ')[-1]


def edit(page, source):
    page.evaluate('(source) => editor.setValue(source)', source)


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    for language in ['ko', 'en', 'ja']:
        for width in [1440, 320]:
            page = browser.new_page(
                locale={'ko': 'ko-KR', 'en': 'en-US', 'ja': 'ja-JP'}[language],
                viewport={'width': width, 'height': 900},
            )
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base)
            page.wait_for_function('() => window.factoryStudio?.getState().valid')
            edit(page, 'MODEL = ' + repr(FIXTURE))
            page.wait_for_function('() => S.valid && !S.busy')
            page.locator('#run-button').click()
            page.wait_for_function('() => S.result?.allocations?.length === 8 && !S.job')
            allocation = page.evaluate('() => S.result.allocations.find(a => a.id === "ALLOC-000008")')
            assert (allocation['before_cursor'], allocation['after_cursor']) == (106, 108)
            decision = allocation['decision_index']
            expected_reason = page.evaluate(
                '(index) => { const e=S.result.events[index]; return localized(e.reason,e.reason_message); }',
                decision,
            )
            page.evaluate('() => { pause(); selectTab("allocations"); }')
            page.locator('#allocation-select').select_option(str(decision))
            panels = page.locator('.allocation-state')
            expect(panels).to_have_count(2)
            expect(panels.nth(0).locator('h3')).to_contain_text('106')
            expect(panels.nth(1).locator('h3')).to_contain_text('108')
            expect(page.locator('#allocation-detail')).to_contain_text(expected_reason)

            expected_blocked = page.evaluate('tr("wip_blocked")')
            expected_transport = page.evaluate('tr("allocation_placement_transport")')
            for panel in [panels.nth(0), panels.nth(1)]:
                for lot_id, logical, route in [('O1', 'N', 'outN'), ('O2', 'M', 'out')]:
                    row = panel.locator('table').first.locator('tbody tr').filter(has_text=lot_id)
                    expect(row).to_have_count(1)
                    cells = row.locator('td')
                    expect(cells.nth(1)).to_have_text(logical)
                    expect(cells.nth(2)).to_have_text(f'{expected_transport} · {route}')
                    badge = cells.nth(3).locator('.trace-state')
                    expect(badge).to_have_text(expected_blocked)
                    expect(badge).to_have_attribute('data-state-known', 'true')
                    expect(cells.nth(5)).not_to_have_text('')

            # Both navigation buttons retain this comparison and its reason.
            page.locator('#allocation-before').click()
            assert page.evaluate('S.cursor') == 106
            expect(page.locator('#allocation-detail')).to_contain_text(expected_reason)
            page.locator('#allocation-after').click()
            assert page.evaluate('S.cursor') == 108
            expect(page.locator('#allocation-detail')).to_contain_text(expected_reason)

            # The formatter covers the complete trace enum, and a future value
            # gets a visually and audibly distinct fallback rather than a blank.
            states = page.evaluate('''() => {
              const values=["waiting","reserved","moving","processing","completed","release_pending",
                "setup","down","maintenance","blocked","starved","offshift","resource_wait","idle","future_hold"];
              const host=document.createElement("div");host.innerHTML=values.map(traceStateBadge).join("");
              return [...host.children].map((el,i)=>({state:values[i],text:el.textContent,
                known:el.dataset.stateKnown,label:el.getAttribute("aria-label"),className:el.className}));
            }''')
            assert all(item['text'] and item['text'] != 'undefined' for item in states)
            assert all(item['known'] == 'true' for item in states[:-1])
            unknown = states[-1]
            assert unknown['known'] == 'false' and 'trace-state-unknown' in unknown['className']
            assert unknown['text'] == page.evaluate('tr("allocation_state_unknown")')
            assert 'future_hold' in unknown['label']
            assert 'undefined' not in page.locator('#allocation-detail').inner_text().lower()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            print(f'PASS issue32 allocation states {language} {width}px', flush=True)
            page.close()
    browser.close()
