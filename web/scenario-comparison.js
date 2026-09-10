"use strict";
const SCENARIO_FORMAT = "factory-scenario-comparison";
function scenarioCanonical(v) {
  if(v===null||typeof v!=="object")return JSON.stringify(v);
  if(Array.isArray(v))return '['+v.map(scenarioCanonical).join(',')+']';
  return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+scenarioCanonical(v[k])).join(',')+'}';
}
async function scenarioHash(value,raw=false) {return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw?value:scenarioCanonical(value))))].map(x=>x.toString(16).padStart(2,'0')).join('');}
function scenarioFail(code){throw Object.assign(new Error(code),{scenarioCode:code});}
function scenarioContract(r){
  if(!r||r.schema_version!==2||!Array.isArray(r.events)||r.events.length>50000||!r.model||!Array.isArray(r.model.machines))scenarioFail('sc_invalid');
  if(![undefined,1,2].includes(r.operational_schema_version)||![undefined,1].includes(r.order_schema_version))scenarioFail('sc_schema');
  const horizon=r.summary?.horizon,unit=r.model.time_unit??'min';
  if(!Number.isFinite(horizon)||horizon<=0||r.model.duration!==horizon)scenarioFail('sc_horizon');
  if(!['min','minutes'].includes(unit))scenarioFail('sc_units');
  let previous=0;for(const [i,e] of r.events.entries()){
    if(e.index!==i||!Number.isFinite(e.time)||e.time<previous||e.time>horizon||!e.state_changes||!e.state_changes.lots)scenarioFail('sc_invalid');previous=e.time;
  }
  return {horizon,unit:'min',schema:[r.schema_version,r.operational_schema_version??null,r.order_schema_version??null]};
}
async function scenarioCreate(name,notes,source,result,runtime){
  if(typeof name!=='string'||!name.trim()||name.length>120||typeof notes!=='string'||notes.length>5000||typeof source!=='string')scenarioFail('sc_invalid');
  const contract=scenarioContract(result),snapshot=JSON.parse(JSON.stringify(result));
  const source_hash=await scenarioHash(source,true),model_hash=await scenarioHash(snapshot.model);
  if(snapshot.execution?.source_sha256&&snapshot.execution.source_sha256!==source_hash)scenarioFail('sc_source_changed');
  return {name:name.trim(),notes,source,source_hash,model_hash,trace_hash:await scenarioHash(snapshot),seed:snapshot.model.seed??null,runtime:JSON.parse(JSON.stringify(runtime??(snapshot.execution?{kind:snapshot.execution.kind,source_revision:snapshot.execution.source_revision??null,runtime:snapshot.execution.runtime??null}:{kind:'unknown'}))),contract,result:snapshot};
}
async function scenarioRestore(doc){
  if(doc?.format!==SCENARIO_FORMAT||doc.version!==1||!Array.isArray(doc.scenarios)||!doc.scenarios.length||doc.scenarios.length>10)scenarioFail('sc_invalid');
  const items=[];for(const item of doc.scenarios){const checked=await scenarioCreate(item.name,item.notes,item.source,item.result,item.runtime);
    for(const key of ['source_hash','model_hash','trace_hash','seed','contract'])if(scenarioCanonical(checked[key])!==scenarioCanonical(item[key]))scenarioFail('sc_integrity');items.push(checked);}
  for(const key of ['baseline','candidate'])if(!Number.isInteger(doc[key])||doc[key]<0||doc[key]>=items.length)scenarioFail('sc_invalid');
  return {items,baseline:doc.baseline,candidate:doc.candidate};
}
function scenarioArtifact(items,baseline,candidate){return {format:SCENARIO_FORMAT,version:1,baseline,candidate,scenarios:items};}
function scenarioMetrics(r){
  const m=Object.create(null),h=r.summary.horizon,w=inventoryProjection(r,r.events.length,{finalView:true}),done=w.allRows.filter(x=>x.status==='completed');
  m.completed_lots=done.length;m.wip_lots=w.allTotals.wip;
  const quantity=rows=>rows.length&&rows.every(x=>x.quantity!==null)?rows.reduce((s,x)=>s+x.quantity,0):rows.length===0&&Array.isArray(r.model.orders)?0:null;
  m.completed_quantity=quantity(done);m.wip_quantity=quantity(w.allRows.filter(x=>x.status!=='completed'));m.throughput_lots=done.length/h;m.throughput_quantity=m.completed_quantity===null?null:m.completed_quantity/h;
  const states=Object.assign(Object.create(null),r.initial_state?.lots);for(const e of r.events)Object.assign(states,e.state_changes.lots);
  const cycles=Object.values(states).filter(l=>l.state==='completed').map(l=>Number.isFinite(l.completed)&&Number.isFinite(l.released_at??l.created)?l.completed-(l.released_at??l.created):null);
  m.cycle_mean=cycles.length&&cycles.every(x=>x!==null)?cycles.reduce((s,x)=>s+x,0)/cycles.length:null;
  if(r.order_plan?.length){const due=orderProjection(r,r.events.length,{finalView:true}).rows.filter(o=>o.due_time!==null);m.late_orders=due.length?due.filter(o=>o.tardiness>0).length:null;m.tardiness=due.length?due.reduce((s,o)=>s+o.tardiness,0):null;}else{m.late_orders=null;m.tardiness=null;}
  if(r.operation_metrics?.machines)for(const [id,v] of Object.entries(r.operation_metrics.machines))for(const [state,time] of Object.entries(v))m['state:'+id+':'+state]=Number.isFinite(time)?time/h:null;
  const operationValues=Object.values(r.operation_metrics?.machines||{});m.processing_share=operationValues.length&&operationValues.every(v=>Number.isFinite(v.processing))?operationValues.reduce((s,v)=>s+v.processing,0)/h/operationValues.length:null;
  const buffers=Object.assign(Object.create(null),r.initial_state?.buffers),full=Object.create(null);let at=0;
  function interval(until){for(const [id,b] of Object.entries(buffers)){if(!Object.hasOwn(full,id))full[id]=Number.isFinite(b.capacity)?0:null;if(Number.isFinite(b.capacity)&&Array.isArray(b.contents)&&b.contents.length>=b.capacity)full[id]+=until-at;}at=until;}
  for(const e of r.events){interval(e.time);Object.assign(buffers,e.state_changes.buffers);}interval(h);for(const [id,time]of Object.entries(full))m['buffer:'+id]=time;
  const finite=Object.values(full).filter(x=>x!==null);m.buffer_full=finite.length?finite.reduce((s,x)=>s+x,0):null;
  const machines=new Map(r.model.machines.map(x=>[x.id,x]));m.cross_line=0;
  for(const e of r.events.filter(e=>e.kind==='move')){const a=machines.get(e.lot?.location),b=machines.get(e.lot?.target);if(a&&b){if(!a.line||!b.line){m.cross_line=null;break;}if(a.line!==b.line)m.cross_line++;}}
  return m;
}
function scenarioEvidence(r){
  const map=new Map(),counts=new Map();for(const e of r.events){
    if(!['decision','assigned','buffer_wait','buffer_enter','machine_state','resource_wait'].includes(e.kind))continue;
    const id=e.affected_lot_id??e.lot?.id??e.machine??'',stem=scenarioCanonical([id,e.kind]),n=counts.get(stem)||0;counts.set(stem,n+1);
    const data={time:e.time,kind:e.kind,lot:id,chosen:e.chosen??null,route:e.route??null,machine:e.machine??null,reason:e.reason??null,transition:e.transition??null,checks:e.checks??null,buffers:e.state_changes.buffers??null,resources:e.state_changes.resources??null};map.set(stem+':'+n,{index:e.index,data});
  }return map;
}
function scenarioCompare(baseline,candidate){
  const a=scenarioContract(baseline.result),b=scenarioContract(candidate.result);if(a.horizon!==b.horizon)scenarioFail('sc_horizon');if(scenarioCanonical(a.schema)!==scenarioCanonical(b.schema))scenarioFail('sc_schema');
  const left=scenarioMetrics(baseline.result),right=scenarioMetrics(candidate.result);
  const metrics=[...new Set([...Object.keys(left),...Object.keys(right)])].map(key=>({key,baseline:left[key]??null,candidate:right[key]??null,delta:Number.isFinite(left[key])&&Number.isFinite(right[key])?right[key]-left[key]:null}));
  const x=scenarioEvidence(baseline.result),y=scenarioEvidence(candidate.result),evidence=[];
  for(const key of new Set([...x.keys(),...y.keys()]))if(scenarioCanonical(x.get(key)?.data??null)!==scenarioCanonical(y.get(key)?.data??null))evidence.push({baseline:x.get(key)??null,candidate:y.get(key)??null});
  const warnings=[];if(scenarioCanonical(baseline.runtime)!==scenarioCanonical(candidate.runtime))warnings.push('sc_runtime_warning');if(baseline.seed!==candidate.seed)warnings.push('sc_seed_warning');
  return {metrics,evidence,warnings,horizon:a.horizon};
}
