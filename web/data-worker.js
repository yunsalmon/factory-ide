"use strict";
importScripts('/data-core.js');
self.onmessage=event=>{
 const {id,request}=event.data;
 try{const result=prepareDataImport(request,progress=>self.postMessage({id,progress}));self.postMessage({id,result});}
 catch(error){self.postMessage({id,result:{ok:false,diagnostics:[dataDiagnostic(request.table||'document',error.row??1,error.field??'document',error.code||'data_invalid_value',...(error.args||['document']))]}});}
};
