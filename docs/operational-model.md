# Explicit buffers and machine operations

[Disruptions and shared resources](disruptions-resources.md) extend this base contract with operational schema version 2, maintenance/resource-wait states, seeded repairs, family setup and operator forms.

The optional operational model extends the existing Python MODEL and schema-v2 trace. Old source files do not need edits. `migrate_model(model)` validates and returns a detached normalized model; parsing and code synchronization still preserve the source declaration and user functions.

## Model contract

```python
# The buffers field inside the existing MODEL literal:
'buffers': [
    {'id': 'CUT_OUTPUT', 'at': 'CUT_A', 'capacity': 2, 'policy': 'fifo'},
    {'id': 'INPUT_STORE', 'at': 'INPUT', 'capacity': 10, 'policy': 'priority'},
]
```

A buffer is the output storage **at** a legacy location: INPUT, one machine ID, or OUTPUT. There is exactly one buffer per location. Missing definitions receive stable `BUF_<location>` IDs, unbounded `capacity: null`, and FIFO display order. Generated-ID collisions receive deterministic numeric suffixes. The normalized model contains `operational_model_version: 1`; migration is idempotent and never mutates its input.

`capacity` is null or a positive integer. Zero-capacity rendezvous and shared storage across multiple machine origins are not modeled. `policy` is `fifo` (entry order) or `priority` (lowest lot priority, ready time, lot ID). Source `priorities` is an optional nonempty numeric list, repeated cyclically like source products; the default is `[0]`.

Normalized buffers also contain `upstream` and `downstream` machine/terminal IDs derived from the routes. INPUT has no upstream producer; a machine buffer stores that machine's finished lots; OUTPUT stores completed shipments. `generated` identifies automatically migrated buffers.

Explicit buffer policies admit the first eligible lot in each buffer to a destination machine's candidate set. An incompatible lot at the head does not prevent later eligible lots from being considered. Candidate route checks excluded by queue discipline carry `reason: "buffer_queue_order"`. The choice hook receives `buffer_id`, `buffer_rank` and `lot_priority` on each candidate. For generated buffers, existing Pull choice hooks retain control over all candidates; this preserves previously supported custom ordering. Existing Push FIFO and allocation IDs are preserved. Changing a generated buffer to an explicit policy requires `generated: false` (or omitting that migration marker in the source).

Machines accept:

```python
{'id': 'M', 'name': 'Machine', 'process': 'P', 'line': 'A', 'time': 6,
 'setup_time': 2,
 'availability': [
     {'state': 'down', 'start': 1, 'end': 3, 'cause': 'repair'},
     {'state': 'offshift', 'start': 5, 'end': 7, 'cause': 'night shift'},
 ]}
```

Windows are sorted, non-overlapping, half-open `[start, end)` intervals in simulation minutes. Adjacent windows are allowed. Setup occurs on every lot after physical arrival. Setup and processing consume only available time; a repair or off-shift interval pauses and later resumes the remaining work. Transport can arrive during a stop, but processing cannot start then. These are deterministic configured periods, not maintenance predictions.

Custom `processing_time` remains supported. Arbitrary `process_lot` generators remain supported without calendars; combining one with availability windows is rejected explicitly because resources and arbitrary generator side effects cannot safely be paused by rewriting elapsed time. Use `processing_time` for work that must pause with a calendar.

## Physical ownership and congestion

Every arrived lot has exactly one `placement`: `{kind: "buffer" | "machine" | "transport", id: <buffer/machine/route ID>}`. Existing `location`, `target`, `state` and allocation fields remain available as the legacy flow view; `placement` is authoritative for physical inventory. A machine reservation is a claim, not another copy of the lot. A reserved lot stays in its buffer until `move`, then occupies transport, and enters the machine at `transport_arrive`.

The finished lot first tries to enter its own machine's output buffer. If that buffer is full, the lot remains physically on that machine and its operational state becomes `blocked`. Processing has ended, and a new lot cannot start there. Removing a buffered lot wakes the upstream producer; it transfers exactly the same held lot once, releases the machine, then makes the lot ready for its next allocation. No capacity is inferred from a destination assignment.

