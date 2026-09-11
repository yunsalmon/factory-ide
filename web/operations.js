"use strict";
// User-defined IDs and families must never resolve Object.prototype properties.
const opDictionary = (values = {}) => Object.assign(Object.create(null), values);
const opOwn = (map, key, fallback) => map && Object.hasOwn(map, key) ? map[key] : fallback;
const operationStates = ['idle','reserved','processing','setup','down','maintenance','blocked','starved','offshift','resource_wait'];
const opText = key => `<span data-op-i18n="${key}">${esc(tr(key))}</span>`;
const opInput = (key,name,value,type='text',extra='') => `<label class="field">${opText(key)}<input name="${esc(name)}" value="${esc(value ?? '')}" type="${type}" ${extra}></label>`;
const opSelect = (key,name,value,options) => `<label class="field">${opText(key)}<select name="${name}">${options.map(([id,key])=>`<option value="${id}" data-op-i18n="${key}" ${value===id?'selected':''}>${tr(key)}</option>`).join('')}</select></label>`;
const opRemove = () => `<button type="button" class="ops-remove" data-op-i18n="ops_remove">${tr('ops_remove')}</button>`;
function refreshOperationLocale() {
  document.querySelectorAll('[data-op-i18n]').forEach(el=>el.textContent=tr(el.dataset.opI18n));
  if (S.selected?.type === 'operations') $('#inspector-title').textContent=tr('ops_configure');
  if (S.selected?.type === 'resources') $('#inspector-title').textContent=tr('ops_resources');
}
function operationView(result, cursor, final) {
  const events=result?.events.slice(0,cursor)||[];
  const horizon=final ? result?.model.duration||0 : events.at(-1)?.time||0;
  const model=result?.model||S.model;
  const current=opDictionary(copy(result?.initial_state?.machine_operations||Object.fromEntries((model?.machines||[]).map(m=>[m.id,{state:'idle',lot:null,since:0,cause:'initialized'}]))));
  const resources=opDictionary(copy(result?.initial_state?.resources||{})), intervals=[], totals=opDictionary();
  for (const id of Object.keys(current)) totals[id]=Object.fromEntries(operationStates.map(s=>[s,0]));
  function close(id,op,end) {const duration=Math.max(0,end-op.since); if(duration) intervals.push({...op,machine:id,start:op.since,end,duration}); totals[id][op.state]=(totals[id][op.state]||0)+duration;}
  for(const e of events) {
    for(const [id,op] of Object.entries(e.state_changes?.machine_operations||{})){close(id,current[id],e.time);current[id]=op;}
    Object.assign(resources,copy(e.state_changes?.resources||{}));
  }
  for(const [id,op] of Object.entries(current)) close(id,op,horizon);
  return {horizon,totals,intervals,resources};
}
function renderOperations() {
  const final=S.operationScope!=='cursor', result=S.result;
  const view=operationView(result,final?result?.events.length:S.cursor,final);
  const rows=Object.entries(view.totals).map(([mid,totals])=>`<tr><td>${esc(mid)}</td>${operationStates.map(state=>`<td data-state="${state}">${fmt(totals[state])}</td>`).join('')}<td class="ops-total">${fmt(Object.values(totals).reduce((a,b)=>a+b,0))}</td></tr>`).join('');
  $('#trace-content').innerHTML=`<div class="ops-panel"><div class="ops-toolbar"><b>${tr('ops_title')}</b><select id="ops-scope" aria-label="${tr('ops_scope')}"><option value="final" ${final?'selected':''}>${tr('ops_final')}</option><option value="cursor" ${!final?'selected':''}>${tr('ops_cursor')}</option></select>${PUBLIC_DEMO?'':`<button id="ops-configure">${tr('ops_configure')}</button><button id="ops-resources">${tr('ops_resources')}</button>`}</div><p>${tr('ops_horizon')}: <strong id="ops-horizon">${fmt(view.horizon)}</strong> min · ${tr('ops_reconcile')}</p><div class="ops-scroll"><table id="ops-kpis"><thead><tr><th>${tr('ops_machine')}</th>${operationStates.map(s=>`<th>${tr('ops_state_'+s)}</th>`).join('')}<th>${tr('ops_total')}</th></tr></thead><tbody>${rows}</tbody></table></div><h3>${tr('ops_timeline')}</h3>${Object.keys(view.totals).map(mid=>`<div class="ops-time-row"><b>${esc(mid)}</b><div class="ops-time-track">${view.intervals.filter(i=>i.machine===mid).map(i=>`<span class="ops-span ops-${i.state}" style="left:${view.horizon?100*i.start/view.horizon:0}%;width:${view.horizon?100*i.duration/view.horizon:0}%" title="${esc(tr('ops_state_'+i.state))} · ${fmt(i.start)}–${fmt(i.end)} min · ${esc(i.lot||'')} · ${esc(operationCause(i.cause))}">${tr('ops_state_'+i.state)}</span>`).join('')}</div></div>`).join('')}<details><summary>${tr('ops_intervals')}</summary><div class="ops-scroll"><table id="ops-intervals"><thead><tr><th>${tr('ops_machine')}</th><th>${tr('ops_state')}</th><th>${tr('ops_start')}</th><th>${tr('ops_end')}</th><th>${tr('ops_duration')}</th><th>${tr('ops_lot')}</th><th>${tr('ops_cause')}</th></tr></thead><tbody>${view.intervals.map(i=>`<tr><td>${esc(i.machine)}</td><td>${tr('ops_state_'+i.state)}</td><td>${fmt(i.start)}</td><td>${fmt(i.end)}</td><td>${fmt(i.duration)}</td><td>${esc(i.lot||'—')}</td><td>${esc(operationCause(i.cause))}</td></tr>`).join('')}</tbody></table></div></details><details><summary>${tr('ops_changeovers')}</summary><div class="ops-scroll"><table id="ops-changeovers"><thead><tr><th>${tr('ops_machine')}</th><th>${tr('ops_lot')}</th><th>${tr('ops_from_family')}</th><th>${tr('ops_to_family')}</th><th>${tr('ops_duration')}</th></tr></thead><tbody>${(result?.events.slice(0,final?result.events.length:S.cursor)||[]).filter(e=>e.kind==='setup_plan').map(e=>`<tr><td>${esc(e.machine)}</td><td>${esc(e.lot.id)}</td><td>${esc(e.previous_family||'*')}</td><td>${esc(e.family)}</td><td>${fmt(e.duration)}</td></tr>`).join('')}</tbody></table></div></details><h3>${tr('ops_resources')}</h3><div id="ops-resource-state">${Object.entries(view.resources).map(([id,r])=>`<div class="info-card"><b>${esc(id)} · ${tr('ops_capacity')} ${r.capacity}</b><p>${tr('ops_holders')}: ${r.holders.map(h=>`${esc(h.lot)} / ${esc(h.machine||'OUTPUT')} (${h.units})`).join(', ')||'—'}</p><p>${tr('ops_waiters')}: ${r.waiters.map(h=>`${esc(h.lot)} / ${esc(h.machine||'OUTPUT')} (${h.units})`).join(', ')||'—'}</p></div>`).join('')||tr('ops_no_resources')}</div></div>`;
  $('#ops-scope').onchange=e=>{S.operationScope=e.target.value;renderOperations();};
  if(!PUBLIC_DEMO){$('#ops-configure').onclick=()=>inspectOperations(S.model.machines[0].id);$('#ops-resources').onclick=inspectSharedResources;}
}
function operationCause(cause) {
  const key='ops_cause_'+cause;
  return translations[locale]?.[key]?tr(key):cause;
}
function bindOpsRows() {
  $$('.ops-remove').forEach(b=>b.onclick=()=>b.closest('.ops-edit-row').remove());
}
function opsWindowRow(window={}, index=0) {
  return `<div class="ops-edit-row ops-window">${opSelect('ops_state',`window_kind_${index}`,window.kind||'offshift',[['offshift','ops_state_offshift'],['down','ops_state_down'],['maintenance','ops_state_maintenance'],['failure','ops_failure']])}${opInput('ops_start',`window_start_${index}`,window.start??0,'number','min="0" step="any" required')}${opInput('ops_end',`window_end_${index}`,window.end??1,'number','min="0" step="any" required')}${opInput('ops_cause',`window_cause_${index}`,window.cause||'')}${opRemove()}</div>`;
}
function opsSetupRow(from='',to='',duration=0,index=0) {
  return `<div class="ops-edit-row ops-setup-row">${opInput('ops_from_family',`family_from_${index}`,from)}${opInput('ops_to_family',`family_to_${index}`,to)}${opInput('ops_duration',`setup_duration_${index}`,duration,'number','min="0" step="any" required')}${opRemove()}</div>`;
}
function bindOperationsForm(handler) {
  $('#ops-form').onsubmit=async e=>{
    e.preventDefault();const button=e.target.querySelector('[type="submit"]');button.disabled=true;
    try{await handler(new FormData(e.target));toast({code:'ops_saved'});}
    catch(error){$('#ops-error').textContent=displayMessage(error.detail||error.message);}
    finally{button.disabled=false;}
  };
}
function opsActions(){return `<div id="ops-error" role="alert" class="form-error"></div><button type="submit" class="primary" data-op-i18n="ops_apply">${tr('ops_apply')}</button>`;}
function inspectOperations(id) {
  if(PUBLIC_DEMO)return;
  const m=S.model.machines.find(m=>m.id===id);if(!m)return;
  S.selected={type:'operations',id};
  const windows=[...(m.availability||[]).map(w=>({...w,kind:w.state})),...(m.maintenance||[]).map(w=>({...w,kind:'maintenance'})),...(Array.isArray(m.failures)?m.failures:[]).map(w=>({kind:'failure',start:w.start,end:w.start+w.repair_time}))];
  const setup=Object.entries(m.setup_matrix||{}).flatMap(([from,targets])=>Object.entries(targets).map(([to,duration])=>({from,to,duration})));
  const seeded=m.failures&&!Array.isArray(m.failures);
  openInspector(tr('ops_configure'),`<label class="field">${opText('ops_machine')}<select id="ops-machine">${S.model.machines.map(x=>`<option value="${esc(x.id)}" ${x.id===id?'selected':''}>${esc(x.name)} (${esc(x.id)})</option>`).join('')}</select></label><p>${opText('ops_preemption')}</p><form id="ops-form">${opInput('ops_setup_default','setup_time',m.setup_time||0,'number','min="0" step="any" required')}${opInput('ops_initial_family','initial_family',m.initial_family||'')}<h3>${opText('ops_setup_matrix')}</h3><div id="ops-setup-rows">${setup.map((r,i)=>opsSetupRow(r.from,r.to,r.duration,i)).join('')}</div><button id="ops-add-setup" type="button">${opText('ops_add')}</button><h3>${opText('ops_windows')}</h3><div id="ops-window-rows">${windows.map(opsWindowRow).join('')}</div><button id="ops-add-window" type="button">${opText('ops_add')}</button>${opSelect('ops_failure_mode','failure_mode',seeded?'seeded':m.failures?.length?'deterministic':'none',[['none','ops_none'],['seeded','ops_seeded'],['deterministic','ops_deterministic']])}${opInput('ops_mtbf','mtbf',seeded?m.failures.mtbf:60,'number','min="0.01" step="any" required')}${opInput('ops_repair','repair_time',seeded?m.failures.repair_time:5,'number','min="0.01" step="any" required')}${opInput('ops_seed','failure_seed',seeded?m.failures.seed||0:0,'number','min="0" max="4294967295" step="1" required')}<h3>${opText('ops_requirements')}</h3>${(S.model.resources||[]).map(r=>`<fieldset><legend>${esc(r.id)}</legend>${opInput('ops_state_setup',`setup_resource_${r.id}`,opOwn(m.resource_requirements?.setup,r.id,0),'number',`min="0" max="${r.capacity}" step="1" required`)}${opInput('ops_state_processing',`processing_resource_${r.id}`,opOwn(m.resource_requirements?.processing,r.id,0),'number',`min="0" max="${r.capacity}" step="1" required`)}</fieldset>`).join('')}${opsActions()}</form>`);
  $('#ops-machine').onchange=e=>inspectOperations(e.target.value);
  $('#ops-add-setup').onclick=()=>{$('#ops-setup-rows').insertAdjacentHTML('beforeend',opsSetupRow('','',0,Date.now()));bindOpsRows();};
  $('#ops-add-window').onclick=()=>{$('#ops-window-rows').insertAdjacentHTML('beforeend',opsWindowRow({},Date.now()));bindOpsRows();};
  bindOpsRows();
  bindOperationsForm(async f=>{
    const matrix=opDictionary();for(const row of $$('.ops-setup-row')){const values=[...row.querySelectorAll('input')].map(el=>el.value);if(!values[0]&&!values[1])continue;if(!values[0]||!values[1])throw new Error(tr('ops_invalid',['setup_matrix']));if(Object.hasOwn(matrix[values[0]]||opDictionary(),values[1]))throw new Error(tr('ops_invalid',['setup_matrix']));(matrix[values[0]]||=opDictionary())[values[1]]=Number(values[2]);}
    const periods=$$('.ops-window').map(row=>{const inputs=row.querySelectorAll('input');return {kind:row.querySelector('select').value,start:Number(inputs[0].value),end:Number(inputs[1].value),...(inputs[2].value.trim()?{cause:inputs[2].value.trim()}:{})};});
    if(periods.some(w=>w.end<=w.start))throw new Error(tr('ops_invalid',['start/end']));
    if(periods.some(w=>w.kind==='failure')&&f.get('failure_mode')!=='deterministic')throw new Error(tr('ops_invalid',['failure_mode']));
    await mutate(model=>{
      const target=model.machines.find(x=>x.id===id);
      target.setup_time=Number(f.get('setup_time'));target.initial_family=f.get('initial_family').trim()||null;
      if(Object.keys(matrix).length||m.setup_matrix)target.setup_matrix=matrix;
      target.availability=periods.filter(w=>['offshift','down'].includes(w.kind)).map(({kind,...w})=>({...w,state:kind})).sort((a,b)=>a.start-b.start);
      target.maintenance=periods.filter(w=>w.kind==='maintenance').map(({kind,...w})=>w);
      target.failures=f.get('failure_mode')==='seeded'?{mtbf:Number(f.get('mtbf')),repair_time:Number(f.get('repair_time')),seed:Number(f.get('failure_seed'))}:f.get('failure_mode')==='deterministic'?periods.filter(w=>w.kind==='failure').map(w=>({start:w.start,repair_time:w.end-w.start})).sort((a,b)=>a.start-b.start):[];
      target.resource_requirements=opDictionary();for(const phase of ['setup','processing']){target.resource_requirements[phase]=opDictionary();for(const r of model.resources||[]){const value=Number(f.get(`${phase}_resource_${r.id}`));if(value)target.resource_requirements[phase][r.id]=value;}}
    });inspectOperations(id);
  });
}
function resourceEditRow(r={},i=0){return `<div class="ops-edit-row ops-resource-row">${opInput('ops_id',`resource_id_${i}`,r.id||'')}${opSelect('ops_kind',`resource_kind_${i}`,r.kind||'operator',[['operator','ops_operator'],['tool','ops_tool'],['transport','ops_transport']])}${opInput('ops_capacity',`resource_capacity_${i}`,r.capacity||1,'number','min="1" max="1000" step="1" required')}${opRemove()}</div>`;}
function inspectSharedResources() {
  if(PUBLIC_DEMO)return;S.selected={type:'resources'};
  const resources=S.model.resources||[];
  openInspector(tr('ops_resources'),`<form id="ops-form"><p>${opText('ops_atomic')}</p><div id="ops-resource-rows">${resources.map(resourceEditRow).join('')}</div><button type="button" id="ops-add-resource">${opText('ops_add')}</button><h3>${opText('ops_families')}</h3>${[...new Set(S.model.source.products)].map((product,i)=>`<label class="field">${esc(product)}<input class="ops-product-family" data-product="${esc(product)}" name="product_family_${i}" value="${esc(opOwn(S.model.product_families,product,product))}" required></label>`).join('')}<h3>${opText('ops_route_resources')}</h3>${S.model.routes.map((route,i)=>`<fieldset><legend>${esc(route.id)} (${esc(route.from)} → ${esc(route.to)})</legend>${resources.map(r=>opInput('ops_units',`route_${i}_${r.id}`,opOwn(route.resources,r.id,0),'number',`min="0" max="${r.capacity}" step="1" data-op-route="${esc(route.id)}" data-resource="${esc(r.id)}" required`)+`<small>${esc(r.id)}</small>`).join('')}</fieldset>`).join('')}${opsActions()}</form>`);
  $('#ops-add-resource').onclick=()=>{$('#ops-resource-rows').insertAdjacentHTML('beforeend',resourceEditRow({},Date.now()));bindOpsRows();};bindOpsRows();
  bindOperationsForm(async()=>{
    const definitions=$$('.ops-resource-row').map(row=>{const inputs=row.querySelectorAll('input');return {...(S.model.resources||[]).find(r=>r.id===inputs[0].value.trim()),id:inputs[0].value.trim(),kind:row.querySelector('select').value,capacity:Number(inputs[1].value)};}).filter(r=>r.id);
    const families=opDictionary(Object.fromEntries($$('.ops-product-family').map(el=>[el.dataset.product,el.value.trim()])));
    const requirements=opDictionary();$$('#ops-form input[data-op-route]').forEach(el=>{if(Number(el.value))(requirements[el.dataset.opRoute]||=opDictionary())[el.dataset.resource]=Number(el.value);});
    await mutate(model=>{model.resources=definitions;model.product_families=opDictionary({...model.product_families,...families});for(const route of model.routes)route.resources=requirements[route.id]||opDictionary();});inspectSharedResources();
  });
}
