# Orders, releases and due dates (schema 1)

`MODEL.orders` is an optional, additive plan. Without that key, the existing `source` generates the same deterministic lots as before; no quantity, order or deadline is invented. With the key (including an empty array), orders replace generated supply. Keep the existing `source` configuration in MODEL for compatibility with the model editor. To return to generated supply, remove `orders` in Python.

Use `examples/orders_demo.py` for split lots, scheduled release, dated deadlines and a custom rule that explicitly uses business priority and due slack. This does not change the default chooser.

## Model and JSON round trip

The planner tab is available before a run. **Edit order plan** edits a JSON document, **Import plan JSON** reads the same schema, and **Export plan JSON** preserves business values. Applying a valid plan updates only the MODEL literal through the existing non-executing parse/sync API, preserves Python functions/comments and invalidates the old replay. Invalid documents and quantity/date errors are reported in ko/en/ja and leave source unchanged. Public static mode permits viewing/export only.

```json
{
  "schema_version": 1,
  "time_origin": "2026-09-10T09:00:00+09:00",
  "order_risk_window": 15,
  "orders": [
    {
      "id": "ORDER_A",
      "product": "A",
      "quantity": 5,
      "release_time": 0,
      "due_date": "2026-09-10T09:30:00+09:00",
      "priority": -2,
      "customer": "Customer",
      "reference": "PO-1",
      "lots": [
        {"id": "LOT_A1", "quantity": 2},
        {"id": "LOT_A2", "quantity": 3, "release_time": 5}
      ]
    }
  ]
}
```

The JSON envelope version is `schema_version: 1`; in MODEL store its `orders`, optional `time_origin`, and optional `order_risk_window` fields. Only these envelope keys are accepted. The authoritative plan validator is `orders.normalize_orders(model)`; it returns detached runtime definitions and never rewrites the input.

- Order and lot IDs: globally unique within each namespace, ASCII letter followed by letters/digits/underscore/hyphen, at most 64 characters. At most 2000 orders and 2000 lots total. The order and lot namespaces are separate.
- `product`: nonempty string. Required `quantity`: finite positive number, at most 1,000,000,000. Booleans are not numbers. Quantities count product units and do not change processing duration or split/merge inventory automatically.
- Omit `lots` to create one lot with the order ID and full quantity. Otherwise every lot needs a unique `id` and positive `quantity`; totals must equal the order quantity (floating arithmetic tolerance: relative 1e-12, absolute 1e-9).
- `release_time`: simulation minutes, nonnegative, default 0. A lot may override it with a later/equal value; otherwise it inherits the order release time. At least one lot must release at the order release time, so the order’s scheduled boundary matches its first lot. Lots are ordered by planned time, with original plan order breaking ties. Order progress becomes released at its first actual lot release, so a split order can retain unreleased quantity.
- `due_time`: optional finite simulation minute, may precede release (an already-late order is valid). No due field means unknown deadline, not zero.
- `due_date`: optional RFC3339 datetime with full seconds and explicit timezone (`Z` or `±HH:MM`), optionally 1–3 fractional second digits. Requires `MODEL.time_origin` in the same format. The normalized due minute is `(due_date - time_origin) / 60 seconds`. If both due fields exist, they must agree within 1e-7 minutes. Original date/priority/customer/reference values remain unchanged in the model, export and lot `order` header; UTC conversion is only derived metadata. Dates without a timezone are rejected.
- `priority`: finite numeric business priority, default 0, from −1,000,000 to 1,000,000,000. Lower values are first only in a policy that explicitly uses them. `customer` and `reference` are optional strings.
- `order_risk_window`: nonnegative finite simulation minutes, default 30. It is a due-soon threshold, not a completion forecast.

JSON plan export is distinct from **Visible results CSV**, which exports the current filtered order rows with localized headers/statuses plus event cursor, time and replay/final mode. CSV is UTF-8 BOM, quotes fields and neutralizes formula-leading cells. Unknown numeric values are blank. Standard Python and trace exports continue to include the full source/model and runtime trace.

## Scheduled release versus finite-buffer admission

Every planned lot releases exactly at its simulation time, including an event at the run horizon. `order_release` records `created`, `released_at`, `release_time`, quantity and order fields. A later lot stays only in the plan until its release event; no future runtime snapshot enters replay.

A finite INPUT buffer may postpone physical admission without postponing release. Released lots waiting outside the factory have:

```json
{"state":"release_pending","location":"INPUT","placement":{"kind":"release","id":"INPUT"}}
```

