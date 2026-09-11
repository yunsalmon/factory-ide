# Operator inventory projection

## Large replay contract

Execution results are immutable after delivery. `inventoryReplay(result, cursor)`
indexes state changes and actor history by result identity, then binary-searches
each entity at the exact event-count cursor. Replacing the result or its events
array invalidates the index; editing events in place is unsupported. Four cursor
snapshots and eight filtered projections are retained per live result, using weak
keys so replaced results can be collected. Consumers treat cached views as read-only.

The snapshot supplies `lots`, `machines`, `machine_operations`, `buffers`,
`resources`, `changedAt`, actor `history`, `lastDecision`, and `operational`.
`graphLots`, `lotEvents`, `machineEvents`, and `bufferEvents` track explicit
state-change records separately from actor-only history. `graph-state.js` consumes
this optional adapter and caches projections by snapshot/scope; its standalone
prefix-replay implementation remains available. Graph lot copies and WIP history
arrays are materialized only when requested.

WIP lot/group/detail tables show at most 15 entries per page. Localized Previous
and Next controls retain keyboard focus; selection uses stable lot IDs across
pages. Closed group details do not build history DOM. Filters, totals, graph
highlights and CSV always refer to the complete filtered projection, not one page.

Recovery retains the existing `*.replay.v1` JSON snapshot and adds a small
`*.replay.v1.position` record paired by a random snapshot ID. Ordinary cursor/tab
changes write only that small record. Legacy snapshots remain readable; corrupt
or mismatched position records cannot replace source or result. For results over
10,000 events, serialization yields after approximately 6 ms and stops if superseded.
The recovery cache bounds serialized event data to 2,000,000 characters; larger
results retain source-only recovery, as do storage quota failures. Closing before
an asynchronous snapshot finishes also recovers source only. Explicit trace export
is independent and remains complete. Oversized snapshots are not repeatedly retried.

Regression commands:

```sh
python tests/browser_large_inventory.py
FACTORY_PERF_GATE=1 python tests/browser_large_replay.py /tmp/large-replay.json
```

The performance fixture is deterministic: 50,000 ordered events, 1,000 unique
waiting lots and an unbounded INPUT buffer. Chromium desktop is 1500×1100 with
CPU rate 1; mobile is 320×844 with CDP CPU rate 4. The gate measures 20 samples per
action through two animation frames and uses nearest-rank p95, with a separate
200 ms long-task ceiling. Fixture construction, first index construction and
initial installation are recorded separately, not silently folded into warm-up.
Run performance measurements without concurrent browser suites. The JSON includes
browser version, component CPU durations, DOM counts, duplicates and page overflow.

`web/inventory-projection.js` exports the pure `inventoryProjection(result, cursor, options = {})` and `inventoryCSV(projection, columns, metadata)` functions. Browser globals and CommonJS are supported; no DOM, locale catalogue or mutable application state is required. UI integration is in `inventory-ui.js`; the app adds one tab dispatch, a graph redraw on tab changes, terminal data attributes and a highlight hook. `WIP` owns stable status/filter values independently from allocation results. No engine/model behavior changes.

The cursor counts events, not timestamps: cursor 0 contains only initial-state lots (none in generated traces), cursor N includes exactly events `[0,N)`. Only `state_changes.lots` complete snapshots (or the legacy `event.lot` fallback), machine changes and history from this prefix are read. Equal-time events remain distinct. Final mode explicitly uses all events and `summary.horizon`; ordinary replay uses the last included event time, including zero. Final `result.lots` is never consulted. Projection never mutates input.

Every observed lot appears once, including OUTPUT. `totals.wip` excludes completed lots. Group totals reconcile with visible rows; `allTotals` is before filters. Grouping is by process, line, or process/line/logical-location/type tuple. Product mix counts lots, not units. Quantity is summed only if every contributing row contains a finite numeric quantity; otherwise it is unknown. Arrival uses `created`, release uses optional `released_at`; these are not inferred from unrelated events. Waiting ages cover waiting/reserved/blocked/setup/down/offshift lots using `wait_since ?? ready_since ?? last state/location change`, preserving zero. Processing/transit/output are excluded from waiting averages.

## Schema-v2 fallback

- INPUT waiting stays at INPUT; other waiting inventory is a queue at its physical source, **not** a count at both source and destination. Push-selected targets remain a separate field.
- A machine reservation in the prefix maps its lot once to the destination's logical reserved slot; physical source is retained for drill-down. In transit takes precedence over reservation.
- Moving inventory has its source→target logical location and route highlight. Processing uses its machine. Completed uses OUTPUT. A latest blocking boundary on a waiting lot yields blocked until ready/assigned/move/start/finish/complete supersedes it.
- Inferred queue groups with at least two visible queue lots show a queue cue. A blocked group shows a blocked cue. These are not capacity/utilization claims. No capacity is invented from machine counts.
- Process/line derive from the current logical machine, or destination while moving, with source as fallback. INPUT/OUTPUT can be unassigned. Operation is optional `lot.operation`, otherwise the physical machine's process.

