// Exact audit workload: ordered 50k events, 1000 unique live INPUT lots.
async function largeReplayFixture(original,{signal,onProgress=()=>{}}={}) {
  const check=()=>{if(signal?.aborted)throw new DOMException('Fixture cancelled','AbortError');};
  check();
  const r=structuredClone({...original,events:[],allocations:[]});
  r.model.source.count=1000;r.summary={horizon:180,arrived:1000,completed:0,mean_cycle_time:0};
  r.initial_state.lots={};const input=Object.values(r.initial_state.buffers).find(b=>b.at==='INPUT');
  input.capacity=null;input.contents=[];r.model.buffers.find(b=>b.id===input.id).capacity=null;
  const ids=[];
  let started=performance.now();
  for(let i=0;i<50000;i++) {
    const id='PERF_'+i%1000,lot={id,product:'A',state:'waiting',location:'INPUT',placement:{kind:'buffer',id:input.id},created:(i%1000)/1000,ready_since:0,target:null,route:null};
    const buffers={};if(i<1000){ids.push(id);buffers[input.id]={...input,contents:[...ids]};}
    r.events.push({index:i,time:i/500,kind:i<1000?'arrival':'ready',lot,affected_lot_id:id,state_changes:{lots:{[id]:lot},machines:{},machine_operations:{},buffers}});
    if((i+1)%128===0&&performance.now()-started>=6){
      onProgress(i+1,50000);check();
      await new Promise(resolve=>setTimeout(resolve,0));check();started=performance.now();
    }
  }
  onProgress(50000,50000);check();
  return r;
}
