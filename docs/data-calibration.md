# Local factory data and calibration (format version 1)

Open **Data & calibration** in the local IDE. Select a table before choosing a CSV, map source columns to the displayed canonical fields, select minutes/seconds/hours and the fixed UTC offset for naive timestamps, then run validation. JSON uses its declared `time_unit` (minutes by default). A dated observation additionally needs an RFC3339 simulation origin; explicit offsets always take precedence over the selected offset. Time values become simulation minutes. Dates require seconds, valid calendar dates and offset components; DST is not inferred. Use an explicit offset in each row when historical offsets differ.

Validation runs in a dedicated Worker. The preview lists validated rows and changed table counts. **Confirm model replacement** is a separate action: dry runs never change source or model. CSV replaces one entire table in a copy of the current model and checks references against that full candidate. For related tables, import a complete JSON model so temporary dangling references are unnecessary. A source edit invalidates a pending preview. The existing local `/api/sync` validator makes the final model replacement atomic and preserves Python functions outside the MODEL literal.

## Templates and round trips

Download CSV or JSON templates from the panel. JSON model templates contain the complete current native model so required structures and source settings are retained. The observations JSON template contains one sample state marker to replace. CSV templates supply the version 1 column header for the selected table. Canonical CSV exports include `schema_version=1` on every row; imported external CSVs may omit that column because the mapping UI selects this version 1 contract. A supplied different version is rejected.

```json
{"schema_version":1,"kind":"model","time_unit":"minutes","model":{"...":"complete native MODEL"}}
```

The native model includes processes, optional line/product catalogs, machines, canonical buffers, routes, orders/lots, source/settings and unknown extension properties. JSON round trips preserve these properties exactly. CSV nested values (`availability`, `lots`, setup/resource extensions) use quoted JSON. `extra_json` retains unknown properties and explicit null/empty scalar values; it cannot override a populated canonical column. All exports are explicit downloads.

```json
{"schema_version":1,"kind":"observations","time_unit":"minutes","time_origin":"2026-09-10T00:00:00Z","events":[
  {"id":"a1","kind":"arrival","time":0,"lot":"L1","quantity":2},
  {"id":"s1","kind":"start","time":2,"lot":"L1","machine":"M1"},
  {"id":"c1","kind":"complete","time":5,"lot":"L1","quantity":2},
  {"id":"m0","kind":"state","time":0,"machine":"M1","state":"processing"},
  {"id":"m5","kind":"state","time":5,"machine":"M1","state":"idle"}
]}
```

Observation IDs are unique. Kinds: arrival, ready, start, finish, complete, state. State markers require a valid machine and state; start/finish also require a valid machine. Other events require a lot. A lot has at most one first arrival and one completion. Quantity is optional; missing completion quantities remain explicitly unknown. Optional products must reference the declared product catalog. Diagnostics identify the table, physical CSV row (including multiline fields), field and localized reason. JSON row numbers are one-based array positions.

## Calibration definitions

Use the same start/end window for observations and simulation. Completed lots and quantity are measured in the inclusive completion window; hourly throughput is completed lots divided by elapsed minutes times 60. Cycle samples are completion minus first arrival, including arrival before the window. Ready-to-start samples are start minus the first ready/arrival marker since the previous start. They include transfer/setup time and are not a claim of pure queue waiting time. Means and linearly interpolated p50/p90 describe both sample distributions, with paired/missing sample counts. Missing pairs are excluded rather than assigned zero.

Utilization is processing time divided by known state time. Known state intervals require consecutive explicit markers, clipped to the chosen window. A closing marker beyond the window can bound an observed interval; a last marker without any subsequent observation leaves an unknown tail. The UI shows each machine's known minutes/window minutes and coverage separately. Do not compare utilization values without considering that coverage. No state is inferred from incomplete lot events.

The simulation adapter derives lot events and machine state markers from the version 2 trace. Order release defines initial arrival; later physical input admission does not reset the cycle. Final view closes states at the trace horizon; cursor view uses only the chosen prefix and closes at its last event. The panel displays the available evidence time, so the remainder of a larger comparison window is visibly unknown. Raw observations are independent of simulation and never silently synthesized to fit it.

## Privacy, limits and integration

Raw file text and observations exist only in the current page's memory. They are neither persisted to localStorage nor uploaded. Confirming a model explicitly sends the validated model to the local IDE and uses its usual source persistence; observations remain separate. Reloading clears observations. Observations can be retained only by an explicit export. If a later model changes referenced machines/products, calibration marks the observations stale until re-imported.

Limits: 8 MiB input file, 20,000 rows, 128 CSV columns, 65,536 characters per cell and 200 displayed diagnostics, plus native engine model limits. Cancel terminates the Worker and invalidates pending File.text callbacks. No partial candidate is applied. Existing models remain runnable during import; the public static demo exposes exports/comparison but no import or confirmation controls.

Integration APIs: `prepareDataImport(request, progress)` returns a staged candidate/diagnostics; `exportDataModel`, `exportDataCSV`, `calibrationMetrics`, `simulatedCalibration` are pure functions in `web/data-core.js`. `web/data-worker.js` owns asynchronous parsing/validation. `web/data-ui.js` owns page-only state and uses the existing revision/mutate contract for explicit replacement. No new server API or engine event schema is introduced.
