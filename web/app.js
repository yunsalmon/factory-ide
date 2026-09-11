"use strict";
const PUBLIC_DEMO = document.documentElement.dataset.mode === "public";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
const copy = (v) => JSON.parse(JSON.stringify(v));
const nextRenderTurn = () => new Promise(resolve => requestAnimationFrame(resolve));
async function revealInitialPanels() {
  for (const panel of $$('[data-boot-stage]')) {
    panel.removeAttribute('data-boot-stage');
    await nextRenderTurn();
  }
  document.documentElement.dataset.uiReady = 'true';
}
const numberFormatters = new Map();
function formatNumber(value, options) {
  const key = `${locale}:${JSON.stringify(options)}`;
  let formatter = numberFormatters.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale, options);
    numberFormatters.set(key, formatter);
  }
  return formatter.format(value);
}
const fmt = (n) => formatNumber(Number(n || 0), {minimumFractionDigits: 1, maximumFractionDigits: 1});
const preciseNumber = (n) => formatNumber(n, {maximumFractionDigits: 6});
const S = {
  token: "",
  source: "",
  model: null,
  revision: 0,
  valid: false,
  result: null,
  cursor: 0,
  playing: false,
  job: null,
  selected: null,
  lot: null,
  tab: "events",
  resultsFinal: false,
  resultFilters: {},
  zoom: 1,
  warnings: [],
  console: "",
  runtimeError: null,
  statusMessage: null,
  toastMessage: null,
  example: "",
  demoResult: null,
  demoVersion: null,
  busy: false,
  runtimeState: "idle",
  runtimeErrorLine: null,
};
let parseTimer,
  playTimer,
  toastTimer,
  validationGeneration = 0,
  autoFit = true,
  runSerial = 0,
  editor = null,
  settingEditor = false,
  errorLine = null;
let graphRenderKey = null;
let localeChangeGeneration = 0;
const eventPageSize = 40;
let eventPage = 0, eventPageCursor = -1, eventPageResult = null, eventPageLot = null;
const browserRuntime = PUBLIC_DEMO
  ? new BrowserPythonRuntime({onState: (state, runtime) => renderRuntimeState(state, runtime)})
  : null;
