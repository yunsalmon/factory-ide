# Graph operational state

The graph reads `graphStateProjection(result, cursor, finalView, fallbackModel)`.
The cursor is the number of included events, not a timestamp. Initial state plus
exactly that prefix supplies machine operations, physical buffers, lot placement,
and evidence event positions. Same-time events remain distinct. Final view folds
all events and labels time with the configured horizon. WIP, order and operations
scope selectors update the graph; other tabs follow the replay cursor.

`machine_operations` is authoritative. The graph does not infer processing or
idle from the legacy `machines` reservation view. A trace without any operational
contract falls back to its recorded legacy machine state and identifies that
limitation in the inspector. A missing machine within an operational contract is
unknown. Projection results are isolated from the source trace, and dictionaries
accept IDs such as `constructor` without inherited-property lookups.

Machine text and accessible names contain the localized current state. A held
lot is only described as physically held when its placement matches the machine;
other reservations/associations are identified separately. The inspector shows
scope/time, cause, held/associated lot, and—when cause is `buffer_full:<id>`—the
exact recorded buffer occupancy/capacity and contents. Its buttons navigate to
WIP at the same scope or to the recorded state/lot/buffer event. Event navigation
explicitly switches to replay. Property-edit fields are not rebuilt during
cursor changes; the operational section refreshes independently.

The original issue31 fixture encodes N as `availability.state='down'` with
`cause='maintenance'`. Its truthful display is down plus the maintenance cause.
An additional fixture uses `maintenance=[{start:0,end:8}]`, which actually emits
`state='maintenance'`. Both are tested, including M blocked at t=2 and all
maintenance/buffer-release boundaries. No engine state is renamed by the UI.

Verification: `tests/test_graph_states.py` checks every engine prefix, legacy and
unknown fallback, causality and isolation. `tests/browser_graph_states.py` runs
ko/en/ja at 1440/320px, keyboard and forced colors, state/buffer/lot links and
replay/final changes, plus setup/offshift/shared-resource waits. Screenshots are
written to ignored `artifacts/graph-operation-*.png`.
