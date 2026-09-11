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

Stored results are structurally checked before adoption: the existing model and
trace-contract validators check model collections, references, horizon, schema,
event indices and times. Replay-specific checks cover event/initial snapshots,
lot and machine records, allocations, warnings, order-plan collections and saved
view settings. Null event entries and empty/malformed models cannot bypass source
validation. Empty event arrays are accepted only with zero summary counts and no
allocations, preserving the zero-work boundary without accepting a truncated
nonempty trace. This is a display-shape check, not a signature or proof that a
user-edited local trace is authentic.

Validation covers deferred inspectors as well as the first paint. A decision
requires both `candidates` and `checks` arrays, even when empty; candidate/check
rows must contain their displayed IDs, numeric values and eligibility fields.
A declined choice may have no chosen ID. Machine-state events require both
previous/current operation records in `transition`; their lot may be null for
pre-release transitions. Machine/route references, assignment kind, setup family,
duration and buffer IDs are required for the event kinds that display them.
Operation snapshots must name existing machines and include interval start times.
Non-decision candidate/check arrays, reason messages, resource-request metadata
and other supplemental fields remain optional; supplied display fields must have
usable shapes. Unknown event kinds fall back to validation rather than silently
claiming support. This does not attempt to authenticate or re-simulate event facts.

The startup gate includes actual DOM row clicks on a restored browser-run decision
in ko/en/ja, all current event-kind display fixtures (including nullable machine
events), required/optional negative controls and later tab switches. This catches
missing fields that a successful initial Results/Events paint cannot exercise.

Projection/rendering is also part of adoption. If it fails, the candidate is
discarded and all replay fields are reset before asynchronous source validation.
A localized message explains that the saved trace could not be restored; the
source editor and Reset remain usable. Validation produces a checked model, not
an invented execution result. A later Run generates a new result normally.
Validation failure, Stop and Reset retain their own outcome rather than being
overwritten by a false ready status. Unavailable browser storage still permits
the ordinary precomputed fresh-start path.

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