const kinds = {
  get machine_state() { return tr("order_machine_state"); },
  get order_release() { return tr("order_actual_release"); },
  get arrival() { return tr("ui_1"); },
  get ready() { return tr("ui_2"); },
  get decision() { return tr("ui_3"); },
  get assigned() { return tr("ui_4"); },
  get route_preference() { return tr("ui_5"); },
  get move() { return tr("ui_6"); },
  get start() { return tr("ui_7"); },
  get finish() { return tr("ui_8"); },
  get complete() { return tr("ui_9"); },
  get blocked() { return tr("ui_10"); },
  get machine_state() { return tr("order_machine_state"); },
  get buffer_enter() { return tr("order_buffer_enter"); },
  get buffer_wait() { return tr("order_buffer_wait"); },
  get transport_arrive() { return tr("order_transport_arrive"); },
};
// One formatter covers every lot and machine state currently emitted by the
// trace contract. A future state remains visible and accessible without
// leaking JavaScript's `undefined` into an operational table.
const traceStateKeys = Object.freeze({
  waiting: "wip_waiting",
  reserved: "wip_reserved",
  moving: "wip_moving",
  processing: "wip_processing",
  completed: "wip_completed",
  release_pending: "wip_release_pending",
  setup: "wip_setup",
  down: "wip_down",
  maintenance: "wip_maintenance",
  blocked: "wip_blocked",
  starved: "ops_state_starved",
  offshift: "wip_offshift",
  resource_wait: "wip_resource_wait",
  idle: "ops_state_idle",
});
function traceStateLabel(state) {
  const key = Object.hasOwn(traceStateKeys, state) ? traceStateKeys[state] : null;
  return tr(key || "allocation_state_unknown");
}
function traceStateBadge(state) {
  const known = Object.hasOwn(traceStateKeys, state);
  const raw = typeof state === "string" && state ? state : tr("allocation_state_missing");
  const detail = known ? traceStateLabel(state) : tr("allocation_state_unknown_detail", [raw]);
  return `<span class="trace-state ${known ? `trace-state-${esc(state)}` : "trace-state-unknown"}" data-state-known="${known}" title="${esc(detail)}" aria-label="${esc(detail)}">${esc(traceStateLabel(state))}</span>`;
}
const tracePlacementKeys = Object.freeze({
  buffer: "allocation_placement_buffer",
  machine: "allocation_placement_machine",
  transport: "allocation_placement_transport",
  release: "allocation_placement_release",
});
function tracePlacementLabel(lot) {
  const placement = lot?.placement;
  if (!placement || typeof placement !== "object") return tr("allocation_placement_unavailable");
  const key = Object.hasOwn(tracePlacementKeys, placement.kind)
    ? tracePlacementKeys[placement.kind]
    : "allocation_placement_unknown";
  return `${tr(key)}${placement.id === undefined || placement.id === null || placement.id === "" ? "" : ` · ${placement.id}`}`;
}
function blockedReason(lot, cursor) {
  if (lot?.state !== "blocked") return "—";
  const event = (S.result?.events.slice(0, cursor) || []).findLast(e =>
    (e.affected_lot_id === lot.id || e.lot?.id === lot.id) &&
    (e.kind === "blocked" || e.kind === "buffer_wait" || e.lot?.state === "blocked") &&
    (e.reason || e.reason_message));
  return event ? localized(event.reason, event.reason_message) : tr("allocation_reason_unavailable");
}
async function api(path, body) {
  if (PUBLIC_DEMO) throw new Error(tr("public_scope"));
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", "X-Factory-Token": S.token },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await res.json();
  if (!res.ok) throw Object.assign(new Error(localized(data.error, data.error_message) || `HTTP ${res.status}`), {detail: data.error_message});
  return data;
}
function displayMessage(message) {
  return message?.code ? tr(message.code, message.args) : message || "";
}
function toast(message) {
  S.toastMessage = message;
  clearTimeout(toastTimer);
  $("#toast").textContent = displayMessage(message);
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 3500);
}
function status(message) {
  S.statusMessage = message;
  $("#status").textContent = displayMessage(message);
}
function renderRuntimeState(state, runtime) {
  S.runtimeState = state;
  const labels = {
    idle: "public_runtime_idle",
    loading: "public_runtime_loading",
    checking: "public_runtime_checking",
    ready: "public_runtime_ready",
    running: "public_runtime_running",
    stopped: "public_runtime_stopped",
    init_error: "public_init_error",
  };
  if (!S.job) $("#run-button").textContent = tr(state === "loading" ? "ui_145" : "ui_152");
  $("#runtime-status").textContent = runtime
    ? `${tr(labels[state] || "public_runtime_ready")} · Python ${runtime.python} · SimPy ${runtime.simpy}`
    : tr(labels[state] || "public_runtime_idle");
}
function markErrorLine(line) {
  if (editor && errorLine !== null) editor.removeLineClass(errorLine, "background", "code-error-line");
  errorLine = null;
  if (editor && Number.isInteger(Number(line)) && Number(line) > 0) {
    errorLine = Number(line) - 1;
    editor.addLineClass(errorLine, "background", "code-error-line");
  }
}
function diagnostics(error = "") {
  if (!error) S.parseErrorDetail = null;
  $("#sync-status").textContent = error
    ? tr("ui_11")
    : tr("ui_12");
  $("#sync-status").className = error ? "error" : "good";
  S.parseError = error;
  const line = S.runtimeErrorLine || (S.parseErrorDetail?.code === "message_22" ? Number(S.parseErrorDetail.args[0]) : null);
  markErrorLine(line);
  $("#diagnostic-count").textContent = (error ? 1 : 0) + S.warnings.length || "";
  if (S.tab === "console") renderTrace();
}
function persist() {
  try {
    localStorage.setItem(PUBLIC_DEMO ? "factory-studio.public.source.v1" : "factory-studio.source.v1", S.source);
  } catch {
    status({code: "ui_13"});
  }
}
function setSource(source) {
  S.source = source;
  $("#code").value = source;
  if (editor) {
    settingEditor = true;
    editor.replaceRange(
      source,
      { line: 0, ch: 0 },
      editor.posFromIndex(editor.getValue().length),
      "+model",
    );
    settingEditor = false;
  }
  S.revision++;
  updateEditor();
  persist();
}
function updateEditor() {
  if (editor) {
    const pos = editor.getCursor();
    $("#cursor-position").textContent = `Ln ${pos.line + 1}, Col ${pos.ch + 1}`;
    return;
  }
  $("#line-numbers").textContent = Array.from(
    { length: $("#code").value.split("\n").length },
    (_, i) => i + 1,
  ).join("\n");
  const value = $("#code").value.slice(0, $("#code").selectionStart).split("\n");
  $("#cursor-position").textContent = `Ln ${value.length}, Col ${value.at(-1).length + 1}`;
}
function invalidateTrace() {
  pause();
  S.result = null;
  S.cursor = 0;
  S.lot = null;
  if (["lot", "event"].includes(S.selected?.type)) {
    S.selected = null;
    $("#inspector").hidden = true;
  }
  $("#event-count").textContent = "0";
  renderPlayback();
  renderMetrics();
  renderTrace();
  renderGraph();
}
async function applyCode(silent = false, generation = validationGeneration) {
  clearTimeout(parseTimer);
  parseTimer = null;
  if (generation !== validationGeneration) return false;
  const revision = S.revision,
    source = S.source;
  try {
    const data = PUBLIC_DEMO ? await browserRuntime.parse(source) : await api("/api/parse", { source });
    if (generation !== validationGeneration || revision !== S.revision) return false;
    S.model = data.model;
    S.warnings = data.warnings;
    S.warningMessages = data.warning_messages || [];
    S.valid = true;
    S.runtimeErrorLine = null;
    diagnostics();
    renderTree();
    renderGraph();
    $("#project-name").textContent = S.model.name || tr("ui_14");
    $("#mode-label").textContent =
      S.model.mode === "pull" ? tr("ui_15") : tr("ui_16");
    if (!silent) status({code: "ui_17"});
    return true;
  } catch (e) {
    if (generation !== validationGeneration || revision !== S.revision) return false;
    S.valid = false;
    S.parseErrorDetail = e.code === "browser_init_error" ? {code: "public_init_error"} : e.code === "browser_init_timeout" ? {code: "public_init_timeout"} : e.detail || e.error_message;
    diagnostics(localized(e.message, S.parseErrorDetail));
    status({code: "ui_18"});
    if (!silent) {
      selectTab("console");
      toast(S.parseErrorDetail || e.message);
    }
    return false;
  }
}
async function mutate(change) {
  if (S.busy || S.job) throw new Error(tr("ui_19"));
  if (!(await applyCode(true)))
    throw new Error(tr("ui_20"));
  const revision = S.revision,
    model = copy(S.model);
  change(model);
  S.busy = true;
  try {
    const data = PUBLIC_DEMO ? await browserRuntime.sync(S.source, model) : await api("/api/sync", { source: S.source, model });
    if (revision !== S.revision)
      throw new Error(tr("ui_21"));
    invalidateTrace();
    setSource(data.source);
    S.model = data.model;
    S.warnings = data.warnings;
    S.warningMessages = data.warning_messages || [];
    S.valid = true;
    diagnostics();
    $("#dirty-dot").textContent = "●";
    $("#project-name").textContent = S.model.name;
    $("#mode-label").textContent =
      S.model.mode === "pull" ? tr("ui_15") : tr("ui_16");
    renderTree();
    renderGraph();
    status({code: "ui_22"});
  } finally {
    S.busy = false;
  }
}
function renderTree() {
  if (!S.model) return;
  $("#tree").innerHTML = S.model.processes
    .map((p) => {
      const machines = S.model.machines.filter((m) => m.process === p.id);
      const lines = [...new Set(machines.map((m) => m.line))];
      return `<div class="tree-process"><button data-process="${esc(p.id)}">⌄ &nbsp; ${esc(p.name)}<small>${machines.length}</small></button>${lines
        .map(
          (l) =>
            `<div class="tree-line">LINE ${esc(l)}</div>${machines
              .filter((m) => m.line === l)
              .map(
                (m) =>
                  `<button class="tree-machine ${S.selected?.id === m.id ? "selected" : ""}" data-machine="${esc(m.id)}">${esc(m.name)}</button>`,
              )
              .join("")}`,
        )
        .join("")}</div>`;
    })
    .join("");
  $$("[data-machine]").forEach((b) => (b.onclick = () => inspectMachine(b.dataset.machine)));
  $$("[data-process]").forEach((b) => (b.onclick = () => inspectProcess(b.dataset.process)));
}
// Replay uses event order, never timestamps; returned objects cannot mutate the trace.
function stateAt(cursor = S.cursor) {
  const lots = {}, machines = {};
  for (const m of S.result?.model.machines || []) machines[m.id] = {state: "idle", lot: null};
  for (const e of S.result?.events.slice(0, cursor) || []) {
    Object.assign(lots, copy(e.state_changes?.lots || (e.lot ? {[e.lot.id]: e.lot} : {})));
    Object.assign(machines, copy(e.state_changes?.machines || {}));
  }
  const queues = {};
  const reserved = new Set(Object.values(machines).filter(m => m.state !== "idle").map(m => m.lot));
  for (const id of ["INPUT", ...(S.result?.model.machines.map(m => m.id) || []), "OUTPUT"])
    queues[id] = Object.values(lots).filter(l => l.state === "waiting" && !reserved.has(l.id) && (l.target || l.location) === id)
      .sort((a,b) => a.ready_since - b.ready_since || a.id.localeCompare(b.id)).map(l => l.id);
  return {lots, machines, queues, time: cursor ? S.result.events[cursor - 1].time : 0};
}
function renderAllocationComparison() {
  const records = S.result.events.filter(e => ["decision", "blocked"].includes(e.kind));
  const current = records.find(e => e.index === S.comparison);
  const select = `<label>${tr("ui_23")} <select id="allocation-select" aria-label="${tr("ui_24")}"><option value="">${tr("ui_25")}</option>${records.map(e => `<option value="${e.index}" ${e === current ? "selected" : ""}>#${e.index} · ${fmt(e.time)}m · ${esc(e.allocation_id || (e.kind === "blocked" ? tr("ui_10") : e.outcome === "route_preference" ? tr("ui_26") : tr("ui_27")))} · ${esc(e.lot?.id || "—")}</option>`).join("")}</select></label>`;
  const container = $("#trace-content");
  container.innerHTML = `<div class="allocation-comparison">${select}<div id="allocation-detail"></div></div>`;
  $("#allocation-select").onchange = e => {
    if (e.target.value !== "") { pause(); seek(Number(e.target.value) + 1); }
  };
  if (!current) { $("#allocation-detail").textContent = tr("ui_28"); return; }
  const allocation = S.result.allocations.find(a => a.id === current.allocation_id);
  const beforeCursor = allocation?.before_cursor ?? current.index;
  const afterCursor = allocation?.after_cursor ?? current.index + (current.outcome === "route_preference" ? 2 : 1);
  const before = stateAt(beforeCursor), after = stateAt(afterCursor);
  function panel(state, other, title, cursor) {
    const row = (value, previous, content) => `<tr class="${JSON.stringify(value) !== JSON.stringify(previous) ? "state-changed" : ""}">${content}</tr>`;
    return `<section class="allocation-state"><h3>${title} ${tr("ui_33")} ${cursor}</h3><h4>${tr("ui_34")}</h4><table><thead><tr><th>${tr("ui_35")}</th><th>${tr("ui_36")}</th><th>${tr("allocation_physical")}</th><th>${tr("ui_37")}</th><th>${tr("ui_38")}</th><th>${tr("allocation_block_reason")}</th></tr></thead><tbody>${Object.values(state.lots).map(l => row(l, other.lots[l.id], `<td>${esc(l.id)}</td><td>${esc(l.location)}</td><td>${esc(tracePlacementLabel(l))}</td><td>${traceStateBadge(l.state)}</td><td>${esc(l.target || tr("ui_39"))}</td><td>${esc(blockedReason(l, cursor))}</td>`)).join("")}</tbody></table><h4>${tr("ui_40")}</h4><table><thead><tr><th>${tr("ui_40")}</th><th>${tr("ui_37")}</th><th>${tr("ui_35")}</th></tr></thead><tbody>${Object.entries(state.machines).map(([id,m]) => row(m, other.machines[id], `<td>${esc(id)}</td><td>${traceStateBadge(m.state)}</td><td>${esc(m.lot || "—")}</td>`)).join("")}</tbody></table><h4>${tr("ui_41")}</h4><p>${tr("ui_42")}</p><table><thead><tr><th>${tr("ui_43")}</th><th>${tr("ui_44")}</th><th>${tr("ui_45")}</th></tr></thead><tbody>${Object.entries(state.queues).map(([id,q]) => row(q, other.queues[id], `<td>${esc(id)}</td><td>${q.length}</td><td>${q.map(esc).join(" → ") || "—"}</td>`)).join("")}</tbody></table></section>`;
  }
  $("#allocation-detail").innerHTML = `<h3>${esc(allocation?.id || tr("ui_46"))} · ${esc(allocation?.destination || (current.outcome === "route_preference" ? tr("ui_26") : tr("ui_47")))}</h3><p>${tr("ui_48")} ${esc(localized(current.reason, current.reason_message))} ${tr("ui_49")} ${S.cursor}</p><p>${tr("ui_50")}</p><button id="allocation-before">${tr("ui_51")}</button> <button id="allocation-after">${tr("ui_52")}</button><div class="allocation-pair">${panel(before, after, tr("ui_53"), beforeCursor)}${panel(after, before, allocation ? tr("ui_54") : tr("ui_55"), afterCursor)}</div><h3>${tr("ui_56")}</h3>${(current.candidates || []).map(c => `<div class="info-card ${c.id === current.chosen ? "selected" : ""}"><b>${c.id === current.chosen ? tr("ui_57") : tr("ui_58")} · ${esc(c.lot_id)} / ${esc(c.route_id)}</b>${esc(c.from)} → ${esc(c.to)} ${tr("ui_59")} ${c.priority} ${tr("ui_60")} ${c.queue_length}</div>`).join("")}${(current.checks || []).filter(c => !c.eligible).map(c => `<div class="info-card rejected">${tr("ui_61")} ${esc(c.lot_id)} / ${esc(c.route_id)} · ${esc(localized(c.reason, c.reason_message))}</div>`).join("")}`;
  $("#allocation-before").onclick = () => {pause(); seek(beforeCursor, true);};
  $("#allocation-after").onclick = () => {pause(); seek(afterCursor, true);};
}
function graphLayout() {
  const positions = { INPUT: { x: 20, y: 157, w: 52, h: 42 } },
    groups = [];
  const lines = [...new Set(S.model.machines.map((m) => m.line))];
  const rows = new Map();
  let y = 78;
  for (const line of lines) {
    const max = Math.max(
      1,
      ...S.model.processes.map(
        (p) => S.model.machines.filter((m) => m.process === p.id && m.line === line).length,
      ),
    );
    rows.set(line, { y, height: max * 96 + 20 });
    y += max * 96 + 35;
  }
  S.model.processes.forEach((p, i) => {
    const x = 103 + i * 195;
    groups.push({ p, x });
    for (const line of lines) {
      const ms = S.model.machines.filter((m) => m.process === p.id && m.line === line);
      ms.forEach(
        (m, j) =>
          (positions[m.id] = { x: x + 16, y: rows.get(line).y + 19 + j * 96, w: 133, h: 76 }),
      );
    }
  });
  const width = 103 + S.model.processes.length * 195 + 71,
    height = Math.max(335, y + 20);
  positions.INPUT.y = height / 2 - 21;
  positions.OUTPUT = { x: width - 63, y: height / 2 - 21, w: 52, h: 42 };
  return { positions, groups, rows, width, height };
}
function graphProjection() {
  const finalView = S.tab === "wip" ? WIP.finalView : S.tab === "operations" ? S.operationScope !== "cursor" : S.tab === "orders" ? PLANNER.finalView : false;
  return graphStateProjection(S.result, S.cursor, finalView, S.model);
}
function graphCause(op) {
  if (op.bufferId !== null && op.bufferId !== undefined) return tr("graph_buffer_full", [op.bufferId]);
  return op.cause ? operationCause(op.cause) : tr("allocation_reason_unavailable");
}
function graphLotDescription(id, op, projection) {
  if (!op.lot) return tr("graph_no_lot");
  const place = projection.lots[op.lot]?.placement;
  return tr(place?.kind === "machine" && place.id === id ? "graph_held_lot" : "graph_assigned_lot", [op.lot]);
}
function graphMachineDescription(machine, op, projection) {
  return [machine.name, traceStateLabel(op.state), tr(projection.finalView ? "wip_final" : "wip_replay"),
    `${fmt(projection.time)} min`, graphLotDescription(machine.id, op, projection), graphCause(op),
    op.buffer ? tr("graph_buffer_occupancy", [op.buffer.id, op.buffer.contents.length, op.buffer.capacity ?? "∞"]) : "", tr("ui_65")].filter(Boolean).join(" · ");
}
function graphShowInventory(projection, lotId, bufferId) {
  pause(); WIP.finalView = projection.finalView; WIP.filters = lotId ? {search:lotId} : {location:bufferId};
  WIP.selectedLot = lotId; WIP.selectedGroup = null; WIP.groupBy = "location";
  S.selected = null; $("#inspector").hidden = true;
  seek(projection.cursor, true); selectTab("wip");
  const target = $(lotId ? "#wip-detail" : "#wip-groups");
  if (target) { target.tabIndex = -1; target.focus(); target.scrollIntoView({block:"nearest"}); }
}
function graphShowEvent(index) {
  pause(); WIP.finalView = false; PLANNER.finalView = false; S.operationScope = "cursor";
  selectTab("events"); inspectEvent(index);
  $("#inspector-content").tabIndex = -1; $("#inspector-content").focus();
}
function renderGraphOperationDetail(projection = graphProjection()) {
  const host = $("#graph-operation-detail");
  if (!host || S.selected?.type !== "machine") return;
  const op = projection.machines[S.selected.id];
  if (!op) { host.textContent = tr("allocation_state_unknown"); return; }
  const focusId = host.contains(document.activeElement) ? document.activeElement.id : null;
  host.tabIndex = -1; host.setAttribute("aria-label", tr("graph_operation_detail"));
  const eventButton = (id, index, label) => index === null ? "" : `<button type="button" id="${id}" data-graph-event="${index}">${tr(label)}</button>`;
  host.innerHTML = `<h4>${tr("graph_operation_detail")}</h4><p id="graph-detail-scope">${tr(projection.finalView ? "wip_final" : "wip_replay")} · ${fmt(projection.time)} min · ${tr("wip_cursor")} ${projection.cursor}</p>
    <p>${traceStateBadge(op.state)} · ${tr("ops_cause")}: ${esc(graphCause(op))}</p>
    ${op.inferred ? `<p>${tr("graph_legacy_state")}</p>` : ""}
    <p>${esc(graphLotDescription(S.selected.id, op, projection))}</p>
    <div class="graph-evidence-actions">${op.lot ? `<button type="button" id="graph-held-lot">${tr("graph_open_lot")}</button>` : ""}${eventButton("graph-state-event", op.event, "graph_state_event")}${eventButton("graph-lot-event", op.lotEvent, "graph_lot_event")}</div>
    ${op.buffer ? `<p id="graph-buffer-occupancy">${esc(tr("graph_buffer_occupancy", [op.buffer.id, op.buffer.contents.length, op.buffer.capacity ?? "∞"]))}</p><p>${tr("graph_buffer_lots")}: ${esc(op.buffer.contents.join(", ") || "—")}</p><div class="graph-evidence-actions"><button type="button" id="graph-open-buffer">${tr("graph_open_buffer")}</button>${eventButton("graph-buffer-event", op.bufferEvent, "graph_buffer_event")}</div>` : op.bufferId ? `<p>${tr("graph_buffer_unavailable", [esc(op.bufferId)])}</p>` : ""}`;
  if (op.lot) $("#graph-held-lot").onclick = () => graphShowInventory(projection, op.lot, null);
  if (op.buffer) $("#graph-open-buffer").onclick = () => graphShowInventory(projection, null, op.buffer.id);
  host.querySelectorAll("[data-graph-event]").forEach(button => button.onclick = () => graphShowEvent(Number(button.dataset.graphEvent)));
  if (focusId && document.getElementById(focusId)) document.getElementById(focusId).focus({preventScroll:true});
}

