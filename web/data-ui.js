"use strict";
// Raw text/observations live only in this page's memory. No storage or upload.
const DATA={table:'machines',format:'csv',file:null,text:null,headers:[],mapping:{},options:{unit:'minutes',timezone:'Z',origin:''},worker:null,serial:0,busy:false,phase:null,pending:null,diagnostics:[],observations:null,message:null,windowStart:0,windowEnd:null,final:true};
function dataValue(value){return value==null?tr('data_unknown'):typeof value==='number'?new Intl.NumberFormat(locale,{maximumFractionDigits:4}).format(value):value;}
function invalidateDataPreview(){DATA.pending=null;DATA.diagnostics=[];DATA.message=null;}
function invalidateDataWork(){DATA.serial++;DATA.busy=false;DATA.phase=null;}
function dataWorkerStop(worker){if(!worker)return;worker.terminate();factoryWorkerResources.release(worker.factoryResource);if(DATA.worker===worker)DATA.worker=null;}
function dataWorkerCreate(){
 const owner={role:'data',resourceId:null};factoryWorkerResources.allocate(owner);let worker;
 try{worker=new Worker('/data-worker.js',{name:`${FACTORY_WORKER_RESOURCE_CONTRACT.data.workerName}-${owner.resourceId}`});}
 catch(error){factoryWorkerResources.release(owner);throw error;}
 worker.factoryResource=owner;return worker;
}
function cancelDataImport(){DATA.serial++;dataWorkerStop(DATA.worker);DATA.busy=false;DATA.pending=null;DATA.phase=null;DATA.message={code:'data_cancelled'};if(S.tab==='data')renderDataPanel();}
function runDataWorker(preview=false){
 if(DATA.text==null)return;dataWorkerStop(DATA.worker);const id=++DATA.serial;DATA.busy=true;DATA.phase={phase:'parsing',rows:0};invalidateDataPreview();
 const revision=S.revision;let worker;try{worker=dataWorkerCreate();}catch{DATA.busy=false;DATA.phase=null;DATA.message={code:'data_worker_error'};if(S.tab==='data')renderDataPanel();return;}DATA.worker=worker;
 worker.onmessage=event=>{if(id!==DATA.serial)return;const message=event.data;if(message.progress){DATA.phase=message.progress;if(S.tab==='data')renderDataPanel();return;}
  dataWorkerStop(worker);DATA.busy=false;DATA.phase=null;const result=message.result;
  if(result.preview){DATA.headers=result.headers;DATA.mapping=Object.fromEntries([...DATA_FIELDS[DATA.table],'extra_json','time_unit','schema_version'].map(field=>[field,result.headers.includes(field)?field:'']));DATA.message={code:'data_mapping_ready',args:[result.summary.rows]};}
  else if(result.ok){DATA.pending={...result,revision};DATA.message={code:'data_dry_ready'};}
  else{DATA.diagnostics=result.diagnostics;DATA.message={code:'data_invalid'};}
  if(S.tab==='data')renderDataPanel();
 };
 worker.onerror=()=>{if(id!==DATA.serial)return;dataWorkerStop(worker);DATA.busy=false;DATA.phase=null;DATA.message={code:'data_worker_error'};if(S.tab==='data')renderDataPanel();};
 worker.postMessage({id,request:{text:DATA.text,format:DATA.format,table:DATA.table,baseModel:copy(S.model),mapping:DATA.mapping,options:{...DATA.options,origin:DATA.options.origin||S.model.time_origin},preview}});
 if(S.tab==='data')renderDataPanel();
}
async function readDataFile(file){
 cancelDataImport();DATA.file=file;DATA.text=null;DATA.headers=[];DATA.mapping={};DATA.diagnostics=[];DATA.message=null;
 if(!file)return;if(file.size>DATA_LIMITS.bytes){DATA.message={code:'data_limit',args:['8 MiB']};renderDataPanel();return;}
 const id=DATA.serial;DATA.format=file.name.toLowerCase().endsWith('.csv')?'csv':'json';DATA.busy=true;DATA.phase={phase:'reading',rows:0};renderDataPanel();
 try{const text=await file.text();if(id!==DATA.serial)return;DATA.text=text;DATA.busy=false;DATA.phase=null;if(DATA.format==='csv')runDataWorker(true);else{DATA.message={code:'data_file_ready'};renderDataPanel();}}catch{if(id!==DATA.serial)return;DATA.busy=false;DATA.phase=null;DATA.message={code:'data_read_error'};renderDataPanel();}
}
async function confirmDataImport(){
 const pending=DATA.pending;if(!pending||DATA.busy)return;
 if(pending.revision!==S.revision){DATA.pending=null;DATA.message={code:'data_stale'};renderDataPanel();return;}
 if(S.busy||S.job){DATA.message={code:'ui_19'};renderDataPanel();return;}
 const id=++DATA.serial,revision=S.revision,source=S.source;
 DATA.busy=true;renderDataPanel();
 try{
  if(pending.kind==='observations'){DATA.observations=pending.candidate;DATA.message={code:'data_observations_ready',args:[pending.candidate.events.length]};}
  else{
   // Prepare without touching source/model/result. Commit only if this exact
   // confirmation is still current after asynchronous Python validation.
   const data=PUBLIC_DEMO?await browserRuntime.sync(source,copy(pending.candidate)):await api('/api/sync',{source,model:copy(pending.candidate)});
   if(id!==DATA.serial)return;
   if(revision!==S.revision||S.busy||S.job){DATA.pending=null;DATA.message={code:'data_stale'};return;}
   invalidateTrace();setSource(data.source);S.model=data.model;S.warnings=data.warnings;S.warningMessages=data.warning_messages||[];S.valid=true;diagnostics();
   $('#dirty-dot').textContent='●';$('#project-name').textContent=S.model.name;$('#mode-label').textContent=tr(S.model.mode==='pull'?'ui_15':'ui_16');renderTree();renderGraph();
   DATA.message={code:'data_model_applied'};
  }
  DATA.pending=null;
 }catch(error){if(id!==DATA.serial)return;DATA.pending=null;DATA.diagnostics=[{table:'model',row:1,field:'model',code:error.detail?.code||'data_apply_error',args:error.detail?.args||[]}];DATA.message={code:'data_invalid'};}
 finally{if(id===DATA.serial){DATA.busy=false;if(S.tab==='data')renderDataPanel();}}
}
function dataFocus(){const el=document.activeElement;return {id:el?.id,map:el?.dataset?.mapField};}
function restoreDataFocus(focus){const el=focus.id?document.getElementById(focus.id):focus.map?Array.from(document.querySelectorAll('[data-map-field]')).find(el=>el.dataset.mapField===focus.map):null;if(el&&!el.disabled)el.focus({preventScroll:true});}
function renderDataPanel(){
 const focus=dataFocus();
 const pending=DATA.pending,columns=[...DATA_FIELDS[DATA.table],'extra_json','time_unit','schema_version'];
 const message=DATA.phase?tr('data_phase_'+DATA.phase.phase,[DATA.phase.rows]):DATA.message?tr(DATA.message.code,DATA.message.args):tr('data_intro');
 $('#trace-content').innerHTML=`<section class="data-panel" aria-label="${tr('data_title')}"><p>${tr('data_private')}</p><div class="data-controls">
  ${`<label>${tr('data_file')}<input id="data-file" type="file" accept=".csv,.json,text/csv,application/json" ${DATA.busy?'disabled':''}></label><label>${tr('data_table')}<select id="data-table" ${DATA.busy?'disabled':''}>${Object.keys(DATA_FIELDS).map(table=>`<option value="${table}" ${table===DATA.table?'selected':''}>${tr('data_table_'+table)}</option>`).join('')}</select></label>`}
  <button id="data-export">${tr('data_export_model')}</button><button id="data-json-template">${tr('data_json_template')}</button><button id="data-template">${tr('data_template')}</button><button id="data-csv-export">${tr('data_csv_export')}</button></div>
  ${`<div class="data-controls"><label>${tr('data_units')}<select id="data-unit" ${DATA.busy?'disabled':''}>${['minutes','seconds','hours'].map(unit=>`<option value="${unit}" ${DATA.options.unit===unit?'selected':''}>${tr('data_unit_'+unit)}</option>`).join('')}</select></label><label>${tr('data_timezone')}<select id="data-timezone" ${DATA.busy?'disabled':''}>${['','Z','+09:00','-05:00'].map(zone=>`<option value="${zone}" ${DATA.options.timezone===zone?'selected':''}>${zone||tr('data_require_offset')}</option>`).join('')}</select></label><label>${tr('data_origin')}<input id="data-origin" placeholder="2026-09-10T00:00:00Z" value="${esc(DATA.options.origin)}" ${DATA.busy?'disabled':''}></label></div>`}
  <p id="data-status" role="status" data-busy="${DATA.busy}">${esc(message)}</p>
  ${DATA.file?`<p>${esc(DATA.file.name)} · ${DATA.file.size} bytes</p>`:''}
  ${DATA.format==='csv'&&DATA.headers.length?`<details open><summary>${tr('data_mapping')}</summary><div class="data-mapping">${columns.map(field=>`<label>${esc(field)}<select data-map-field="${field}" ${DATA.busy?'disabled':''}><option value="">${tr('data_ignore')}</option>${DATA.headers.map(header=>`<option value="${esc(header)}" ${DATA.mapping[field]===header?'selected':''}>${esc(header)}</option>`).join('')}</select></label>`).join('')}</div></details>`:''}
  ${`<div class="data-controls"><button id="data-dry-run" ${DATA.text==null||DATA.busy?'disabled':''}>${tr('data_dry_run')}</button><button id="data-cancel" ${!DATA.busy&&!pending?'disabled':''}>${tr('data_cancel')}</button><button id="data-confirm" ${!pending||DATA.busy?'disabled':''}>${tr(pending?.kind==='observations'?'data_use_observations':'data_replace_model')}</button></div>`}
  ${pending?`<section id="data-preview"><h3>${tr('data_preview')}</h3><p>${tr('data_row_count',[pending.summary.rows])}</p><ul>${pending.summary.tables.map(change=>`<li>${tr('data_table_'+change.table)}: ${change.before} → ${change.after}</li>`).join('')}</ul><p>${tr('data_atomic')}</p></section>`:''}
  ${DATA.diagnostics.length?`<div class="data-scroll" role="region" tabindex="0" aria-label="${tr('data_errors')}"><table id="data-errors"><caption>${tr('data_errors')}</caption><thead><tr>${['table','row','field','diagnostic'].map(key=>`<th scope="col">${tr('data_'+key)}</th>`).join('')}</tr></thead><tbody>${DATA.diagnostics.map(error=>`<tr><td>${esc(error.table)}</td><td>${error.row}</td><td>${esc(error.field)}</td><td>${esc(tr(error.code,error.args))}</td></tr>`).join('')}</tbody></table></div><p>${tr('data_error_limit',[DATA_LIMITS.diagnostics])}</p>`:''}
  <section id="data-calibration"></section></section>`;
 $('#data-export').onclick=()=>download('factory_model.v1.json',JSON.stringify(exportDataModel(S.model),null,2),'application/json');
 $('#data-json-template').onclick=()=>download(`factory_${DATA.table}.v1.json`,JSON.stringify(DATA.table==='observations'?{schema_version:1,kind:'observations',time_unit:'minutes',events:[{id:'sample',kind:'state',time:0,machine:S.model.machines[0].id,state:'idle'}]}:exportDataModel(S.model),null,2),'application/json');
 $('#data-template').onclick=()=>download(`factory_${DATA.table}.v1.csv`,exportDataCSV(DATA.table), 'text/csv;charset=utf-8');
 $('#data-csv-export').onclick=()=>download(`factory_${DATA.table}.v1.csv`,exportDataCSV(DATA.table,DATA.table==='observations'?DATA.observations?.events||[]:S.model[DATA.table]||[]),'text/csv;charset=utf-8');
 {
  $('#data-file').onchange=e=>readDataFile(e.target.files[0]);$('#data-table').onchange=e=>{DATA.table=e.target.value;invalidateDataPreview();if(DATA.headers.length)DATA.mapping=Object.fromEntries([...DATA_FIELDS[DATA.table],'extra_json','time_unit','schema_version'].map(field=>[field,DATA.headers.includes(field)?field:'']));renderDataPanel();};
  $('#data-unit').onchange=e=>{DATA.options.unit=e.target.value;invalidateDataPreview();renderDataPanel();};$('#data-timezone').onchange=e=>{DATA.options.timezone=e.target.value;invalidateDataPreview();renderDataPanel();};$('#data-origin').onchange=e=>{DATA.options.origin=e.target.value;invalidateDataPreview();renderDataPanel();};
  $$('[data-map-field]').forEach(el=>el.onchange=e=>{DATA.mapping[e.target.dataset.mapField]=e.target.value;invalidateDataPreview();renderDataPanel();});
  $('#data-dry-run').onclick=()=>runDataWorker(false);$('#data-cancel').onclick=cancelDataImport;$('#data-confirm').onclick=confirmDataImport;
 }
 renderCalibration();restoreDataFocus(focus);
}
function renderCalibration(){
 const focus=dataFocus();
 const start=DATA.windowStart,end=DATA.windowEnd??S.model?.duration??1;
 let observed=null,simulated=null,error=null;
 try{if(DATA.observations){const invalid=validateObservations(DATA.observations.events,S.model);if(invalid.length)error=tr('data_observations_stale');else observed=calibrationMetrics(DATA.observations.events,S.model,start,end);}simulated=simulatedCalibration(S.result,S.cursor,DATA.final,start,end);}catch(problem){error=tr(problem.code||'data_bad_window',problem.args);}
 const rows=[['throughput_lots',m=>m.throughput.lots],['throughput_quantity',m=>m.throughput.quantity],['throughput_rate',m=>m.throughput.lots_per_hour],['cycle_mean',m=>m.cycle.mean],['cycle_p50',m=>m.cycle.p50],['cycle_p90',m=>m.cycle.p90],['wait_mean',m=>m.wait.mean],['wait_p50',m=>m.wait.p50],['wait_p90',m=>m.wait.p90]];
 const cell=(value)=>esc(dataValue(value));
 $('#data-calibration').innerHTML=`<h3>${tr('data_calibration')}</h3><div class="data-controls"><label>${tr('data_window_start')}<input id="data-window-start" type="number" step="any" value="${start}"></label><label>${tr('data_window_end')}<input id="data-window-end" type="number" step="any" value="${end}"></label><label>${tr('data_sim_view')}<select id="data-sim-view"><option value="final" ${DATA.final?'selected':''}>${tr('data_final')}</option><option value="cursor" ${!DATA.final?'selected':''}>${tr('data_cursor')}</option></select></label>${DATA.observations?`<button id="data-export-observations">${tr('data_export_observations')}</button>`:''}</div><p>${tr('data_metric_rules')}</p>
  ${error?`<p role="alert">${esc(error)}</p>`:''}
  <div class="data-scroll" role="region" tabindex="0" aria-label="${tr('data_calibration')}"><table id="data-metrics"><caption>${tr('data_comparison')}</caption><thead><tr><th scope="col">${tr('data_metric')}</th><th scope="col">${tr('data_observed')}</th><th scope="col">${tr('data_simulated')}</th><th scope="col">${tr('data_difference')}</th></tr></thead><tbody>${rows.map(([key,get])=>{const a=observed?get(observed):null,b=simulated?get(simulated):null;return `<tr data-metric="${key}"><th scope="row">${tr('data_'+key)}</th><td>${cell(a)}</td><td>${cell(b)}</td><td>${cell(a==null||b==null?null:b-a)}</td></tr>`;}).join('')}</tbody></table></div>
  <div id="data-coverage">${[['observed',observed],['simulated',simulated]].map(([name,metrics])=>`<h4>${tr('data_'+name)}</h4>${metrics?`<p>${tr('data_coverage',[metrics.coverage.in_window,metrics.coverage.events,metrics.cycle.count,metrics.coverage.completed,metrics.wait.count,metrics.coverage.starts,metrics.coverage.quantity_missing])}</p>${metrics.available_until!==undefined?`<p>${tr('data_sim_until',[metrics.available_until])}</p>`:''}<ul>${metrics.utilization.map(machine=>`<li>${esc(machine.machine)} · ${tr('data_utilization')}: ${cell(machine.value==null?null:100*machine.value)}% · ${tr('data_known')}: ${cell(machine.known)} / ${cell(metrics.window.duration)} min (${cell(100*machine.coverage)}%)</li>`).join('')}</ul>`:`<p>${tr('data_no_'+name)}</p>`}`).join('')}<p>${tr('data_coverage_note')}</p></div>`;
 for(const field of ['start','end'])$('#data-window-'+field).onchange=e=>{DATA[field==='start'?'windowStart':'windowEnd']=Number(e.target.value);renderCalibration();};
 $('#data-sim-view').onchange=e=>{DATA.final=e.target.value==='final';renderCalibration();};
 restoreDataFocus(focus);
 if(DATA.observations)$('#data-export-observations').onclick=()=>download('factory_observations.v1.json',JSON.stringify(DATA.observations,null,2),'application/json');
}