A full INPUT buffer postpones admission; source items outside the factory have no lot record yet. Their original scheduled time is kept when they arrive. A finite OUTPUT terminal buffer retains completed shipments. If it is full, already departed shipments wait in transport with `state: "blocked"`; the diagnostic names the terminal buffer and waiting lots. No automatic terminal drain is implied. Machine blocking occurs at the producer's finite output buffer; subsequent transport/storage blocking is a different physical stage.

The engine checks exclusive buffer membership, capacity, transport/machine ownership and unique machine claims before every event is recorded. There are no phantom lots or duplicate inventory entries.

## Trace and inventory projection

The trace retains `schema_version: 2`, existing events/allocations and their ID links. Additions are:

- `operational_schema_version: 1` and `initial_state` containing lots, legacy machines, buffers and machine operations.
- `state_changes.buffers`: ID-keyed full replacement records with ordered `contents` lot IDs.
- `state_changes.machine_operations`: ID-keyed replacement records `{state, lot, since, cause}`.
- `state_changes.lots`: every changed lot since the previous event, including placement changes made during machine-only transitions.
- `machine_state` events with `transition.previous` (including `end` and `duration`) and `transition.current`.
- `transport_arrive`, `buffer_enter` and `buffer_wait` events.
- `operational_diagnostics`: structured `deadlock`, `horizon_wait`, `no_progress`, or `source_backpressure` findings containing involved machine, buffer and lot IDs where applicable.

Diagnosis also checks inventory independently of the machine's operational state. A full finite buffer at the horizon records its occupancy, capacity, queued lots and consuming machine(s), even while processing is still scheduled to finish. It is `horizon_wait` when future events remain, not deadlock. A halted waiting queue without future events is `no_progress`; an idle consumer that declined selection is identified with cause `selection_declined`. Buffer diagnostics include `machines` for all related producers/consumers and retain the singular `machine` field when there is one consumer.

The operational machine states are `idle`, `reserved`, `processing`, `setup`, `down`, `blocked`, `starved`, and `offshift`. Starvation means an available machine has no eligible input; it ends when an eligible lot can be reserved. A deliberate chooser decline leaves the available machine idle with cause `selection_declined`. Calendar states override the underlying work stage. No operational record reports processing during setup/down/offshift. The next transition closes the preceding interval; the final `since` interval remains open at the run horizon.

`state_changes.machines` remains the old three-state compatibility projection: processing only during actual processing, reserved for a claimed lot, otherwise idle. Detailed states must be read from `machine_operations`. Machine-only events set `allocation_id: null` and `affected_lot_id: null` when no lot is affected. Their legacy `lot` envelope is an existing context lot so older schema-v2 readers remain usable; inventory consumers must apply `state_changes`, not infer physical changes from that envelope.

```python
from inventory import replay_inventory
state = replay_inventory(result, cursor=17)  # Exactly 17 consumed events.
```

The returned inventory is detached and JSON-compatible:

- `lots`, `machines`, `queues`, `time`: compatible lot/three-state machine/destination waiting view. Legacy `queues` uses location IDs, target-or-location grouping, and excludes machine claims.
- `buffers`: explicit physical storage, ordered contents, occupancy and available capacity.
- `buffer_queues`: unclaimed waiting lots by stable buffer ID, in buffer policy order.
- `machine_operations`: complete recorded operational states.
- `summary`: arrived, completed and WIP counts; `cursor` and inventory `schema_version: 1`.

Historical schema-v2 traces without the extension receive generated unbounded buffers and a documented best-effort operational projection from their recorded states. They cannot recover historical setup/downtime that was never recorded. Cursor zero is the initial empty inventory. Replaying the same cursor cannot mutate the trace and is independent of later runtime changes. Use array/event order rather than merging simultaneous timestamps.

The existing allocation table assumed transport arrival equals processing start. With setup or calendar delays, new consumers should use `transport_arrive` for physical arrival and `machine_operations` for processing intervals. Updating that UI is outside this engine-only change.

## Validation

`python -m unittest discover -s tests` covers migration, capacity, priority/FIFO, source backpressure, starvation, blocked-lot resumption, same-time transitions, adjacent calendars, terminal congestion and every-cursor inventory invariants. Existing browser smoke verifies compatibility. A baseline comparison against commit `404ab2cfaba318d1eababc97111ddba737cce782` preserves both demo modes' 570 allocation/movement/processing event tuples and summary values.
