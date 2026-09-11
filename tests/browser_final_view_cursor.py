"""Cursor UI stays exact while WIP alone may project the final graph state."""
import atexit
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import execute


ROOT = Path(__file__).resolve().parents[1]
trace = execute((ROOT / "examples" / "public_demo.py").read_text())
server = subprocess.Popen(
    [sys.executable, str(ROOT / "server.py"), "--port", "0"],
    cwd=ROOT,
    stdout=subprocess.PIPE,
    text=True,
)
atexit.register(server.terminate)
base = server.stdout.readline().strip().split(" → ")[-1]


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for language in ["ko", "en", "ja"]:
        for width in [1440, 320]:
            page = browser.new_page(locale=language, viewport={"width": width, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            page.wait_for_function(
                '() => document.documentElement.dataset.uiReady === "true" && S.valid'
            )
            page.locator("#language").select_option(language)
            page.evaluate(
                "result => {pause();S.model=result.model;S.result=result;S.cursor=0;selectTab('wip')}",
                trace,
            )
            length = page.evaluate("S.result.events.length")
            cursors = [0, length // 2, length]
            observations = []
            for final_view in [False, True]:
                for cursor in cursors:
                    observation = page.evaluate(
                        """([finalView,cursor]) => {
                          selectTab('wip');
                          WIP.finalView=finalView;
                          WIP.filters={}; WIP.selectedLot=null; WIP.selectedGroup=null;
                          graphRenderKey=null;
                          const originalStateAt=stateAt, originalGraphProjection=graphStateProjection;
                          const scans={cursor:0,graph:0};
                          stateAt=function(...args){scans.cursor++;return originalStateAt(...args)};
                          graphStateProjection=function(...args){scans.graph++;return originalGraphProjection(...args)};
                          try { seek(cursor); }
                          finally { stateAt=originalStateAt; graphStateProjection=originalGraphProjection; }
                          const replay=originalStateAt(cursor), graph=originalGraphProjection(S.result,cursor,finalView,S.model);
                          const labels=Object.fromEntries([...document.querySelectorAll('[data-node]')].map(node=>[
                            node.dataset.node,node.querySelector('.node-status').textContent
                          ]));
                          return {
                            finalView,cursor,scans,
                            replayTime:replay.time,
                            timelineTime:document.querySelector('#timeline-time').textContent,
                            metricTime:document.querySelector('#metric-time').textContent,
                            metricComplete:document.querySelector('#metric-complete').textContent,
                            metricWip:document.querySelector('#metric-wip').textContent,
                            expectedComplete:Object.values(replay.lots).filter(l=>l.state==='completed').length,
                            expectedTotal:Object.keys(replay.lots).length,
                            graphCursor:graph.cursor,
                            graphTime:graph.time,
                            graphScope:document.querySelector('#graph-scope').textContent,
                            graphLabels:labels,
                            expectedGraphLabels:Object.fromEntries(Object.entries(graph.machines).map(([id,m])=>[id,traceStateLabel(m.state)])),
                            timelineCursor:document.querySelector('#timeline').value,
                            timelineEnd:document.querySelector('#timeline-end').textContent,
                          };
                        }""",
                        [final_view, cursor],
                    )
                    assert observation["scans"] == {
                        "cursor": 1 if final_view else 0,
                        "graph": 1,
                    }, observation
                    expected_time = page.evaluate("value => `${fmt(value)} min`", observation["replayTime"])
                    assert observation["timelineTime"] == expected_time, observation
                    assert observation["metricTime"] == expected_time, observation
                    assert observation["timelineCursor"] == str(cursor), observation
                    assert observation["timelineEnd"] == f"{cursor} / {length}", observation
                    assert int(observation["metricWip"]) == (
                        observation["expectedTotal"] - observation["expectedComplete"]
                    ), observation
                    assert observation["metricComplete"].split()[0] == str(observation["expectedComplete"]), observation
                    assert observation["graphCursor"] == (length if final_view else cursor), observation
                    expected_graph_time = page.evaluate("value => `${fmt(value)} min`", observation["graphTime"])
                    assert expected_graph_time in observation["graphScope"], observation
                    assert observation["graphLabels"] == observation["expectedGraphLabels"], observation
                    observations.append(observation)

            # Toggling the final graph view invalidates only the graph scope; cursor UI remains exact.
            middle = cursors[1]
            page.evaluate("([cursor])=>{seek(cursor);WIP.finalView=false;renderGraph();renderInventory()}", [middle])
            replay_scope = page.locator("#graph-scope").inner_text()
            replay_time = page.locator("#timeline-time").inner_text()
            page.locator("#wip-view").select_option("final")
            assert page.locator("#graph-scope").inner_text() != replay_scope
            assert page.locator("#timeline-time").inner_text() == replay_time
            page.locator("#wip-view").select_option("replay")
            assert page.locator("#graph-scope").inner_text() == replay_scope
            assert page.locator("#timeline-time").inner_text() == replay_time
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors, errors
            print(f"PASS final-view cursor {language} {width}px ({len(observations)} cases)", flush=True)
            page.close()
    browser.close()

server.terminate()
