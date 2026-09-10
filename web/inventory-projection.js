"use strict";
// Pure, locale-independent projection. See docs/inventory-projection.md.
function inventoryProjection(result, cursor, options = {}) {
  const events = result?.events || [];
  const end = options.finalView ? events.length : Math.max(0, Math.min(events.length, Math.trunc(Number(cursor) || 0)));
  const prefix = events.slice(0, end);
  const time = options.finalView ? result?.summary?.horizon ?? prefix.at(-1)?.time ?? 0 : prefix.at(-1)?.time ?? 0;
  const machines = Object.fromEntries((result?.model?.machines || []).map(m => [m.id, m]));
  const buffers = Object.assign(Object.create(null), Object.fromEntries((result?.model?.buffers || []).map(b => [b.id, b])));
  const initial = result?.initial_state || {};
  const lots = new Map(Object.entries(initial.lots || {})), machineStates = Object.assign(Object.create(null), initial.machines),
    operations = Object.assign(Object.create(null), initial.machine_operations), history = new Map(), changedAt = new Map();
  Object.assign(buffers, initial.buffers);
  for (const [id, lot] of lots) changedAt.set(id, lot.location_since ?? 0);
  for (const e of prefix) {
    const changes = e.state_changes?.lots ?? (e.lot ? {[e.lot.id]: e.lot} : {});
    for (const [id, snapshot] of Object.entries(changes)) {
      const previous = lots.get(id);
      if (!previous || previous.location !== snapshot.location || previous.state !== snapshot.state || JSON.stringify(previous.placement) !== JSON.stringify(snapshot.placement)) changedAt.set(id, e.time);
      lots.set(id, {...snapshot, id});
    }
    Object.assign(machineStates, e.state_changes?.machines || {});
    Object.assign(buffers, e.state_changes?.buffers);
    Object.assign(operations, e.state_changes?.machine_operations);
    if (e.lot && e.affected_lot_id !== null) {
      if (!history.has(e.lot.id)) history.set(e.lot.id, []);
      history.get(e.lot.id).push(e);
    }
  }
  const reservations = new Map(Object.entries(machineStates).filter(([,m]) => m.state === "reserved" && m.lot).map(([id,m]) => [m.lot,id]));
  const rows = [...lots.values()].map(lot => {
    const events = history.get(lot.id) || [];
    const latest = events.at(-1);
    const boundary = events.findLast(e => ["ready", "arrival", "assigned", "move", "start", "finish", "complete", "blocked"].includes(e.kind));
    const reserved = reservations.get(lot.id);
    let status = lot.state === "completed" ? "completed" : lot.state === "moving" ? "moving" : lot.state === "processing" ? "processing" : lot.state === "blocked" || (lot.state === "waiting" && boundary?.kind === "blocked") ? "blocked" : reserved || lot.state === "reserved" ? "reserved" : "waiting";
    const operation = lot.placement?.kind === "machine" ? operations[lot.placement.id] : null;
    if (operation?.lot === lot.id && ["setup", "down", "offshift", "blocked"].includes(operation.state)) status = operation.state;
    const physical = lot.placement?.id ?? lot.location;
    let location = status === "completed" ? "OUTPUT" : status === "reserved" ? reserved || lot.target || lot.location : physical;
    const buffer = lot.placement?.kind === "buffer" || !lot.placement ? buffers[physical] : null;
    let kind = status === "completed" ? "output" : status === "moving" ? "transit" : status === "reserved" ? "reserved" : status === "processing" ? "processing" : status === "blocked" ? "blocked" : (buffer?.at ?? location) === "INPUT" ? "input" : "queue";
    if (["setup", "down", "offshift"].includes(status)) kind = status;
    if (status === "moving") location = `${lot.location} → ${lot.target ?? "?"}`;
    const anchor = buffer?.graph_node ?? buffer?.at ?? (status === "moving" ? lot.target : location);
    const meta = buffers[anchor] || machines[anchor] || buffer || machines[physical] || {};
    const route = lot.route || lot.assigned_route || latest?.route || "";
    const waiting = ["waiting", "reserved", "blocked", "setup", "down", "offshift"].includes(status);
    const since = waiting ? lot.wait_since ?? lot.ready_since ?? changedAt.get(lot.id) ?? null : null;
    const wait = since == null ? null : Math.max(0, time - since);
    // Explicit capacity applies to the whole physical buffer, not a filtered subgroup.
    const capacity = buffer && Number.isFinite(buffer.capacity) && buffer.capacity >= 0 ? buffer.capacity : null;
    const decision = events.findLast(e => ["decision", "blocked"].includes(e.kind));
    return {id: lot.id, product: lot.product ?? "", quantity: Number.isFinite(lot.quantity) ? lot.quantity : null,
      process: buffer?.process ?? meta.process ?? "", line: buffer?.line ?? meta.line ?? "", location, physical,
      placement: lot.placement ?? null, cause: operation?.cause ?? null, kind, status, target: lot.target ?? "", operation: lot.operation ?? machines[physical]?.process ?? "",
      arrival: lot.created ?? null, release: lot.released_at ?? null, locationArrival: lot.location_since ?? changedAt.get(lot.id) ?? null,
      wait, waitSince: since, route, node: anchor ?? physical, capacity, inferred: !buffer && kind === "queue",
      decision: decision?.index ?? null, history: events.map(e => e.index)};
  });
  const filters = options.filters || {};
  const search = String(filters.search || "").toLocaleLowerCase();
  const visible = rows.filter(r => ["process", "line", "location", "status"].every(k => !filters[k] || r[k] === filters[k]) &&
    (!search || `${r.id} ${r.product}`.toLocaleLowerCase().includes(search)));
  const groupBy = ["process", "line", "location"].includes(options.groupBy) ? options.groupBy : "location";
  const map = new Map();
  for (const row of visible) {
    const key = groupBy === "location" ? JSON.stringify([row.process,row.line,row.location,row.kind]) : JSON.stringify([row[groupBy]]);
    if (!map.has(key)) map.set(key, {key, label: row[groupBy], process: row.process, line: row.line, location: row.location, kind: row.kind, rows: []});
    map.get(key).rows.push(row);
  }
  const stats = values => {
    const waits = values.filter(r => r.wait !== null);
    const oldest = [...waits].sort((a,b) => b.wait - a.wait || a.id.localeCompare(b.id))[0];
    const quantities = values.filter(r => r.quantity !== null);
    const mix = Object.create(null);
    for (const row of values) mix[row.product] = (mix[row.product] || 0) + 1;
    return {count: values.length, wip: values.filter(r => r.status !== "completed").length,
      quantity: quantities.length === values.length && values.length ? quantities.reduce((s,r) => s+r.quantity,0) : null,
      knownQuantity: quantities.reduce((s,r) => s+r.quantity,0), quantityKnownCount: quantities.length,
      mix, oldest: oldest?.id ?? null, oldestWait: oldest?.wait ?? null,
      averageWait: waits.length ? waits.reduce((s,r) => s+r.wait,0)/waits.length : null};
  };
  const groups = [...map.values()].map(g => {
    const locations = new Set(g.rows.map(r => r.physical));
    const buffer = groupBy === "location" && locations.size === 1 ? buffers[g.rows[0].physical] : null;
    const capacity = buffer && Number.isFinite(buffer.capacity) && buffer.capacity >= 0 ? buffer.capacity : null;
    const occupancy = buffer ? buffer.contents?.length ?? rows.filter(r => r.physical === buffer.id && !["moving","completed"].includes(r.status)).length : null;
    return {...g,...stats(g.rows), capacity, occupancy,
      congestion: capacity !== null ? occupancy >= capacity ? "capacity" : "available" : g.rows.some(r => r.status === "blocked") ? "blocked" : g.rows.filter(r => r.inferred).length >= 2 ? "queue" : "unknown"};
  });
  return {cursor:end,time,finalView:!!options.finalView,groupBy,rows:visible,allRows:rows,groups,totals:stats(visible),allTotals:stats(rows)};
}
function inventoryCSV(projection, columns, metadata = {}) {
  const cell = value => {
    let text = String(value ?? "");
    if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
    return '"' + text.replace(/"/g,'""') + '"';
  };
  const fields = [...columns, ...Object.entries(metadata).map(([name,value]) => [name, () => value])];
  return '\uFEFF' + [fields.map(([name]) => name), ...projection.rows.map(row => fields.map(([,get]) => get(row)))].map(row => row.map(cell).join(',')).join('\r\n');
}
if (typeof module !== "undefined") module.exports = {inventoryProjection, inventoryCSV};
