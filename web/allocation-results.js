"use strict";
// A single event-prefix projection drives the table, timeline and CSV.
function allocationResults(result, cursor, finalView = false, filters = {}) {
  const events = result.events.slice(0, finalView ? result.events.length : cursor);
  const time = finalView ? result.summary.horizon : events.at(-1)?.time ?? 0;
  const machines = Object.fromEntries(result.model.machines.map(m => [m.id, m]));
  const endpoint = id => ({machine: id || "", process: machines[id]?.process || "", line: machines[id]?.line || ""});
  const grouped = new Map(), latest = new Map();
  for (const e of events) {
    latest.set(e.lot.id, e);
    if (e.allocation_id) {
      if (!grouped.has(e.allocation_id)) grouped.set(e.allocation_id, []);
      grouped.get(e.allocation_id).push(e);
    }
  }
  const rows = [];
  for (const a of result.allocations) {
    const history = grouped.get(a.id) || [];
    const assigned = history.find(e => e.kind === "assigned");
    if (!assigned) continue;
    const move = history.find(e => e.kind === "move"), start = history.find(e => e.kind === "start");
    const finish = history.find(e => e.kind === "finish"), complete = history.find(e => e.kind === "complete");
    const route = result.model.routes.find(r => r.id === a.route_id);
    const reservation = assigned.assignment_kind === "reservation";
    rows.push({id: a.id, lot: a.lot_id, product: assigned.lot.product,
      source: endpoint(route.from), destination: endpoint(a.destination), route: a.route_id,
      mode: a.mode, assigned: assigned.time, move: move?.time ?? null,
      arrived: (start || complete)?.time ?? null, start: start?.time ?? null, finish: finish?.time ?? null,
      status: complete ? tr("ui_167") : finish ? tr("ui_166") : start ? tr("ui_30") : move ? tr("ui_29") : reservation ? tr("ui_164") : a.destination === "OUTPUT" ? tr("ui_165") : tr("ui_163"),
      decision: a.decision_index, after: a.after_cursor, reservation,
    });
  }
  for (const e of latest.values()) {
    const lot = e.lot;
    if (lot.state !== "waiting" || lot.target || lot.allocation_id) continue;
    const history = events.filter(item => item.lot.id === lot.id);
    const boundary = history.findLastIndex(item => item.kind === "ready");
    const decision = history.slice(Math.max(0, boundary)).findLast(item => ["decision", "blocked"].includes(item.kind));
    rows.push({id: "", lot: lot.id, product: lot.product, source: endpoint(lot.location), destination: endpoint(null),
      route: lot.preferred_route || "", mode: result.model.mode, assigned: null, move: null, arrived: null, start: null, finish: null,
      status: decision?.kind === "blocked" ? tr("ui_169") : tr("ui_168"), decision: decision?.index ?? null, after: e.index + 1});
  }
  return {time, rows: rows.filter(r =>
    (!filters.process || [r.source.process, r.destination.process].includes(filters.process)) &&
    (!filters.line || [r.source.line, r.destination.line].includes(filters.line)) &&
    (!filters.machine || [r.source.machine, r.destination.machine].includes(filters.machine)) &&
    (!filters.lot || r.lot === filters.lot) && (!filters.status || r.status === filters.status) &&
    (!filters.search || r.lot.toLowerCase().includes(filters.search.toLowerCase())))};
}
function allocationColumns() { return [
  [tr("ui_195"), r => r.id], [tr("ui_35"), r => r.lot], [tr("ui_72"), r => r.product],
  [tr("ui_196"), r => r.source.process], [tr("ui_197"), r => r.source.line], [tr("ui_198"), r => r.source.machine],
  [tr("ui_199"), r => r.destination.process], [tr("ui_200"), r => r.destination.line], [tr("ui_201"), r => r.destination.machine],
  [tr("ui_63"), r => r.route], [tr("ui_202"), r => r.mode], [tr("ui_203"), r => r.assigned],
  [tr("ui_204"), r => r.move], [tr("ui_205"), r => r.arrived],
  [tr("ui_206"), r => r.start], [tr("ui_207"), r => r.finish], [tr("ui_37"), r => r.status],
]; }
function allocationCSV(rows, metadata) {
  const cell = v => '"' + String(v ?? "").replace(/"/g, '""') + '"';
  const columns = [...allocationColumns(), ...Object.entries(metadata).map(([k,v]) => [k, () => v])];
  return '\uFEFF' + [columns.map(([name]) => name), ...rows.map(r => columns.map(([,get]) => get(r)))].map(row => row.map(cell).join(",")).join("\r\n");
}
