"use strict";
const SCENARIOS={items:[],baseline:0,candidate:0,name:'',notes:'',busy:false,error:null,loaded:false};
const scenarioStorage='factory-studio.scenarios.v1';
function scenarioSaveStorage(){try{localStorage.setItem(scenarioStorage,JSON.stringify(scenarioArtifact(SCENARIOS.items,SCENARIOS.baseline,SCENARIOS.candidate)));}catch{SCENARIOS.error='sc_storage';}}
function scenarioNumber(n,signed=false){return n===null?tr('sc_unavailable'):new Intl.NumberFormat(locale,{maximumFractionDigits:4,signDisplay:signed?'exceptZero':'auto'}).format(n);}
function scenarioLabel(key){if(key.startsWith('state:')){const parts=key.split(':');return `${tr('sc_utilization')} · ${parts.slice(1,-1).join(':')} · ${tr('ops_state_'+parts.at(-1))}`;}if(key.startsWith('buffer:'))return `${tr('sc_buffer')} · ${key.slice(7)}`;return tr('sc_'+key);}
function scenarioLoadResult(index,event=null){
  if(S.job||S.busy)return;
  clearTimeout(parseTimer);
  const item=SCENARIOS.items[index];pause();setSource(item.source);S.model=copy(item.result.model);S.result=copy(item.result);S.valid=true;S.lot=null;S.selected=null;S.runtimeError=null;
  S.warnings=S.result.warnings||[];S.warningMessages=S.result.warning_messages||[];$('#inspector').hidden=true;renderTree();diagnostics();
  seek(event===null?S.result.events.length:event+1);selectTab(event===null?'events':'allocations');
  if(event!==null){if(S.result.events[event]?.kind==='decision'){S.comparison=event;renderAllocationComparison();}else{selectTab('events');inspectEvent(event);}}
  persistReplay();
}
async function scenarioRunSave(){
  if(SCENARIOS.busy||S.job||S.busy)return;
  const name=SCENARIOS.name.trim(),notes=SCENARIOS.notes;if(!name||SCENARIOS.items.length>=10){SCENARIOS.error='sc_invalid';renderScenarios();return;}
  const old=S.result;SCENARIOS.busy=true;SCENARIOS.error=null;renderScenarios();
  try{
    await run();pause();
    if(!S.result||S.result===old||S.runtimeError)scenarioFail('sc_run_failed');
    const execution=S.result.execution,runtime=execution?{kind:execution.kind,source_revision:execution.source_revision??null,runtime:execution.runtime}:S.localRuntime;
    const item=await scenarioCreate(name,notes,S.source,S.result,runtime);
    SCENARIOS.items.push(item);SCENARIOS.candidate=SCENARIOS.items.length-1;scenarioSaveStorage();
  }catch(error){SCENARIOS.error=error.scenarioCode||'sc_invalid';}
  finally{SCENARIOS.busy=false;selectTab('scenarios');}
}
function renderScenarios(){
  if(!SCENARIOS.loaded){SCENARIOS.loaded=true;try{const text=localStorage.getItem(scenarioStorage);if(text)scenarioRestore(JSON.parse(text)).then(saved=>{if(!SCENARIOS.items.length){Object.assign(SCENARIOS,saved);if(S.tab==='scenarios')renderScenarios();}}).catch(()=>{SCENARIOS.error='sc_integrity';if(S.tab==='scenarios')renderScenarios();});}catch{}}
  let comparison=null,error=SCENARIOS.error;
  if(SCENARIOS.items.length)try{comparison=scenarioCompare(SCENARIOS.items[SCENARIOS.baseline],SCENARIOS.items[SCENARIOS.candidate]);}catch(e){error=e.scenarioCode||'sc_invalid';}
  const selector=(key)=>`<label>${tr('sc_'+key)}<select id="sc-${key}">${SCENARIOS.items.map((x,i)=>`<option value="${i}" ${SCENARIOS[key]===i?'selected':''}>${esc(x.name)}</option>`).join('')}</select></label>`;
  $('#trace-content').innerHTML=`<section class="scenarios"><h2>${tr('sc_title')}</h2><p>${tr('sc_help')}</p><div class="sc-controls"><label>${tr('sc_name')}<input id="sc-name" maxlength="120" value="${esc(SCENARIOS.name)}"></label><label>${tr('sc_notes')}<textarea id="sc-notes" maxlength="5000">${esc(SCENARIOS.notes)}</textarea></label><button id="sc-run" ${SCENARIOS.busy||S.job||S.busy?'disabled':''}>${tr(SCENARIOS.busy?'sc_running':'sc_run')}</button><button id="sc-export" ${SCENARIOS.items.length?'':'disabled'}>${tr('sc_export')}</button><label>${tr('sc_import')}<input id="sc-import" type="file" accept="application/json,.json" ${SCENARIOS.busy?'disabled':''}></label></div><p role="alert" id="sc-error">${error?tr(error):''}</p>
  ${SCENARIOS.items.length?`<div class="sc-controls">${selector('baseline')}${selector('candidate')}<button id="sc-restore-a">${tr('sc_restore_a')}</button><button id="sc-restore-b">${tr('sc_restore_b')}</button></div><details><summary>${tr('sc_inputs')}</summary>${SCENARIOS.items.map(x=>`<article><h3>${esc(x.name)}</h3><p>${esc(x.notes)}</p><dl><dt>${tr('sc_source_hash')}</dt><dd>${x.source_hash}</dd><dt>${tr('sc_model_hash')}</dt><dd>${x.model_hash}</dd><dt>${tr('ops_seed')}</dt><dd>${esc(x.seed??tr('sc_unavailable'))}</dd><dt>${tr('sc_runtime')}</dt><dd>${esc(JSON.stringify(x.runtime))}</dd></dl><details><summary>${tr('sc_inputs')}</summary><pre>${esc(x.source)}</pre><pre>${esc(JSON.stringify(x.result.model,null,2))}</pre></details></article>`).join('')}</details>`:''}
  ${comparison?`<p>${tr('sc_alignment')} ${scenarioNumber(comparison.horizon)} min</p>${comparison.warnings.map(k=>`<p role="status">${tr(k)}</p>`).join('')}<p>${tr('sc_correlation')}</p><div class="sc-scroll" role="region" tabindex="0" aria-label="${tr('sc_title')}"><table id="sc-metrics"><caption>${tr('sc_deltas')}</caption><thead><tr><th>${tr('sc_kpi')}</th><th>${tr('sc_baseline')}</th><th>${tr('sc_candidate')}</th><th>${tr('sc_delta')}</th></tr></thead><tbody>${comparison.metrics.map(x=>`<tr data-sc-metric="${esc(x.key)}"><th>${esc(scenarioLabel(x.key))}</th><td>${scenarioNumber(x.baseline)}</td><td>${scenarioNumber(x.candidate)}</td><td>${scenarioNumber(x.delta,true)}</td></tr>`).join('')}</tbody></table></div><h3>${tr('sc_evidence')} (${comparison.evidence.length})</h3><p>${tr('sc_evidence_note')}</p><ol id="sc-evidence">${comparison.evidence.slice(0,100).map(pair=>`<li>${['baseline','candidate'].map(side=>{const e=pair[side];return e?`<button data-sc-side="${side}" data-sc-event="${e.index}">${tr('sc_'+side)} #${e.index} · ${scenarioNumber(e.data.time)} min · ${esc(e.data.lot)} · ${esc(kinds[e.data.kind]||e.data.kind)}</button><pre>${esc(JSON.stringify(e.data,null,2))}</pre>`:`<span>${tr('sc_'+side)}: ${tr('sc_unavailable')}</span>`;}).join('')}</li>`).join('')}</ol>`:''}</section>`;
  $('#sc-name').oninput=e=>SCENARIOS.name=e.target.value;$('#sc-notes').oninput=e=>SCENARIOS.notes=e.target.value;$('#sc-run').onclick=scenarioRunSave;
  $('#sc-export').onclick=()=>download('factory_comparison.json',JSON.stringify(scenarioArtifact(SCENARIOS.items,SCENARIOS.baseline,SCENARIOS.candidate),null,2),'application/json');
  $('#sc-import').onchange=async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>25000000)scenarioFail('sc_invalid');const loaded=await scenarioRestore(JSON.parse(await file.text()));Object.assign(SCENARIOS,loaded,{error:null});scenarioSaveStorage();}catch(error){SCENARIOS.error=error.scenarioCode||'sc_invalid';}renderScenarios();};
  for(const key of ['baseline','candidate'])if($('#sc-'+key))$('#sc-'+key).onchange=e=>{SCENARIOS[key]=Number(e.target.value);SCENARIOS.error=null;scenarioSaveStorage();renderScenarios();};
  if($('#sc-restore-a')){$('#sc-restore-a').onclick=()=>scenarioLoadResult(SCENARIOS.baseline);$('#sc-restore-b').onclick=()=>scenarioLoadResult(SCENARIOS.candidate);}
  $$('[data-sc-event]').forEach(el=>el.onclick=()=>scenarioLoadResult(SCENARIOS[el.dataset.scSide],Number(el.dataset.scEvent)));
}
