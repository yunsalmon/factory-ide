"use strict";
// Immutable execution traces are indexed once by result identity. Cursor queries
// binary-search per-entity histories instead of replaying every preceding event.
const inventoryIndexes = new WeakMap();
const inventoryViews = new WeakMap();
const inventoryStateKeys=['lots','machines','machine_operations','buffers','resources'];
function* inventoryIndexSteps(result) {
    const events=result.events||[];
    const index={events,length:events.length,series:{},actors:new Map(),decisions:[],cache:new Map(),operationalAt:Object.hasOwn(result.initial_state||{},'machine_operations')?0:Infinity};
    for(const key of inventoryStateKeys){
      const series=index.series[key]=new Map();
      for(const [id,value] of Object.entries(result.initial_state?.[key]||{}))series.set(id,[{at:0,value,since:value.location_since??0}]);
    }
    const boundaries=new Set(['ready','arrival','assigned','move','start','finish','complete','blocked']);
    for(let i=0;i<events.length;i++){
      const e=events[i],changes=e.state_changes||{},at=i+1;
      if(e.kind==='decision'||e.kind==='blocked')index.decisions.push({at,event:e});
      if(Object.hasOwn(changes,'machine_operations'))index.operationalAt=Math.min(index.operationalAt,at);
      for(const key of inventoryStateKeys){
        const values=changes[key]??(key==='lots'&&e.lot?{[e.lot.id]:e.lot}:null);
        if(!values)continue;
        for(const id of Object.keys(values)){
          const value=values[id],series=index.series[key];let list=series.get(id);
          if(!list)series.set(id,list=[]);
          const previous=list[list.length-1];let since=previous?.since??e.time;
          if(key==='lots'&&(!previous||previous.value.location!==value.location||previous.value.state!==value.state||previous.value.placement?.kind!==value.placement?.kind||previous.value.placement?.id!==value.placement?.id))since=e.time;
          const entry={at,value,since};
          if(key==='lots')entry.graph=Object.hasOwn(changes.lots||{},id)?entry:previous?.graph;
          list.push(entry);
        }
      }
      if(e.lot&&e.affected_lot_id!==null){
        let list=index.actors.get(e.lot.id);if(!list)index.actors.set(e.lot.id,list=[]);
        const previous=list[list.length-1];
        list.push({at,event:e,boundary:boundaries.has(e.kind)?e:previous?.boundary,decision:e.kind==='decision'||e.kind==='blocked'?e:previous?.decision});
      }
      if((i+1)%128===0)yield i+1;
    }
    return index;
}
function inventoryIndexCurrent(result,index) {
  return index&&index.events===result.events&&index.length===result.events.length;
}
// Prepare before publishing a large result to synchronous replay consumers.
// Incomplete/cancelled builds never enter the shared index cache.
async function prepareInventoryReplay(result,{signal,isCurrent=()=>true,onProgress=()=>{}}={}) {
  if(!result)return;
  const events=result.events, length=events.length;
  const check=()=>{if(signal?.aborted||!isCurrent()||result.events!==events||events.length!==length)throw new DOMException('Replay preparation cancelled','AbortError');};
  check();if(inventoryIndexCurrent(result,inventoryIndexes.get(result)))return;
  const steps=inventoryIndexSteps(result);
  let start=performance.now();
  for(;;){
    check();const step=steps.next();
    if(step.done){onProgress(length,length);check();inventoryIndexes.set(result,step.value);return;}
    if(performance.now()-start>=6){
      onProgress(step.value,length);
      await new Promise(resolve=>setTimeout(resolve,0));
      check();start=performance.now();
    }
  }
}
function inventoryReplay(result, cursor) {
  const events=result?.events||[],end=Math.max(0,Math.min(events.length,Math.trunc(Number(cursor)||0)));
  if(!result)return {cursor:0,time:0,lots:{},machines:{},machine_operations:{},buffers:{},resources:{},lotEvents:{},machineEvents:{},bufferEvents:{},changedAt:{},history:new Map(),operational:false};
  let index=inventoryIndexes.get(result);
  if(!inventoryIndexCurrent(result,index)){
    const steps=inventoryIndexSteps(result);let step;
    do{step=steps.next();}while(!step.done);
    index=step.value;inventoryIndexes.set(result,index);
  }
  if(index.cache.has(end))return index.cache.get(end);
  const upper=list=>{let lo=0,hi=list.length;while(lo<hi){const mid=(lo+hi)>>>1;if(list[mid].at<=end)lo=mid+1;else hi=mid;}return lo;};
  const state={cursor:end,time:events[end-1]?.time??0,graphLots:Object.assign(Object.create(null),result.initial_state?.lots),lotEvents:Object.create(null),machineEvents:Object.create(null),bufferEvents:Object.create(null),changedAt:Object.create(null),history:new Map(),operational:index.operationalAt<=end};
  for(const key of Object.keys(index.series)){
    const values=state[key]=Object.create(null);
    for(const [id,list] of index.series[key]){
      const entry=list[upper(list)-1];if(!entry)continue;values[id]=entry.value;
      const map=key==='lots'?'lotEvents':key==='machine_operations'?'machineEvents':key==='buffers'?'bufferEvents':null;
      if(map&&entry.at&&key!=='lots')state[map][id]=entry.at-1;
      if(key==='lots'&&entry.graph){state.graphLots[id]=entry.graph.value;state.lotEvents[id]=entry.graph.at-1;}
      if(key==='lots')state.changedAt[id]=entry.since;
    }
  }
  for(const [id,list] of index.actors){const count=upper(list);if(count)state.history.set(id,{list,count,last:list[count-1]});}
  state.lastDecision=index.decisions[upper(index.decisions)-1]?.event.index;
  index.cache.set(end,state);if(index.cache.size>4)index.cache.delete(index.cache.keys().next().value);
  return state;
}
// Pure, locale-independent projection. See docs/inventory-projection.md.
function inventoryProjection(result, cursor, options = {}) {
  if(!result)return inventoryProject(result,cursor,options);
  let entry=inventoryViews.get(result);
  if(!entry||entry.events!==result.events||entry.length!==result.events?.length){entry={events:result.events,length:result.events?.length,views:new Map()};inventoryViews.set(result,entry);}
  const key=JSON.stringify([cursor,!!options.finalView,options.groupBy,options.filters||{}]);
  if(entry.views.has(key))return entry.views.get(key);
  const value=inventoryProject(result,cursor,options);entry.views.set(key,value);
  if(entry.views.size>8)entry.views.delete(entry.views.keys().next().value);
  return value;
}
function inventoryProject(result, cursor, options = {}) {
  const events = result?.events || [];
  const end = options.finalView ? events.length : Math.max(0, Math.min(events.length, Math.trunc(Number(cursor) || 0)));
  const snapshot=inventoryReplay(result,end);
  const time=options.finalView ? result?.summary?.horizon ?? snapshot.time : snapshot.time;
  const machines=Object.fromEntries((result?.model?.machines||[]).map(m=>[m.id,m]));
  const buffers=Object.assign(Object.create(null),Object.fromEntries((result?.model?.buffers||[]).map(b=>[b.id,b])),snapshot.buffers);
  const lots=new Map(Object.entries(snapshot.lots)),machineStates=snapshot.machines,operations=snapshot.machine_operations;
  const history=snapshot.history,changedAt=new Map(Object.entries(snapshot.changedAt));
  const reservations = new Map(Object.entries(machineStates).filter(([,m]) => m.state === "reserved" && m.lot).map(([id,m]) => [m.lot,id]));
  const rows = [...lots.values()].map(lot => {
    const actor = history.get(lot.id);
    const latest = actor?.last.event;
    const boundary = actor?.last.boundary;
    const reserved = reservations.get(lot.id);
    let status = lot.state === "completed" ? "completed" : lot.state === "moving" ? "moving" : lot.state === "processing" ? "processing" : lot.state === "blocked" || (lot.state === "waiting" && boundary?.kind === "blocked") ? "blocked" : reserved || lot.state === "reserved" ? "reserved" : "waiting";
    const operation = lot.placement?.kind === "machine" ? operations[lot.placement.id] : null;
    if (operation?.lot === lot.id && ["setup", "down", "maintenance", "offshift", "blocked", "resource_wait"].includes(operation.state)) status = operation.state;
    if (lot.state === "release_pending") status = "release_pending";
    const physical = lot.placement?.id ?? lot.location;
    let location = status === "completed" ? "OUTPUT" : status === "reserved" ? reserved || lot.target || lot.location : physical;
    const buffer = lot.placement?.kind === "buffer" || !lot.placement ? buffers[physical] : null;
    let kind = status === "completed" ? "output" : status === "moving" ? "transit" : status === "reserved" ? "reserved" : status === "processing" ? "processing" : status === "blocked" ? "blocked" : (buffer?.at ?? location) === "INPUT" ? "input" : "queue";
    if (["setup", "down", "maintenance", "offshift", "resource_wait", "release_pending"].includes(status)) kind = status;
    if (status === "release_pending") location = "RELEASE";
    if (status === "moving") location = `${lot.location} → ${lot.target ?? "?"}`;
    const anchor = buffer?.graph_node ?? buffer?.at ?? (status === "moving" ? lot.target : location);
    const meta = buffers[anchor] || machines[anchor] || buffer || machines[physical] || {};
    const route = lot.route || lot.assigned_route || latest?.route || "";
    const waiting = ["waiting", "reserved", "blocked", "setup", "down", "maintenance", "offshift", "resource_wait", "release_pending"].includes(status);
    const since = waiting ? lot.wait_since ?? lot.ready_since ?? changedAt.get(lot.id) ?? null : null;
    const wait = since == null ? null : Math.max(0, time - since);
    // Explicit capacity applies to the whole physical buffer, not a filtered subgroup.
    const capacity = buffer && Number.isFinite(buffer.capacity) && buffer.capacity >= 0 ? buffer.capacity : null;
    const decision = actor?.last.decision;
    return {id: lot.id, product: lot.product ?? "", quantity: Number.isFinite(lot.quantity) ? lot.quantity : null,
      process: buffer?.process ?? meta.process ?? "", line: buffer?.line ?? meta.line ?? "", location, physical,
      placement: lot.placement ?? null, cause: operation?.cause ?? null, kind, status, target: lot.target ?? "", operation: lot.operation ?? machines[physical]?.process ?? "",
      arrival: lot.created ?? null, release: lot.released_at ?? null, locationArrival: lot.location_since ?? changedAt.get(lot.id) ?? null,
      wait, waitSince: since, route, node: anchor ?? physical, capacity, inferred: !buffer && kind === "queue",
      decision: decision?.index ?? null, get history(){return actor ? actor.list.slice(0,actor.count).map(item=>item.event.index) : [];}};
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
    const oldest = waits.reduce((best,row)=>!best||row.wait>best.wait||(row.wait===best.wait&&row.id.localeCompare(best.id)<0)?row:best,null);
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
if (typeof module !== "undefined") module.exports = {prepareInventoryReplay, inventoryReplay, inventoryProjection, inventoryCSV};
