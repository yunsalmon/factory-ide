"use strict";
const PLANNER={finalView:false,filters:{},selected:null};
function plannerProjection() {
  const result=S.result || {model:S.model,events:[]};
  const inventory=typeof inventoryProjection==="function" ? inventoryProjection(result,S.cursor,{finalView:PLANNER.finalView}) : null;
  return orderProjection(result,S.cursor,{...PLANNER,inventory});
}
function orderValue(value) {return value==null?tr("order_unknown"):typeof value==="number"?new Intl.NumberFormat(locale,{maximumFractionDigits:3}).format(value):value;}
function plannerColumns() {return [
  [tr("order_id"),r=>r.id],[tr("order_product"),r=>r.product],[tr("order_quantity"),r=>r.quantity],
  [tr("order_release"),r=>r.release_time],[tr("order_due"),r=>r.due_time],[tr("order_priority"),r=>r.priority],
  [tr("order_progress"),r=>tr("order_"+r.progress)],[tr("order_risk"),r=>tr("order_"+r.risk)],
  [tr("order_throughput"),r=>r.completed_quantity],[tr("order_wip"),r=>r.wip_quantity],[tr("order_unreleased_qty"),r=>r.unreleased_quantity],
  [tr("order_slack"),r=>r.slack],[tr("order_tardiness"),r=>r.tardiness],[tr("order_cycle"),r=>r.cycle_time],
  [tr("order_customer"),r=>r.customer],[tr("order_reference"),r=>r.reference],[tr("order_due_date"),r=>r.due_date]];}
