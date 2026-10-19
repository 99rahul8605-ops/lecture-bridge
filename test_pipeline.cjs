'use strict';
// Simulate one manual click, 4 authorized TS segments, Railway API, and Telegram "sent" status.
// Runs offline; proves ordering and transfer control flow, not Internet speed/DRM access.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const script = fs.readFileSync(__dirname + '/Railway_Auto_Telegram.user.js','utf8');
let callback;
const created=[];
function elem(tag) {
  const obj={tag, style:{}, textContent:'', hidden:false,
    className:'', id:'', disabled:false,
    append(...children) { this.children=(this.children||[]).concat(children); },
    appendChild(x) { this.append(x); },
    addEventListener(kind,fn) { if (tag==='button' && kind==='click') callback=fn; },
  };
  created.push(obj);
  return obj;
}
const document={
  getElementById() {return null;},
  createElement:elem,
  head:elem('head'), body:elem('body'),
  documentElement:elem('html'), title:'Mock lecture',
  querySelector() { return null; }
};
const host='https://vidcloud.eu.org';
const mediaHost='https://bunny-cdn-qbg-s6.testwave.cc';
const playlist=mediaHost+'/playlist.docx';
const ts= new Uint8Array(376);
ts[0]=0x47; ts[188]=0x47;
const events=[];
let uploaded=0, finished=false, began=false;
const makeRes=(code,data)=>({ok: code>=200 && code<300,status:code,
  async json(){return data;}, async text(){return data;}, async arrayBuffer(){return ts.slice().buffer;}});
async function fetch(url,opt) {
  if (url === playlist) return makeRes(200,'#EXTM3U\n#EXTINF:5,\na.pdf\n#EXTINF:5,\nb.pdf\n#EXTINF:5,\nc.pdf\n#EXTINF:5,\nd.pdf\n#EXT-X-ENDLIST');
  if (/\/[abcd]\.pdf$/.test(url)) {events.push('fetch-'+url.slice(-5)); return makeRes(200,null);}
  if (url.endsWith('/api/relay/start')) {began=true; events.push('start'); return makeRes(201,{job_id:'TEST'});}
  const match=url.match(/\/api\/relay\/TEST\/segment\/(\d+)/);
  if (match) {
    assert(began);
    assert(opt.body instanceof ArrayBuffer,'API should upload segment bytes, not whole movie');
    assert.strictEqual(+match[1],uploaded,'Segments sent in playlist order');
    events.push('upload-'+uploaded);
    uploaded++;
    return makeRes(200,{});
  }
  if (url.endsWith('/api/relay/TEST/finish')) {assert.strictEqual(uploaded,4); finished=true; events.push('finish'); return makeRes(202,{});}
  if (url.endsWith('/api/relay/TEST/status')) {assert(finished); events.push('sent');return makeRes(200,{status:'sent',title:'Mock lecture'});}
  throw new Error('Unexpected fetch: '+url);
}
const sandbox={document, fetch, URL, URLSearchParams, ArrayBuffer, Uint8Array,
  performance:{getEntriesByType:()=>[{name:playlist}],now:()=>Date.now()},
  location:{origin:host,search:'?video_name=Mock+lecture'},
  setTimeout:cb=>{Promise.resolve().then(cb);return 1;},console,
};
vm.runInNewContext(script,sandbox,{filename:'Railway_Auto_Telegram.user.js'});
assert.strictEqual(typeof callback,'function','One-click button installed');
(async()=>{
  await callback();
  assert.strictEqual(uploaded,4);
  assert(finished);
  assert(events.indexOf('upload-0') < events.indexOf('upload-1'));
  assert(events.indexOf('upload-3') < events.indexOf('finish'));
  assert(events.indexOf('sent') > events.indexOf('finish'));
  const button=created.find(x=>x.tag==='button');
  assert.strictEqual(button.disabled,false);
  const status=created.find(x=>x.className==='relay-message');
  assert(status.textContent.includes('Sent to Telegram'));
  console.log('PASS: button -> HLS manifest -> 4 TS segments -> ordered upload -> finish -> Telegram sent');
  console.log('Event order:',events.join(', '));
})().catch(e=>{console.error(e);process.exitCode=1;});
