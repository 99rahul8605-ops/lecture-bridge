// ==UserScript==
// @name         Lecture Cloud Queue (PC OFF Pilot)
// @namespace    lecture-bridge-cloud
// @version      1.0.0
// @description  Queue authorized unencrypted HLS VOD streams for Railway-side download, conversion and Telegram; no DRM/session bypass.
// @match        https://vidcloud.eu.org/index.php*
// @match        https://vidcloud.eu.org/play.php*
// @run-at       document-idle
// @grant        GM_getValue
// @grant        GM_setValue
// ==/UserScript==
(() => {
  'use strict';
  const RELAY = 'https://ubuntu-production-9d09.up.railway.app';
  const HOST = 'bunny-cdn-qbg-s6.testwave.cc';
  const TOKEN_NAME = 'lecture-cloud-queue-access-key';
  const panel = document.createElement('div');
  panel.id = 'lecture-cloud-queue-panel';
  panel.style.cssText = 'position:fixed!important;bottom:12px!important;right:12px!important;z-index:2147483646!important;box-sizing:border-box!important;width:min(360px,94vw)!important;max-height:45vh!important;overflow-y:auto!important;border-radius:12px!important;border:1px solid #4485b7!important;background:#142234!important;color:#f0f7ff!important;padding:12px!important;box-shadow:0 4px 18px #0008!important;font:12px/1.5 system-ui!important;';
  const label = document.createElement('div');
  label.textContent = '☁ Railway Cloud Queue • PC OFF trial';
  label.style.cssText = 'font-size:14px;font-weight:700;margin-bottom:8px';
  const row = document.createElement('div');
  row.style.cssText = 'display:flex;flex-wrap:wrap;gap:5px';
  function makeButton(text, handler) {
    const b = document.createElement('button');
    b.type='button';b.textContent=text;
    b.style.cssText='border:0;border-radius:7px;background:#2365a0;color:#fff;padding:7px;cursor:pointer;font-size:12px';
    b.addEventListener('click',handler);
    row.appendChild(b);return b;
  }
  const status = document.createElement('div');
  status.style.cssText = 'margin-top:8px;white-space:pre-wrap;overflow-wrap:anywhere;color:#ffe9a6';
  const counts = document.createElement('div');
  counts.style.cssText = 'margin-top:6px;font-weight:700;color:#acdfff';
  const list = document.createElement('ol');
  list.style.cssText = 'margin:5px 0 0 0;padding-left:20px;max-height:125px;overflow:auto;overflow-wrap:anywhere';
  panel.append(label,row,status,counts,list);
  (document.body||document.documentElement).appendChild(panel);
  const say = text => {status.textContent=text;};

  function key(ask=true) {
    let value=GM_getValue(TOKEN_NAME,'');
    if (!value && ask) {
      const input=prompt('Railway setup_deploy.sh ke output se CLOUD QUEUE KEY paste karo (Telegram BOT_TOKEN nahi).');
      if (input && input.trim().length>=24) {
        value=input.trim();GM_setValue(TOKEN_NAME,value);
      }
    }
    return value;
  }
  async function api(path, method='GET', payload=null) {
    const k=key(method!=='GET');
    if (!k) throw new Error('Cloud queue key set karo: Change Key button.');
    const response=await fetch(RELAY + path, {
      method, headers: {'X-Cloud-Queue-Key':k, ...(payload?{'Content-Type':'application/json'}:{})},
      ...(payload?{body:JSON.stringify(payload)}:{})
    });
    const data=await response.json().catch(()=>({}));
    if (!response.ok) throw new Error(`Railway HTTP ${response.status}: ${data.error||'Request failed'}`);
    return data;
  }
  function foundManifest() {
    const matches=performance.getEntriesByType('resource')
      .map(x=>x.name).filter(name=>{
        try {let u=new URL(name);return u.protocol==='https:' && u.hostname===HOST && /\.(docx|m3u8)$/i.test(u.pathname);}
        catch {return false;}
      });
    // If the player fetched a master and variant media playlist, most recent is the media URL.
    return matches.length?matches[matches.length-1]:null;
  }
  let inProgress=false;
  async function add() {
    if(inProgress)return;
    if (location.pathname!=='/play.php') {say('Chapter se lecture kholo, phir Play se playlist load karo.');return;}
    const p=new URLSearchParams(location.search);
    const videoId=p.get('video_id');
    const title=p.get('video_name')||'Lecture';
    if(!videoId){say('Video ID missing: real chapter card se player kholo.');return;}
    const manifest=foundManifest();
    if(!manifest){say('Playlist abhi browser mein detect nahi hui. Video Play karo, playlist load hote hi Pause karke dobara ☁ Add dabao.');return;}
    inProgress=true; say('Railway cloud queue mein add kar raha hoon…');
    try {
      const result=await api('/api/cloud/enqueue','POST',{videoId,title,manifest});
      say(result.duplicate?'Pehle se Cloud Queue mein hai.':'✅ Railway Cloud Queue mein add ho gaya. Browser Back karke next lecture add kar sakte ho.');
      document.querySelectorAll('video,audio').forEach(el=>{try {el.muted=true;el.pause();}catch(e){}});
      await refresh();
    } catch(err) {say('⚠️ '+err.message);} finally {inProgress=false;}
  }
  async function refresh() {
    try {
      const {jobs,persistent}=await api('/api/cloud/queue');
      const n={waiting:0,downloading:0,processing:0,telegram_sending:0,sent:0,failed:0,interrupted:0};
      for(const j of jobs){if(j.state in n)n[j.state]++;}
      counts.textContent=`Waiting ${n.waiting} | Downloading ${n.downloading} | Processing ${n.processing} | TG ${n.telegram_sending} | Sent ${n.sent} | Error ${n.failed+n.interrupted}`;
      list.replaceChildren();
      for(const j of jobs.slice(0,12)) {
        const li=document.createElement('li');
        let pct=j.total?` • ${j.received}/${j.total}`:'';
        if(j.state==='telegram_sending' && j.telegram_total) pct=` • ${Math.round(j.telegram_uploaded/j.telegram_total*100)}%`;

        li.textContent=`${j.title.slice(0,48)} — ${j.state}${pct}`;
        if(j.error) li.title=j.error;
        list.appendChild(li);
      }
      if(!persistent) say('⚠️ Cloud Queue storage volume not configured; Railway restart par queue lose ho sakti hai.');
    } catch(err) {
      if(key(false)) say('Status unavailable: '+err.message);
    }
  }
  makeButton('☁ Add this lecture',add);
  makeButton('↻ Refresh stats',refresh);
  makeButton('🔑 Change Key',()=>{
    const value=prompt('New CLOUD_QUEUE_KEY (Telegram BOT_TOKEN nahi):');
    if(value && value.trim().length>=24){GM_setValue(TOKEN_NAME,value.trim());say('Key updated');refresh();}
  });
  say('Cloud mode: har lecture par Play → Pause → ☁ Add. Railway accepted hone ke baad PC OFF possible hai, provided CDN allows access.');
  if(key(false)) refresh();
  setInterval(()=>{if(key(false))refresh();},10000);
})();
