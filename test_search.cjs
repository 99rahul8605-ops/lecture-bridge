'use strict';
const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const src=fs.readFileSync(__dirname+'/VidCloud_Auto_Catalog.user.js','utf8');
const b=src.indexOf('  function searchBatch(job) {');const e=src.indexOf('  // NAV_CORE_BEGIN',b);
assert(b>0 && e>b);
const snip=src.slice(b,e);
function run(modalActive, batchName, inputValue='') {
  const events=[];
  const input={value:inputValue,dispatchEvent:()=>events.push('input')};
  const modal={classList:{contains:n=>modalActive&&n==='active'}};
  const mainInput={value:'',getClientRects:()=>[1]};
  const ctx={mainSearchSelector:'input[placeholder*="Search batches by name"],input[placeholder*="Search batches by"]',searchStartedAt:0,nextTickDelay:10000,document:{
    querySelector(s){if(s==='#globalSearchInput')return input;if(s==='#globalSearchModal')return modal;
      if(s.startsWith('input[placeholder*='))return mainInput;return null},
    querySelectorAll:()=>[]},
    HTMLInputElement:function(){}, Event:class{constructor(type){this.type=type}},
    visible:()=>true,clickNative:(el,act)=>{events.push('click '+act);return true},
    workerStatus:m=>events.push('status '+m),Date,console};
  vm.createContext(ctx);vm.runInContext(snip,ctx);
  const result=ctx.searchBatch({batch_name:batchName,subject_name:'Advanced Accounting',chapter_name:'AS 21 9 Lectures',video_id:'d'.repeat(24),title:'Lecture'});
  return {events,result,input};
}
const batch='CA Inter Udesh Regular May 2027 Group 1';
let r=run(false,batch);assert.equal(r.events[0],'click Open website batch search input');console.log('PASS closed modal opens website search');
r=run(true,batch);assert.equal(r.input.value,batch);assert(r.events.includes('input'));console.log('PASS open modal enters exact batch name and dispatches event');
r=run(true,'AS 21 Consolidated Financial Statements 9 Lectures | 10 Notes');
assert.equal(r.events.includes('input'),false);assert.equal(r.result,false);console.log('PASS rejects chapter text as batch query');
r=run(true,'Advanced Accounting');assert.equal(r.result,false);console.log('PASS rejects subject as batch query');
console.log('4 search UI tests passed');