This additive placement kind is **external released backlog**, not a buffer member, machine claim or transport. It is included once in released unfinished quantity. When INPUT has space, the lot receives `admitted_at`, enters the real buffer and emits `arrival`; release time remains unchanged. No buffer can overflow. Backlog at termination is listed by `order_release_backpressure` diagnostics. Cycle time includes waiting outside INPUT, from actual release to completion. Source-based legacy backpressure semantics remain unchanged.

Trace `schema_version` stays 2 with additive `order_schema_version: 1` and `order_plan`. The #10 operational inventory adapter accepts the release placement and preserves its identity; buffer queues exclude it. WIP consumers should show this as a distinct external release queue and must not count it as INPUT buffer occupancy.

## Cursor metrics and truthful selection reasons

`orderProjection(result, cursor, options)` in `web/order-projection.js` consumes only events `[0,cursor)`. Cursor 0 has no runtime lots. Unreleased orders/lots come from the visible plan, never from future event snapshots. Current time is the last included event time (including zero); `finalView: true` uses the complete prefix and `summary.horizon`. Equal-time event boundaries remain distinct.

- Released quantity + unreleased quantity = order quantity; completed quantity + released unfinished quantity = released quantity.
- For unfinished orders, slack = due time − current time. For completed orders it freezes at due time − last lot completion time. Lot slack freezes at its own completion. Tardiness = max(0, −slack); no deadline gives null for both.
- Risk is late when slack < 0, due soon for unfinished orders when 0 ≤ slack ≤ threshold, otherwise within due time. Completed on-time orders never remain due soon. This risk label makes no prediction about remaining work, calendars or queue delays.
- Order cycle time is last completion − first actual release once all lots complete. The summary mean completed cycle is quantity-weighted across completed lots. Attainment is the fraction of completed, dated orders whose tardiness is zero; unfinished/no-due orders are excluded, and no denominator means unknown.

Candidates retain existing route `priority` and gain copied `order_id`, `quantity`, `lot_priority`, `release_time`, `due_time`, `due_date`, `customer`, `reference`, `order` (raw header, excluding nested lots), and derived `due_slack`. `context.orders` is a detached map of raw order headers. Existing copied `context.lot`, timing/process lot arguments also carry the lot fields. Mutating these copies cannot alter the plan, trace or engine state; this is a data-isolation guarantee, not a security sandbox for local Python.

The default chooser still sorts route priority, queue length, ready time, ID. It records `selection_policy: default_route` and `order_factors_used: false`. Custom choosers record `selection_policy: custom` and `order_factors_used: null`; their returned reason is displayed verbatim or through an explicit structured message. The UI never infers that an attractive due date or priority caused a custom selection. Explicit buffer priority policies remain separately defined by #10 and can restrict eligible queue heads.

## Integration boundary and UI

The planner owns `PLANNER` filters, final mode and selection without touching WIP/result filters. `orderProjection` is pure and exposes order rows, planned/released lot drilldown, location quantities, prefix-bounded operations/history/allocation indexes and filtered/unfiltered totals. It accepts optional `options.inventory.rows` (lot ID, location, node, operation) from a WIP projection; the UI supplies `inventoryProjection` when installed. Otherwise `orderLotLocation` reads #10 buffer placements (`buffer.id` → `buffer.at`), machine/transport and external release placements; legacy schema-v2 lots use physical locations. This keeps later #11 integration additive rather than requiring its branch now.

Order drilldown shows lots, actual and planned release/admission, operation, location, slack and recorded selection reasons. Allocation buttons seek that exact decision event. The graph/history button explicitly seeks the full event prefix when opened from final view, then uses the existing lot graph/history inspector. Tables have captions and scoped headings, native labelled filters retain keyboard focus, and compact layouts scroll tables within a named focusable region. Customer/product/IDs and custom reasons are user data and are not translated.

## Verification

```sh
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python -m unittest discover -s tests -v
python tests/browser_orders.py
python tests/browser_smoke.py
```

Order tests cover validation/roundtrip/timezone and mismatch errors, scheduled and horizon releases, finite INPUT backlog, every-cursor quantity and due reconciliation, time zero/future isolation, custom-copy mutation isolation, truthful default/custom metadata and old-model determinism. Browser coverage includes ko/en/ja on desktop/mobile, actual JSON import/export and localized failures, prefix/final metrics, filtered CSV, exact allocation navigation, keyboard focus and page overflow. Screenshots are written under `artifacts/orders-*.png`.
