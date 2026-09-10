"use strict";
// Browser/Worker-only canonical import and calibration. Never performs I/O.
const DATA_LIMITS={bytes:8*1024*1024,rows:20000,columns:128,cell:65536,diagnostics:200};
const DATA_FIELDS={
 processes:['id','name'],lines:['id','name'],
 machines:['id','name','process','line','time','setup_time','initial_family','availability','setup_matrix','resource_requirements'],
 buffers:['id','at','capacity','policy'],routes:['id','from','to','priority','delay','product','enabled','resources'],
 products:['id','name','family'],orders:['id','product','quantity','release_time','due_time','due_date','priority','customer','reference','lots'],
 observations:['id','kind','time','lot','machine','product','quantity','state']};
const DATA_JSON_FIELDS=new Set(['availability','setup_matrix','resource_requirements','resources','lots']);
const DATA_NUMERIC_FIELDS=new Set(['time','setup_time','capacity','priority','delay','quantity','release_time','due_time']);
const DATA_STATES=['idle','reserved','processing','setup','down','blocked','starved','offshift','maintenance','resource_wait'];
const dataCopy=value=>JSON.parse(JSON.stringify(value));
function dataDiagnostic(table,row,field,code,...args){return {table,row,field,code,args};}
function dataFailure(code,...args){const error=Error(code);error.code=code;error.args=args;throw error;}
function dataUnit(unit){const factors={minutes:1,seconds:1/60,hours:60};if(!Object.hasOwn(factors,unit))dataFailure('data_bad_unit',unit);return factors[unit];}
function dataOffset(zone){if(zone==='Z')return zone;if(!/^[+-]\d{2}:\d{2}$/.test(zone||'')||Number(zone.slice(1,3))>23||Number(zone.slice(4))>59)dataFailure('data_bad_timezone',zone);return zone;}
function dataDate(value,zone){
 if(typeof value!=='string')dataFailure('data_bad_timestamp',value);
 let text=value;
 if(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?$/.test(text)){if(!zone)dataFailure('data_bad_timestamp',value);text+=dataOffset(zone);}
 const match=text.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?(Z|[+-]\d{2}:\d{2})$/);
 if(!match)dataFailure('data_bad_timestamp',value);
 dataOffset(match[8]);const [year,month,day,hour,minute,second]=match.slice(1,7).map(Number);
 const leap=year%4===0&&(year%100!==0||year%400===0),days=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31];
 if(year<1||month<1||month>12||day<1||day>days[month-1]||hour>23||minute>59||second>59)dataFailure('data_bad_timestamp',value);
 const time=Date.parse(text);if(!Number.isFinite(time))dataFailure('data_bad_timestamp',value);return time;
}
function dataTime(value,unit,origin,zone){
 if(typeof value==='number'||(typeof value==='string'&&value.trim()!==''&&Number.isFinite(Number(value)))){
  const time=Number(value)*dataUnit(unit);if(!Number.isFinite(time))dataFailure('data_bad_number',value);return time;
 }
 if(!origin)dataFailure('data_origin_required');return (dataDate(value,zone)-dataDate(origin))/60000;
}
function dataCSV(text){
 if(text.length>DATA_LIMITS.bytes)dataFailure('data_limit','bytes');
 text=text.replace(/^\uFEFF/,'');const rows=[];let row=[],cell='',quoted=false,closed=false,line=1,rowLine=1;
 const addCell=()=>{if(cell.length>DATA_LIMITS.cell)dataFailure('data_limit','cell');row.push(cell);cell='';closed=false;if(row.length>DATA_LIMITS.columns)dataFailure('data_limit','columns');};
 const addRow=()=>{addCell();if(row.some(v=>v!==''))rows.push({cells:row,line:rowLine});row=[];if(rows.length>DATA_LIMITS.rows+1)dataFailure('data_limit','rows');};
 for(let i=0;i<text.length;i++){
  const c=text[i];if(quoted){if(c==='"'){if(text[i+1]==='"'){cell+='"';i++;}else{quoted=false;closed=true;}}else{cell+=c;if(c==='\n')line++;}}
  else if(c==='"'){if(cell||closed)dataFailure('data_bad_csv',line);quoted=true;}
  else if(c===','){addCell();}
  else if(c==='\n'||c==='\r'){if(c==='\r'&&text[i+1]==='\n')i++;addRow();line++;rowLine=line;}
  else {if(closed)dataFailure('data_bad_csv',line);cell+=c;}
  if(cell.length>DATA_LIMITS.cell)dataFailure('data_limit','cell');
 }
 if(quoted)dataFailure('data_bad_csv',line);if(cell||row.length||closed)addRow();
 if(!rows.length)dataFailure('data_empty');const headers=rows.shift().cells;
 if(headers.some(h=>!h)||new Set(headers).size!==headers.length)dataFailure('data_bad_header');
 for(const r of rows)if(r.cells.length!==headers.length)dataFailure('data_bad_csv',r.line);
 return {headers,rows};
}
function dataCSVRecord(table,record,headers,mapping,options){
 const output=Object.create(null),fields=DATA_FIELDS[table],index=new Map(headers.map((name,i)=>[name,i]));
 const get=field=>{const header=mapping[field]??field;return header&&index.has(header)?record.cells[index.get(header)]:undefined;};
 const version=get('schema_version');if(version&&version!=='1')dataFailure('data_bad_schema');
 const extra=get('extra_json');if(extra){let parsed;try{parsed=JSON.parse(extra);}catch{dataFailure('data_bad_json');}if(!parsed||Array.isArray(parsed)||typeof parsed!=='object'||Object.keys(parsed).some(k=>fields.includes(k)&&get(k)!==undefined&&get(k)!==''))dataFailure('data_bad_extra');Object.assign(output,parsed);}
 for(const field of fields){const value=get(field);if(value===undefined||value==='')continue;
  if(DATA_JSON_FIELDS.has(field)){try{output[field]=JSON.parse(value);}catch{dataFailure('data_field_json',field);}}
  else if(field==='enabled'){if(!['true','false'].includes(value))dataFailure('data_bad_boolean',field);output[field]=value==='true';}
  else if(DATA_NUMERIC_FIELDS.has(field)&&!(table==='observations'&&field==='time')){if(value==='null'&&['capacity','due_time'].includes(field))output[field]=null;else{if(!Number.isFinite(Number(value)))dataFailure('data_field_number',field);output[field]=Number(value);}}
  else output[field]=value==='null'&&field==='due_date'?null:value;
 }
 const unit=get('time_unit')||options.unit||'minutes';convertDataTimes(output,table,unit,options);
 return output;
}
function convertDataTimes(row,table,unit,options){
 const factor=dataUnit(unit),scale=(object,key)=>{if(object[key]!=null){if(typeof object[key]!=='number'||!Number.isFinite(object[key]))dataFailure('data_field_number',key);object[key]*=factor;}};
 if(table==='machines'){scale(row,'time');scale(row,'setup_time');for(const window of row.availability||[]){scale(window,'start');scale(window,'end');}for(const targets of Object.values(row.setup_matrix||{}))for(const key of Object.keys(targets))scale(targets,key);}
 if(table==='routes')scale(row,'delay');
 if(table==='orders'){scale(row,'release_time');scale(row,'due_time');for(const lot of row.lots||[])scale(lot,'release_time');}
 if(table==='observations'&&row.time!==undefined)row.time=dataTime(row.time,unit,options.origin,options.timezone);
}
function validateDataModel(model,rowMap={}){
 const errors=[],push=(table,index,field,code,...args)=>{if(errors.length<DATA_LIMITS.diagnostics)errors.push(dataDiagnostic(table,rowMap[table]?.[index]??index+1,field,code,...args));};
 const numeric=(value,table,index,field,min=0,max=1e6,integer=false)=>{if(typeof value!=='number'||!Number.isFinite(value)||value<min||value>max||(integer&&!Number.isInteger(value)))push(table,index,field,'data_invalid_value',field);};
 if(!model||typeof model!=='object'||Array.isArray(model)){push('model',0,'model','data_invalid_value','model');return errors;}
 const sets={};for(const table of Object.keys(DATA_FIELDS).filter(t=>t!=='observations')){
  const rows=model[table];sets[table]=new Set();if(rows===undefined&&['lines','products','buffers','orders'].includes(table))continue;
  if(!Array.isArray(rows)||(!rows.length&&['processes','machines','routes'].includes(table))){push(table,0,table,'data_invalid_value',table);continue;}
  if(rows.length>DATA_LIMITS.rows){push(table,0,table,'data_limit','rows');continue;}
  rows.forEach((row,i)=>{if(!row||typeof row!=='object'||Array.isArray(row)){push(table,i,'row','data_invalid_value','row');return;}
   if(typeof row.id!=='string'||!new RegExp('^[A-Za-z][A-Za-z0-9_-]{0,'+(['orders','buffers'].includes(table)?63:39)+'}$').test(row.id)||(['processes','machines','routes'].includes(table)&&['INPUT','OUTPUT'].includes(row.id)))push(table,i,'id','data_invalid_value','id');
   if(sets[table].has(row.id))push(table,i,'id','data_duplicate',row.id);sets[table].add(row.id);
   if(row.name!==undefined&&typeof row.name!=='string')push(table,i,'name','data_invalid_value','name');
  });
 }
 const records=table=>Array.isArray(model[table])?model[table].map(x=>x&&typeof x==='object'&&!Array.isArray(x)?x:{}):[];
 const reference=(value,allowed,table,i,field)=>{if(!allowed.has(value))push(table,i,field,'data_reference',value);};
 numeric(model.duration,'settings',0,'duration',.01,100000);numeric(model.seed,'settings',0,'seed',0,2**32-1,true);
 if(!['pull','push'].includes(model.mode))push('settings',0,'mode','data_invalid_value','mode');
 const source=model.source||{};numeric(source.count,'source',0,'count',1,2000,true);numeric(source.interval,'source',0,'interval',.01);
 if(!Array.isArray(source.products)||!source.products.length||source.products.some(p=>typeof p!=='string'||!p.trim()))push('source',0,'products','data_invalid_value','products');
 if(Array.isArray(model.products))for(const product of Array.isArray(source.products)?source.products:[])reference(product,sets.products,'source',0,'products');
 records('machines').forEach((row,i)=>{reference(row.process,sets.processes,'machines',i,'process');if(typeof row.line!=='string'||!row.line.trim())push('machines',i,'line','data_invalid_value','line');else if(Array.isArray(model.lines))reference(row.line,sets.lines,'machines',i,'line');numeric(row.time,'machines',i,'time',.01);numeric(row.setup_time===undefined?0:row.setup_time,'machines',i,'setup_time');let end=-1;
  if(row.availability!==undefined&&!Array.isArray(row.availability))push('machines',i,'availability','data_invalid_value','availability');else for(const window of row.availability||[]){if(!window||typeof window!=='object'){push('machines',i,'availability','data_invalid_value','availability');continue;}numeric(window.start,'machines',i,'availability.start');numeric(window.end,'machines',i,'availability.end');if(!['down','offshift'].includes(window.state)||window.end<=window.start||window.start<end)push('machines',i,'availability','data_invalid_value','availability');if(window.cause!==undefined&&typeof window.cause!=='string')push('machines',i,'availability.cause','data_invalid_value','cause');end=window.end;}});
 records('routes').forEach((row,i)=>{reference(row.from,new Set(['INPUT',...sets.machines]),'routes',i,'from');reference(row.to,new Set(['OUTPUT',...sets.machines]),'routes',i,'to');if(row.from===row.to||(row.from==='INPUT'&&row.to==='OUTPUT'))push('routes',i,'to','data_invalid_value','to');numeric(row.delay,'routes',i,'delay');numeric(row.priority,'routes',i,'priority',0,1000);if(typeof row.enabled!=='boolean')push('routes',i,'enabled','data_invalid_value','enabled');if(typeof row.product!=='string')push('routes',i,'product','data_invalid_value','product');else if(Array.isArray(model.products)&&row.product!=='*')reference(row.product,sets.products,'routes',i,'product');});
 const locations=new Set();records('buffers').forEach((row,i)=>{reference(row.at,new Set(['INPUT','OUTPUT',...sets.machines]),'buffers',i,'at');if(locations.has(row.at))push('buffers',i,'at','data_duplicate',row.at);locations.add(row.at);if(row.capacity!=null)numeric(row.capacity,'buffers',i,'capacity',1,Number.MAX_SAFE_INTEGER,true);if(!['fifo','priority'].includes(row.policy??'fifo'))push('buffers',i,'policy','data_invalid_value','policy');});
 const priorities=source.priorities===undefined?[0]:source.priorities;if(!Array.isArray(priorities)||!priorities.length)push('source',0,'priorities','data_invalid_value','priorities');else priorities.forEach((value,i)=>numeric(value,'source',0,'priorities',-1e6));
 const lotIds=new Set();records('orders').forEach((order,i)=>{
  numeric(order.quantity,'orders',i,'quantity',Number.MIN_VALUE,1e9);numeric(order.release_time??0,'orders',i,'release_time',0,1e9);numeric(order.priority??0,'orders',i,'priority',-1e6,1e9);if(order.due_time!=null)numeric(order.due_time,'orders',i,'due_time',-1e9,1e9);
  if(typeof order.product!=='string'||!order.product.trim())push('orders',i,'product','data_invalid_value','product');else if(Array.isArray(model.products))reference(order.product,sets.products,'orders',i,'product');
  for(const key of ['customer','reference'])if(order[key]!==undefined&&typeof order[key]!=='string')push('orders',i,key,'data_invalid_value',key);
  if(order.due_date!=null){try{const due=dataTime(order.due_date,'minutes',model.time_origin);if(order.due_time!=null&&Math.abs(due-order.due_time)>1e-7)push('orders',i,'due_date','data_invalid_value','due_date / due_time');}catch(error){push('orders',i,'due_date',error.code||'data_bad_timestamp',...(error.args||[]));}}
  const lots=order.lots??[{id:order.id,quantity:order.quantity,release_time:order.release_time??0}];if(!Array.isArray(lots)||!lots.length){push('orders',i,'lots','data_invalid_value','lots');return;}let sum=0,first=Infinity;
  for(const lot of lots){if(!lot||typeof lot!=='object'){push('orders',i,'lots','data_invalid_value','lots');continue;}if(typeof lot.id!=='string'||!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(lot.id))push('orders',i,'lots.id','data_invalid_value','lots.id');if(lotIds.has(lot.id))push('orders',i,'lots.id','data_duplicate',lot.id);lotIds.add(lot.id);numeric(lot.quantity,'orders',i,'lots.quantity',Number.MIN_VALUE,1e9);numeric(lot.release_time??order.release_time??0,'orders',i,'lots.release_time',order.release_time??0,1e9);sum+=lot.quantity;first=Math.min(first,lot.release_time??order.release_time??0);}
  if(Math.abs(sum-order.quantity)>Math.max(1e-9,Math.abs(order.quantity)*1e-12))push('orders',i,'quantity','data_quantity_sum',order.quantity,sum);if(first!==(order.release_time??0))push('orders',i,'release_time','data_invalid_value','first lot release_time');
 });
 if(lotIds.size>2000||records('orders').length>2000||records('machines').length>100||records('routes').length>500)push('model',0,'rows','data_limit','model rows');
 if(model.time_origin!==undefined)try{dataDate(model.time_origin);}catch(error){push('settings',0,'time_origin',error.code,...error.args);}
 if(model.order_risk_window!==undefined)numeric(model.order_risk_window,'settings',0,'order_risk_window',0,1e9);
 return errors;
}
function validateObservations(events,model,rowNumbers=[]){
 const diagnostics=[],ids=new Set(),machines=new Set((model.machines||[]).map(m=>m.id));const products=Array.isArray(model.products)?new Set(model.products.map(p=>p.id)):null;
 const push=(i,field,code,...args)=>{if(diagnostics.length<DATA_LIMITS.diagnostics)diagnostics.push(dataDiagnostic('observations',rowNumbers[i]??i+1,field,code,...args));};
 const lifecycle=new Set();events.forEach((e,i)=>{
  if(!e||typeof e!=='object'||Array.isArray(e)){push(i,'row','data_invalid_value','row');return;}
  if(typeof e.id!=='string'||!e.id.trim())push(i,'id','data_invalid_value','id');if(ids.has(e.id))push(i,'id','data_duplicate',e.id);ids.add(e.id);
  if(!['arrival','ready','start','finish','complete','state'].includes(e.kind))push(i,'kind','data_invalid_value','kind');
  if(typeof e.time!=='number'||!Number.isFinite(e.time))push(i,'time','data_bad_timestamp',e.time);
  if(e.kind!=='state'&&(typeof e.lot!=='string'||!e.lot.trim()))push(i,'lot','data_invalid_value','lot');
  if(['state','start','finish'].includes(e.kind)||e.machine)if(!machines.has(e.machine))push(i,'machine','data_reference',e.machine);
  if(e.kind==='state'&&!DATA_STATES.includes(e.state))push(i,'state','data_invalid_value','state');
  if(e.quantity!==undefined&&(typeof e.quantity!=='number'||!Number.isFinite(e.quantity)||e.quantity<0))push(i,'quantity','data_invalid_value','quantity');
  if(e.product&&products&&!products.has(e.product))push(i,'product','data_reference',e.product);
  if(['arrival','complete'].includes(e.kind)){const key=JSON.stringify([e.lot,e.kind]);if(lifecycle.has(key))push(i,'kind','data_duplicate',key);lifecycle.add(key);}
 });return diagnostics;
}
function prepareDataImport(request,progress=()=>{}){
 const {text,format,table,baseModel}=request,options=request.options||{},mapping=request.mapping||{};
 if(typeof text!=='string'||text.length>DATA_LIMITS.bytes)dataFailure('data_limit','bytes');
 progress({phase:'parsing',rows:0});let kind,candidate,events,origin=options.origin||baseModel?.time_origin,rows=[],rowMap={},diagnostics=[];
 if(format==='csv'){
  if(!Object.hasOwn(DATA_FIELDS,table))dataFailure('data_invalid_table');const parsed=dataCSV(text);rows=parsed.rows;if(request.preview)return {ok:true,preview:true,headers:parsed.headers,summary:{rows:rows.length}};
  const mapped=Object.values(mapping).filter(Boolean);if(mapped.some(v=>!parsed.headers.includes(v))||new Set(mapped).size!==mapped.length)dataFailure('data_bad_mapping');
  const records=[];for(let i=0;i<rows.length;i++){try{records.push(dataCSVRecord(table,rows[i],parsed.headers,mapping,{...options,origin}));}catch(error){if(diagnostics.length<DATA_LIMITS.diagnostics)diagnostics.push(dataDiagnostic(table,rows[i].line,error.args?.[0]||'row',error.code||'data_invalid_value',...(error.args||[])));}if(i%1000===0)progress({phase:'parsing',rows:i});}
  if(diagnostics.length)return {ok:false,diagnostics};rowMap[table]=rows.map(r=>r.line);
  if(table==='observations'){kind='observations';events=records;}else{kind='model';candidate=dataCopy(baseModel);candidate[table]=records;}
 }else{
  let document;try{document=JSON.parse(text);}catch{dataFailure('data_bad_json');}
  if(!document||document.schema_version!==1||!['model','observations'].includes(document.kind))dataFailure('data_bad_schema');
  kind=document.kind;const unit=document.time_unit||'minutes';dataUnit(unit);origin=document.time_origin||origin;
  if(kind==='model'){
   if(!document.model||typeof document.model!=='object'||Array.isArray(document.model))dataFailure('data_bad_schema');candidate=dataCopy(document.model);
   const scale=dataUnit(unit);for(const key of ['duration','order_risk_window'])if(candidate[key]!=null){if(typeof candidate[key]!=='number')dataFailure('data_field_number',key);candidate[key]*=scale;}if(candidate.source?.interval!=null){if(typeof candidate.source.interval!=='number')dataFailure('data_field_number','interval');candidate.source.interval*=scale;}
   for(const t of Object.keys(DATA_FIELDS).filter(t=>t!=='observations')){if(candidate[t]!==undefined&&!Array.isArray(candidate[t])){diagnostics.push(dataDiagnostic(t,1,t,'data_invalid_value',t));continue;}const records=candidate[t]||[];if(rows.length+records.length>DATA_LIMITS.rows)dataFailure('data_limit','rows');rows.push(...records);for(let i=0;i<records.length;i++)try{convertDataTimes(records[i],t,unit,{...options,origin:candidate.time_origin});}catch(error){if(diagnostics.length<DATA_LIMITS.diagnostics)diagnostics.push(dataDiagnostic(t,i+1,'row',error.code||'data_invalid_value',...(error.args||[])));}}
  }else{if(!Array.isArray(document.events))dataFailure('data_bad_schema');events=dataCopy(document.events);rows=events;if(events.length>DATA_LIMITS.rows)dataFailure('data_limit','rows');for(let i=0;i<events.length;i++)try{convertDataTimes(events[i],'observations',unit,{...options,origin});}catch(error){if(diagnostics.length<DATA_LIMITS.diagnostics)diagnostics.push(dataDiagnostic('observations',i+1,'time',error.code||'data_invalid_value',...(error.args||[])));}}
 }
 progress({phase:'validating',rows:rows.length});
 if(kind==='model')diagnostics.push(...validateDataModel(candidate,rowMap));else diagnostics.push(...validateObservations(events,baseModel,rowMap.observations));
 diagnostics=diagnostics.slice(0,DATA_LIMITS.diagnostics);if(diagnostics.length)return {ok:false,diagnostics};
 return {ok:true,kind,candidate:kind==='model'?candidate:{schema_version:1,kind:'observations',time_unit:'minutes',...(origin?{time_origin:origin}:{}),events},
  summary:{rows:rows.length,tables:kind==='model'?Object.keys(DATA_FIELDS).filter(t=>t!=='observations'&&JSON.stringify(candidate[t])!==JSON.stringify(baseModel[t])).map(table=>({table,before:baseModel[table]?.length||0,after:candidate[table]?.length||0})):[]}};
}
function exportDataModel(model){return {schema_version:1,kind:'model',time_unit:'minutes',model:dataCopy(model)};}
function exportDataCSV(table,records=[]){
 const fields=['schema_version',...DATA_FIELDS[table],'extra_json'];const quote=v=>'"'+String(v??'').replace(/"/g,'""')+'"';
 const rows=records.map(record=>fields.map(field=>field==='schema_version'?1:field==='extra_json'?JSON.stringify(Object.fromEntries(Object.entries(record).filter(([key])=>!DATA_FIELDS[table].includes(key)||record[key]===null||record[key]===''))):record[field]===null||record[field]===''?'':typeof record[field]==='object'?JSON.stringify(record[field]):record[field]));
 return '\uFEFF'+[fields,...rows].map(row=>row.map(quote).join(',')).join('\r\n');
}
function dataDistribution(values){
 if(!values.length)return {count:0,mean:null,min:null,max:null,p50:null,p90:null};const sorted=[...values].sort((a,b)=>a-b),q=p=>{const i=(sorted.length-1)*p,lo=Math.floor(i);return sorted[lo]+(sorted[Math.ceil(i)]-sorted[lo])*(i-lo);};
 return {count:values.length,mean:values.reduce((a,b)=>a+b,0)/values.length,min:sorted[0],max:sorted.at(-1),p50:q(.5),p90:q(.9)};
}
function calibrationMetrics(events,model,start,end){
 if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)dataFailure('data_bad_window');
 const sorted=events.map((event,index)=>({...event,_index:index})).sort((a,b)=>a.time-b.time||a._index-b._index);
 const arrivals=new Map(),ready=new Map(),completed=[],cycles=[],waits=[],states=new Map(),stateIntervals=[],quantities=new Map();let cycleMissing=0,waitMissing=0,startCount=0;
 for(const e of sorted){if(e.time>end){if(e.kind==='state'){const previous=states.get(e.machine);if(previous&&previous.time<end){const a=Math.max(start,previous.time);if(end>a)stateIntervals.push({machine:e.machine,state:previous.state,start:a,end,duration:end-a});states.delete(e.machine);}}continue;}
  if(e.quantity!==undefined)quantities.set(e.lot,e.quantity);
  if(e.kind==='arrival'&&!arrivals.has(e.lot))arrivals.set(e.lot,e.time);
  if(['arrival','ready'].includes(e.kind)&&!ready.has(e.lot))ready.set(e.lot,e.time);
  if(e.kind==='start'){if(e.time>=start){startCount++;if(ready.has(e.lot))waits.push(e.time-ready.get(e.lot));else waitMissing++;}ready.delete(e.lot);}
  if(e.kind==='complete'&&e.time>=start){completed.push({lot:e.lot,quantity:quantities.get(e.lot)??null});if(arrivals.has(e.lot))cycles.push(e.time-arrivals.get(e.lot));else cycleMissing++;}
  if(e.kind==='state'){
   const previous=states.get(e.machine);if(previous){const a=Math.max(start,previous.time),b=Math.min(end,e.time);if(b>a)stateIntervals.push({machine:e.machine,state:previous.state,start:a,end:b,duration:b-a});}states.set(e.machine,e);
  }
 }
 const utilization=(model.machines||[]).map(machine=>{const intervals=stateIntervals.filter(i=>i.machine===machine.id),known=intervals.reduce((sum,i)=>sum+i.duration,0),processing=intervals.filter(i=>i.state==='processing').reduce((sum,i)=>sum+i.duration,0);return {machine:machine.id,known,unknown:Math.max(0,end-start-known),processing,value:known?processing/known:null,coverage:known/(end-start)};});
 const knownQuantity=completed.filter(e=>e.quantity!==null),quantity=knownQuantity.length===completed.length?knownQuantity.reduce((sum,e)=>sum+e.quantity,0):null;
 return {window:{start,end,duration:end-start},throughput:{lots:completed.length,quantity,quantity_known:knownQuantity.length,lots_per_hour:completed.length/(end-start)*60},cycle:dataDistribution(cycles),wait:dataDistribution(waits),utilization,
  coverage:{events:events.length,in_window:events.filter(e=>e.time>=start&&e.time<=end).length,completed:completed.length,cycle_missing:cycleMissing,starts:startCount,wait_missing:waitMissing,quantity_missing:completed.length-knownQuantity.length},stateIntervals};
}
function simulatedCalibration(result,cursor,final,start,end){
 if(!result)return null;const source=result.events.slice(0,final?result.events.length:cursor),limit=final?result.summary.horizon:source.at(-1)?.time??0,events=[],arrivals=new Set(),states=new Map();
 for(const [machine,op] of Object.entries(result.initial_state?.machine_operations||{})){events.push({kind:'state',time:0,machine,state:op.state});states.set(machine,op.state);}
 for(const event of source){const lot=event.lot,kind=event.kind;
  if(lot&&['order_release','arrival','ready','start','finish','complete'].includes(kind)){
   let normalized=kind;if(['order_release','arrival'].includes(kind)){if(arrivals.has(lot.id))normalized=null;else{normalized='arrival';arrivals.add(lot.id);}}
   if(normalized)events.push({kind:normalized,time:event.time,lot:lot.id,machine:event.machine,quantity:lot.quantity});
  }
  for(const [machine,op] of Object.entries(event.state_changes?.machine_operations||{})){events.push({kind:'state',time:event.time,machine,state:op.state});states.set(machine,op.state);}
 }
 for(const [machine,state] of states)events.push({kind:'state',time:limit,machine,state});
 const metrics=calibrationMetrics(events,result.model,start,end);metrics.available_until=limit;return metrics;
}
if(typeof module!=='undefined')module.exports={DATA_LIMITS,DATA_FIELDS,dataCSV,prepareDataImport,validateDataModel,validateObservations,exportDataModel,exportDataCSV,calibrationMetrics,simulatedCalibration,dataDate,dataTime,dataDistribution};
