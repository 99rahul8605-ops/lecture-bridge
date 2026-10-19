'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source=fs.readFileSync(__dirname+'/VidCloud_Auto_Catalog.user.js','utf8');
const begin=source.indexOf('  // NAV_CORE_BEGIN');
const finish=source.indexOf('  function hasWebsitePlayerContext()',begin);
assert(begin>=0 && finish>begin, 'navigation block exists');
const navSource=source.slice(begin,finish);
const job={batch_id:'a'.repeat(24),subject_id:'b'.repeat(24),chapter_id:'c'.repeat(24),video_id:'d'.repeat(24), title:'Lecture 01',batch_name:'CA Inter Udesh Regular May 2027 Group 1'};
function el(attrs={}, label='', option={}) {
  return { attrs, innerText:label, textContent:label,
    getAttribute(k){return attrs[k]||null},hasAttribute(k){return Object.hasOwn(attrs,k)},
    getClientRects(){return [1]}, closest(k){ if(k==='#globalSearchModal') return option.inModal ? {} : null;return null; }
  };
}
function caseTest(title, settings, expected) {
  const elements=settings.elements||[];
  const modal=settings.modal ? {classList:{contains:k=>k==='active'},getClientRects:()=>[1]} :
    {classList:{contains:()=>false},getClientRects:()=>[]};
  const calls=[];
  function matches(item,sel) {
    return sel.split(',').some(one=>{
      one=one.trim();
      if(one.startsWith('#globalSearchResults ')) return item.closest('#globalSearchModal') && matches(item,one.split(' ').at(-1));
      if(one.startsWith('[')) {
        const attr=one.match(/^\[([^\]]+)\]$/)?.[1];
        return !!attr && item.hasAttribute(attr);
      }
      if(one==='button'||one==='[role="tab"]') return !!item.attrs['is-button'];
      return false;
    });
  }
  const doc={
    querySelector(selector) {
      if(selector==='#globalSearchModal') return modal;
      if(selector==='input[placeholder*="Search batches by name"],input[placeholder*="Search batches by"]') return settings.mainSearch ? el({},'') : null;
      if(selector==='#globalSearchCloseBtn') return settings.modal ? el({},'Close') : null;
      return null;
    },
    querySelectorAll(sel) {return elements.filter(x=>matches(x,sel));}
  };
  const ctx={
    document:doc, location:{pathname:'/index.php',hash:settings.hash||'',origin:'https://vidcloud.eu.org',assign:u=>calls.push(['goHome',u])},
    visible:()=>true, workerStatus:s=>calls.push(['status',s]),
    clickNative:(item,action)=>{calls.push(['click',action,item]);return true},
    searchBatch:()=>{calls.push(['search']);return true},
    nextTickDelay:0,console:{info:()=>{}}, Date, URL,
  };
  vm.createContext(ctx);
  vm.runInContext(navSource,ctx);
  if(settings.stale) vm.runInContext('noTargetSince=Date.now()-20000;',ctx);
  const r=ctx.navigateByWebsite(job);
  const actions=calls.filter(x=>x[0]!=='status');
  assert(actions.length, `${title}: has action`);
  assert.equal(actions[0][0],expected.kind,`${title}: kind`);
  if(expected.includes) assert(actions[0][1].includes(expected.includes),`${title}: action`);
  assert(r===true || r===false, `${title}: returns boolean`);
  console.log('PASS',title,actions[0][0],String(actions[0][1]).slice(0,55));
}
const IDs={subject:el({'data-id':job.subject_id},'Advanced Accounting 13 chapters • 88 lectures'),
 chapter:el({'data-id':job.chapter_id},'AS 21 Consolidated Financial Statements 9 Lectures | 10 Notes'),
 video:el({'data-video-id':job.video_id},'Lecture 01 02:15:00'),
 recent:el({'data-video-id':'1'.repeat(24)},'Recently Watched Lecture'),
 wrongBatch:el({'data-id':'2'.repeat(24)},'Random Course 3 chapters • 4 lectures'),
 correctBatch:el({'data-batch-id':job.batch_id},job.batch_name),
 matchSearch:el({'data-batch-id':job.batch_id},job.batch_name,{inModal:true}),
 tab:el({'is-button':true},'Lectures'),
};
caseTest('HOME #batches with Recently Watched NEVER mistaken for course',
  {hash:'#batches',elements:[IDs.recent,IDs.wrongBatch]}, {kind:'search'});
caseTest('HOME #batches with target batch tile clicks batch',
  {hash:'#batches',elements:[IDs.recent,IDs.correctBatch]}, {kind:'click',includes:'batch tile'});
caseTest('GLOBAL SEARCH exact batch result clicked',
  {hash:'#batches',modal:true,elements:[IDs.recent,IDs.matchSearch]}, {kind:'click',includes:'batch result'});
caseTest('SUBJECT page chooses exact subject ID',
  {elements:[IDs.subject]}, {kind:'click',includes:'subject'});
caseTest('STALE #batches hash with real target subject still clicks subject',
  {hash:'#batches',elements:[IDs.subject]}, {kind:'click',includes:'subject'});
caseTest('CHAPTER page chooses exact chapter ID',
  {elements:[IDs.chapter]}, {kind:'click',includes:'chapter'});
caseTest('LECTURE list chooses exact video ID',
  {elements:[IDs.video,IDs.tab]}, {kind:'click',includes:'lecture'});
caseTest('Global modal overlays chapter, close instead of search',
  {modal:true,elements:[IDs.chapter,IDs.matchSearch]}, {kind:'click',includes:'Closing global search'});
caseTest('WRONG course page resets to HOME only after grace interval',
  {elements:[IDs.wrongBatch],stale:true}, {kind:'goHome',includes:'index.php#batches'});
console.log('9 mocked navigation scenarios passed');
