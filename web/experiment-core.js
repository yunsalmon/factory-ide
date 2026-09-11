"use strict";
const EXP_LIMITS={runs:30,concurrency:2,source:1000000,trace:5000000,artifact:20000000,metrics:2000};
const EXP_T95=[null,12.706204736,4.30265273,3.182446305,2.776445105,2.570581836,2.446911851,2.364624252,2.306004135,2.262157163,2.228138852,2.20098516,2.17881283,2.160368656,2.144786688,2.131449546,2.119905299,2.109815578,2.10092204,2.093024054,2.085963447,2.079613845,2.073873068,2.06865761,2.063898562,2.059538553,2.055529439,2.051830516,2.048407142,2.045229642];
function experimentFail(code){throw Object.assign(Error(code),{experimentCode:code});}
// Validate the original graph before JSON copying or canonical hashing can turn non-finite numbers into null.
function experimentFinite(value){const stack=[value],seen=new Set();while(stack.length){const item=stack.pop();if(typeof item==='number'&&!Number.isFinite(item))experimentFail('ex_invalid');if(item&&typeof item==='object'&&!seen.has(item)){seen.add(item);for(const child of Object.values(item))stack.push(child);}}}
function experimentCopy(value){experimentFinite(value);return JSON.parse(JSON.stringify(value));}
async function experimentHash(value,raw=false){experimentFinite(value);return scenarioHash(value,raw);}
function experimentSeeds(text){const tokens=text.trim().split(/[\s,]+/);if(!text.trim()||tokens.some(s=>!/^\d+$/.test(s)))experimentFail('ex_seeds');const seeds=tokens.map(Number);if(!seeds.length||seeds.length>EXP_LIMITS.runs||new Set(seeds).size!==seeds.length||seeds.some(s=>!Number.isInteger(s)||s<0||s>4294967295))experimentFail('ex_seeds');return seeds;}
function experimentStats(values){
 if(!values.length)return {n:0,mean:null,p50:null,p90:null,min:null,max:null,interval:null,values:[]};
 if(values.some(v=>!Number.isFinite(v)))experimentFail('ex_invalid');
 const sorted=[...values].sort((a,b)=>a-b),n=sorted.length,mean=sorted.reduce((a,b)=>a+b,0)/n;
 const q=p=>{const i=(n-1)*p,a=Math.floor(i);return sorted[a]+(sorted[Math.ceil(i)]-sorted[a])*(i-a);};
 const variance=n>1?sorted.reduce((s,v)=>s+(v-mean)**2,0)/(n-1):null,half=n>1?EXP_T95[n-1]*Math.sqrt(variance/n):null;
 if(!Number.isFinite(mean)||(half!==null&&!Number.isFinite(half)))experimentFail('ex_invalid');
 return {n,mean,p50:q(.5),p90:q(.9),min:sorted[0],max:sorted.at(-1),interval:half===null?null:[mean-half,mean+half],values:sorted};
}
function experimentAggregate(experiment){
 const success=experiment.runs.filter(r=>r.status==='success'),keys=new Set(success.flatMap(r=>Object.keys(r.metrics)));
 return [...keys].sort().map(key=>{const measured=success.filter(r=>Number.isFinite(r.metrics[key]));return {key,...experimentStats(measured.map(r=>r.metrics[key])),excluded:experiment.runs.length-measured.length,missing:success.length-measured.length};});
}
async function experimentDefinition(name,notes,source,model,seeds,concurrency,checkCurrent=()=>{}){
 if(typeof name!=='string'||!name.trim()||name.length>120||typeof notes!=='string'||notes.length>5000||typeof source!=='string'||new TextEncoder().encode(source).length>EXP_LIMITS.source||![1,2].includes(concurrency))experimentFail('ex_invalid');
 experimentSeeds(seeds.join(','));
 const snapshot=experimentCopy(model);experimentFinite(seeds);checkCurrent();
 const source_hash=await experimentHash(source,true);checkCurrent();
 const model_hash=await experimentHash(snapshot);checkCurrent();
 return {name:name.trim(),notes,source,model:snapshot,seeds:[...seeds],concurrency,source_hash,model_hash};
}
class ExperimentRunner{
 constructor({factory=()=>new BrowserPythonRuntime({role:'experiment'}),onUpdate=()=>{}}={}){this.factory=factory;this.onUpdate=onUpdate;this.workers=new Set();this.active=false;this.cancelled=false;}
 cancel(){if(!this.active)return;this.cancelled=true;for(const worker of this.workers)worker.stop();}
 async run(definition){
  if(this.active)experimentFail('ex_busy');definition=experimentCopy(definition);experimentSeeds(definition.seeds.join(','));if(![1,2].includes(definition.concurrency))experimentFail('ex_invalid');this.active=true;this.cancelled=false;
  const exp={version:1,definition,runs:definition.seeds.map((seed,index)=>({index,seed,status:'pending',runtime:null})),representative:null,contract:null};this.experiment=exp;let next=0;
  const lane=async()=>{const worker=this.factory();this.workers.add(worker);try{while(!this.cancelled){const index=next++;if(index>=exp.runs.length)break;const row=exp.runs[index];row.status='running';this.onUpdate(exp);
   try{const payload=await worker.runSeed(definition.source,row.seed);if(this.cancelled)break;
    experimentFinite(payload);experimentFinite(worker.runtime);
    const result=payload.result,contract=scenarioContract(result),expected=experimentCopy(definition.model);expected.seed=row.seed;
    const input_model_hash=await experimentHash(payload.input_model??result.model);
    if(await experimentHash(expected)!==input_model_hash)experimentFail('ex_model');
    if(exp.contract&&scenarioCanonical(exp.contract)!==scenarioCanonical(contract))experimentFail('ex_incompatible');
    const metrics=scenarioMetrics(result);if(Object.keys(metrics).length>EXP_LIMITS.metrics||Object.values(metrics).some(v=>v!==null&&!Number.isFinite(v)))experimentFail('ex_invalid');
    const runtime=experimentCopy(worker.runtime),trace_hash=await experimentHash(result),source_hash=await experimentHash(payload.source,true),model_hash=await experimentHash(result.model);
    if(this.cancelled)break;if(new TextEncoder().encode(JSON.stringify(result)).length>EXP_LIMITS.trace)experimentFail('ex_limit');exp.contract=contract;Object.assign(row,{status:'success',metrics,runtime,trace_hash,source_hash,model_hash,input_model_hash});
    if(!exp.representative||index<exp.representative.index){exp.representative={index,result,source:payload.source};}
   }catch(error){if(!this.cancelled)Object.assign(row,{status:'failed',runtime:worker.runtime?experimentCopy(worker.runtime):null,error:String(error.error||error.message||error).slice(0,2000)});}
   if(!this.cancelled)this.onUpdate(exp);
  }}finally{worker.stop();this.workers.delete(worker);}};
  try{await Promise.all(Array.from({length:Math.min(definition.concurrency,exp.runs.length)},lane));}finally{for(const row of exp.runs)if(['pending','running'].includes(row.status))row.status='cancelled';this.active=false;this.workers.clear();this.onUpdate(exp);}return exp;
 }
}
function experimentCompare(a,b){if(!a.contract||!b.contract)experimentFail('ex_incompatible');if(scenarioCanonical(a.contract)!==scenarioCanonical(b.contract))experimentFail('ex_incompatible');const x=new Map(experimentAggregate(a).map(r=>[r.key,r])),y=new Map(experimentAggregate(b).map(r=>[r.key,r]));return [...new Set([...x.keys(),...y.keys()])].sort().map(key=>({key,baseline:x.get(key)??null,candidate:y.get(key)??null,delta:x.get(key)?.mean!=null&&y.get(key)?.mean!=null?y.get(key).mean-x.get(key).mean:null}));}
async function experimentExport(experiments){const doc={format:'factory-experiments',version:1,experiments:experimentCopy(experiments)};const artifact={...doc,content_hash:await experimentHash(doc)};if(new TextEncoder().encode(JSON.stringify(artifact)).length>EXP_LIMITS.artifact)experimentFail('ex_limit');return artifact;}
async function experimentRestore(doc){
 experimentFinite(doc);
 if(doc?.format!=='factory-experiments'||doc.version!==1||!Array.isArray(doc.experiments)||!doc.experiments.length||doc.experiments.length>2||new TextEncoder().encode(JSON.stringify(doc)).length>EXP_LIMITS.artifact)experimentFail('ex_invalid');
 if(doc.content_hash!==await experimentHash({format:doc.format,version:doc.version,experiments:doc.experiments}))experimentFail('ex_integrity');
 for(const exp of doc.experiments){if(exp.version!==1)experimentFail('ex_invalid');const d=exp.definition,checked=await experimentDefinition(d.name,d.notes,d.source,d.model,d.seeds,d.concurrency);if(checked.source_hash!==d.source_hash||checked.model_hash!==d.model_hash||!Array.isArray(exp.runs)||exp.runs.length!==d.seeds.length)experimentFail('ex_integrity');
  for(const [i,row] of exp.runs.entries()){if(row.index!==i||row.seed!==d.seeds[i]||!['success','failed','cancelled'].includes(row.status))experimentFail('ex_invalid');if(row.status==='success'&&(!row.metrics||Object.keys(row.metrics).length>EXP_LIMITS.metrics||Object.values(row.metrics).some(v=>v!==null&&!Number.isFinite(v))))experimentFail('ex_invalid');}
  const successful=exp.runs.filter(r=>r.status==='success');if(successful.length&&!exp.representative)experimentFail('ex_integrity');for(const row of successful){const expected=experimentCopy(d.model);expected.seed=row.seed;if(await experimentHash(expected)!==row.input_model_hash||!row.runtime||typeof row.runtime!=='object'||![row.source_hash,row.trace_hash].every(h=>typeof h==='string'&&/^[a-f0-9]{64}$/.test(h)))experimentFail('ex_integrity');}
  if(exp.representative){const {index,result,source}=exp.representative,row=exp.runs[index];if(!row||row.status!=='success'||await experimentHash(result)!==row.trace_hash||await experimentHash(source,true)!==row.source_hash||await experimentHash(result.model)!==row.model_hash||scenarioCanonical(scenarioContract(result))!==scenarioCanonical(exp.contract)||scenarioCanonical(scenarioMetrics(result))!==scenarioCanonical(row.metrics))experimentFail('ex_integrity');}
  experimentAggregate(exp);
 }
 return experimentCopy(doc.experiments);
}
