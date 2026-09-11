# Shifts, failures, maintenance, family setup and shared resources

The operational model builds on [explicit buffers](operational-model.md). Existing models and core allocation semantics remain unchanged. The trace still uses `schema_version: 2`; `operational_schema_version: 2` adds two exclusive machine states, `maintenance` and `resource_wait`, and replayable resource snapshots. No future events are manufactured when a run stops at the horizon.

## Configuring an operator model

The **Machine operations** tab is available before and after execution. Its **Shifts, setup & failures** form edits a selected machine's default setup, initial family, family changeovers, off-shift/down/maintenance/failure windows, failure seed and shared resource requirements. **Shared resources** edits capacities, product families and route transport requirements. Labels, validation messages, state names and built-in causes support Korean, English and Japanese. User IDs/family names and custom cause text are preserved. The read-only public demo shows results but does not expose editing.

Machine fields are optional:

```python
# Fields inside a machine object in the MODEL literal:
'setup_time': 1,
'initial_family': 'red',
'setup_matrix': {'red': {'blue': 3}, 'blue': {'red': 2}, '*': {'red': 1}},
'availability': [{'state': 'offshift', 'start': 10, 'end': 20}],
'maintenance': [{'start': 30, 'end': 32, 'cause': 'planned_maintenance'}],
'failures': {'mtbf': 25, 'repair_time': 2, 'seed': 7},
'resource_requirements': {'setup': {'OP': 1, 'TOOL': 1}, 'processing': {'OP': 1}},
```

Model-level declarations:

```python
'product_families': {'product-a': 'red', 'product-b': 'blue'},
'resources': [
    {'id': 'OP', 'kind': 'operator', 'capacity': 1},
    {'id': 'TOOL', 'kind': 'tool', 'capacity': 1},
    {'id': 'CART', 'kind': 'transport', 'capacity': 2},
],
```

A route may have `resources: {'CART': 1}`. Quantities are positive integers up to the resource capacity; a missing requirement consumes nothing. The form uses zero to remove a requirement. Resource IDs must be unique, and every requirement must reference an existing resource. Deleting a referenced definition is rejected instead of silently losing the requirement. Different resource kinds share the same auditable capacity rules.

## Preemption and sequence semantics

All windows are half-open `[start, end)` in simulation minutes. Machine setup and processing pause at unavailable boundaries, release their entire shared-resource bundle, and later reacquire resources before resuming the **remaining** active duration. They retain the physical lot and its machine reservation. No completed work or finished setup is restarted. A requester that is currently unavailable does not block available requesters from using shared resources.

Transport waits for its full bundle before departure. Once departed, transport is non-preemptive, retains resources until physical arrival and may arrive while the destination machine is off shift. The destination cannot begin setup/processing until available. A transport wait at a destination machine is recorded as `resource_wait`; OUTPUT transport waits are recorded against the lot/request without claiming a machine.

Acquisition is atomic: a request never holds only part of its bundle. Older eligible requests sharing a required resource get first claim; independent requests may proceed. This avoids circular waits caused by partial acquisition. Setup and processing use separate request lifetimes; resources are returned between those phases. Resource release at a machine interruption is part of the same recorded operational transition, so no event cursor shows unavailable work still holding its active-phase bundle.

Family setup is computed once for each machine visit. A product maps through `product_families`, defaulting to its product ID. The machine remembers the family after setup completes. An explicit `from → to` matrix entry wins, then the `* → to` default. Without either entry, same-family transitions take zero time and other transitions use `setup_time`. `*` also denotes an unset initial family. An explicit same-family matrix entry is allowed. When there is **no setup_matrix field**, legacy constant `setup_time` still applies to every lot. `setup_plan` and `setup_complete` record the family and required active duration, including explicit zero-time matrix transitions.

## Failures and planned maintenance

Deterministic failures are a sorted, non-overlapping list:

```python
'failures': [{'start': 5, 'repair_time': 2}, {'start': 12, 'repair_time': 1}]
```

Seeded failures use an exponential gap with mean `mtbf` after the preceding repair ends; the first gap starts at time zero. Gaps use wall-clock simulation time, including idle and off-shift time. Repair time is fixed. A dedicated RNG derives its seed from the model seed, stable machine ID and failure seed, so consuming routing/timing randomness does not change the failure plan. Identical configuration reproduces identical event ordering and plans. Generation is bounded to 5,000 failures per machine and fails explicitly beyond that limit.

`disruption_plan` exports the exact generated and configured windows. Failure windows carry stable failure IDs, source `seeded_failure` or `deterministic_failure`, state `down`, and cause `failure_repair`. Preventive maintenance has state `maintenance` and source `maintenance`. Existing generic down/off-shift availability remains supported.

Failures, maintenance and shifts may overlap. The exclusive operational state uses precedence **down/repair, maintenance, offshift, available work**; a failure repair takes precedence over a generic down window. Original windows remain in `disruption_plan`, while exclusive KPI durations count each instant only once. Work resumes only when all applicable unavailable windows end. This models configured downtime/repair, not stochastic maintenance optimization or repair crews.

As in the buffer contract, arbitrary `process_lot` generators cannot be safely suspended through user resource side effects. Runs with actual disruption windows or nonempty machine shared-resource requirements reject that hook explicitly; use `processing_time` for pausable work. Unconfigured legacy generators remain supported, including empty requirement maps from the forms.

## Replay, timeline and KPI contracts

The ten operational states are `idle`, `reserved`, `processing`, `setup`, `down`, `maintenance`, `blocked`, `starved`, `offshift` and `resource_wait`. `state_changes.machine_operations` remains authoritative; legacy `state_changes.machines` keeps its three-state compatibility projection.

`initial_state.resources` and `state_changes.resources` contain ID-keyed records with capacity, kind, holders and waiters. Each request has a stable ID, lot, machine (nullable for OUTPUT transport), phase, requested time, full requirements and resource-specific units. Events `resource_wait`, `resource_acquire`, `resource_release`, and `machine_state.resource_releases` describe contention and preemption. Wait records expose both the required bundle and queued/holding requesters through the same event snapshot. `inventory.replay_inventory` returns detached resources alongside buffers, lots and machine operations.

`result.operation_metrics` includes:

- `horizon`: the configured simulation duration;
- `intervals`: positive-duration, exclusive machine intervals with start/end/duration, state, cause and affected lot;
- `machines`: a duration total for every operational state on every machine.

Every machine's total is exactly the horizon within floating-point tolerance, including an unfinished final interval. `operation_metrics(result, cursor=..., final=False)` provides prefix-only intervals through the selected event time. At cursor zero its horizon and totals are zero. Closing an interval for reporting does not generate a processing-complete event.

The operator tab defaults to **Final run · full horizon** and provides **Current replay cursor**. It shows reconciled KPI columns, timeline segments, exact interval rows, family changeover records and current resource holders/waiters. Prefix mode uses only consumed events, and final mode includes the trailing interval through the run horizon. Other legacy allocation/timeline views are retained; use the operator view for interruption-aware durations and shared-resource waits.

## Verification

`python -m unittest discover -s tests` includes family-sequence, seeded repair, RNG independence, atomic bundles, preemption, transport and simultaneous failure/setup/shift/resource counterexamples. Every tested cursor checks resource capacity and immutable projection; state duration totals reconcile to the horizon. `python tests/browser_operations.py` verifies forms, ko/en/ja, resource replay and UI/backend KPI agreement. Existing browser smoke and fixed-base demo tuple/summary parity verify compatibility.
