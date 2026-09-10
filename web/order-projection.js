"use strict";
// Pure prefix projection. Optional inventory rows let WIP consumers share locations.
function orderDefinitions(model) {
  return (model?.orders || []).map(raw => {
    const due = raw.due_time ?? (raw.due_date && model.time_origin ? (Date.parse(raw.due_date)-Date.parse(model.time_origin))/60000 : null);
    return {id:raw.id, raw, quantity:raw.quantity, release_time:raw.release_time ?? 0, due_time:due,
      risk_window:model.order_risk_window ?? 30,
      lots:(raw.lots || [{id:raw.id,quantity:raw.quantity}]).map(lot=>({id:lot.id,quantity:lot.quantity,order_id:raw.id,product:raw.product,
        release_time:lot.release_time ?? raw.release_time ?? 0, due_time:due, due_date:raw.due_date ?? null, customer:raw.customer ?? null, reference:raw.reference ?? null, priority:raw.priority ?? 0}))};
  });
}
function orderLotLocation(lot, model) {
  if (!lot) return {location:null,node:null,state:"unreleased",operation:null};
  const placement=lot.placement;
  const buffer=(model.buffers || []).find(b=>b.id===placement?.id);
  const node=placement?.kind==="buffer" ? buffer?.at ?? lot.location : placement?.kind==="machine" ? placement.id : lot.target || lot.location;
  const machine=(model.machines || []).find(m=>m.id===node);
  return {location:placement?.kind==="release" ? "INPUT (release)" : placement?.kind==="buffer" ? placement.id : placement?.kind==="transport" ? `${lot.location} → ${lot.target ?? "OUTPUT"}` : lot.location,
    node, state:lot.state, operation:machine?.process ?? null, route:lot.route || lot.assigned_route || null};
}
function orderProjection(result, cursor, options={}) {
  const events=result?.events || [], model=result?.model || {};
  const end=options.finalView ? events.length : Math.max(0,Math.min(events.length,Math.trunc(Number(cursor)||0)));
  const prefix=events.slice(0,end), time=options.finalView ? result?.summary?.horizon ?? prefix.at(-1)?.time ?? 0 : prefix.at(-1)?.time ?? 0;
  const states=new Map(Object.entries(result?.initial_state?.lots || {})), histories=new Map();
  for(const e of prefix) {
    for(const [id,lot] of Object.entries(e.state_changes?.lots ?? (e.lot ? {[e.lot.id]:e.lot} : {}))) states.set(id,lot);
    const id=Object.hasOwn(e,"affected_lot_id") ? e.affected_lot_id : e.lot?.id;
    if(id) {if(!histories.has(id))histories.set(id,[]);histories.get(id).push(e);}
  }
  const inventory=new Map((options.inventory?.rows || []).map(row=>[row.id,row]));
  const definitions=result?.order_plan || orderDefinitions(model);
  const metrics=(due,at,window)=>({slack:due==null?null:due-at,tardiness:due==null?null:Math.max(0,at-due),
    risk:due==null?"no_due":at>due?"late":due-at<=window?"at_risk":"on_time"});
  const rows=definitions.map(order=>{
    const lots=order.lots.map(plan=>{
      const snapshot=states.get(plan.id), history=histories.get(plan.id)||[], done=snapshot?.state==="completed";
      const location=orderLotLocation(snapshot,model), shared=inventory.get(plan.id);
      const releasedAt=snapshot?.released_at ?? snapshot?.created ?? null;
      return {...plan,...location,...(shared?{location:shared.location,node:shared.node,operation:shared.operation}:{}),
        released:!!snapshot, released_at:releasedAt, admitted_at:snapshot?.admitted_at ?? null,
        completed:done, completed_at:done?snapshot.completed:null,
        cycle_time:done?snapshot.completed-releasedAt:null, elapsed:releasedAt==null?null:time-releasedAt,
        ...metrics(order.due_time,done?snapshot.completed:time,order.risk_window),
        history:history.map(e=>e.index), allocations:history.filter(e=>e.kind==="decision").map(e=>e.index)};
    });
    const released=lots.filter(l=>l.released), completed=lots.filter(l=>l.completed);
    const done=completed.length===lots.length;
    const completedAt=done?Math.max(...completed.map(l=>l.completed_at)):null;
    const firstRelease=released.length?Math.min(...released.map(l=>l.released_at)):null;
    const releasedQuantity=released.reduce((sum,l)=>sum+l.quantity,0), completedQuantity=completed.reduce((sum,l)=>sum+l.quantity,0);
    const locations=Object.create(null);
    for(const lot of lots) {const key=lot.location ?? "unreleased";locations[key]=(locations[key]||0)+lot.quantity;}
    const due=metrics(order.due_time,done?completedAt:time,order.risk_window);
    // Completed on-time orders are no longer at risk.
    if(done && due.risk==="at_risk")due.risk="on_time";
    return {id:order.id,product:order.raw.product,quantity:order.quantity,priority:order.raw.priority ?? 0,
      customer:order.raw.customer ?? null,reference:order.raw.reference ?? null,due_date:order.raw.due_date ?? null,
      release_time:order.release_time,due_time:order.due_time,released_quantity:releasedQuantity,
      unreleased_quantity:Math.max(0,order.quantity-releasedQuantity),completed_quantity:completedQuantity,
      wip_quantity:Math.max(0,releasedQuantity-completedQuantity),progress:done?"completed":released.length?"released":"unreleased",
      completed_at:completedAt,cycle_time:done?completedAt-firstRelease:null,
      ...due,lots,locations};
  });
  const filter=options.filters || {}, search=(filter.search || "").toLocaleLowerCase();
  const visible=rows.filter(row=>(!filter.progress || row.progress===filter.progress)&&(!filter.risk || row.risk===filter.risk)&&
    (!search || [row.id,row.product,row.customer,row.reference,...row.lots.map(l=>l.id)].join(" ").toLocaleLowerCase().includes(search)));
  const totals=values=>{
    const finished=values.filter(o=>o.progress==="completed"&&o.due_time!=null), ontime=finished.filter(o=>o.tardiness===0);
    const completeLots=values.flatMap(o=>o.lots).filter(l=>l.completed), quantity=completeLots.reduce((s,l)=>s+l.quantity,0);
    return {orders:values.length,quantity:values.reduce((s,o)=>s+o.quantity,0),released:values.reduce((s,o)=>s+o.released_quantity,0),
      throughput:quantity,wip:values.reduce((s,o)=>s+o.wip_quantity,0),unreleased:values.reduce((s,o)=>s+o.unreleased_quantity,0),
      due_completed:finished.length,on_time:ontime.length,attainment:finished.length?ontime.length/finished.length:null,
      mean_cycle_time:quantity?completeLots.reduce((s,l)=>s+l.cycle_time*l.quantity,0)/quantity:null};
  };
  return {cursor:end,time,finalView:!!options.finalView,rows:visible,allRows:rows,totals:totals(visible),allTotals:totals(rows),
    unassignedLots:[...states.values()].filter(l=>!l.order_id).map(l=>l.id)};
}
function orderCSV(projection,columns) {
  const quote=v=>{let s=String(v??"");if(/^[=+\-@\t\r]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';};
  return '\uFEFF'+[columns.map(([name])=>name),...projection.rows.map(row=>columns.map(([,get])=>get(row)))].map(row=>row.map(quote).join(',')).join('\r\n');
}
if(typeof module!=="undefined")module.exports={orderDefinitions,orderLotLocation,orderProjection,orderCSV};
