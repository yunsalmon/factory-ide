# Scenario comparison

Use **Scenario comparison → Run and save scenario** after editing the model or Python. Give each snapshot a name and optional raw notes. This runs through the existing local or isolated browser executor; only a fresh successful result is saved. Choose any baseline and candidate, including the same snapshot. The browser retains up to ten snapshots when storage is available. Export JSON for durable storage/sharing; importing replaces the comparison collection after validation. Import and replay links never execute Python. The ordinary Run button remains explicit execution after restoring a source.

## Snapshot and artifact contract

`web/scenario-comparison.js` exposes `scenarioCreate`, `scenarioCompare`, `scenarioArtifact`, and asynchronous `scenarioRestore`. UI state is owned by `SCENARIOS` in `scenario-ui.js`, independently of WIP/order/allocation filters. `scenarioLoadResult` loads one saved result into the existing replay views.

A version-1 `factory-scenario-comparison` document contains a scenarios array and zero-based baseline/candidate indexes. Each scenario includes name, notes, complete Python source, result model/trace, seed, runtime metadata, and comparison contract. Source SHA-256 hashes exact UTF-8 source; model and trace SHA-256 use recursively sorted object keys, original array order, and JavaScript JSON numeric serialization. Names/notes do not affect input hashes. Runtime includes actual Python/SimPy versions and local application version, or browser engine revision/Pyodide versions. These hashes detect accidental mismatches, not authenticity or provenance from an untrusted signer.

Restoration recomputes all hashes and derived contract/seed metadata before replacing the collection. Maximum 10 scenarios, 50,000 events per result, and 25 MB uploaded artifact. A source hash supplied by browser execution must agree. Runtime differences and different seeds produce explicit comparison warnings. An artifact contains every source and event, so it can be reviewed without accessing the original server. Do not treat imported source as executed merely because its trace was restored.

Only trace schema 2 and known operational schema 1/2 and order schema 1 (or absent legacy versions) are supported. The exact schema tuple must match across a pair. Each result must have a positive finite horizon equal to model.duration; horizons must be identical across the pair. The engine's implicit time unit is minutes; explicit `min` and `minutes` normalize to `min`. Other units are rejected. There is no silent clipping, extrapolation or rate-based normalization of unequal horizons.

## KPI definitions

All metrics use the final trace and the common horizon; signed delta is **candidate − baseline**. Missing measurements appear as unavailable and produce an unavailable delta, never an invented zero.

- Completed/WIP lots use the integrated inventory projection. Completed/WIP quantities require known quantities for every relevant lot. An explicitly ordered population with an empty completed/WIP set has known zero quantity; generated legacy lots have unknown quantity.
- Throughput is completed lots or known completed quantity divided by the horizon in minutes.
- Mean completed lead/cycle time is completion minus actual release (or legacy creation); it includes external INPUT admission wait. No completed measurements means unavailable.
- Late orders and total order tardiness use integrated final order/due projection, including unfinished and future orders with due dates. Orders without deadlines are excluded; no measured deadlines means unavailable.
- Each machine/state utilization is its recorded duration divided by the horizon. Mean processing utilization requires all expected machines from the model and trace, every state defined by the operational schema, a matching measurement horizon, and state durations reconciling to that horizon. A missing machine or even a missing zero-valued state makes the aggregate unavailable; the denominator never silently shrinks. Individual missing state rows also remain unavailable. Complete measured zero processing remains zero.
- Finite-buffer congestion is the duration its actual contents meet capacity, integrating initial state and every buffer delta through the horizon. Completed OUTPUT storage counts. Unbounded/unknown capacity is unavailable. Expected buffers are collected from model definitions, initial snapshots and all buffer deltas. Any positive-duration interval without contents and matching capacity evidence makes that buffer unavailable, even if measurements later resume; an omitted entire expected buffer is also unavailable. The total is available only when every expected finite buffer is measured, and may exceed the horizon because buffers overlap. Fully observed empty buffers retain true zero full time.
- Cross-line movement counts `move` events between known machines whose nonempty line IDs differ. INPUT/OUTPUT legs are excluded; a machine-to-machine move with missing line metadata makes the count unavailable.

## Trace evidence

Allocation decisions/assignments, buffer entry/wait, machine-state transitions and resource waits are paired by lot (or machine), event kind and occurrence number. Changed payloads expose their exact time, route, reason, checks, queues, resource or transition data and link to the stored event in either scenario. This is an auditable correspondence, not a claim of identical causal identities. Changes are observations, **not proof of causation**. The UI shows the first 100 differences; all traces and remaining evidence inputs are retained in the export. Saved inputs expand to complete source and model JSON.

## Validation

`python -m unittest discover -s tests -v` covers stable hashes, identical zero differences, signed quantity/lot changes, independent congestion integration, compatibility rejection, missing values and artifact integrity. `python tests/browser_scenarios.py` covers ko/en/ja desktop/320px actual executions, export/import, unchanged restored metrics, linked traces, no execution requests during restoration and reload. Pass a static Nginx URL as an argument to exercise the same flow through the browser Python Worker.