## Historical explicit-buffer adapter (version 1)

A producer may add `model.buffers: [{id, process?, line?, capacity?, graph_node?}]`. IDs are distinct from machine and terminal IDs. `capacity` is a nonnegative finite **lot count**, static during a run; missing capacity means unknown. `graph_node` is an existing machine/terminal anchor until the graph supports buffer nodes. An explicit-buffer lot has `location: buffer.id` in each complete event-prefix lot snapshot, with its actual state. `quantity`, `released_at`, `location_since`, `wait_since`, and `operation` are optional lot fields; timestamps are in simulation minutes and zero is valid. A producer must not insert future states in earlier snapshots. No guessed timestamp conversion is performed.

Buffer process/line override anchor metadata. Full unfiltered physical buffer occupancy excludes moving/completed lots and is compared with explicit capacity, even while product/status filters hide other occupants. Capacity cues are only shown for location groups, not sums across unrelated buffers. This historical adapter remains supported alongside the operational trace contract below. Dynamic capacity, deletes, split/merge and partial lot patches require a versioned extension and are unsupported.

## UI and export

WIP tab offers replay/final switch, grouping, process/line/location/status filters, lot/product search, group selection, lot history and recorded allocation/blocked reason links. Selection highlights existing graph nodes and moving routes without changing replay time; opening a reason explicitly seeks its recorded event. History is prefix bounded. Final mode displays a separate notice and full-run graph snapshot; returning to another tab restores the ordinary replay graph.

CSV uses the exact visible lot rows, columns and localized statuses at click time plus mode/cursor/time metadata. Unknown numeric fields are blank in CSV and “Not recorded” in the UI. Quoting protects commas/newlines/quotes; spreadsheet formula-leading text is prefixed with an apostrophe. Filters use stable IDs across ko/en/ja changes. On compact screens tables scroll within labelled keyboard-focusable regions, not the page.

## Validation

Install `requirements-dev.txt` and Chromium, then run:

```sh
python -m unittest discover -s tests -v
python tests/browser_inventory.py
python tests/browser_smoke.py
python tests/browser_results.py
```

Projection tests compare every actual Pull/Push trace prefix with an independent latest-snapshot oracle, exercise all seven location/state categories, poison future data, preserve time/quantity zero, test explicit buffer capacity before filtering, and check CSV quoting/formula protection. Browser tests cover all three locales on desktop and iPhone13 emulation, group/filter/search totals, visible CSV rows/cursor, graph node/route highlights, bounded history, reason navigation, language changes and page overflow. `artifacts/wip-*.png` contains browser evidence. CI runs the projection suite and WIP browser test after installing Chromium.

## Operational trace integration

For operational schema 1 traces, replay starts with `initial_state` and applies
`lots`, `buffers`, `machines`, and `machine_operations` deltas through the selected
cursor. `placement` is the authoritative single physical location: row `physical`
is its buffer, machine, or transport ID. Buffer graph anchors use `at`; historical
adapter fixtures can still supply `graph_node`. Logical reserved destinations and
moving route labels remain separate from physical storage.

Group occupancy uses the prefix buffer's complete `contents`, before filtering.
Reserved lots remain stored until movement, and completed lots retained in OUTPUT
count toward storage occupancy while contributing zero WIP. Setup, down, offshift,
and blocked machine ownership overrides the legacy three-state projection for
that lot. Machine-only events with `affected_lot_id: null` do not become fictitious
lot history. Historical traces without operational placements retain the prior
inferred-location behavior.

Integration checks: `python -m unittest discover -s tests` compares every pull/push
cursor against `inventory.replay_inventory`; `python tests/browser_inventory.py`
checks all three locales and mobile layout. Build `deploy/Dockerfile`, then run
`python tests/browser_public.py http://127.0.0.1:PORT` against its static container
to exercise actual browser execution and WIP/allocation/results together.

Order/disruption integration retains the same placement contract. An order with
`state: release_pending` and `placement.kind: release` is an external supply lot:
WIP shows a separate pending-admission group (`location: RELEASE`), and it does
not consume INPUT capacity until its arrival/buffer-entry event. It remains in
released WIP totals, matching the engine and order planner. Machine-owned
`maintenance` and `resource_wait` states are shown directly with localized labels.
Order due-date projection retains its own physical-location adapter; WIP logical
reservation destinations do not overwrite order physical locations.

The static browser bundle includes orders, disruptions and operation metrics as
Python runtime modules, plus all three dashboard scripts. The public browser test
runs a finite-INPUT, delayed-order, offshift and shared-operator model in Pyodide,
then checks every tab around pre-lot machine events, release and completion in
ko/en/ja at desktop and mobile widths.