function renderPlanner() {
  const p=plannerProjection(),columns=plannerColumns();
  const focus=document.activeElement?.id,selection=document.activeElement?.selectionStart;
  const selector=(id,values,value)=>`<select id="${id}">${values.map(v=>`<option value="${v}" ${v===value?"selected":""}>${tr("order_"+(v||"all"))}</option>`).join("")}</select>`;
  $("#trace-content").innerHTML=`<section class="order-planner" aria-label="${tr("order_title")}"><div class="order-controls">
    <label>${tr("order_view")}${selector("order-view",["replay","final"],PLANNER.finalView?"final":"replay")}</label>
    <label>${tr("order_progress")}${selector("order-progress",["","unreleased","released","completed"],PLANNER.filters.progress||"")}</label>
    <label>${tr("order_risk")}${selector("order-risk",["","no_due","on_time","at_risk","late"],PLANNER.filters.risk||"")}</label>
    <label>${tr("order_search")}<input id="order-search" type="search" value="${esc(PLANNER.filters.search||"")}"></label>
    <button id="order-reset">${tr("order_reset")}</button><button id="order-csv">${tr("order_csv")}</button>
    <button id="order-export">${tr("order_export")}</button>${PUBLIC_DEMO?"":`<button id="order-edit">${tr("order_edit")}</button><label>${tr("order_import")}<input id="order-import" type="file" accept="application/json,.json"></label>`}</div>
    <p id="order-context" role="status">${tr(PLANNER.finalView?"order_final":"order_replay")} · ${tr("order_cursor")} ${p.cursor} · ${orderValue(p.time)} min</p>
    <p>${tr("order_rules",[S.model?.order_risk_window ?? 30])}</p><p>${tr("order_dispatch_note")}</p>
    <dl class="order-totals">${[["count",p.totals.orders],["quantity",p.totals.quantity],["throughput",p.totals.throughput],["wip",p.totals.wip],["unreleased_qty",p.totals.unreleased],["attainment",p.totals.attainment==null?null:Math.round(p.totals.attainment*100)+"%"],["mean_cycle",p.totals.mean_cycle_time]].map(([key,value])=>`<div><dt>${tr("order_"+key)}</dt><dd id="order-total-${key}">${orderValue(value)}</dd></div>`).join("")}</dl>
    ${p.unassignedLots.length?`<p>${tr("order_legacy",[p.unassignedLots.length])}</p>`:""}
    <div class="order-table-wrap" role="region" tabindex="0" aria-label="${tr("order_title")}"><table id="order-table"><caption>${tr("order_title")}</caption><thead><tr>${columns.map(([label])=>`<th scope="col">${label}</th>`).join("")}</tr></thead><tbody>${p.rows.map((r,i)=>`<tr>${columns.map(([,get],j)=>j?`<td>${esc(orderValue(get(r)))}</td>`:`<th scope="row"><button data-order-row="${i}" aria-pressed="${PLANNER.selected===r.id}">${esc(r.id)}</button></th>`).join("")}</tr>`).join("")}</tbody></table></div>
    ${!p.rows.length?`<p>${tr("order_empty")}</p>`:""}<section id="order-detail" aria-label="${tr("order_detail")}"></section></section>`;
  $("#order-view").onchange=e=>{PLANNER.finalView=e.target.value==="final";renderPlanner();};
  for(const key of ["progress","risk"])$("#order-"+key).onchange=e=>{PLANNER.filters[key]=e.target.value;renderPlanner();};
  $("#order-search").oninput=e=>{PLANNER.filters.search=e.target.value;renderPlanner();};
  $("#order-reset").onclick=()=>{PLANNER.filters={};renderPlanner();};
  $("#order-csv").onclick=()=>download("factory_orders.csv",orderCSV(p,[...columns,[tr("order_cursor"),()=>p.cursor],[tr("order_time"),()=>p.time],[tr("order_view"),()=>tr(PLANNER.finalView?"order_final":"order_replay")]]),"text/csv;charset=utf-8");
  $("#order-export").onclick=()=>download("factory_order_plan.json",JSON.stringify(plannerDocument(),null,2),"application/json");
  if(!PUBLIC_DEMO){$("#order-edit").onclick=editOrderPlan;$("#order-import").onchange=async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>500000)throw Error(tr("order_document_invalid"));await applyOrderDocument(JSON.parse(await file.text()));}catch(error){toast(error.detail||{code:"order_document_invalid"});}};}
  $$('[data-order-row]').forEach(el=>el.onclick=()=>{pause();PLANNER.selected=p.rows[Number(el.dataset.orderRow)].id;renderPlanner();$("#order-detail").scrollIntoView({block:"nearest"});});
  const row=p.rows.find(r=>r.id===PLANNER.selected);
  if(row) {
    $("#order-detail").innerHTML=`<h3>${esc(row.id)} · ${tr("order_detail")}</h3><p>${tr("order_locations")}: ${esc(Object.entries(row.locations).map(([k,v])=>`${k==="unreleased"?tr("order_unreleased"):k}: ${orderValue(v)}`).join(" · "))}</p>
      ${row.lots.map((lot,i)=>`<details><summary>${esc(lot.id)} · ${tr("order_quantity")} ${orderValue(lot.quantity)} · ${tr("order_"+lot.state)}</summary><dl>${[["release",lot.release_time],["actual_release",lot.released_at],["admission",lot.admitted_at],["location",lot.location],["operation",lot.operation],["slack",lot.slack],["tardiness",lot.tardiness],["cycle",lot.cycle_time]].map(([k,v])=>`<dt>${tr("order_"+k)}</dt><dd>${esc(orderValue(v))}</dd>`).join("")}</dl>
      ${lot.released?`<button data-order-lot="${i}">${tr("order_graph")}</button>`:""}<h4>${tr("order_history")}</h4><ol>${lot.history.map(index=>{const event=S.result.events[index];return `<li>${orderValue(event.time)} min · ${esc(["machine_state","buffer_enter","transport_arrive","buffer_wait"].includes(event.kind)?tr("order_"+event.kind):kinds[event.kind]||event.kind)} · ${esc(event.machine||event.lot.location)}${event.kind==="decision"?`<p>${esc(localized(event.reason,event.reason_message))}</p><p>${tr(event.selection_policy==="default_route"?"order_default_reason":"order_custom_reason")}</p><button data-order-decision="${index}">${tr("order_allocation")}</button>`:""}</li>`;}).join("")}</ol></details>`).join("")}`;
    $$('[data-order-lot]').forEach(el=>el.onclick=()=>{const lot=row.lots[Number(el.dataset.orderLot)];pause();if(PLANNER.finalView)seek(p.cursor);S.lot=lot.id;renderGraph();inspectLot(lot.id);});
    $$('[data-order-decision]').forEach(el=>el.onclick=()=>{pause();S.comparison=Number(el.dataset.orderDecision);seek(S.comparison+1,true);selectTab("allocations");});
  }
  if(focus?.startsWith("order-")&&$("#"+focus)){const el=$("#"+focus);el.focus({preventScroll:true});if(selection!=null&&el.type==="search")el.setSelectionRange(selection,selection);}
}
function plannerDocument() {return {schema_version:1,orders:copy(S.model?.orders || []),...(S.model?.time_origin?{time_origin:S.model.time_origin}:{}),order_risk_window:S.model?.order_risk_window ?? 30};}
async function applyOrderDocument(doc) {
  if(!doc||Array.isArray(doc)||doc.schema_version!==1||!Array.isArray(doc.orders)||Object.keys(doc).some(k=>!["schema_version","orders","time_origin","order_risk_window"].includes(k)))throw Error(tr("order_document_invalid"));
  await mutate(model=>{model.orders=doc.orders;if(doc.time_origin!==undefined)model.time_origin=doc.time_origin;else delete model.time_origin;if(doc.order_risk_window!==undefined)model.order_risk_window=doc.order_risk_window;else delete model.order_risk_window;});
  PLANNER.selected=null;selectTab("orders");
}
function editOrderPlan() {
  const dialog=document.createElement("dialog");dialog.className="order-editor";
  dialog.innerHTML=`<form><label>${tr("order_edit")}<textarea id="order-document" rows="16"></textarea></label><p>${tr("order_editor_help")}</p><p id="order-error" role="alert"></p><button type="submit">${tr("order_apply")}</button><button type="button" id="order-close">${tr("order_close")}</button></form>`;
  document.body.append(dialog);dialog.querySelector('textarea').value=JSON.stringify(plannerDocument(),null,2);
  dialog.querySelector('form').onsubmit=async e=>{e.preventDefault();try{await applyOrderDocument(JSON.parse(dialog.querySelector('textarea').value));dialog.close();}catch(error){dialog.querySelector('#order-error').textContent=error.detail?localized(error.message,error.detail):error instanceof SyntaxError?tr("order_document_invalid"):error.message||tr("order_document_invalid");}};
  dialog.querySelector('#order-close').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();dialog.showModal();
}
