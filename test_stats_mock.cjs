'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const script = fs.readFileSync(__dirname+'/Railway_Auto_Telegram.user.js','utf8');
const timer = (ms) => new Promise(r=>setTimeout(r,ms));
async function check(lanes, failIndex = -1, failTimes = Infinity, simulateLostAck = false) {
  let click, selector, status;
  const all=[];
  function elem(tag) {
    const ob={tag,style:{}, textContent:'', hidden:false, disabled:false,
      append(...els){ this.children=(this.children||[]).concat(els); },
      appendChild(el){this.append(el);},
      addEventListener(k,fn){if(tag==='button'&&k==='click') click=fn;}}
    if(tag==='select') selector=ob;
    all.push(ob);return ob;
  }
  const document={getElementById(){return null;},createElement:elem,
    head:elem('head'),body:elem('body'),documentElement:elem('html'),
    title:'Test lecture',querySelector(){return null}};
  let sending=0, maxSending=0, downloading=0, maxDownloading=0, finished=false, cancelled=false;
  const accepted = new Map();
  const attempts = new Map();
  const host='https://bunny-cdn-qbg-s6.testwave.cc';
  const playlist=host+'/test.docx';
  const parts=Array.from({length:7},(_,i)=>{
    const d=new Uint8Array(376);d[0]=d[188]=0x47;d[1]=i+1;return d;
  });
  const response=(code,data)=>({ok:code>=200&&code<300,status:code,
    async json(){return data;},async text(){return data;},async arrayBuffer(){return data;}});
  async function fetch(url, opts={}) {
    if(url===playlist) return response(200, '#EXTM3U\n'+parts.map((_,i)=>`#EXTINF:3,\n${i}.pdf\n`).join('')+'#EXT-X-ENDLIST');
    const ix=url.match(/\/(\d+)\.pdf$/);
    if(ix) {const i=+ix[1]; downloading++; maxDownloading=Math.max(maxDownloading,downloading); await timer(12+i%3); downloading--; return response(200,parts[i].slice().buffer);}
    if(url.endsWith('/api/relay/start')) return response(201,{job_id:'T1'});
    const up=url.match(/\/api\/relay\/T1\/segment\/(\d+)$/);
    if(up) {
      const i=+up[1];sending++;maxSending=Math.max(maxSending,sending);
      await timer(10+(i%2));
      sending--;
      const n=(attempts.get(i)||0)+1; attempts.set(i,n);
      if (simulateLostAck && i===failIndex && n===1) {
        assert(!accepted.has(i));accepted.set(i,Array.from(new Uint8Array(opts.body)));
        return response(503,{error:'ack lost'});
      }
      if(i===failIndex && n<=failTimes && !simulateLostAck) return response(503,{error:'mock server failure'});
      if (accepted.has(i)) return response(200,{received:accepted.size,already_received:true});
      accepted.set(i,Array.from(new Uint8Array(opts.body)));
      return response(200,{received:accepted.size});
    }
    if(url.endsWith('/api/relay/T1/finish')) {
      assert.strictEqual(accepted.size, parts.length);
      assert.strictEqual(sending,0);finished=true;return response(202,{status:'processing'});
    }
    if(url.endsWith('/api/relay/T1/cancel')) {cancelled=true;return response(200,{status:'cancelled'});}
    if(url.endsWith('/api/relay/T1/status')) {assert(finished);return response(200,{status:'sent',title:'Test'});}
    throw new Error('Unexpected '+url);
  }
  const sandbox={document,fetch,URL,URLSearchParams,ArrayBuffer,Uint8Array,AbortController,clearTimeout,
    performance:{getEntriesByType:()=>[{name:playlist}],now:()=>Date.now()},
    location:{origin:'https://vidcloud.eu.org',search:'?video_name=Test'},
    setTimeout:(fn)=>Promise.resolve().then(fn),setInterval,clearInterval,console};
  vm.runInNewContext(script,sandbox);
  selector.value=String(lanes);
  status=all.find(o=>o.className==='relay-message');
  const stats=all.find(o=>o.className==='relay-stats');
  assert(stats, 'Missing stats panel');
  assert.strictEqual(typeof click,'function');
  await click();
  if(failIndex < 0 || failTimes < Infinity || simulateLostAck) {
    assert(finished && !cancelled);
    assert.strictEqual(accepted.size,7);
    for(let i=0;i<7;i++) assert.deepStrictEqual(accepted.get(i),Array.from(parts[i]));
    assert(status.textContent.includes('Sent to Telegram'));
    assert(stats.textContent.includes('CDN download: 7/7'), stats.textContent);
    assert(stats.textContent.includes('VPS upload:   7/7'), stats.textContent);
    assert(stats.textContent.includes(`peak ↓${maxDownloading}`), stats.textContent);
    assert(stats.textContent.includes('MiB/s'), stats.textContent);
    assert(stats.textContent.includes(`L${lanes} `), stats.textContent);
    assert(maxSending<=lanes);
    if(lanes>1) { assert(maxSending>1, 'No real parallel POST uploads'); assert(maxDownloading>1, 'No parallel segment downloads'); }
    console.log(`PASS: ${lanes} lane(s), peak downloads=${maxDownloading}, peak POSTs=${maxSending}, stats, 7 segments, ordered IDs`);
    if (failIndex >= 0) assert(attempts.get(failIndex) >= 2, 'Retry not observed');
  } else {
    assert(!finished && cancelled);
    assert(status.textContent.includes('Failed'));
    assert(stats.textContent.includes('CDN download:'));
    assert(stats.textContent.includes('VPS upload:'));
    console.log('PASS: failing upload cancels job and does not finish');
  }
  assert.strictEqual(selector.disabled,false);
  assert.strictEqual(downloading, 0);
  assert.strictEqual(sending, 0);
}
(async()=>{for(const n of [1,2,3]) await check(n);await check(2,2);await check(1,2,2);await check(1,2,Infinity,true);})().catch(e=>{console.error(e);process.exitCode=1});
