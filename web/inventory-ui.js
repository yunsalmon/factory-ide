"use strict";
// UI state deliberately stays separate from allocation-result filters.
const WIP = {finalView:false, filters:{}, groupBy:"location", selectedGroup:null, selectedLot:null};
function wipProjection() { return inventoryProjection(S.result,S.cursor,WIP); }
const wipFormatters = new Map();
function wipValue(value) {
  if (value == null) return tr("wip_unknown");
  if (typeof value !== "number") return value || "—";
  let formatter = wipFormatters.get(locale);
  if (!formatter) { formatter = new Intl.NumberFormat(locale,{maximumFractionDigits:2}); wipFormatters.set(locale,formatter); }
  return formatter.format(value);
}
function wipColumns() { return [
  [tr("wip_lot"),r=>r.id], [tr("wip_product"),r=>r.product], [tr("wip_quantity"),r=>r.quantity],
  [tr("wip_process"),r=>r.process], [tr("wip_line"),r=>r.line], [tr("wip_location"),r=>r.location],
  [tr("wip_kind"),r=>tr("wip_"+r.kind)], [tr("wip_status"),r=>tr("wip_"+r.status)],
  [tr("wip_arrival"),r=>r.arrival], [tr("wip_release"),r=>r.release], [tr("wip_operation"),r=>r.operation],
  [tr("wip_target"),r=>r.target], [tr("wip_wait"),r=>r.wait]
]; }
function highlightWipGraph(projection = null) {
  $$(".wip-highlight").forEach(el=>el.classList.remove("wip-highlight"));
  if (S.tab !== "wip" || !S.result) return;
  projection ||= wipProjection();
  const selected=WIP.selectedLot ? projection.rows.filter(r=>r.id===WIP.selectedLot) : projection.groups.find(g=>g.key===WIP.selectedGroup)?.rows || [];
  const nodes=new Set(selected.flatMap(r=>[r.node,r.physical]));
  const routes=new Set(selected.filter(r=>r.status==="moving").map(r=>r.route));
  $$("[data-node], [data-terminal]").forEach(el=>el.classList.toggle("wip-highlight",nodes.has(el.dataset.node||el.dataset.terminal)));
  $$("[data-route]").forEach(el=>el.classList.toggle("wip-highlight",routes.has(el.dataset.route)));
}
function renderInventory(projection = null) {
  const p=projection || wipProjection(), columns=wipColumns();
  if (WIP.selectedLot && !p.rows.some(r=>r.id===WIP.selectedLot)) WIP.selectedLot=null;
  if (WIP.selectedGroup && !p.groups.some(g=>g.key===WIP.selectedGroup)) WIP.selectedGroup=null;
  const select=(key,values,label)=>`<label>${tr(label)} <select data-wip-filter="${key}"><option value="">${tr("wip_all")}</option>${values.map(value=>`<option value="${esc(value)}" ${WIP.filters[key]===value?"selected":""}>${esc(key==="status"?tr("wip_"+value):value)}</option>`).join("")}</select></label>`;
  const totals=p.totals;
  $("#trace-content").innerHTML=`<section class="wip-dashboard" aria-label="${tr("wip_title")}">
    <div class="wip-controls"><label>${tr("wip_view")} <select id="wip-view"><option value="replay" ${!WIP.finalView?"selected":""}>${tr("wip_replay")}</option><option value="final" ${WIP.finalView?"selected":""}>${tr("wip_final")}</option></select></label>
    <label>${tr("wip_group")} <select id="wip-group">${["location","process","line"].map(k=>`<option value="${k}" ${WIP.groupBy===k?"selected":""}>${tr("wip_"+k)}</option>`).join("")}</select></label>
    ${["process","line","location","status"].map(key=>select(key,[...new Set(p.allRows.map(r=>r[key]).filter(Boolean))].sort(),"wip_"+key)).join("")}
    <label>${tr("wip_search")} <input id="wip-search" type="search" value="${esc(WIP.filters.search||"")}"></label><button id="wip-reset">${tr("wip_reset")}</button><button id="wip-csv">${tr("wip_csv")}</button></div>
    <p id="wip-context" role="status">${tr(WIP.finalView?"wip_final":"wip_replay")} · ${tr("wip_cursor")} ${p.cursor} · ${wipValue(p.time)} min</p>
    <p>${tr(WIP.finalView?"wip_final_note":"wip_replay_note")}</p>
    <dl class="wip-totals"><div><dt>${tr("wip_visible")}</dt><dd id="wip-visible">${totals.count}</dd></div><div><dt>${tr("wip_wip")}</dt><dd id="wip-total">${totals.wip}</dd></div><div><dt>${tr("wip_all_wip")}</dt><dd>${p.allTotals.wip}</dd></div><div><dt>${tr("wip_quantity")}</dt><dd>${wipValue(totals.quantity)}</dd></div></dl>
    <p>${tr("wip_rules")}</p><div class="wip-table-wrap" tabindex="0" role="region" aria-label="${tr("wip_groups")}"><table id="wip-groups"><caption>${tr("wip_groups")}</caption><thead><tr>${["group","kind","count","quantity","mix","oldest_wait","average","oldest","congestion"].map(k=>`<th scope="col">${tr("wip_"+k)}</th>`).join("")}</tr></thead><tbody>${p.groups.map((g,i)=>`<tr><th scope="row"><button data-wip-group="${i}" aria-pressed="${WIP.selectedGroup===g.key}">${esc(g.label||tr("wip_unassigned"))}</button></th><td>${p.groupBy==="location"?tr("wip_"+g.kind):"—"}</td><td>${g.count}</td><td>${wipValue(g.quantity)}</td><td>${esc(Object.entries(g.mix).map(([k,v])=>`${k}: ${v}`).join(", "))}</td><td>${wipValue(g.oldestWait)}</td><td>${wipValue(g.averageWait)}</td><td>${esc(g.oldest||"—")}</td><td>${tr("wip_cue_"+g.congestion)}${g.capacity!==null?` (${g.occupancy}/${g.capacity})`:""}</td></tr>`).join("")}</tbody></table></div>
    <div class="wip-table-wrap" tabindex="0" role="region" aria-label="${tr("wip_lots")}"><table id="wip-lots"><caption>${tr("wip_lots")}</caption><thead><tr>${columns.map(([label])=>`<th scope="col">${label}</th>`).join("")}</tr></thead><tbody>${p.rows.map((r,i)=>`<tr>${columns.map(([,get],j)=>j===0?`<th scope="row"><button data-wip-lot="${i}" aria-pressed="${WIP.selectedLot===r.id}">${esc(r.id)}</button></th>`:`<td>${esc(wipValue(get(r)))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
    ${p.rows.length?"":`<p>${tr("wip_empty")}</p>`}<section id="wip-detail" aria-label="${tr("wip_detail")}"></section></section>`;
  $("#wip-view").onchange=e=>{WIP.finalView=e.target.value==="final";renderGraph();renderInventory();};
  $("#wip-group").onchange=e=>{WIP.groupBy=e.target.value;WIP.selectedGroup=null;renderInventory();};
  $$("[data-wip-filter]").forEach(el=>el.onchange=()=>{WIP.filters[el.dataset.wipFilter]=el.value;renderInventory();});
  $("#wip-search").oninput=e=>{const start=e.target.selectionStart;WIP.filters.search=e.target.value;renderInventory();$("#wip-search").focus();try{$("#wip-search").setSelectionRange(start,start);}catch{}};
  $("#wip-reset").onclick=()=>{WIP.filters={};WIP.selectedLot=null;WIP.selectedGroup=null;renderInventory();};
  $("#wip-csv").onclick=()=>download("factory_wip.csv",inventoryCSV(p,columns,{[tr("wip_cursor")]:p.cursor,[tr("wip_time")]:p.time,[tr("wip_view")]:tr(WIP.finalView?"wip_final":"wip_replay")}),"text/csv;charset=utf-8");
  $$("[data-wip-group]").forEach(el=>el.onclick=()=>{pause();WIP.selectedGroup=p.groups[Number(el.dataset.wipGroup)].key;WIP.selectedLot=null;renderInventory(p);$("#graph-viewport").scrollIntoView({block:"nearest"});});
  $$("[data-wip-lot]").forEach(el=>el.onclick=()=>{pause();WIP.selectedLot=p.rows[Number(el.dataset.wipLot)].id;WIP.selectedGroup=null;renderInventory(p);$("#wip-detail").scrollIntoView({block:"nearest"});});
  const row=p.rows.find(r=>r.id===WIP.selectedLot);
  const selectedRows=row?[row]:p.groups.find(g=>g.key===WIP.selectedGroup)?.rows||[];
  if (selectedRows.length) {
    $("#wip-detail").innerHTML=`<h3>${tr("wip_detail")}</h3>${selectedRows.map(r=>`<details ${row?"open":""}><summary>${esc(r.id)} · ${esc(r.product)} · ${esc(r.location)}</summary><p>${tr("wip_physical")}: ${esc(r.physical)} · ${tr("wip_location_arrival")}: ${wipValue(r.locationArrival)} min</p><h4>${tr("wip_history")}</h4><ol>${r.history.map(index=>{const e=S.result.events[index];return `<li>${wipValue(e.time)} min · ${esc(kinds[e.kind]||e.kind)} · ${esc(e.lot.location)} ${e.reason?`· ${esc(localized(e.reason,e.reason_message))}`:""}</li>`;}).join("")}</ol>${r.decision!==null?`<button data-wip-reason="${r.decision}">${tr("wip_reason")}</button>`:`<p>${tr("wip_no_reason")}</p>`}</details>`).join("")}`;
    $$("[data-wip-reason]").forEach(el=>el.onclick=()=>{WIP.finalView=false;pause();S.comparison=Number(el.dataset.wipReason);seek(S.comparison+1,true);selectTab("allocations");});
  }
  highlightWipGraph(p);
}
