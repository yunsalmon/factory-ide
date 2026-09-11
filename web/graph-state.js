"use strict";
// Pure replay projection: operational state is authoritative; legacy traces
// without that contract fall back to their recorded machine snapshot only.
function graphStateProjection(result, cursor, finalView = false, fallbackModel = {}) {
  const events = result?.events || [], model = result?.model || fallbackModel;
  const end = finalView ? events.length : Math.max(0, Math.min(events.length, Math.trunc(Number(cursor) || 0)));
  const initial = result?.initial_state || {};
  const dictionary = value => Object.assign(Object.create(null), value);
  const operations = dictionary(initial.machine_operations), legacy = dictionary(initial.machines);
  const buffers = dictionary(initial.buffers), lots = dictionary(initial.lots);
  const machineEvents = dictionary(), lotEvents = dictionary(), bufferEvents = dictionary();
  let operational = Object.hasOwn(initial, "machine_operations");
  for (let i = 0; i < end; i++) {
    const e = events[i], changes = e.state_changes || {};
    if (Object.hasOwn(changes, "machine_operations")) operational = true;
    for (const [id, value] of Object.entries(changes.machine_operations || {})) {
      operations[id] = value; machineEvents[id] = i;
    }
    for (const [id, value] of Object.entries(changes.lots || {})) { lots[id] = value; lotEvents[id] = i; }
    for (const [id, value] of Object.entries(changes.buffers || {})) { buffers[id] = value; bufferEvents[id] = i; }
    Object.assign(legacy, changes.machines || {});
  }
  const machines = dictionary();
  for (const m of model.machines || []) {
    const recorded = operational ? operations[m.id] : legacy[m.id];
    const op = recorded || {state: "unknown", lot: null, cause: null, since: null};
    const cause = typeof op.cause === "string" ? op.cause : "";
    const bufferId = op.state === "blocked" && cause.startsWith("buffer_full:") ? cause.slice("buffer_full:".length) : null;
    const buffer = bufferId !== null && Object.hasOwn(buffers, bufferId) ? buffers[bufferId] : null;
    machines[m.id] = {...op, lot: op.lot ?? null, inferred: !operational,
      event: machineEvents[m.id] ?? null, lotEvent: op.lot == null ? null : lotEvents[op.lot] ?? null,
      buffer: buffer ? {...buffer, contents: [...(buffer.contents || [])]} : null,
      bufferId, bufferEvent: bufferId === null ? null : bufferEvents[bufferId] ?? null};
  }
  return {cursor: end, finalView, time: finalView ? model.duration ?? events.at(-1)?.time ?? 0 : events[end - 1]?.time ?? 0,
    machines: dictionary(structuredClone(machines)), buffers: dictionary(structuredClone(buffers)), lots: dictionary(structuredClone(lots))};
}
if (typeof module !== "undefined") module.exports = {graphStateProjection};