function renderGraph(inventoryView = null, graphView = null) {
  if (!S.model) return;
  const viewport = $("#graph-viewport"),
    finalView = S.tab === "wip" ? WIP.finalView : S.tab === "operations" ? S.operationScope !== "cursor" : S.tab === "orders" ? PLANNER.finalView : false,
    renderKey = [S.model, S.result, S.cursor, finalView, S.tab === "wip", S.lot,
      S.selected?.type, S.selected?.id, WIP.selectedLot, WIP.selectedGroup, locale,
      autoFit, autoFit ? viewport.clientWidth : S.zoom];
  if (graphRenderKey && renderKey.every((value, index) => graphRenderKey[index] === value)) {
    if ($("#graph-operation-detail:empty")) renderGraphOperationDetail(graphView || graphProjection());
    return;
  }
  const { positions, groups, rows, width, height } = graphLayout();
  if (autoFit)
    S.zoom = Math.min(1.15, Math.max(0.38, (viewport.clientWidth - 12) / width));
  const projection = graphView || graphProjection(),
    { lots, machines } = projection,
    active = S.result?.events[projection.cursor - 1];
  const routeHistory = new Set(), terminalCounts = {INPUT: 0, OUTPUT: 0}, waitingCounts = new Map();
  if (S.lot) for (let index = 0; index < projection.cursor; index++) {
    const event = S.result.events[index];
    if (event.lot?.id === S.lot && event.kind === "move") routeHistory.add(event.route);
  }
  for (const lot of Object.values(lots)) {
    if (Object.hasOwn(terminalCounts, lot.location)) terminalCounts[lot.location]++;
    if (lot.state === "waiting") waitingCounts.set(lot.location, (waitingCounts.get(lot.location) || 0) + 1);
  }
  let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width * S.zoom}" height="${height * S.zoom}" viewBox="0 0 ${width} ${height}" role="group" aria-label="${tr("ui_62")}"><defs><marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0 0 L6 3 L0 6" fill="#a6bca6"/></marker><marker id="arrow-active" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0 0 L6 3 L0 6" fill="#32855c"/></marker></defs>`;
  for (const { p, x } of groups) {
    svg += `<rect x="${x}" y="21" width="165" height="${height - 40}" rx="11" fill="#f7faf5" fill-opacity=".85" stroke="#e7eee2"/><text x="${x + 15}" y="47" class="process-heading">${esc(p.name)}</text><text x="${x + 143}" y="47" class="process-number">${String(groups.findIndex((g) => g.p === p) + 1).padStart(2, "0")}</text>`;
    for (const [line, row] of rows)
      svg += `<text x="${x + 17}" y="${row.y + 8}" class="line-label">LINE ${esc(line)}</text>`;
  }
  for (const r of S.model.routes) {
    const a = positions[r.from],
      b = positions[r.to];
    if (!a || !b) continue;
    const x1 = a.x + a.w,
      y1 = a.y + a.h / 2,
      x2 = b.x,
      y2 = b.y + b.h / 2;
    const backwards = x2 <= x1;
    const d = backwards
      ? `M${x1} ${y1} C${x1 + 55} ${y1 - 60},${x2 - 55} ${y2 - 60},${x2} ${y2}`
      : `M${x1} ${y1} C${x1 + (x2 - x1) * 0.48} ${y1},${x2 - (x2 - x1) * 0.48} ${y2},${x2} ${y2}`;
    const ma = S.model.machines.find((m) => m.id === r.from),
      mb = S.model.machines.find((m) => m.id === r.to);
    const cross = ma && mb && ma.line !== mb.line;
    const current = active?.route === r.id;
    const highlight =
      routeHistory.has(r.id) || (S.selected?.type === "route" && S.selected.id === r.id);
    svg += `<g class="route-group" data-route="${esc(r.id)}" tabindex="0" role="button" aria-label="${tr("ui_63")} ${esc(r.from)} → ${esc(r.to)}"><title>${esc(r.from)} → ${esc(r.to)} ${tr("ui_59")} ${r.priority} · ${r.delay} ${tr("ui_64")} ${esc(r.product)}</title><path class="route-hit" d="${d}"/><path class="route-path ${cross ? "cross" : ""} ${!r.enabled ? "disabled" : ""} ${highlight ? "highlight" : ""} ${current ? "current" : ""}" d="${d}" marker-end="url(#${highlight ? "arrow-active" : "arrow"})"/>${current ? `<circle r="4" fill="#d7a24f"><animateMotion dur="1.8s" repeatCount="indefinite" path="${d}"/></circle>` : ""}</g>`;
  }
  for (const terminal of ["INPUT", "OUTPUT"]) {
    const p = positions[terminal],
      count = terminalCounts[terminal];
    svg += `<rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="8" class="graph-terminal" data-terminal="${terminal}"/><text x="${p.x + p.w / 2}" y="${p.y + 17}" text-anchor="middle" class="terminal-text">${terminal}</text><text x="${p.x + p.w / 2}" y="${p.y + 31}" text-anchor="middle" class="terminal-text">${count} lots</text>`;
  }
  for (const m of S.model.machines) {
    const p = positions[m.id],
      state = machines[m.id],
      waiting = waitingCounts.get(m.id) || 0;
    const selected = S.selected?.id === m.id;
    const stateLabel = traceStateLabel(state.state), detail = graphMachineDescription(m, state, projection);
    svg += `<g class="machine-node ${esc(state?.state || "")} ${selected ? "selected" : ""}" data-node="${esc(m.id)}" tabindex="0" role="button" aria-label="${esc(detail)}"><title>${esc(detail)}</title><rect class="node-bg" x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="7"/><rect x="${p.x + 10}" y="${p.y + 11}" width="20" height="20" rx="5" fill="${state?.state === "processing" ? "#d7ebda" : "#eff3ea"}"/><path d="M${p.x + 15} ${p.y + 25}v-8h4v4h5v4z" fill="none" stroke="#719069" stroke-width="1.2"/><text x="${p.x + 37}" y="${p.y + 21}" class="node-name">${esc(m.name.length > 10 ? m.name.slice(0, 9) + "…" : m.name)}</text><text x="${p.x + 37}" y="${p.y + 33}" class="node-meta">${esc(m.id)} · ${m.time}m</text><line x1="${p.x + 10}" y1="${p.y + 41}" x2="${p.x + p.w - 10}" y2="${p.y + 41}" stroke="#edf2e8"/><circle cx="${p.x + 13}" cy="${p.y + 51}" r="2.4" fill="${state?.state === "processing" ? "#51a277" : ["reserved", "setup", "resource_wait", "blocked"].includes(state.state) ? "#d5a45a" : ["down", "maintenance", "offshift"].includes(state.state) ? "#80677a" : "#b6c5b0"}"/><text x="${p.x + 21}" y="${p.y + 54}" class="node-status">${esc(stateLabel)}</text><text x="${p.x + 10}" y="${p.y + 68}" class="node-meta">${esc(state.lot ? (state.lot.length > 14 ? state.lot.slice(0, 13) + "…" : state.lot) : "—")}</text><text x="${p.x + p.w - 10}" y="${p.y + 68}" text-anchor="end" class="node-meta">${tr("ui_2")} ${waiting}</text></g>`;
  }
  svg += "</svg>";
  $("#graph").innerHTML = svg;
  $("#zoom-label").textContent = Math.round(S.zoom * 100) + "%";
  $("#graph-scope").textContent = `${tr(projection.finalView ? "wip_final" : "wip_replay")} · ${fmt(projection.time)} min`;
  highlightWipGraph(inventoryView);
  renderGraphOperationDetail(projection);
  $$("[data-node]").forEach((g) => {
    g.onclick = () => { pause(); inspectMachine(g.dataset.node); $("#graph-operation-detail")?.focus({preventScroll:true}); };
    g.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); g.onclick(); }
    };
  });
  $$("[data-route]").forEach((g) => {
    g.onclick = () => inspectRoute(g.dataset.route);
    g.onkeydown = (e) => {
      if (e.key === "Enter") g.onclick();
    };
  });
  graphRenderKey = renderKey;
}
function renderMetrics(snapshot = stateAt()) {
  const { lots, time } = snapshot,
    all = Object.values(lots),
    done = all.filter((l) => l.state === "completed");
  $("#metric-time").innerHTML = `${fmt(time)} <small>min</small>`;
  $("#metric-complete").innerHTML = `${done.length} <small>/ ${all.length}</small>`;
  $("#metric-wip").textContent = all.length - done.length;
  $("#metric-cycle").innerHTML =
    `${done.length ? fmt(done.reduce((sum, l) => sum + l.completed - l.created, 0) / done.length) : "—"} <small>min</small>`;
}
function renderPlayback(snapshot = stateAt()) {
  const count = S.result?.events.length || 0;
  $("#timeline").max = count;
  $("#timeline").value = S.cursor;
  $("#timeline-time").textContent = fmt(snapshot.time) + " min";
  $("#timeline-end").textContent = `${S.cursor} / ${count}`;
  $("#play-button").textContent = S.playing ? "Ⅱ" : "▶";
  for (const id of ["play-button", "step-button", "rewind-button", "export-trace"])
    $("#" + id).disabled = !count;
  $("#event-count").textContent = count;
}
function selectTab(tab) {
  S.tab = tab;
  const inventoryView = tab === "wip" && S.result ? wipProjection() : null;
  renderGraph(inventoryView);
  $$("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  renderTrace(inventoryView);
}
function renderTrace(inventoryView = null) {
  const container = $("#trace-content");
  container.dataset.renderedTab = S.tab;
  if (S.tab === "console") {
    container.innerHTML = `<pre class="console ${S.parseError ? "error" : ""}">${esc([S.parseError, ...S.warnings.map((w,i) => localized(w, S.warningMessages?.[i])), S.console, displayMessage(S.runtimeError)].filter(Boolean).join("\n\n") || tr("ui_67"))}</pre>`;
    return;
  }
  if (S.tab === "experiments") { renderExperiments(); return; }
  if (S.tab === "scenarios") { renderScenarios(); return; }
  if (S.tab === "data") { renderDataPanel(); return; }
  if (S.tab === "orders") { renderPlanner(); return; }
  if (S.tab === "operations") { renderOperations(); return; }
  if (!S.result) {
    container.innerHTML =
      `<div class="empty"><b>${tr("ui_68")}</b>${tr("ui_69")}<br>${tr("ui_70")}</div>`;
    return;
  }
  if (S.tab === "wip") { renderInventory(inventoryView); return; }
  if (S.tab === "results") { renderAllocationResults(); return; }
  if (S.tab === "allocations") { renderAllocationComparison(); return; }
  const events = S.result.events.slice(0, S.cursor);
  if (S.tab === "utilization") {
    const time = stateAt().time,
      spans = {},
      started = {};
    for (const e of events) {
      if (e.kind === "start") started[e.machine] = e.time;
      if (e.kind === "finish" && started[e.machine] !== undefined) {
        spans[e.machine] = (spans[e.machine] || 0) + e.time - started[e.machine];
        delete started[e.machine];
      }
    }
    for (const [id, t] of Object.entries(started)) spans[id] = (spans[id] || 0) + time - t;
    container.innerHTML =
      S.model.machines
        .map((m) => {
          const value = time ? ((spans[m.id] || 0) / time) * 100 : 0;
          return `<div class="util-row"><span>${esc(m.name)}</span><div class="util-track"><div style="width:${value}%"></div></div><span>${fmt(value)}%</span></div>`;
        })
        .join("") +
      `<p class="inline-help" style="padding:0 18px">${tr("ui_71")}</p>`;
    return;
  }
  if (S.tab === "lots") {
    const lots = Object.values(stateAt().lots);
    container.innerHTML = `<table><thead><tr><th>${tr("lot_id")}</th><th>${tr("ui_72")}</th><th>${tr("ui_37")}</th><th>${tr("ui_73")}</th><th>${tr("ui_74")}</th></tr></thead><tbody>${lots.map((l) => `<tr class="clickable ${S.lot === l.id ? "selected-lot" : ""}" data-lot="${esc(l.id)}"><td class="mono">${esc(l.id)}</td><td>${esc(l.product)}</td><td>${esc({ waiting: tr("ui_2"), moving: tr("ui_6"), processing: tr("ui_66"), completed: tr("ui_9") }[l.state])}</td><td>${esc(l.location)}${l.target && l.target !== l.location ? " → " + esc(l.target) : ""}</td><td>${fmt((l.completed ?? stateAt().time) - l.created)} min</td></tr>`).join("")}</tbody></table>`;
    $$("[data-lot]").forEach((r) => (r.onclick = () => inspectLot(r.dataset.lot)));
    return;
  }
  const matching = events.filter((e) => !S.lot || e.lot?.id === S.lot);
  if (eventPageCursor !== S.cursor || eventPageResult !== S.result || eventPageLot !== S.lot) {
    eventPage = 0; eventPageCursor = S.cursor; eventPageResult = S.result; eventPageLot = S.lot;
  }
  const pageCount = Math.max(1, Math.ceil(matching.length / eventPageSize));
  eventPage = Math.min(eventPage, pageCount - 1);
  const newerOffset = eventPage * eventPageSize,
    pageEnd = matching.length - newerOffset,
    pageStart = Math.max(0, pageEnd - eventPageSize),
    filtered = matching.slice(pageStart, pageEnd).reverse(),
    pager = pageCount > 1 ? `<nav class="event-pages" aria-label="${tr("event_page_label")}"><button id="event-newer" ${eventPage === 0 ? "disabled" : ""}>${tr("event_page_newer")}</button><span>${tr("event_page_status", [eventPage + 1, pageCount])}</span><button id="event-older" ${eventPage === pageCount - 1 ? "disabled" : ""}>${tr("event_page_older")}</button></nav>` : "";
  container.innerHTML =
    (S.lot
      ? `<div class="lot-filter">${esc(S.lot)} ${tr("ui_75")}<button id="clear-lot">${tr("ui_76")}</button></div>`
      : "") +
    `${pager}<table><thead><tr><th>${tr("event_time")}</th><th>${tr("event_lot")}</th><th>${tr("event_kind")}</th><th>${tr("event_location")}</th><th>${tr("event_detail")}</th></tr></thead><tbody>${filtered.map((e) => `<tr class="clickable ${e.index === S.cursor - 1 ? "current" : ""}" data-event="${e.index}"><td class="mono">${fmt(e.time)}</td><td class="mono">${esc(e.lot?.id || "—")}</td><td><span class="kind ${e.kind}">${kinds[e.kind] || esc(e.kind)}</span></td><td>${esc(e.machine || e.lot?.location || "—")}</td><td>${esc(e.kind === "decision" ? localized(e.reason, e.reason_message) : e.kind === "move" ? `${e.lot.location} → ${e.lot.target}` : e.kind === "start" ? (e.duration === null ? tr("ui_77") : `${tr("ui_78")} ${fmt(e.duration)} min`) : localized(e.reason, e.reason_message) || e.route || "—")}</td></tr>`).join("")}</tbody></table>`;
  $$("[data-event]").forEach((r) => (r.onclick = () => inspectEvent(Number(r.dataset.event))));
  if ($("#event-newer")) $("#event-newer").onclick = () => { eventPage--; renderTrace(); };
  if ($("#event-older")) $("#event-older").onclick = () => { eventPage++; renderTrace(); };
  if ($("#clear-lot"))
    $("#clear-lot").onclick = () => {
      S.lot = null;
      closeInspector();
      renderTrace();
      renderGraph();
    };
}
function seek(cursor, preserveComparison = false) {
  S.cursor = Math.max(0, Math.min(S.result?.events.length || 0, cursor));
  if (!preserveComparison) {
    const event = S.result?.events[S.cursor - 1];
    const allocation = S.result?.allocations?.find(a => a.id === event?.allocation_id);
    S.comparison = event?.kind === "decision" || event?.kind === "blocked" ? event.index : allocation?.decision_index ?? S.result?.events.slice(0, S.cursor).findLast(e => ["decision", "blocked"].includes(e.kind))?.index;
  }
  scheduleReplayPersistence();
  const graphView = graphProjection(), inventoryView = S.tab === "wip" ? wipProjection() : null;
  renderPlayback(graphView);
  renderMetrics(graphView);
  renderGraph(inventoryView, graphView);
  renderTrace(inventoryView);
  if (S.selected?.type === "lot") inspectLot(S.selected.id, false);
}
function pause() {
  S.playing = false;
  clearTimeout(playTimer);
  $("#play-button").textContent = "▶";
}
function play() {
  if (!S.result) return;
  if (S.playing) {
    pause();
    return;
  }
  if (S.cursor >= S.result.events.length) seek(0);
  S.playing = true;
  renderPlayback();
  tick();
}
function tick() {
  if (!S.playing) return;
  const speed = Number($("#speed").value),
    old = S.cursor;
  let next = Math.min(S.result.events.length, old + speed),
    stop = false;
  if ($("#break-decision").checked) {
    for (let i = old; i < next; i++)
      if (S.result.events[i].kind === "decision") {
        next = i + 1;
        stop = true;
        break;
      }
  }
  seek(next);
  if (stop) {
    pause();
    inspectEvent(next - 1);
    status({code: "ui_79"});
  } else if (next === S.result.events.length) {
    pause();
    status({code: "ui_80"});
  } else playTimer = setTimeout(tick, 240);
}
function openInspector(title, html) {
  $("#inspector-title").textContent = title;
  $("#inspector-content").innerHTML = html;
  $("#inspector").hidden = false;
}
function closeInspector() {
  $("#inspector").hidden = true;
  S.selected = null;
  renderTree();
  renderGraph();
}
function field(label, name, value, type = "text", extra = "") {
  return `<label class="field"><span>${esc(label)}</span><input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;
}
function selectField(label, name, value, options) {
  return `<label class="field"><span>${esc(label)}</span><select name="${name}">${options.map((o) => `<option value="${esc(o[0])}" ${o[0] === value ? "selected" : ""}>${esc(o[1])}</option>`).join("")}</select></label>`;
}
function actions(deletable = false) {
  return `<div id="form-error" class="form-error" role="alert"></div><div class="form-actions">${deletable ? `<button type="button" id="delete-item" class="danger">${tr("ui_81")}</button>` : ""}<button class="primary" type="submit">${tr("ui_82")}</button></div>`;
}
function bindForm(handler) {
  $("#property-form").onsubmit = async (e) => {
    e.preventDefault();
    const button = e.submitter;
    button.disabled = true;
    try {
      await handler(new FormData(e.target));
      toast({code: "ui_83"});
    } catch (err) {
      if ($("#form-error")) $("#form-error").textContent = err.message;
      else toast(err.detail || err.message);
    } finally {
      button.disabled = false;
    }
  };
}
function locate(id) {
  const code = $("#code"),
    index = S.source.indexOf(`'id': '${id}'`);
  if (index < 0) return;
  if (editor) {
    editor.focus();
    const pos = editor.posFromIndex(index);
    editor.setSelection(pos, editor.posFromIndex(index + id.length + 8));
    editor.scrollIntoView(pos, 80);
    return;
  }
  code.focus();
  code.setSelectionRange(index, index + id.length + 8);
  code.scrollTop = (S.source.slice(0, index).split("\n").length - 4) * 21;
  $("#line-numbers").scrollTop = code.scrollTop;
  updateEditor();
}
function inspectMachine(id, newMachine = false) {
  if (PUBLIC_DEMO && newMachine) return toast({code: "public_scope"});
  const m = newMachine
    ? { id, name: tr("ui_84"), process: S.model.processes[0].id, line: "A", time: 5 }
    : S.model.machines.find((m) => m.id === id);
  if (!m) return;
  S.selected = { type: "machine", id };
  openInspector(
    newMachine ? tr("ui_85") : tr("ui_86"),
    `<h3>${esc(m.name)}</h3>${newMachine ? "" : '<section id="graph-operation-detail"></section>'}<p>${tr("ui_87")}</p><form id="property-form">${field("ID", "id", m.id, "text", newMachine ? 'required pattern="[A-Za-z][A-Za-z0-9_-]*"' : "readonly")}${field(tr("ui_88"), "name", m.name, "text", "required")}${selectField(
      tr("ui_89"),
      "process",
      m.process,
      S.model.processes.map((p) => [p.id, p.name]),
    )}${field(tr("ui_90"), "line", m.line, "text", "required")}${field(tr("ui_91"), "time", m.time, "number", 'min="0.01" step="any" required')}${actions(!newMachine && !PUBLIC_DEMO)}</form>${!newMachine ? `<button id="locate-code" class="quiet" style="margin-top:15px">${tr("ui_92")}</button>` : ""}`,
  );
  bindForm(async (f) => {
    const machine = {
      id: f.get("id"),
      name: f.get("name"),
      process: f.get("process"),
      line: f.get("line"),
      time: Number(f.get("time")),
    };
    await mutate((model) => {
      if (newMachine) model.machines.push(machine);
      else
        Object.assign(
          model.machines.find((x) => x.id === id),
          machine,
        );
    });
    inspectMachine(machine.id);
  });
  if (!newMachine) {
    $("#locate-code").onclick = () => locate(id);
    if (!PUBLIC_DEMO) $("#delete-item").onclick = async () => {
      try {
        await mutate((model) => {
          model.machines = model.machines.filter((m) => m.id !== id);
          model.routes = model.routes.filter((r) => r.from !== id && r.to !== id);
        });
        closeInspector();
        toast({code: "ui_93"});
      } catch (e) {
        $("#form-error").textContent = e.message;
      }
    };
  }
  renderTree();
  renderGraph();
}
function inspectRoute(id, newRoute = false) {
  if (PUBLIC_DEMO) return toast({code: "public_scope"});
  const r = newRoute
    ? {
        id,
        from: S.model.machines[0].id,
        to: "OUTPUT",
        priority: 0,
        delay: 1,
        product: "*",
        enabled: true,
      }
    : S.model.routes.find((r) => r.id === id);
  if (!r) return;
  S.selected = { type: "route", id };
  const machines = S.model.machines.map((m) => [m.id, `${m.name} (${m.id})`]);
  openInspector(
    newRoute ? tr("ui_94") : tr("ui_95"),
    `<span class="info-tag">${tr("routing_rule")}</span><h3 style="margin-top:12px">${esc(r.from)} → ${esc(r.to)}</h3><p>${tr("ui_96")}</p><form id="property-form">${field("ID", "id", r.id, "text", newRoute ? "required" : "readonly")}${selectField(tr("ui_97"), "from", r.from, [["INPUT", tr("ui_98")], ...machines])}${selectField(tr("ui_99"), "to", r.to, [...machines, ["OUTPUT", tr("ui_100")]])}${field(tr("ui_101"), "priority", r.priority, "number", 'min="0" max="1000" required')}${field(tr("ui_102"), "delay", r.delay, "number", 'min="0" step="any" required')}${field(tr("ui_103"), "product", r.product, "text", "required")}<label class="field"><span>${tr("ui_104")}</span><input name="enabled" type="checkbox" ${r.enabled ? "checked" : ""}></label>${actions(!newRoute)}</form>`,
  );
  bindForm(async (f) => {
    const route = {
      id: f.get("id"),
      from: f.get("from"),
      to: f.get("to"),
      priority: Number(f.get("priority")),
      delay: Number(f.get("delay")),
      product: f.get("product"),
      enabled: f.get("enabled") === "on",
    };
    await mutate((model) => {
      if (newRoute) model.routes.push(route);
      else
        Object.assign(
          model.routes.find((x) => x.id === id),
          route,
        );
    });
    inspectRoute(route.id);
  });
  if (!newRoute)
    $("#delete-item").onclick = async () => {
      try {
        await mutate((m) => {
          m.routes = m.routes.filter((r) => r.id !== id);
        });
        closeInspector();
      } catch (e) {
        $("#form-error").textContent = e.message;
      }
    };
  renderGraph();
}
function inspectProcess(id, newProcess = false) {
  if (PUBLIC_DEMO) return toast({code: "public_scope"});
  const p = newProcess ? { id, name: tr("ui_105") } : S.model.processes.find((p) => p.id === id);
  S.selected = { type: "process", id };
  openInspector(
    newProcess ? tr("ui_106") : tr("ui_107"),
    `<p>${tr("ui_108")}</p><form id="property-form">${field("ID", "id", p.id, "text", newProcess ? "required" : "readonly")}${field(tr("ui_88"), "name", p.name, "text", "required")}${actions(!newProcess)}</form>`,
  );
  bindForm(async (f) => {
    await mutate((m) => {
      if (newProcess) m.processes.push({ id: f.get("id"), name: f.get("name") });
      else m.processes.find((p) => p.id === id).name = f.get("name");
    });
    closeInspector();
  });
  if (!newProcess)
    $("#delete-item").onclick = async () => {
      try {
        await mutate((m) => {
          if (m.machines.some((x) => x.process === id))
            throw new Error(tr("ui_109"));
          m.processes = m.processes.filter((p) => p.id !== id);
        });
        closeInspector();
      } catch (e) {
        $("#form-error").textContent = e.message;
      }
    };
}
function inspectSettings() {
  if (PUBLIC_DEMO) return toast({code: "public_scope"});
  const m = S.model;
  if (!m) return;
  S.selected = { type: "settings" };
  openInspector(
    tr("ui_110"),
    `<form id="property-form">${field(tr("ui_111"), "name", m.name, "text", "required")}${selectField(
      tr("ui_112"),
      "mode",
      m.mode,
      [
        ["pull", tr("ui_113")],
        ["push", tr("ui_114")],
      ],
    )}<p>${tr("ui_115")}</p>${field(tr("ui_116"), "duration", m.duration, "number", 'min="0.01" max="100000" step="any" required')}${field(tr("ui_117"), "seed", m.seed, "number", 'min="0" max="4294967295" step="1" required')}${field(tr("ui_118"), "count", m.source.count, "number", 'min="1" max="2000" step="1" required')}${field(tr("ui_119"), "interval", m.source.interval, "number", 'min="0.01" step="any" required')}${field(tr("ui_120"), "products", m.source.products.join(", "), "text", "required")}${actions()}</form>`,
  );
  bindForm(async (f) => {
    await mutate((m) => {
      m.name = f.get("name");
      m.mode = f.get("mode");
      for (const k of ["duration", "seed"]) m[k] = Number(f.get(k));
      m.source = {
        count: Number(f.get("count")),
        interval: Number(f.get("interval")),
        products: f
          .get("products")
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      };
    });
    closeInspector();
  });
}
function inspectEvent(index) {
  const e = S.result?.events[index];
  if (!e) return;
  pause();
  S.selected = { type: "event", id: index };
  seek(index + 1);
  if (!e.lot) {
    const transition=e.transition;
    const label=state=>Object.hasOwn(translations[locale], "order_"+state) ? tr("order_"+state) : traceStateLabel(state);
    openInspector(tr("order_machine_state"), `<h3>${esc(e.machine)}</h3><p>${fmt(e.time)} min</p><p>${esc(label(transition?.previous.state))} → ${esc(label(transition?.current.state))}</p><p>${esc(operationCause(transition?.current.cause||""))}</p>`);
    renderGraph();
    return;
  }
  let html = `<span class="info-tag">${esc(kinds[e.kind])} · ${fmt(e.time)} min</span><h3 style="margin-top:13px">${esc(e.lot?.id || "—")} <span class="subtle">${tr("ui_72")} ${esc(e.lot.product)}</span></h3><button id="track-lot" class="quiet">${tr("ui_121")}</button>`;
  if (e.kind === "decision") {
    html += `<div class="info-card selected"><b>${tr("ui_122")}</b>${esc(localized(e.reason, e.reason_message))}<small>${e.decision_mode === "pull" && e.machine ? `${esc(e.machine)}${tr("ui_123")}` : tr("ui_124")}</small></div><h3>${tr("ui_125")} ${e.candidates.length}${tr("ui_126")}</h3>`;
    for (const c of e.candidates)
      html += `<div class="info-card ${c.id === e.chosen ? "selected" : ""}"><b>${c.id === e.chosen ? tr("ui_127") : tr("ui_27")} · ${esc(c.lot_id)}</b><span class="route-key">${esc(c.from)} → ${esc(c.to)}</span><br>${tr("ui_128")} ${c.priority} ${tr("ui_129")} ${c.queue_length}<br>${c.same_line ? tr("ui_130") : tr("ui_131")} ${tr("ui_132")} ${fmt(c.ready_since)}m<small>${c.id === e.chosen ? tr("ui_133") : tr("ui_134")}</small></div>`;
    for (const c of e.checks.filter((c) => !c.eligible))
      html += `<div class="info-card rejected"><b>${tr("ui_135")} ${esc(c.lot_id)} / ${esc(c.route_id)}</b>${esc(localized(c.reason, c.reason_message))}</div>`;
    html +=
      `<p>${tr("ui_136")}</p>`;
  } else if (e.kind === "machine_state") {
    html += `<div class="info-card"><b>${esc(e.machine)}</b><p>${traceStateBadge(e.transition?.previous.state)} → ${traceStateBadge(e.transition?.current.state)}</p><p>${esc(operationCause(e.transition?.current.cause || ""))}</p></div>`;
  } else {
    html += `<div class="info-card"><b>${esc(e.lot.location)}${e.lot.target ? " → " + esc(e.lot.target) : ""}</b>${esc(localized(e.reason, e.reason_message) || e.route || tr("ui_137"))}${e.duration ? `<br>${tr("ui_138")} ${fmt(e.duration)} min` : ""}</div>`;
    for (const c of e.checks || [])
      html += `<div class="info-card rejected">${esc(c.route_id)} · ${esc(localized(c.reason, c.reason_message))}</div>`;
  }
  html += `<button id="compare-allocation">${tr("ui_139")}</button>`;
  openInspector(tr("ui_140"), html);
  $("#compare-allocation").onclick = () => { closeInspector(); selectTab("allocations"); };
  $("#track-lot").onclick = () => inspectLot(e.lot.id);
  renderGraph();
}
function inspectLot(id, refresh = true) {
  S.lot = id;
  S.selected = { type: "lot", id };
  const events = (S.result?.events.slice(0, S.cursor) || []).filter((e) => e.lot?.id === id);
  openInspector(
    tr("ui_141"),
    `<h3>${esc(id)}</h3><p>${tr("ui_142")}</p>${
      events
        .filter((e) => ["arrival", "decision", "move", "complete", "blocked"].includes(e.kind))
        .map(
          (e) =>
            `<div class="info-card" data-lot-event="${e.index}" role="button" tabindex="0"><small>${fmt(e.time)} min · ${esc(kinds[e.kind])}</small><b>${esc(e.kind === "move" ? e.lot.location + " → " + e.lot.target : e.lot.location)}</b>${esc(localized(e.reason, e.reason_message) || "")}</div>`,
        )
        .join("") || `<p>${tr("ui_143")}</p>`
    }`,
  );
  $$("[data-lot-event]").forEach((el) => {
    el.onclick = () => inspectEvent(Number(el.dataset.lotEvent));
    el.onkeydown = (e) => {
      if (e.key === "Enter") el.onclick();
    };
  });
  if (refresh) {
    renderGraph();
    renderTrace();
  }
}
async function run() {
  if (S.job || (PUBLIC_DEMO && browserRuntime.state === "loading")) {
    await cancelRun();
    return;
  }
  if (S.busy) return;
  const ticket = ++runSerial,
    browserJobId = PUBLIC_DEMO ? `browser-${ticket}` : null;
  S.busy = true;
  if (browserJobId) {
    S.job = browserJobId;
    $("#run-button").textContent = tr("ui_145");
  }
  if (!(await applyCode())) {
    if (ticket === runSerial) {
      S.job = null;
      $("#run-button").textContent = tr("ui_152");
    }
    S.busy = false;
    return;
  }
  const source = S.source,
    revision = S.revision;
  if (ticket !== runSerial) return;
  S.busy = true;
  S.runtimeError = null;
  S.console = "";
  pause();
  status({code: "ui_144"});
  $("#run-button").textContent = tr("ui_145");
  try {
    let p;
    if (PUBLIC_DEMO) {
      S.busy = false;
      p = await browserRuntime.run(source);
      if (S.job !== browserJobId) return;
    } else {
      const { job_id } = await api("/api/run", { source });
      S.job = job_id;
      S.busy = false;
      let job;
      do {
        await new Promise((r) => setTimeout(r, 120));
        if (S.job !== job_id) return;
        job = await api("/api/jobs/" + job_id);
      } while (job.status === "running");
      if (S.job !== job_id) return;
      if (job.status === "cancelled") {
        status({code: "ui_146"});
        return;
      }
      p = job.payload;
    }
    S.console = (p.result?.console || p.console || "") + (p.traceback ? "\n" + p.traceback : "");
    if (!p.ok) throw Object.assign(new Error(localized(p.error, p.error_message)), {detail: p.error_message});
    if (S.revision !== revision) {
      toast({code: "ui_147"});
      status({code: "ui_148"});
      return;
    }
    if (PUBLIC_DEMO) {
      const sourceHash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(source)))]
        .map((value) => value.toString(16).padStart(2, "0")).join("");
      p.result.execution = {
        kind: "browser",
        source_sha256: sourceHash,
        source_revision: S.demoVersion.source_revision,
        runtime: copy(browserRuntime.runtime),
      };
    }
    S.result = p.result;
    S.warnings = p.result.warnings;
    S.warningMessages = p.result.warning_messages || [];
    S.cursor = 0;
    S.lot = null;
    S.selected = null;
    S.runtimeErrorLine = null;
    $("#inspector").hidden = true;
    diagnostics();
    selectTab("events");
    seek(Math.min(S.result.events.length, 1));
    status({code: "run_complete", args: [S.result.events.length]});
    persistReplay();
    play();
  } catch (e) {
    if (ticket !== runSerial) return;
    S.console = (e.console || S.console || "") + (e.traceback ? "\n" + e.traceback : "");
    S.runtimeError = e.code === "browser_init_error" ? {code: "public_init_error"} : e.code === "browser_init_timeout" ? {code: "public_init_timeout"} : e.code === "browser_timeout" ? {code: "public_timeout"} : e.detail || e.error_message || e.message;
    S.runtimeErrorLine = e.line || null;
    markErrorLine(S.runtimeErrorLine);
    selectTab("console");
    toast(S.runtimeError);
    status({code: "ui_151"});
  } finally {
    if (ticket === runSerial) {
      S.job = null;
      S.busy = false;
      $("#run-button").textContent = tr("ui_152");
    }
  }
}
async function cancelRun() {
  const id = S.job;
  if (!id && !(PUBLIC_DEMO && browserRuntime.state === "loading")) return;
  if (PUBLIC_DEMO) {
    ++S.revision;
    clearTimeout(parseTimer);
    browserRuntime.stop();
  }
  else await api("/api/cancel", { job_id: id });
  ++runSerial;
  S.job = null;
  S.busy = false;
  $("#run-button").textContent = tr("ui_152");
  status({code: "ui_153"});
}
function download(filename, text, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function save() {
  download("factory_model.py", S.source, "text/x-python");
  $("#dirty-dot").textContent = "";
  toast({code: "ui_154"});
}
function nextId(prefix, items) {
  let i = 1;
  while (items.some((x) => x.id === prefix + i)) i++;
  return prefix + i;
}
$("#code").addEventListener("input", () => {
  S.source = $("#code").value;
  S.revision++;
  S.valid = false;
  S.runtimeError = null;
  S.runtimeErrorLine = null;
  persist();
  updateEditor();
  invalidateTrace();
  $("#dirty-dot").textContent = "●";
  $("#sync-status").textContent = tr("ui_155");
  clearTimeout(parseTimer);
  const generation = validationGeneration;
  parseTimer = setTimeout(() => {
    parseTimer = null;
    if (generation === validationGeneration) applyCode(true, generation);
  }, 500);
});
$("#code").addEventListener("scroll", () => ($("#line-numbers").scrollTop = $("#code").scrollTop));
$("#code").addEventListener("click", updateEditor);
$("#code").addEventListener("keyup", updateEditor);
// Textarea fallback mirrors the explicit CodeMirror indentation shortcuts.
// Tab is deliberately absent: it always navigates, including with a selection.
function indentPlainEditor(el, more) {
  const source = el.value, anchor = el.selectionStart, head = el.selectionEnd;
  const start = anchor === 0 ? 0 : source.lastIndexOf("\n", anchor - 1) + 1;
  const endAnchor = head > anchor && source[head - 1] === "\n" ? head - 1 : head;
  const nextNewline = source.indexOf("\n", endAnchor);
  const end = nextNewline < 0 ? source.length : nextNewline;
  const block = source.slice(start, end);
  const lines = block.split("\n");
  const changed = lines.map(line => more ? "    " + line : line.replace(/^(?: {1,4}|\t)/, "")).join("\n");
  if (changed === block) return;
  const firstDelta = changed.split("\n")[0].length - lines[0].length;
  el.setRangeText(changed, start, end, "select");
  if (anchor === head) {
    const caret = Math.max(start, anchor + firstDelta);
    el.setSelectionRange(caret, caret);
  }
  el.dispatchEvent(new Event("input", {bubbles:true}));
}
$("#editor-skip-results").onclick = () => {
  pause();
  const target = document.querySelector("[data-tab]");
  target?.focus();
  target?.scrollIntoView({block:"nearest"});
};
$("#code").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && ["BracketLeft", "BracketRight"].includes(e.code)) {
    e.preventDefault();
    indentPlainEditor(e.target, e.code === "BracketRight");
  }
});
$("#apply-code").onclick = () => applyCode();
$("#run-button").onclick = run;
$("#save-button").onclick = save;
$("#reset-button").onclick = async () => {
  if (!PUBLIC_DEMO) return;
  if (S.job) await cancelRun();
  browserRuntime.stop();
  pause();
  setSource(S.example);
  S.result = copy(S.demoResult);
  S.model = S.result.model;
  S.warnings = S.result.warnings;
  S.warningMessages = S.result.warning_messages || [];
  S.runtimeError = null;
  S.runtimeErrorLine = null;
  S.console = S.result.console || "";
  S.valid = true;
  try {
    localStorage.removeItem("factory-studio.public.source.v1");
    localStorage.removeItem("factory-studio.public.replay.v1");
  } catch {}
  diagnostics(); renderTree(); renderGraph(); seek(1); selectTab("events");
  status({code: "public_reset_done"});
};
$("#settings-button").onclick = inspectSettings;
$("#close-inspector").onclick = closeInspector;
$("#add-machine").onclick = () => S.model && inspectMachine(nextId("M", S.model.machines), true);
$("#add-route").onclick = () => S.model && inspectRoute(nextId("r", S.model.routes), true);
$("#add-process").onclick = () => S.model && inspectProcess(nextId("P", S.model.processes), true);
$("#play-button").onclick = play;
$("#rewind-button").onclick = () => {
  pause();
  seek(0);
};
$("#step-button").onclick = () => {
  pause();
  seek(S.cursor + 1);
  if (S.result?.events[S.cursor - 1]?.kind === "decision") inspectEvent(S.cursor - 1);
};
$("#timeline").oninput = (e) => {
  pause();
  seek(Number(e.target.value));
};
$$("[data-tab]").forEach((b) => (b.onclick = () => selectTab(b.dataset.tab)));
$$("[data-view]").forEach(
  (b) =>
    (b.onclick = () => {
      $$("[data-view]").forEach((x) => x.classList.toggle("active", x === b));
      $("#studio").className = "studio " + b.dataset.view;
      requestAnimationFrame(() => {
        renderGraph();
        editor?.refresh();
      });
    }),
);
$("#zoom-in").onclick = () => {
  autoFit = false;
  S.zoom = Math.min(1.8, S.zoom + 0.1);
  renderGraph();
};
$("#zoom-out").onclick = () => {
  autoFit = false;
  S.zoom = Math.max(0.3, S.zoom - 0.1);
  renderGraph();
};
$("#zoom-fit").onclick = () => {
  autoFit = true;
  renderGraph();
};
$("#export-trace").onclick = () =>
  S.result && download("factory_trace.json", JSON.stringify(S.result, null, 2), "application/json");
$("#help-button").onclick = () => $("#help").showModal();
$("#close-help").onclick = () => $("#help").close();
$("#file-button").onclick = () => (editor ? editor.focus() : $("#code").focus());
$("#import-button").onclick = () => $("#file-input").click();
$("#file-input").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    if (file.size > 1_000_000) throw new Error(tr("ui_156"));
    const source = await file.text();
    if (PUBLIC_DEMO) await browserRuntime.parse(source);
    else await api("/api/parse", { source });
    if (S.job) await cancelRun();
    invalidateTrace();
    setSource(source);
    closeInspector();
    await applyCode();
    toast({code: "ui_157"});
  } catch (e) {
    toast(e.detail || e.message);
  } finally {
    $("#file-input").value = "";
  }
};
window.addEventListener("keydown", (e) => {
  if (e.defaultPrevented) return; // CodeMirror already handled its scoped command.
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    save();
  }
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    run();
  }
  if (e.key === "Escape") closeInspector();
});
new ResizeObserver(() => {
  if (S.model) renderGraph();
}).observe($("#graph-viewport"));
function enhanceEditor(selection = null) {
  if (editor || !window.CodeMirror) return editor;
  const textarea = $("#code");
  selection ||= {
    start: textarea.selectionStart,
    end: textarea.selectionEnd,
    direction: textarea.selectionDirection || "none",
  };
  editor = CodeMirror.fromTextArea($("#code"), {
    mode: { name: "python", version: 3 },
    theme: "factory",
    lineNumbers: true,
    indentUnit: 4,
    tabSize: 4,
    indentWithTabs: false,
    matchBrackets: true,
    lineWrapping: false,
    viewportMargin: 12,
    extraKeys: {
      "Ctrl-Enter": run,
      "Cmd-Enter": run,
      "Ctrl-S": save,
      "Cmd-S": save,
      "Ctrl-/": "toggleComment",
      "Cmd-/": "toggleComment",
      // false stops CodeMirror keymap fallthrough while preserving the browser's
      // native focus navigation. Removing these entries would restore its trap.
      Tab: false,
      "Shift-Tab": false,
      "Ctrl-]": "indentMore",
      "Cmd-]": "indentMore",
      "Ctrl-[": "indentLess",
      "Cmd-[": "indentLess",
      "Ctrl-Space": (cm) =>
        cm.showHint({
          completeSingle: false,
          extraKeys: {
            Tab: (_cm, completion) => { completion.close(); return CodeMirror.Pass; },
            "Shift-Tab": (_cm, completion) => { completion.close(); return CodeMirror.Pass; },
          },
          hint: (instance) => {
            const pos = instance.getCursor(),
              token = instance.getTokenAt(pos),
              start = /[\w]+$/.test(token.string) ? token.start : pos.ch;
            const prefix = instance.getRange({ line: pos.line, ch: start }, pos);
            const words = [
              "MODEL",
              "choose_candidate",
              "processing_time",
              "process_lot",
              "candidates",
              "context",
              "machine",
              "lot",
              "return",
              "def",
              "if",
              "elif",
              "else",
              "for",
              "while",
              "import",
              "from",
              "yield",
              "True",
              "False",
              "None",
              "min",
              "max",
              "sorted",
              "len",
              "print",
              "range",
              "simpy",
              ...Object.keys(S.model || {}),
            ];
            return {
              list: [...new Set(words)].filter((w) => w.startsWith(prefix)),
              from: { line: pos.line, ch: start },
              to: pos,
            };
          },
        }),
    },
  });
  editor.getWrapperElement().setAttribute("aria-label", tr("ui_158"));
  editor.getInputField().setAttribute("aria-label", tr("ui_159"));
  editor.getInputField().setAttribute("aria-describedby", "editor-keyboard-help");
  $(".code-wrap").classList.add("enhanced");
  editor.on("change", () => {
    if (settingEditor) return;
    editor.save();
    $("#code").dispatchEvent(new Event("input"));
  });
  editor.on("cursorActivity", updateEditor);
  new ResizeObserver(() => editor.refresh()).observe($(".editor-panel"));
  const anchor = selection.direction === "backward" ? selection.end : selection.start;
  const head = selection.direction === "backward" ? selection.start : selection.end;
  editor.setSelection(editor.posFromIndex(anchor), editor.posFromIndex(head), {scroll: false});
  editor.focus();
  return editor;
}
enhanceEditor();
$("#enhance-editor").onclick = async () => {
  const button = $("#enhance-editor"), textarea = $("#code");
  const selection = {start: textarea.selectionStart, end: textarea.selectionEnd, direction: textarea.selectionDirection || "none"};
  button.disabled = true;
  try {
    await window.loadEnhancedEditor?.(selection);
    button.hidden = Boolean(editor);
  } catch (error) {
    button.disabled = false;
    window.showFactoryLoadFailure?.("editor", error.message);
  }
};
if (editor) $("#enhance-editor").hidden = true;
async function boot() {
  try {
    await window.initialLocaleReady;
    translateStatic();
    if (!PUBLIC_DEMO) {
      try {
        await window.loadEnhancedEditor?.();
        if (editor) $("#enhance-editor").hidden = true;
      } catch (error) {
        window.showFactoryLoadFailure?.("editor", error.message);
      }
    }
    if (PUBLIC_DEMO) {
      const response = await fetch("/demo.json");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      await nextRenderTurn();
      S.example = data.source;
      S.demoResult = copy(data.result);
      S.demoVersion = data.version;
      let restoredPublicReplay = false;
      let source = data.source;
      try { source = localStorage.getItem("factory-studio.public.source.v1") || source; } catch {}
      setSource(source);
      await nextRenderTurn();
      S.model = data.result.model; S.result = source === data.source ? copy(data.result) : null; S.valid = source === data.source;
      S.warnings = data.result.warnings; S.warningMessages = data.result.warning_messages;
      const runtime = data.version.browser_runtime;
      $("#demo-version").textContent = `${data.version.source_revision} · trace v${data.result.schema_version} · Pyodide ${runtime.pyodide} · Python ${runtime.python} · SimPy ${runtime.simpy} · ${data.version.trace_sha256}`;
      if (source !== data.source) {
        await applyCode(true);
      }
      try {
        const saved = JSON.parse(localStorage.getItem("factory-studio.public.replay.v1"));
        if (saved?.source === source && saved.result?.schema_version === 2) {
          S.model = saved.result.model; S.result = saved.result; S.valid = true;
          S.cursor = Number.isInteger(saved.cursor) && saved.cursor >= 0 && saved.cursor <= S.result.events.length ? saved.cursor : 1;
          S.tab = saved.tab || "events";
          S.resultsFinal = saved.resultsFinal || false; S.resultFilters = saved.filters || {};
          S.warnings = S.result.warnings; S.warningMessages = S.result.warning_messages || [];
          restoredPublicReplay = true;
        }
      } catch {}
      await nextRenderTurn();
      $("#project-name").textContent = S.model.name;
      diagnostics(); renderTree();
      await nextRenderTurn();
      seek(S.result ? (restoredPublicReplay ? S.cursor : 1) : 0);
      selectTab(S.result ? S.tab : "console");
      status({code: "public_ready"});
      await revealInitialPanels();
      return;
    }
    const data = await api("/api/bootstrap");
    S.token = data.token;
    S.localRuntime = data.runtime;
    S.example = data.source;
    let source = data.source;
    try {
      source = localStorage.getItem("factory-studio.source.v1") || source;
    } catch {}
    setSource(source);
    await applyCode(true);
    try {
      const saved = JSON.parse(localStorage.getItem("factory-studio.replay.v1"));
      if (saved?.source === S.source && saved.result?.schema_version === 2) {
        S.result = saved.result; S.cursor = saved.cursor; S.tab = saved.tab || "events";
        S.resultsFinal = saved.resultsFinal || false; S.resultFilters = saved.filters || {};
        S.warnings = S.result.warnings; S.warningMessages = S.result.warning_messages || [];
        seek(S.cursor); selectTab(S.tab);
      }
    } catch {}
    renderPlayback();
    renderTrace();
    status({code: "ui_160"});
    await revealInitialPanels();
  } catch (e) {
    status({code: "ui_161"});
    toast(e.detail || e.message);
    await revealInitialPanels();
  }
}
boot();
// Public read-only state accessor for local integration tests and diagnostics.
window.factoryStudio = {
  getState: () => ({
    model: copy(S.model),
    cursor: S.cursor,
    eventCount: S.result?.events.length || 0,
    valid: S.valid,
    playing: S.playing,
    job: S.job,
    runtimeState: S.runtimeState,
    result: copy(S.result),
  }),
  pause,
};

function selectResult(row) {
  pause();
  S.lot = row.lot;
  S.selected = row.route ? {type: "route", id: row.route} : {type: "machine", id: row.source.machine};
  S.comparison = row.decision;
  seek(row.after, true);
  if (row.decision !== null) selectTab("allocations");
  else toast({code: "unallocated_location", args: [row.lot]});
}
function renderAllocationResults() {
  const projection = allocationResults(S.result, S.cursor, S.resultsFinal, S.resultFilters);
  const {rows, time} = projection;
  const model = S.result.model, columns = allocationColumns();
  const statuses = [tr("ui_163"), tr("ui_164"), tr("ui_165"), tr("ui_29"), tr("ui_30"), tr("ui_166"), tr("ui_167"), tr("ui_168"), tr("ui_169")];
  const choices = {process: model.processes.map(p => p.id), line: model.machines.map(m => m.line),
    machine: ["INPUT", ...model.machines.map(m => m.id), "OUTPUT"], lot: S.result.events.filter(e => e.kind === "arrival").map(e => e.lot.id), status: statuses};
  const names = {process: tr("ui_89"), line: tr("ui_90"), machine: tr("ui_40"), lot: tr("ui_35"), status: tr("ui_37")};
  const filterHTML = Object.entries(choices).map(([key,values]) => `<label>${names[key]} <select data-result-filter="${key}"><option value="">${tr("ui_170")}</option>${[...new Set(values)].map(v => `<option value="${esc(v)}" ${S.resultFilters[key] === v ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>`).join("");
  $("#trace-content").innerHTML = `<div class="allocation-results"><div class="result-controls"><label>${tr("ui_171")} <select id="results-view"><option value="replay" ${!S.resultsFinal ? "selected" : ""}>${tr("ui_172")}</option><option value="final" ${S.resultsFinal ? "selected" : ""}>${tr("ui_173")}</option></select></label>${filterHTML}<label>${tr("ui_174")} <input id="result-search" value="${esc(S.resultFilters.search || "")}" type="search"></label><button id="result-reset">${tr("ui_175")}</button><button id="result-csv">${tr("ui_176")}</button></div><p id="result-context">${S.resultsFinal ? tr("ui_177") : `${tr("ui_178")} ${S.cursor}`} · ${time} min · ${rows.length}${tr("ui_179")} ${Object.values(S.resultFilters).some(Boolean) ? tr("ui_180") : tr("ui_181")}${tr("ui_182")}</p><div class="result-table-wrap"><table id="result-table"><thead><tr>${columns.map(([name]) => `<th>${name}</th>`).join("")}</tr></thead><tbody>${rows.map((r,i) => `<tr data-result-row="${i}">${columns.map(([,get],j) => {const value = get(r); return `<td>${j === 0 ? `<button data-result-select="${i}">${esc(r.id || tr("ui_183"))}</button>` : esc(typeof value === "number" ? preciseNumber(value) : value ?? "")}</td>`;}).join("")}</tr>`).join("")}</tbody></table></div>${rows.length ? "" : `<p>${tr("ui_184")}</p>`}<h3>${tr("ui_185")}</h3><p>${tr("ui_186")}</p><div id="machine-timeline"></div></div>`;
  $("#results-view").onchange = e => {S.resultsFinal = e.target.value === "final"; renderTrace();};
  $$('[data-result-filter]').forEach(el => el.onchange = () => {S.resultFilters[el.dataset.resultFilter] = el.value; renderTrace();});
  $("#result-search").oninput = e => {
    const pos = e.target.selectionStart;
    S.resultFilters.search = e.target.value; renderTrace();
    $("#result-search").focus(); $("#result-search").setSelectionRange(pos, pos);
  };
  $("#result-reset").onclick = () => {S.resultFilters = {}; renderTrace();};
  $("#result-csv").onclick = () => download("factory_allocations.csv", allocationCSV(rows, {
    [tr("ui_171")]: S.resultsFinal ? tr("ui_173") : tr("ui_172"), [tr("ui_187")]: time,
    [tr("ui_188")]: S.resultsFinal ? S.result.events.length : S.cursor,
    [tr("ui_189")]: Object.values(S.resultFilters).some(Boolean) ? JSON.stringify(S.resultFilters) : tr("ui_181"),
  }), "text/csv;charset=utf-8");
  $$('[data-result-select]').forEach(el => el.onclick = () => selectResult(rows[Number(el.dataset.resultSelect)]));
  const axis = Math.max(time, 0.000001), width = 800, left = 180;
  let y = 35, body = "";
  for (const machine of model.machines) {
    const lanes = rows.map((r,i) => ({r,i})).filter(({r}) => r.id && r.destination.machine === machine.id);
    if (!lanes.length) continue;
    body += `<text x="5" y="${y}">${esc(machine.process)} / ${esc(machine.line)} / ${esc(machine.id)}</text>`; y += 22;
    for (const {r,i} of lanes) {
      body += `<text x="5" y="${y+13}">${esc(r.id)} · ${esc(r.lot)}</text>`;
      const spans = [[r.assigned, r.move, r.reservation ? "reserved" : "queued", r.reservation ? tr("ui_32") : tr("ui_190")],
        [r.move, r.arrived, "moving", tr("ui_6")], [r.start, r.finish, "processing", tr("ui_191")]];
      // Reservation continues during transport; separate vertical strips preserve overlaps.
      if (r.reservation) spans[0][1] = r.start;
      spans.forEach(([start,end,kind,label],strip) => {
        if (start === null) return;
        const endTime = end ?? time;
        body += `<g data-result-span="${i}" tabindex="0" role="button" aria-label="${esc(r.id)} ${label} ${start} ~ ${endTime}${end === null ? tr("ui_192") : ''} min"><title>${esc(r.id)} · ${label}: ${start} ~ ${endTime} min${end === null ? tr("ui_193") : ''}</title><rect class="span-${kind}" x="${left + start/axis*width}" y="${y+strip*8}" width="${Math.max(2,(endTime-start)/axis*width)}" height="7"/>${end === null ? `<text x="${left + endTime/axis*width + 3}" y="${y+strip*8+8}">*</text>` : ""}</g>`;
      });
      y += 32;
    }
  }
  const ticks = Array.from({length: 6}, (_,i) => `<line x1="${left+i*width/5}" x2="${left+i*width/5}" y1="22" y2="${y}" stroke="#e5e9e5"/><text x="${left+i*width/5}" y="15">${Number((time*i/5).toFixed(4))} min</text>`).join("");
  $("#machine-timeline").innerHTML = `<svg width="1050" height="${y+10}" role="img" aria-label="${tr("ui_194")}">${ticks}${body}</svg>`;
  $$('[data-result-span]').forEach(el => {
    el.onclick = () => selectResult(rows[Number(el.dataset.resultSpan)]);
    el.onkeydown = e => {if (["Enter", " "].includes(e.key)) {e.preventDefault(); el.onclick();}};
  });
}

let replayResultCache = null, replayResultCacheValue = "", replayPersistenceHandle = null;
function persistReplay() {
  try {
    const key = PUBLIC_DEMO ? "factory-studio.public.replay.v1" : "factory-studio.replay.v1";
    if (S.result) {
      if (replayResultCache !== S.result) {
        replayResultCache = S.result;
        replayResultCacheValue = JSON.stringify(S.result);
      }
      const payload = `{"source":${JSON.stringify(S.source)},"result":${replayResultCacheValue},"cursor":${S.cursor},"tab":${JSON.stringify(S.tab)},"resultsFinal":${Boolean(S.resultsFinal)},"filters":${JSON.stringify(S.resultFilters)}}`;
      localStorage.setItem(key, payload);
    }
    else localStorage.removeItem(key);
  } catch { /* Source saving remains independent when a large trace exceeds quota. */ }
}
function scheduleReplayPersistence() {
  if (replayPersistenceHandle !== null) return;
  const complete = () => { replayPersistenceHandle = null; persistReplay(); };
  replayPersistenceHandle = window.requestIdleCallback
    ? requestIdleCallback(complete, {timeout: 500})
    : setTimeout(complete, 0);
}
function flushReplayPersistence() {
  if (replayPersistenceHandle !== null) {
    if (window.cancelIdleCallback) cancelIdleCallback(replayPersistenceHandle);
    else clearTimeout(replayPersistenceHandle);
    replayPersistenceHandle = null;
  }
  persistReplay();
}
function invalidatePendingValidation() {
  clearTimeout(parseTimer);
  parseTimer = null;
  validationGeneration++;
}
window.addEventListener("pagehide", () => {
  flushReplayPersistence();
  // Invalidate deferred producers before terminating current consumers. A
  // persisted BFCache page keeps its UI/source, but performs no hidden work;
  // a later user action uses the new validation generation normally.
  invalidateDataWork();
  invalidatePendingValidation();
  cancelExperiment();
  dataWorkerStop(DATA.worker);
  browserRuntime?.stop();
});
$("#language").onchange = async () => {
  const generation = ++localeChangeGeneration;
  const oldLocale = locale;
  const translateOld = text => {
    const key = Object.keys(translations[oldLocale]).find(k => translations[oldLocale][k] === text);
    return key ? tr(key) : text;
  };
  const form = [...($$("#property-form input, #property-form select"))].map(el => ({name: el.name, value: el.value, checked: el.checked}));
  const selected = copy(S.selected), cursor = S.cursor;
  const cmCursor = editor?.getCursor();
  const inspectorOpen = !$("#inspector").hidden;
  const nextLocale = $("#language").value;
  try {
    await window.loadLocale?.(nextLocale);
  } catch (error) {
    if (generation === localeChangeGeneration) {
      $("#language").value = locale;
      window.showFactoryLoadFailure?.("locale", error.message);
    }
    return;
  }
  if (generation !== localeChangeGeneration) return;
  locale = nextLocale;
  try { localStorage.setItem(localeKey, locale); } catch {}
  if (S.resultFilters.status) S.resultFilters.status = translateOld(S.resultFilters.status);
  translateStatic();
  $("#project-name").textContent = S.model?.name || tr("ui_14");
  $("#mode-label").textContent = tr(S.model?.mode === "push" ? "ui_16" : "ui_15");
  $("#run-button").textContent = tr(S.job ? "ui_145" : "ui_152");
  if (PUBLIC_DEMO) renderRuntimeState(S.runtimeState, browserRuntime.runtime);
  $("#status").textContent = displayMessage(S.statusMessage);
  $("#toast").textContent = displayMessage(S.toastMessage);
  diagnostics(localized(S.parseError, S.parseErrorDetail) || "");
  renderTree(); renderGraph(); renderMetrics(); renderPlayback(); renderTrace();
  if (inspectorOpen && selected) {
    if (selected.type === "machine") inspectMachine(selected.id, !S.model.machines.some(m => m.id === selected.id));
    if (selected.type === "route") inspectRoute(selected.id, !S.model.routes.some(m => m.id === selected.id));
    if (selected.type === "process") inspectProcess(selected.id, !S.model.processes.some(m => m.id === selected.id));
    if (selected.type === "settings") inspectSettings();
    if (selected.type === "lot") inspectLot(selected.id);
    if (selected.type === "event") { inspectEvent(selected.id); seek(cursor, true); }
    form.forEach(value => { const el = $("#property-form [name='" + value.name + "']"); if (el) {el.value = value.value; el.checked = value.checked;} });
  }
  refreshOperationLocale();
  editor?.getWrapperElement().setAttribute("aria-label", tr("ui_158"));
  editor?.getInputField().setAttribute("aria-label", tr("ui_159"));
  if (cmCursor) editor.setCursor(cmCursor);
  persistReplay();
};
