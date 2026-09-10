"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
const copy = (v) => JSON.parse(JSON.stringify(v));
const fmt = (n) => Number(n || 0).toFixed(1);
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
  zoom: 1,
  warnings: [],
  console: "",
  example: "",
  busy: false,
};
let parseTimer,
  playTimer,
  toastTimer,
  autoFit = true,
  runSerial = 0,
  editor = null,
  settingEditor = false,
  errorLine = null;
const kinds = {
  arrival: "투입",
  ready: "대기",
  decision: "로트 선택",
  assigned: "목적지 배정",
  move: "이동",
  start: "처리 시작",
  finish: "처리 종료",
  complete: "완료",
  blocked: "경로 없음",
};
async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", "X-Factory-Token": S.token },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}
function toast(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 3500);
}
function status(message) {
  $("#status").textContent = message;
}
function diagnostics(error = "") {
  $("#sync-status").textContent = error
    ? "● 코드 오류 · 마지막 유효 공정도 표시"
    : "● 코드 ↔ 공정도 동기화";
  $("#sync-status").className = error ? "error" : "good";
  S.parseError = error;
  if (editor && errorLine !== null) {
    editor.removeLineClass(errorLine, "background", "code-error-line");
    errorLine = null;
  }
  const line = error.match(/^(\d+)행:/);
  if (editor && line) {
    errorLine = Number(line[1]) - 1;
    editor.addLineClass(errorLine, "background", "code-error-line");
  }
  $("#diagnostic-count").textContent = (error ? 1 : 0) + S.warnings.length || "";
  if (S.tab === "console") renderTrace();
}
function persist() {
  try {
    localStorage.setItem("factory-studio.source.v1", S.source);
  } catch {
    status("브라우저 자동 저장 불가 · Python 파일로 저장하세요.");
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
async function applyCode(silent = false) {
  clearTimeout(parseTimer);
  const revision = S.revision,
    source = S.source;
  try {
    const data = await api("/api/parse", { source });
    if (revision !== S.revision) return false;
    S.model = data.model;
    S.warnings = data.warnings;
    S.valid = true;
    diagnostics();
    renderTree();
    renderGraph();
    $("#project-name").textContent = S.model.name || "공장 프로젝트";
    $("#mode-label").textContent =
      S.model.mode === "pull" ? "PULL · 후공정 선택" : "PUSH · 전공정 배정";
    if (!silent) status("코드와 공정도를 동기화했습니다.");
    return true;
  } catch (e) {
    if (revision !== S.revision) return false;
    S.valid = false;
    diagnostics(e.message);
    status("코드를 확인하세요. 콘솔 / 진단에서 오류를 볼 수 있습니다.");
    if (!silent) {
      selectTab("console");
      toast(e.message);
    }
    return false;
  }
}
async function mutate(change) {
  if (S.busy || S.job) throw new Error("실행 또는 동기화가 끝난 뒤 수정하세요.");
  if (!(await applyCode(true)))
    throw new Error("코드 오류를 먼저 수정하세요. 마지막 유효 모델로 덮어쓰지 않습니다.");
  const revision = S.revision,
    model = copy(S.model);
  change(model);
  S.busy = true;
  try {
    const data = await api("/api/sync", { source: S.source, model });
    if (revision !== S.revision)
      throw new Error("코드가 변경되었습니다. 편집 내용을 다시 적용하세요.");
    invalidateTrace();
    setSource(data.source);
    S.model = data.model;
    S.warnings = data.warnings;
    S.valid = true;
    diagnostics();
    $("#dirty-dot").textContent = "●";
    $("#project-name").textContent = S.model.name;
    $("#mode-label").textContent =
      S.model.mode === "pull" ? "PULL · 후공정 선택" : "PUSH · 전공정 배정";
    renderTree();
    renderGraph();
    status("공정도 변경을 Python에 반영했습니다.");
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
function stateAt(cursor = S.cursor) {
  const lots = {},
    machines = {};
  for (const e of S.result?.events.slice(0, cursor) || []) {
    lots[e.lot.id] = e.lot;
    if (e.machine && ["move", "start"].includes(e.kind))
      machines[e.machine] = {
        lot: e.lot.id,
        state: e.kind === "move" ? "moving" : "busy",
        since: e.time,
      };
    if (e.machine && e.kind === "finish") delete machines[e.machine];
  }
  return { lots, machines, time: cursor ? S.result.events[cursor - 1].time : 0 };
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
    rows.set(line, { y, height: max * 81 + 20 });
    y += max * 81 + 35;
  }
  S.model.processes.forEach((p, i) => {
    const x = 103 + i * 195;
    groups.push({ p, x });
    for (const line of lines) {
      const ms = S.model.machines.filter((m) => m.process === p.id && m.line === line);
      ms.forEach(
        (m, j) =>
          (positions[m.id] = { x: x + 16, y: rows.get(line).y + 19 + j * 81, w: 133, h: 61 }),
      );
    }
  });
  const width = 103 + S.model.processes.length * 195 + 71,
    height = Math.max(335, y + 20);
  positions.INPUT.y = height / 2 - 21;
  positions.OUTPUT = { x: width - 63, y: height / 2 - 21, w: 52, h: 42 };
  return { positions, groups, rows, width, height };
}
function renderGraph() {
  if (!S.model) return;
  const { positions, groups, rows, width, height } = graphLayout();
  if (autoFit)
    S.zoom = Math.min(1.15, Math.max(0.38, ($("#graph-viewport").clientWidth - 12) / width));
  const { lots, machines } = stateAt(),
    active = S.result?.events[S.cursor - 1];
  const routeHistory = new Set(
    S.lot
      ? (S.result?.events.slice(0, S.cursor) || [])
          .filter((e) => e.lot.id === S.lot && e.kind === "move")
          .map((e) => e.route)
      : [],
  );
  let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width * S.zoom}" height="${height * S.zoom}" viewBox="0 0 ${width} ${height}" role="img" aria-label="공정, 라인, 머신 및 로트 이동 경로"><defs><marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0 0 L6 3 L0 6" fill="#a6bca6"/></marker><marker id="arrow-active" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0 0 L6 3 L0 6" fill="#32855c"/></marker></defs>`;
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
    svg += `<g class="route-group" data-route="${esc(r.id)}" tabindex="0" role="button" aria-label="경로 ${esc(r.from)} → ${esc(r.to)}"><title>${esc(r.from)} → ${esc(r.to)} · 우선순위 ${r.priority} · ${r.delay} min · 제품 ${esc(r.product)}</title><path class="route-hit" d="${d}"/><path class="route-path ${cross ? "cross" : ""} ${!r.enabled ? "disabled" : ""} ${highlight ? "highlight" : ""} ${current ? "current" : ""}" d="${d}" marker-end="url(#${highlight ? "arrow-active" : "arrow"})"/>${current ? `<circle r="4" fill="#d7a24f"><animateMotion dur="1.8s" repeatCount="indefinite" path="${d}"/></circle>` : ""}</g>`;
  }
  for (const terminal of ["INPUT", "OUTPUT"]) {
    const p = positions[terminal],
      count = Object.values(lots).filter((l) => l.location === terminal).length;
    svg += `<rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="8" class="graph-terminal"/><text x="${p.x + p.w / 2}" y="${p.y + 17}" text-anchor="middle" class="terminal-text">${terminal}</text><text x="${p.x + p.w / 2}" y="${p.y + 31}" text-anchor="middle" class="terminal-text">${count} lots</text>`;
  }
  for (const m of S.model.machines) {
    const p = positions[m.id],
      state = machines[m.id],
      waiting = Object.values(lots).filter(
        (l) => l.location === m.id && l.state === "waiting",
      ).length;
    const selected = S.selected?.id === m.id;
    svg += `<g class="machine-node ${state?.state || ""} ${selected ? "selected" : ""}" data-node="${esc(m.id)}" tabindex="0" role="button" aria-label="${esc(m.name)} 속성"><rect class="node-bg" x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="7"/><rect x="${p.x + 10}" y="${p.y + 11}" width="20" height="20" rx="5" fill="${state?.state === "busy" ? "#d7ebda" : "#eff3ea"}"/><path d="M${p.x + 15} ${p.y + 25}v-8h4v4h5v4z" fill="none" stroke="#719069" stroke-width="1.2"/><text x="${p.x + 37}" y="${p.y + 21}" class="node-name">${esc(m.name.length > 10 ? m.name.slice(0, 9) + "…" : m.name)}</text><text x="${p.x + 37}" y="${p.y + 33}" class="node-meta">${esc(m.id)} · ${m.time}m</text><line x1="${p.x + 10}" y1="${p.y + 41}" x2="${p.x + p.w - 10}" y2="${p.y + 41}" stroke="#edf2e8"/><circle cx="${p.x + 13}" cy="${p.y + 51}" r="2.4" fill="${state?.state === "busy" ? "#51a277" : state ? "#d5a45a" : "#b6c5b0"}"/><text x="${p.x + 21}" y="${p.y + 54}" class="node-status">${state ? `${esc(state.lot)} ${state.state === "busy" ? "처리" : "이동"}` : "IDLE"}</text><text x="${p.x + p.w - 10}" y="${p.y + 54}" text-anchor="end" class="node-meta">대기 ${waiting}</text></g>`;
  }
  svg += "</svg>";
  $("#graph").innerHTML = svg;
  $("#zoom-label").textContent = Math.round(S.zoom * 100) + "%";
  $$("[data-node]").forEach((g) => {
    g.onclick = () => inspectMachine(g.dataset.node);
    g.onkeydown = (e) => {
      if (e.key === "Enter") g.onclick();
    };
  });
  $$("[data-route]").forEach((g) => {
    g.onclick = () => inspectRoute(g.dataset.route);
    g.onkeydown = (e) => {
      if (e.key === "Enter") g.onclick();
    };
  });
}
function renderMetrics() {
  const { lots, time } = stateAt(),
    all = Object.values(lots),
    done = all.filter((l) => l.state === "completed");
  $("#metric-time").innerHTML = `${fmt(time)} <small>min</small>`;
  $("#metric-complete").innerHTML = `${done.length} <small>/ ${all.length}</small>`;
  $("#metric-wip").textContent = all.length - done.length;
  $("#metric-cycle").innerHTML =
    `${done.length ? fmt(done.reduce((sum, l) => sum + l.completed - l.created, 0) / done.length) : "—"} <small>min</small>`;
}
function renderPlayback() {
  const count = S.result?.events.length || 0;
  $("#timeline").max = count;
  $("#timeline").value = S.cursor;
  $("#timeline-time").textContent = fmt(stateAt().time) + " min";
  $("#timeline-end").textContent = `${S.cursor} / ${count}`;
  $("#play-button").textContent = S.playing ? "Ⅱ" : "▶";
  for (const id of ["play-button", "step-button", "rewind-button", "export-trace"])
    $("#" + id).disabled = !count;
  $("#event-count").textContent = count;
}
function selectTab(tab) {
  S.tab = tab;
  $$("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  renderTrace();
}
function renderTrace() {
  const container = $("#trace-content"),
    events = S.result?.events.slice(0, S.cursor) || [];
  if (S.tab === "console") {
    container.innerHTML = `<pre class="console ${S.parseError ? "error" : ""}">${esc([S.parseError, ...S.warnings, S.console].filter(Boolean).join("\n\n") || "진단 없음. 로컬 SimPy 런타임이 준비되었습니다.\n코드 수정 → 공정도 갱신 → 시뮬레이션 실행")}</pre>`;
    return;
  }
  if (!S.result) {
    container.innerHTML =
      '<div class="empty"><b>첫 시뮬레이션을 실행해 보세요.</b>로트가 움직일 때마다, 경로와 선택 이유가 여기에 기록됩니다.<br>상단의 실행 버튼 또는 Ctrl+Enter</div>';
    return;
  }
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
      '<p class="inline-help" style="padding:0 18px">처리 시작~종료 시간 ÷ 경과 시간. 이동은 제외하며 사용자 정의 프로세스 내부 대기는 포함합니다.</p>';
    return;
  }
  if (S.tab === "lots") {
    const lots = Object.values(stateAt().lots);
    container.innerHTML = `<table><thead><tr><th>LOT ID</th><th>제품</th><th>상태</th><th>현재 위치 → 목적지</th><th>경과 시간</th></tr></thead><tbody>${lots.map((l) => `<tr class="clickable ${S.lot === l.id ? "selected-lot" : ""}" data-lot="${esc(l.id)}"><td class="mono">${esc(l.id)}</td><td>${esc(l.product)}</td><td>${esc({ waiting: "대기", moving: "이동", processing: "처리", completed: "완료" }[l.state])}</td><td>${esc(l.location)}${l.target && l.target !== l.location ? " → " + esc(l.target) : ""}</td><td>${fmt((l.completed ?? stateAt().time) - l.created)} min</td></tr>`).join("")}</tbody></table>`;
    $$("[data-lot]").forEach((r) => (r.onclick = () => inspectLot(r.dataset.lot)));
    return;
  }
  const filtered = events
    .filter((e) => !S.lot || e.lot.id === S.lot)
    .slice(-150)
    .reverse();
  container.innerHTML =
    (S.lot
      ? `<div class="lot-filter">${esc(S.lot)} 경로 추적 중<button id="clear-lot">전체 로트 보기</button></div>`
      : "") +
    `<table><thead><tr><th>TIME (MIN)</th><th>LOT</th><th>EVENT</th><th>LOCATION</th><th>DETAIL</th></tr></thead><tbody>${filtered.map((e) => `<tr class="clickable ${e.index === S.cursor - 1 ? "current" : ""}" data-event="${e.index}"><td class="mono">${fmt(e.time)}</td><td class="mono">${esc(e.lot.id)}</td><td><span class="kind ${e.kind}">${kinds[e.kind] || esc(e.kind)}</span></td><td>${esc(e.machine || e.lot.location)}</td><td>${esc(e.kind === "decision" ? e.reason : e.kind === "move" ? `${e.lot.location} → ${e.lot.target}` : e.kind === "start" ? (e.duration === null ? "사용자 정의 SimPy 프로세스" : `처리 시간 ${fmt(e.duration)} min`) : e.reason || e.route || "—")}</td></tr>`).join("")}</tbody></table>`;
  $$("[data-event]").forEach((r) => (r.onclick = () => inspectEvent(Number(r.dataset.event))));
  if ($("#clear-lot"))
    $("#clear-lot").onclick = () => {
      S.lot = null;
      closeInspector();
      renderTrace();
      renderGraph();
    };
}
function seek(cursor) {
  S.cursor = Math.max(0, Math.min(S.result?.events.length || 0, cursor));
  renderPlayback();
  renderMetrics();
  renderGraph();
  renderTrace();
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
    status("의사결정 기록에서 재생을 멈췄습니다.");
  } else if (next === S.result.events.length) {
    pause();
    status("기록 재생 완료");
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
  return `<div id="form-error" class="form-error" role="alert"></div><div class="form-actions">${deletable ? '<button type="button" id="delete-item" class="danger">삭제</button>' : ""}<button class="primary" type="submit">코드에 적용</button></div>`;
}
function bindForm(handler) {
  $("#property-form").onsubmit = async (e) => {
    e.preventDefault();
    const button = e.submitter;
    button.disabled = true;
    try {
      await handler(new FormData(e.target));
      toast("코드와 공정도에 반영했습니다.");
    } catch (err) {
      if ($("#form-error")) $("#form-error").textContent = err.message;
      else toast(err.message);
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
  const m = newMachine
    ? { id, name: "새 머신", process: S.model.processes[0].id, line: "A", time: 5 }
    : S.model.machines.find((m) => m.id === id);
  if (!m) return;
  S.selected = { type: "machine", id };
  openInspector(
    newMachine ? "머신 추가" : "머신 속성",
    `<h3>${esc(m.name)}</h3><p>머신 하나는 한 번에 로트 하나를 처리합니다. 같은 공정·라인으로 그룹화됩니다.</p><form id="property-form">${field("ID", "id", m.id, "text", newMachine ? 'required pattern="[A-Za-z][A-Za-z0-9_-]*"' : "readonly")}${field("이름", "name", m.name, "text", "required")}${selectField(
      "공정",
      "process",
      m.process,
      S.model.processes.map((p) => [p.id, p.name]),
    )}${field("라인", "line", m.line, "text", "required")}${field("기본 처리 시간 (min)", "time", m.time, "number", 'min="0.01" step="any" required')}${actions(!newMachine)}</form>${!newMachine ? '<button id="locate-code" class="quiet" style="margin-top:15px">Python 정의로 이동 ↗</button>' : ""}`,
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
    $("#delete-item").onclick = async () => {
      try {
        await mutate((model) => {
          model.machines = model.machines.filter((m) => m.id !== id);
          model.routes = model.routes.filter((r) => r.from !== id && r.to !== id);
        });
        closeInspector();
        toast("머신과 연결 경로를 삭제했습니다.");
      } catch (e) {
        $("#form-error").textContent = e.message;
      }
    };
  }
  renderTree();
  renderGraph();
}
function inspectRoute(id, newRoute = false) {
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
    newRoute ? "이동 경로 추가" : "이동 규칙",
    `<span class="info-tag">ROUTING RULE</span><h3 style="margin-top:12px">${esc(r.from)} → ${esc(r.to)}</h3><p>숫자가 낮은 경로가 우선입니다. 서로 다른 라인도 연결할 수 있습니다. ‘*’는 모든 제품을 허용합니다.</p><form id="property-form">${field("ID", "id", r.id, "text", newRoute ? "required" : "readonly")}${selectField("출발", "from", r.from, [["INPUT", "INPUT · 투입"], ...machines])}${selectField("도착", "to", r.to, [...machines, ["OUTPUT", "OUTPUT · 완료"]])}${field("우선순위 (작을수록 우선)", "priority", r.priority, "number", 'min="0" max="1000" required')}${field("이동 시간 (min)", "delay", r.delay, "number", 'min="0" step="any" required')}${field("허용 제품 (* = 전체)", "product", r.product, "text", "required")}<label class="field"><span>경로 활성화</span><input name="enabled" type="checkbox" ${r.enabled ? "checked" : ""}></label>${actions(!newRoute)}</form>`,
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
  const p = newProcess ? { id, name: "새 공정" } : S.model.processes.find((p) => p.id === id);
  S.selected = { type: "process", id };
  openInspector(
    newProcess ? "공정 추가" : "공정 속성",
    `<p>공정 안에 라인과 머신을 구성합니다. 직렬·병렬 관계는 머신 사이 경로로 정의합니다.</p><form id="property-form">${field("ID", "id", p.id, "text", newProcess ? "required" : "readonly")}${field("이름", "name", p.name, "text", "required")}${actions(!newProcess)}</form>`,
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
            throw new Error("공정에 속한 머신을 먼저 이동하거나 삭제하세요.");
          m.processes = m.processes.filter((p) => p.id !== id);
        });
        closeInspector();
      } catch (e) {
        $("#form-error").textContent = e.message;
      }
    };
}
function inspectSettings() {
  const m = S.model;
  if (!m) return;
  S.selected = { type: "settings" };
  openInspector(
    "실행 설정",
    `<form id="property-form">${field("프로젝트 이름", "name", m.name, "text", "required")}${selectField(
      "로트 이동 결정 방식",
      "mode",
      m.mode,
      [
        ["pull", "Pull · 후공정이 로트 선택"],
        ["push", "Push · 전공정이 목적지 배정"],
      ],
    )}<p>Pull은 머신이 비었을 때 후보 로트를 선택합니다. Push는 처리를 마친 로트의 목적지를 먼저 정하고, 해당 목적지에서 FIFO로 처리합니다.</p>${field("실행 기간 (min)", "duration", m.duration, "number", 'min="0.01" max="100000" step="any" required')}${field("랜덤 시드", "seed", m.seed, "number", 'min="0" max="4294967295" step="1" required')}${field("전체 로트 수", "count", m.source.count, "number", 'min="1" max="2000" step="1" required')}${field("투입 간격 (min)", "interval", m.source.interval, "number", 'min="0.01" step="any" required')}${field("제품 순환 목록 (쉼표 구분)", "products", m.source.products.join(", "), "text", "required")}${actions()}</form>`,
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
  let html = `<span class="info-tag">${esc(kinds[e.kind])} · ${fmt(e.time)} min</span><h3 style="margin-top:13px">${esc(e.lot.id)} <span class="subtle">제품 ${esc(e.lot.product)}</span></h3><button id="track-lot" class="quiet">이 로트의 전체 경로 추적 ↗</button>`;
  if (e.kind === "decision") {
    html += `<div class="info-card selected"><b>선택 이유</b>${esc(e.reason)}<small>${e.decision_mode === "pull" && e.machine ? `${esc(e.machine)}가 준비된 로트를 선택` : "출발 로트가 다음 목적지 선택"}</small></div><h3>비교한 후보 ${e.candidates.length}개</h3>`;
    for (const c of e.candidates)
      html += `<div class="info-card ${c.id === e.chosen ? "selected" : ""}"><b>${c.id === e.chosen ? "✓ 선택" : "후순위"} · ${esc(c.lot_id)}</b><span class="route-key">${esc(c.from)} → ${esc(c.to)}</span><br>우선순위 ${c.priority} · 목적지 대기 ${c.queue_length}<br>${c.same_line ? "동일 라인" : "다른 라인 또는 입출고"} · 준비 ${fmt(c.ready_since)}m<small>${c.id === e.chosen ? "반환된 후보 ID와 일치" : "유효 후보였으나 사용자 선택 함수가 선택하지 않음"}</small></div>`;
    for (const c of e.checks.filter((c) => !c.eligible))
      html += `<div class="info-card rejected"><b>제외 · ${esc(c.lot_id)} / ${esc(c.route_id)}</b>${esc(c.reason)}</div>`;
    html +=
      "<p>후보 비교는 이 시점의 준비된 로트와 연결 경로를 기준으로 합니다. 처리 중 로트는 후보가 아닙니다.</p>";
  } else {
    html += `<div class="info-card"><b>${esc(e.lot.location)}${e.lot.target ? " → " + esc(e.lot.target) : ""}</b>${esc(e.reason || e.route || "로트 상태 변경")}${e.duration ? `<br>처리 시간: ${fmt(e.duration)} min` : ""}</div>`;
    for (const c of e.checks || [])
      html += `<div class="info-card rejected">${esc(c.route_id)} · ${esc(c.reason)}</div>`;
  }
  openInspector("의사결정 / 이벤트", html);
  $("#track-lot").onclick = () => inspectLot(e.lot.id);
  renderGraph();
}
function inspectLot(id, refresh = true) {
  S.lot = id;
  S.selected = { type: "lot", id };
  const events = (S.result?.events.slice(0, S.cursor) || []).filter((e) => e.lot.id === id);
  openInspector(
    "로트 경로 추적",
    `<h3>${esc(id)}</h3><p>현재 재생 시점까지의 이동과 선택입니다. 공정도에서 지나온 경로가 강조됩니다.</p>${
      events
        .filter((e) => ["arrival", "decision", "move", "complete", "blocked"].includes(e.kind))
        .map(
          (e) =>
            `<div class="info-card" data-lot-event="${e.index}" role="button" tabindex="0"><small>${fmt(e.time)} min · ${esc(kinds[e.kind])}</small><b>${esc(e.kind === "move" ? e.lot.location + " → " + e.lot.target : e.lot.location)}</b>${esc(e.reason || "")}</div>`,
        )
        .join("") || "<p>아직 투입되지 않은 로트입니다.</p>"
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
  if (S.job) {
    await cancelRun();
    return;
  }
  if (S.busy) return;
  S.busy = true;
  if (!(await applyCode())) {
    S.busy = false;
    return;
  }
  const source = S.source,
    revision = S.revision,
    ticket = ++runSerial;
  S.busy = true;
  pause();
  status("로컬 SimPy 실행 중…");
  $("#run-button").textContent = "■ 실행 중지";
  try {
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
      status("실행을 중지했습니다.");
      return;
    }
    const p = job.payload;
    S.console = (p.result?.console || p.console || "") + (p.traceback ? "\n" + p.traceback : "");
    if (!p.ok) throw new Error(p.error);
    if (S.revision !== revision) {
      toast("실행 중 코드가 바뀌어 이전 결과를 적용하지 않았습니다. 다시 실행하세요.");
      status("코드 변경됨 · 다시 실행 필요");
      return;
    }
    S.result = p.result;
    S.warnings = p.result.warnings;
    S.cursor = 0;
    S.lot = null;
    S.selected = null;
    $("#inspector").hidden = true;
    diagnostics();
    selectTab("events");
    seek(Math.min(S.result.events.length, 1));
    status(`SimPy 실행 완료 · ${S.result.events.length}개 이벤트 · 재생 중`);
    play();
  } catch (e) {
    S.console += "\n" + e.message;
    selectTab("console");
    toast(e.message);
    status("실행 오류 · 콘솔을 확인하세요.");
  } finally {
    if (ticket === runSerial) {
      S.job = null;
      S.busy = false;
      $("#run-button").textContent = "▶ 시뮬레이션 실행";
    }
  }
}
async function cancelRun() {
  const id = S.job;
  if (!id) return;
  await api("/api/cancel", { job_id: id });
  ++runSerial;
  S.job = null;
  S.busy = false;
  $("#run-button").textContent = "▶ 시뮬레이션 실행";
  status("실행 프로세스를 중지했습니다.");
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
  toast("Python 파일을 저장했습니다.");
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
  persist();
  updateEditor();
  invalidateTrace();
  $("#dirty-dot").textContent = "●";
  $("#sync-status").textContent = "● 코드 분석 중…";
  clearTimeout(parseTimer);
  parseTimer = setTimeout(() => applyCode(true), 500);
});
$("#code").addEventListener("scroll", () => ($("#line-numbers").scrollTop = $("#code").scrollTop));
$("#code").addEventListener("click", updateEditor);
$("#code").addEventListener("keyup", updateEditor);
$("#code").addEventListener("keydown", (e) => {
  if (e.key === "Tab") {
    e.preventDefault();
    const el = e.target;
    el.setRangeText("    ", el.selectionStart, el.selectionEnd, "end");
    el.dispatchEvent(new Event("input"));
  }
});
$("#apply-code").onclick = () => applyCode();
$("#run-button").onclick = run;
$("#save-button").onclick = save;
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
    if (file.size > 1_000_000) throw new Error("파일 한도는 1 MB입니다.");
    const source = await file.text();
    await api("/api/parse", { source });
    if (S.job) await cancelRun();
    invalidateTrace();
    setSource(source);
    closeInspector();
    await applyCode();
    toast("Python 프로젝트를 열었습니다.");
  } catch (e) {
    toast(e.message);
  } finally {
    $("#file-input").value = "";
  }
};
window.addEventListener("keydown", (e) => {
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
if (window.CodeMirror) {
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
      Tab: (cm) =>
        cm.somethingSelected() ? cm.indentSelection("add") : cm.replaceSelection("    "),
      "Shift-Tab": (cm) => cm.indentSelection("subtract"),
      "Ctrl-Space": (cm) =>
        cm.showHint({
          completeSingle: false,
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
  editor.getWrapperElement().setAttribute("aria-label", "Python 코드 편집기");
  editor.getInputField().setAttribute("aria-label", "Python 코드 입력");
  $(".code-wrap").classList.add("enhanced");
  editor.on("change", () => {
    if (settingEditor) return;
    editor.save();
    $("#code").dispatchEvent(new Event("input"));
  });
  editor.on("cursorActivity", updateEditor);
  new ResizeObserver(() => editor.refresh()).observe($(".editor-panel"));
}
async function boot() {
  try {
    const data = await api("/api/bootstrap");
    S.token = data.token;
    S.example = data.source;
    let source = data.source;
    try {
      source = localStorage.getItem("factory-studio.source.v1") || source;
    } catch {}
    setSource(source);
    await applyCode(true);
    renderPlayback();
    renderTrace();
    status("준비됨 · 예제 공장을 실행하거나 모델을 편집하세요.");
  } catch (e) {
    status("서버 연결 실패");
    toast(e.message);
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
  }),
  pause,
};
