const fs=require('fs'), vm=require('vm'), assert=require('assert').strict;
const src=fs.readFileSync(__dirname+'/Lecture_Two_Tab_Queue.user.js','utf8');
const shared=new Map();
const required=['batch_id','subject_id','topic_id','video_id','typeId','video_url','video_name','video_img','video_type','play_type'];
const href=(n)=>{const u=new URL('https://vidcloud.eu.org/play.php');for(const k of required)u.searchParams.set(k,k==='video_id'?`video${n}`:k==='video_name'?`Lecture ${n}`:`value${n}`);return u.href};
let nextId=1;
function environment(url,tabSession,{sender=false,hasManifest=false,media=[]}={}){
  const intervals=[],timeouts=[];
  let clockMs=Date.now();
  const locationUrl=new URL(url);
  class El {
    constructor(tag){this.tagName=tag;this.style={};this.children=[];this.dataset={};this.listeners={};this.hidden=false;this.disabled=false;this.textContent='';this.isConnected=false;this.innerHTML='';this.parentElement=null;this._named={};}
    appendChild(child){this.children.push(child);child.isConnected=true;child.parentElement=this;return child;}
    append(...children){for(const child of children)this.appendChild(child);}
    addEventListener(k,fn){this.listeners[k]=fn;}
    click(){this.listeners.click?.();}
    querySelector(s){
      if(s==='button'||s==='.relay-message')return null;
      if(!this._named[s])this._named[s]=new El('div');
      return this._named[s];
    }
    querySelectorAll(s){return this.children.filter(x=>x.tagName==='button')}
    replaceChildren(){this.children=[];}
    getAttribute(k){return null}
    closest(s){return null}
  }
  const body=new El('body'),head=new El('head'), root=new El('div');root.id='railway-lecture-link-v1';
  let sendCount=0;let senderStatus=new El('div');
  const sendButton=new El('button');sendButton.textContent='🔗 📤 Send MP4 to Telegram';
  sendButton.addEventListener('click',()=>{sendCount++;senderStatus.textContent='Finding authorized HLS playlist…';sendButton.disabled=true});
  root.appendChild(sendButton);
  root.querySelector=(s)=>s==='.relay-message'?senderStatus:null;
  root.querySelectorAll=(s)=>s==='button'?[sendButton]:[];
  const document={body,head,documentElement:head,createElement:(tag)=>new El(tag),
    getElementById(id){return sender && id==='railway-lecture-link-v1'?root:null;},
    querySelectorAll(sel){return sel==='video, audio'?media:[];},
    addEventListener(_event,_fn,_capture){}};
  const loc={href:locationUrl.href,pathname:locationUrl.pathname,search:locationUrl.search,origin:locationUrl.origin,assigned:null,assign(next){this.assigned=next}};
  const sandbox={
    URL,URLSearchParams,location:loc,document,console,confirm:()=>true,
    crypto:{randomUUID:()=>`tab-${nextId++}`},
    performance:{getEntriesByType:()=>hasManifest?[{name:'https://bunny-cdn-qbg-s6.testwave.cc/manifest.docx'}]:[]},
    sessionStorage:{getItem:k=>tabSession.get(k)??null,setItem:(k,v)=>tabSession.set(k,String(v)),removeItem:k=>tabSession.delete(k)},
    GM_getValue:(k,def)=>shared.has(k)?structuredClone(shared.get(k)):def,
    GM_setValue:(k,v)=>shared.set(k,structuredClone(v)),
    GM_addValueChangeListener:()=>1,
    setInterval:(fn,ms)=>{intervals.push({fn,ms});return intervals.length},
    setTimeout:(fn,ms)=>{timeouts.push({fn,ms});return timeouts.length},
    Date:{now:()=>clockMs},Math,Array,JSON,Error,Object,String,Number,Boolean,RegExp
  };
  vm.runInNewContext(src,sandbox,{filename:'Queue.user.js',timeout:5000});
  const panel=sender?root.children.at(-1):body.children.at(-1);
  function click(action){const b={dataset:{action}};panel.listeners.click({target:{closest:()=>b}})}
  function tick(){const callback=intervals.find(x=>x.ms===1200);if(callback)callback.fn()}
  function silenceTick(){const callback=intervals.find(x=>x.ms===700);if(callback)callback.fn()}
  function triggerStart(){for(const timer of timeouts.splice(0))timer.fn()}
  return {click,tick,silenceTick,triggerStart,advance(ms){clockMs+=ms},loc,panel,sendButton,senderStatus,get sendCount(){return sendCount}};
}
function state(){return shared.get('lecture-two-tab-queue-v1')}
let passed=0;
function t(name,fn){fn();passed++;console.log('PASS',name)}
const browseSession=new Map();
const browseMedia={muted:false,defaultMuted:false,paused:false,pauseCount:0,pause(){this.paused=true;this.pauseCount++}};
const browserOne=environment(href(1),browseSession,{hasManifest:true,media:[browseMedia]});
browserOne.click('add');
browserOne.silenceTick();
t('browse tab video is never automatically muted or paused',()=>{assert.equal(browseMedia.muted,false);assert.equal(browseMedia.pauseCount,0)});
t('browse tab queues real lecture without program_id',()=>assert.equal(state().items[0].id,'video1'));
const browserTwo=environment(href(2),browseSession);
browserTwo.click('add');
t('browse tab 2 preserves FIFO queue',()=>assert.deepEqual(state().items.map(x=>x.id),['video1','video2']));
const workerSession=new Map();
const workerHome=environment('https://vidcloud.eu.org/index.php',workerSession);
workerHome.click('start');workerHome.triggerStart();
t('worker opens the first REAL full player URL',()=>{assert.equal(workerHome.loc.assigned,href(1));assert.equal(state().activeId,'video1');assert.equal(state().items[0].status,'opening')});
const preManifestMedia={muted:false,defaultMuted:false,paused:false,pauseCount:0,pause(){this.paused=true;this.pauseCount++}};
const preManifestWorker=environment(href(1),workerSession,{hasManifest:false,media:[preManifestMedia]});
preManifestWorker.silenceTick();
t('worker mutes before playlist discovery without blocking playback trigger',()=>{assert.equal(preManifestMedia.muted,true);assert.equal(preManifestMedia.pauseCount,0)});
const workerMedia={muted:false,defaultMuted:false,paused:false,pauseCount:0,pause(){this.paused=true;this.pauseCount++}};
const workerOnVideoOne=environment(href(1),workerSession,{sender:true,hasManifest:true,media:[workerMedia]});
workerOnVideoOne.tick();
workerOnVideoOne.silenceTick();
t('worker mutes and pauses player after manifest becomes available',()=>{assert.equal(workerMedia.muted,true);assert.equal(workerMedia.defaultMuted,true);assert.equal(workerMedia.pauseCount,1)});
t('worker does not repeatedly pause an already paused player',()=>{workerOnVideoOne.silenceTick();assert.equal(workerMedia.pauseCount,1)});
t('worker triggers existing one-click uploader once',()=>{assert.equal(workerOnVideoOne.sendCount,1);assert.equal(state().items[0].status,'uploading')});
workerOnVideoOne.senderStatus.textContent='✅ Lecture 1: all 25 segments safely uploaded. MP4 conversion + Telegram sending is running in background.';
workerOnVideoOne.tick();
t('successful VPS acceptance recorded without claiming Telegram delivery',()=>{assert.equal(state().items[0].status,'relay_accepted');assert.equal(state().activeId,null);assert.equal(state().items[0].url,'')});
workerOnVideoOne.sendButton.disabled=false;
workerOnVideoOne.tick();
t('worker automatically proceeds to queued video 2',()=>{assert.equal(workerOnVideoOne.loc.assigned,href(2));assert.equal(state().activeId,'video2')});
const workerOnVideoTwo=environment(href(2),workerSession,{sender:true,hasManifest:true});workerOnVideoTwo.tick();
// Temporarily displayed errors must not stop active work (sender button disabled).
workerOnVideoTwo.senderStatus.textContent='Failed: CDN segment 3 HTTP 403';
workerOnVideoTwo.tick();
t('failure message during live sender does not stop queue',()=>{assert.equal(state().items[1].status,'uploading');assert.equal(state().run,true)});
workerOnVideoTwo.advance(20000);workerOnVideoTwo.tick();
t('even old-looking failure text does not override active upload',()=>{assert.equal(state().items[1].status,'uploading');assert.equal(state().run,true)});
// Error appears only when uploader's request and cleanup have actually ended.
workerOnVideoTwo.sendButton.disabled=false;workerOnVideoTwo.tick();
t('idle failure must settle before pausing queue',()=>{assert.equal(state().items[1].status,'uploading');assert.equal(state().run,true)});
workerOnVideoTwo.advance(6000);workerOnVideoTwo.tick();
t('on final upload error the failed lecture is recorded but worker keeps running',()=>{
  assert.equal(state().items[1].status,'failed');
  assert.equal(state().run,true);
  assert.equal(state().activeId,null);
});
const browseSession2=new Map();
const browserThree=environment(href(3),browseSession2);
browserThree.click('add');
workerOnVideoTwo.tick();
t('worker advances to next lecture automatically after failure',()=>{
  assert.equal(workerOnVideoTwo.loc.assigned,href(3));
  assert.equal(state().activeId,'video3');
});
const workerOnThree=environment(href(3),workerSession,{sender:true,hasManifest:true});
workerOnThree.tick();
workerOnThree.senderStatus.textContent='✅ All 22 segments safely uploaded; Railway converting + sending to Telegram in background.';
workerOnThree.tick();
t('success after failure does not lose failed lecture record',()=>{
  assert.equal(state().items.find(x=>x.id==='video3').status,'relay_accepted');
  assert.equal(state().items.find(x=>x.id==='video2').status,'failed');
});
browserThree.click('retryFailed');
t('manual Retry failed requeues only failed lectures',()=>{
  assert.equal(state().items.find(x=>x.id==='video2').status,'queued');
  assert.equal(state().items.find(x=>x.id==='video3').status,'relay_accepted');
});
console.log(`${passed} browser integration tests passed`);
