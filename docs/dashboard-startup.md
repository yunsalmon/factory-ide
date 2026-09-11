# Dashboard startup and saved replay restoration

Public startup preloads the same-origin `demo.json` while deferred scripts load.
The generated HTML contains a visible, localized loading message above the
dashboard, including a readable fallback before JavaScript loads. The message
remains visible while a saved source without a matching replay is validated.
Startup selects the saved tab before seeking, so the first graph and Results
table render once rather than repeating the same work during initialization.

A matching schema-v2 saved replay supplies its model, exact result, execution
metadata, cursor (including zero), selected tab, final/cursor mode and filters.
It is restored before any Python initialization. No source is run or revalidated
just to review an existing snapshot. Subsequent edits and Run retain the normal
worker validation and execution path. A missing, mismatched or malformed replay
falls back to source validation; it never substitutes an unrelated example trace.
An empty saved source is preserved. Reset and Stop still invalidate pending
worker initialization. The source-only fallback necessarily depends on runtime
availability and is not the saved-result dashboard performance case.

## Reproducible measurement

Build the exact revision with `deploy/Dockerfile`, start an isolated loopback
Nginx container, then run:

```sh
python tests/browser_startup.py http://127.0.0.1:13337
python scripts/measure_dashboard_startup.py http://127.0.0.1:13337 --output /tmp/local-startup.json
python scripts/measure_dashboard_startup.py https://factory.yshnote.com --output /tmp/public-startup.json
```

The benchmark first runs an edited source in the real browser Python runtime to
create its saved fixture. Each profile uses at least five fresh and five restored
contexts. Both clear the browser HTTP cache; fresh contexts have no storage,
restored contexts contain the actual result/source at cursor 15 and Results tab.
Restoration is checked by deep equality before interacting. Timings end after a
real WIP tab click and two animation frames, rather than merely checking that a
response arrived. Runtime worker counts and early progress times are recorded.

Desktop uses 1500px, 20Mbps down/10Mbps up, 20ms latency and native CPU. Mobile
uses 320px, 1.6Mbps down/750Kbps up, 150ms latency and 4x CPU slowdown. Page CDP
network shaping does not guarantee shaping dedicated Worker traffic; worker-free
fresh/snapshot restoration avoids that limitation. This is not a physical-device
or independently routed mobile-network test. Percentiles interpolate sorted
samples; raw samples, browser version, resource timings, response cache headers,
fixture source hash and served revision are saved with the summary.

Browser-cold does not mean CDN-cold: the tool does not purge or mutate CDN cache.
`CF-Cache-Status`/`Age` in the raw responses identify the observed edge state.
There is one client and no concurrent load generation. Public acceptance requires
the exact candidate to be published and measured at its real URL; local or
browser-overlay measurements do not establish deployed performance. Budgets are
p75 <= 3000ms desktop and <= 5000ms mobile for both fresh and restored sessions.

The change does not alter Nginx headers, worker CSP, pinned runtime artifacts,
WASM alias/integrity, cache policy, or the static `/api/*` rejection boundary.
