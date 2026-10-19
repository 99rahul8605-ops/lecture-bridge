// ==UserScript==
// @name         Lecture Two-Tab Queue Manager
// @namespace    lecture-bridge-queue
// @version      1.1.0-resume
// @description  Queue actual authorized lecture page URLs in a browsing tab. A separate worker tab runs the existing one-click Telegram uploader and shows queue stats. Does not alter media handling.
// @match        https://vidcloud.eu.org/index.php*
// @match        https://vidcloud.eu.org/play.php*
// @run-at       document-idle
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_addValueChangeListener
// ==/UserScript==

/*
 * Install alongside exactly ONE existing "Lecture Auto Send to Telegram" script.
 * Tab A: index.php -> Start Worker, leave it open (do not navigate it).
 * Tab B: open any lecture using the site's normal chapter cards -> Add to Queue -> Back.
 * Only queues existing authorized URLs. No key/signature generation, verification or DRM bypass.
 * IMPORTANT: Full player URLs may include session-specific video parameters. Stored in
 * Tampermonkey script storage, never sent to another API by this addon. Clear after use.
 */
(() => {
  'use strict';
  const VERSION = '1.1.0';
  const STORAGE_KEY = 'lecture-two-tab-queue-v1';
  const LEASE_KEY = 'lecture-two-tab-queue-lease-v1';
  const TAB_KEY = 'lecture-two-tab-worker-id';
  const ACTIVE_UPLOAD_TAB_KEY = 'lecture-two-tab-active-upload-id';
  const MAX_QUEUE = 60;
  const URL_TTL_MS = 12 * 60 * 60 * 1000; // signed session/player URLs may expire
  const LEASE_MS = 20000;
  const OPEN_TIMEOUT_MS = 2 * 60 * 1000;
  const CHECK_EVERY_MS = 1200;
  const BUSY_RETRY_MS = 45000;
  const BUSY_MAX_MS = 12 * 60 * 1000;
  const FAILURE_SETTLE_MS = 5000; // Do not trust an old/transient error while uploader is active
  // program_id is optional on some genuine player URLs. Do not invent it or rewrite URLs.
  const REQUIRED_PARAMS = [
    'batch_id', 'subject_id', 'topic_id', 'video_id',
    'typeId', 'video_url', 'video_name', 'video_img', 'video_type', 'play_type'
  ];

  // ===== testable, DOM-independent operations =====
  // BEGIN QUEUE CORE
  const QueueCore = (() => {
    const empty = () => ({items: [], activeId: null, run: false, workerId: null, leaseUntil: 0});
    const fresh = s => s && typeof s === 'object' && Array.isArray(s.items)
      ? {...empty(), ...s, items: s.items.slice()} : empty();
    const identity = (urlString) => {
      const url = new URL(urlString);
      if (url.origin !== 'https://vidcloud.eu.org' || url.pathname !== '/play.php') {
        throw new Error('Open a lecture using the website chapter card first.');
      }
      const missing = REQUIRED_PARAMS.filter(key => !url.searchParams.get(key));
      if (missing.length) throw new Error(`Lecture player URL incomplete (${missing.length} required parameters missing). Open using the website card.`);
      const videoId = url.searchParams.get('video_id');
      const title = (url.searchParams.get('video_name') || 'Lecture').slice(0, 120);
      return {id: videoId, title, url: url.href};
    };
    const add = (s, urlString, now) => {
      s = fresh(s);
      const item = identity(urlString);
      const prior = s.items.find(x => x.id === item.id);
      if (prior) {
        if (['queued', 'opening', 'uploading', 'relay_accepted', 'telegram_sent'].includes(prior.status)) {
          return {state:s, inserted:false};
        }
        // Reopening the same lecture via the real website card refreshes expired/failed URL.
        if (s.activeId === item.id) throw new Error('This lecture is already active.');
        prior.url = item.url; prior.title = item.title; prior.addedAt = now;
        prior.status = 'queued'; prior.message = ''; prior.startedAt = 0; prior.busySince = 0;
        return {state:s, inserted:true};
      }
      if (s.items.filter(x => ['queued','opening','uploading'].includes(x.status)).length >= MAX_QUEUE) {
        throw new Error(`Queue limit ${MAX_QUEUE} reached.`);
      }
      s.items.push({...item, status:'queued', addedAt:now, startedAt:0, message:'', busySince:0});
      return {state:s, inserted:true};
    };
    const next = (s, now) => {
      s = fresh(s);
      if (s.activeId) return {state:s, item:null};
      let item = s.items.find(x => x.status === 'queued');
      while(item && now - item.addedAt > URL_TTL_MS) {
        item.status = 'expired'; item.message = 'Open this lecture from its chapter card again to refresh its URL.';
        item = s.items.find(x => x.status === 'queued');
      }
      if (!item) return {state:s, item:null};
      item.status='opening'; item.startedAt=now; item.busySince=0;
      s.activeId=item.id;
      return {state:s, item:{...item}};
    };
    const complete = (s, videoId, status) => {
      s=fresh(s);
      const item=s.items.find(x=>x.id===videoId);
      if (item) {item.status=status; item.message=''; item.url='';}
      if(s.activeId===videoId) s.activeId=null;
      return s;
    };
    const counts = (s) => {
      s = fresh(s);
      return {
        queued: s.items.filter(x => x.status === 'queued').length,
        active: s.items.filter(x => ['opening','uploading'].includes(x.status)).length,
        server: s.items.filter(x => x.status === 'relay_accepted').length,
        sent: s.items.filter(x => x.status === 'telegram_sent').length,
        attention: s.items.filter(x => ['failed','expired','interrupted'].includes(x.status)).length
      };
    };
    return {empty, fresh, identity, add, next, complete, counts};
  })();
  // END QUEUE CORE

  const makeId = () => (crypto.randomUUID ? crypto.randomUUID() :
    `${Date.now()}-${Math.random().toString(36).slice(2)}`);
  const workerId = sessionStorage.getItem(TAB_KEY);
  let localWorkerId = workerId;
  let currentWorkStart = 0;
  let workerNextAction = 0;
  let pendingNavigation = false;
  let initiatedUploadHere = false;
  let failureSeenAt = 0;
  let failureText = '';
  function resetFailureObservation() {failureSeenAt = 0; failureText = '';}
  function failedAndIdleFor(msg) {
    if (failureText !== msg || !failureSeenAt) {failureText = msg; failureSeenAt = Date.now(); return false;}
    return Date.now() - failureSeenAt >= FAILURE_SETTLE_MS;
  }
  let lastLeaseWrite = 0;
  let workerStatus = 'Idle';
  const interruptedOnLoad = sessionStorage.getItem(ACTIVE_UPLOAD_TAB_KEY);
  const readable = {
    queued: 'Waiting', opening: 'Opening player', uploading: 'Browser uploading',
    relay_accepted: 'VPS accepted (Telegram pending)', telegram_sent: 'Telegram sent',
    failed: 'Failed', expired: 'Expired URL', interrupted: 'Interrupted'
  };

  function load() {
    try {return QueueCore.fresh(GM_getValue(STORAGE_KEY, null));}
    catch(error) {console.error('[Lecture queue] Storage read failed',error); return QueueCore.empty();}
  }
  function save(state) {GM_setValue(STORAGE_KEY, state);}
  function mutate(fn) {
    const s = load();
    const result = fn(s) || s;
    save(result);
    return result;
  }
  function readLease() {return GM_getValue(LEASE_KEY, {owner:null,until:0});}
  function ownLease(s) {const l=readLease();return localWorkerId && s.workerId === localWorkerId && l.owner===localWorkerId && l.until>Date.now();}
  function toast(message) {workerStatus=message; render();}

  const panel = document.createElement('section');
  panel.id='lecture-two-tab-queue-panel';
  panel.innerHTML = `
    <div class="ltq-title">📋 Lecture queue <span class="ltq-version">v${VERSION}</span></div>
    <div class="ltq-controls">
      <button type="button" data-action="add">+ Add this lecture</button>
      <button type="button" data-action="start">▶ Start worker HERE</button>
      <button type="button" data-action="stop">⏸ Stop after current</button>
      <button type="button" data-action="clear">Clear finished</button>
      <button type="button" data-action="clearAttention">Clear failed only</button>
      <button type="button" data-action="retryFailed">↻ Retry failed</button>
      <button type="button" data-action="removeWaiting">Remove waiting</button>
    </div>
    <div class="ltq-counts"></div>
    <div class="ltq-message"></div>
    <ol class="ltq-list"></ol>
    <div class="ltq-hint">2 tabs: one worker tab kept open; other tab adds lectures from real player pages.</div>
  `;
  const css=document.createElement('style');
  css.textContent=`
    #lecture-two-tab-queue-panel{box-sizing:border-box;background:#101d31!important;color:#ecf5ff!important;
      border:1px solid #4b6481!important;border-radius:10px!important;padding:10px!important;
      width:min(390px,94vw)!important;max-height:48vh!important;overflow-y:auto!important;
      box-shadow:0 4px 14px #0008!important;font:12px/1.45 system-ui,Arial!important;
      text-align:left!important;white-space:normal!important}
    #lecture-two-tab-queue-panel *{box-sizing:border-box!important}
    #lecture-two-tab-queue-panel .ltq-title{font-size:14px!important;font-weight:700!important;margin-bottom:6px!important}
    #lecture-two-tab-queue-panel .ltq-version{font-weight:400!important;opacity:.6!important}
    #lecture-two-tab-queue-panel .ltq-controls{display:flex!important;flex-wrap:wrap!important;gap:5px!important;margin-bottom:7px!important}
    #lecture-two-tab-queue-panel button{color:white!important;background:#2965a6!important;border:0!important;
      border-radius:6px!important;padding:6px 8px!important;cursor:pointer!important;font:12px system-ui!important;
      box-shadow:none!important;display:inline-block!important}
    #lecture-two-tab-queue-panel button:disabled{opacity:.45!important;cursor:not-allowed!important}
    #lecture-two-tab-queue-panel .ltq-counts{font:12px/1.5 ui-monospace,monospace!important;color:#b4e7ff!important;margin:3px 0!important}
    #lecture-two-tab-queue-panel .ltq-message{color:#ffe7a9!important;overflow-wrap:anywhere!important;margin:5px 0!important}
    #lecture-two-tab-queue-panel .ltq-list{padding-left:20px!important;margin:5px 0!important;max-height:120px!important;overflow:auto!important}
    #lecture-two-tab-queue-panel .ltq-list li{margin:2px 0!important;overflow-wrap:anywhere!important;list-style:decimal!important}
    #lecture-two-tab-queue-panel .ltq-hint{opacity:.7!important;font-size:11px!important;margin-top:7px!important}
  `;
  (document.head || document.documentElement).appendChild(css);
  function attachPanel() {
    const sender = document.getElementById('railway-lecture-link-v1');
    if (sender) {
      if (panel.parentElement!==sender) sender.appendChild(panel);
      panel.style.position='';panel.style.top='';panel.style.right='';panel.style.zIndex='';
      return;
    }
    if (!panel.isConnected) {
      panel.style.position='fixed'; panel.style.top='80px'; panel.style.right='12px';
      panel.style.zIndex='2147483646';
      document.body.appendChild(panel);
    }
  }
  attachPanel();
  const attachTimer=setInterval(attachPanel,1000);
  // No dependency on site source code: the existing uploader owns all network transfers.

  function render() {
    const s=load(); const c=QueueCore.counts(s);
    const current=s.items.find(x=>x.id===s.activeId);
    const counts=panel.querySelector('.ltq-counts');
    counts.textContent=`Waiting ${c.queued} | Active ${c.active} | VPS accepted ${c.server} | TG sent ${c.sent} | Attention ${c.attention}`;
    const message=panel.querySelector('.ltq-message');
    message.textContent=(ownLease(s)?'🤖 Worker THIS tab: ':'') + workerStatus +
      (current?` | Now: ${current.title.slice(0,48)}`:'');
    const list=panel.querySelector('.ltq-list');
    list.replaceChildren();
    for (const item of s.items.slice(-25)) {
      const li=document.createElement('li');
      li.textContent=`${item.title.slice(0,57)} — ${readable[item.status] || item.status}`;
      if (item.message) li.title=item.message;
      list.appendChild(li);
    }
    panel.querySelector('[data-action="add"]').disabled = location.pathname!=='/play.php';
    const lease=readLease();
    panel.querySelector('[data-action="start"]').disabled = Boolean(lease.owner &&
      lease.owner !== localWorkerId && lease.until > Date.now());
  }
  render();
  try {GM_addValueChangeListener(STORAGE_KEY,()=>render());} catch(_) {}

  function addCurrent() {
    try {
      const result=QueueCore.add(load(),location.href,Date.now());
      if (result.inserted) save(result.state);
      toast(result.inserted?'✅ Lecture added. Go Back in BROWSE tab and add more.':'Already in queue.');
    } catch(error) {toast(`⚠️ ${error.message}`);}
  }

  function startWorker() {
    const s=load(), now=Date.now(), lease=readLease();
    if (lease.owner && lease.owner!==localWorkerId && lease.until>now) {
      toast('Worker already running in another tab. Use that tab.');return;
    }
    if (!localWorkerId) {
      localWorkerId=makeId();
      sessionStorage.setItem(TAB_KEY,localWorkerId);
    }
    if(s.activeId && s.workerId!==localWorkerId) {
      toast('Previous worker has unfinished active lecture. Do not restart it blindly; check Telegram and clear/retry manually.');
      return;
    }
    s.workerId=localWorkerId; s.leaseUntil=now+LEASE_MS; s.run=true;lastLeaseWrite=now;
    GM_setValue(LEASE_KEY,{owner:localWorkerId,until:now+LEASE_MS});
    save(s);
    toast('▶ Worker ON • 🔇 Auto-Mute/Pause armed. Keep this tab open; add lectures from a SECOND tab.');
    setTimeout(tickWorker,100);
  }
  function stopWorker() {
    mutate(s=>{s.run=false;return s;});
    toast('⏸ Stop requested. Current upload (if any) can finish.');
  }
  function clearFinished() {
    mutate(s=>{s.items=s.items.filter(x=>!['relay_accepted','telegram_sent'].includes(x.status));return s;});
    toast('Completed queue entries cleared.');
  }
  function clearAttention() {
    if(!confirm('Remove only failed/interrupted/expired queue entries? First verify whether their upload actually reached Telegram. Waiting lectures will stay.'))return;
    mutate(s=>{s.items=s.items.filter(x=>!['failed','expired','interrupted'].includes(x.status));return s;});
    toast('Failed/interrupted/expired entries cleared; waiting lectures preserved.');
  }
  function retryFailed() {
    let retried = 0, expired = 0;
    mutate(s => {
      if (s.activeId) { return s; }
      for (const item of s.items) {
        if (!['failed','interrupted','expired'].includes(item.status)) continue;
        if (!item.url || Date.now() - item.addedAt > URL_TTL_MS) {
          item.status = 'expired';
          item.message = 'Open lecture via real chapter card, then Add this lecture again.';
          expired++;
          continue;
        }
        item.status = 'queued'; item.startedAt = 0; item.busySince = 0;
        item.message = ''; retried++;
      }
      return s;
    });
    toast(`♻ Requeued ${retried} failed lecture(s). ${expired} expired URL(s) need re-adding from chapter cards.`);
  }
  function removeWaiting() {
    if(!confirm('Remove all waiting/failed/expired lectures? The active upload is not affected.'))return;
    mutate(s=>{s.items=s.items.filter(x=>!['queued','failed','expired','interrupted'].includes(x.status));return s;});
    toast('Waiting/failed/expired entries removed. Active upload unchanged.');
  }
  panel.addEventListener('click',event=>{
    const b=event.target.closest('button[data-action]'); if(!b)return;
    const action=b.dataset.action;
    if(action==='add')addCurrent();
    else if(action==='start')startWorker();
    else if(action==='stop')stopWorker();
    else if(action==='clear')clearFinished();
    else if(action==='clearAttention')clearAttention();
    else if(action==='retryFailed')retryFailed();
    else if(action==='removeWaiting')removeWaiting();
  });

  // Worker-only playback control. Let the genuine player discover its authorized
  // playlist first; then silence/pause playback while the separate uploader runs.
  // This does NOT pause the Chrome -> relay transfer or block media requests.
  function silenceWorkerPlayer() {
    if (!localWorkerId) return;
    const s = load();
    if (!ownLease(s) || !s.run) return; // never alter the BROWSE tab
    const item = s.items.find(x => x.id === s.activeId);
    // Do not pause before playlist discovery: some players only request the
    // manifest after Play. Mute is safe even before the manifest is available.
    const safeToPause = Boolean(item && ['opening', 'uploading'].includes(item.status)
      && (playlistObserved() || (item.status === 'uploading' && senderElements()?.btn.disabled)));
    const documents = [document];
    if (typeof document.querySelectorAll === 'function') {
      for (const frame of document.querySelectorAll('iframe')) {
        try { if (frame.contentDocument) documents.push(frame.contentDocument); }
        catch (_) { /* cross-origin iframe: browser disallows access */ }
      }
    }
    for (const doc of documents) {
      if (typeof doc.querySelectorAll !== 'function') continue;
      for (const media of doc.querySelectorAll('video, audio')) {
        try {
          media.muted = true;
          media.defaultMuted = true;
          if (safeToPause && !media.paused && typeof media.pause === 'function') {
            media.pause();
          }
        } catch (_) { /* inaccessible/replaced player; try again next tick */ }
      }
    }
  }
  // Also guard replays between scheduled checks, including dynamically loaded video.
  if (typeof document.addEventListener === 'function') {
    document.addEventListener('play', () => silenceWorkerPlayer(), true);
  }
  setInterval(silenceWorkerPlayer, 700);

  function senderElements() {
    const root=document.getElementById('railway-lecture-link-v1');
    if(!root)return null;
    const btn=[...root.querySelectorAll('button')].find(b=>/Send MP4 to Telegram/i.test(b.textContent));
    const status=root.querySelector('.relay-message');
    return btn && status ? {btn,status} : null;
  }
  function playlistObserved() {
    return performance.getEntriesByType('resource').some(r=>{
      try {const u=new URL(r.name);return /\.(docx|m3u8)$/i.test(u.pathname);}
      catch(_) {return false;}
    });
  }
  function interrupt(s,id,reason) {
    const item=s.items.find(x=>x.id===id);
    if(item){item.status='interrupted';item.message=reason;}
    if(s.activeId===id)s.activeId=null;
    s.run=false;
    return s;
  }
  if(interruptedOnLoad) {
    sessionStorage.removeItem(ACTIVE_UPLOAD_TAB_KEY);
    const s=load();
    if(s.workerId===localWorkerId && s.activeId===interruptedOnLoad) {
      save(interrupt(s,interruptedOnLoad,'Page reloaded during upload. Check existing Railway job before re-adding.'));
      workerStatus='⚠️ Upload interrupted by page reload. Queue paused.';
    }
  }

  function finishCurrent(s,videoId,status) {
    sessionStorage.removeItem(ACTIVE_UPLOAD_TAB_KEY);
    initiatedUploadHere=false;
    currentWorkStart=0;
    workerNextAction=0;
    resetFailureObservation();
    const updated=QueueCore.complete(s,videoId,status);
    save(updated);
    toast(status==='telegram_sent'?'✅ Telegram sent; advancing':'✅ VPS accepted; Telegram sending may continue in background. Advancing.');
  }
  function failCurrent(s,videoId,message) {
    resetFailureObservation();
    sessionStorage.removeItem(ACTIVE_UPLOAD_TAB_KEY);
    initiatedUploadHere=false;
    const item=s.items.find(x=>x.id===videoId);
    if(item){item.status='failed';item.message=message;}
    if(s.activeId===videoId)s.activeId=null;
    // Don't block remaining lectures after a real final failure.
    // Keep URL + title for explicit Retry failed after current queue runs.
    // Frontend is confirmed idle before this function is called.
    save(s);
    toast(`⏭ Skipped failed lecture; next queued lecture will start. ${message.slice(0,100)}`);
  }

  function tickWorker() {
    if(!localWorkerId) return;
    let s=load();
    const lease=readLease();
    if(s.workerId!==localWorkerId || lease.owner!==localWorkerId) return;
    if(Date.now()-lastLeaseWrite>6000) {
      lastLeaseWrite=Date.now();
      GM_setValue(LEASE_KEY,{owner:localWorkerId,until:Date.now()+LEASE_MS});
    }
    // If stop was requested, keep browser tab and any ongoing sender intact.
    if(!s.run) {
      workerStatus=s.activeId?'⏸ Stop requested; current upload can finish':'⏸ Worker stopped';
      // We still monitor an active sender to record successful completion.
      if(!s.activeId) {
        s.workerId=null;s.leaseUntil=0;save(s);
        GM_setValue(LEASE_KEY,{owner:null,until:0});
        sessionStorage.removeItem(TAB_KEY);localWorkerId=null;
        render();return;
      }
    }
    if(!s.activeId) {
      if(!s.run) return;
      // A manual/previous upload may still own this tab. Never navigate away from it.
      const sender = senderElements();
      if(sender && sender.btn.disabled) {
        workerStatus='⏳ Existing browser upload still running; will start queue after it finishes';
        return;
      }
      const picked=QueueCore.next(s,Date.now());
      save(picked.state);
      if(!picked.item) {workerStatus='✅ Queue empty; waiting for new lectures';return;}
      s=picked.state;
      workerStatus=`Opening ${picked.item.title.slice(0,60)}…`;
    }
    const item=s.items.find(x=>x.id===s.activeId);
    if(!item) {s.activeId=null;save(s);return;}
    if(item.status==='opening') {
      if(!s.run) return; // paused before starting upload
      if(location.pathname!=='/play.php'||new URLSearchParams(location.search).get('video_id')!==item.id) {
        const existingSender=senderElements();
        if(existingSender && existingSender.btn.disabled) {
          workerStatus='⏳ Existing browser upload is busy; not navigating away';
          return;
        }
        if(pendingNavigation)return;
        if(!item.url) {failCurrent(s,item.id,'Missing player URL; re-add from website card.');return;}
        try {
          // Validate again before using URL from storage. Never construct security fields.
          QueueCore.identity(item.url);
          pendingNavigation=true;
          location.assign(item.url);
          return;
        } catch(error) {pendingNavigation=false;failCurrent(s,item.id,error.message);return;}
      }
      const sender=senderElements();
      if(!sender) {
        workerStatus='Waiting for existing Send MP4 uploader script…';
      } else if(playlistObserved()) {
        if(sender.btn.disabled) {
          workerStatus='Existing uploader busy; waiting.';
        } else {
          item.status='uploading'; item.startedAt=Date.now();
          save(s);
          initiatedUploadHere=true;
          sessionStorage.setItem(ACTIVE_UPLOAD_TAB_KEY,item.id);
          workerStatus=`⬆ Uploading ${item.title.slice(0,40)}…`;
          sender.btn.click(); // exact existing one-click uploader; no replacement for media handling
        }
      } else if(Date.now()-(item.startedAt||Date.now())>OPEN_TIMEOUT_MS) {
        failCurrent(s,item.id,'No authorized media playlist loaded within 2 minutes. Play the video normally or refresh the URL.');
        return;
      } else {
        workerStatus='Waiting for player playlist; press Play if autoplay is blocked.';
      }
    }
    if(item.status==='uploading') {
      const sender=senderElements();
      if(!sender) {workerStatus='Waiting for uploader status…';return;}
      const msg=sender.status.textContent.trim();
      if(/^✅\s*Sent to Telegram:/i.test(msg)) {finishCurrent(s,item.id,'telegram_sent');return;}
      if(/^✅/.test(msg)&&/all\s+\d+\s+segments\s+safely\s+uploaded/i.test(msg)) {
        finishCurrent(s,item.id,'relay_accepted');return;
      }
      if(/^Failed:/i.test(msg)) {
        // The uploader may be cleaning up an old request, or have started a fresh
        // attempt while the last error is still displayed. Its disabled button is
        // the strongest existing signal that network work is actually in progress.
        if(sender.btn.disabled) {
          resetFailureObservation();
          workerStatus=`⏳ Uploader is still working; ignoring temporary error text`;
          return;
        }
        // Require the SAME final error to remain with an idle sender for 5 sec.
        // Otherwise a stale failure can incorrectly stop a successful upload.
        if(!failedAndIdleFor(msg)) {
          workerStatus='⏳ Confirming uploader is idle after error (5s)…';
          return;
        }
        if(/\b429\b/.test(msg)) {
          if(!item.busySince)item.busySince=Date.now();
          if(Date.now()-item.busySince>BUSY_MAX_MS) {
            failCurrent(s,item.id,'Relay remained full for 3 hours; try later.');return;
          }
          if(workerNextAction===0)workerNextAction=Date.now()+BUSY_RETRY_MS;
          workerStatus=`⏳ Relay busy (429); retry in ${Math.max(0,Math.ceil((workerNextAction-Date.now())/1000))}s`;
          save(s);
          if(Date.now()>=workerNextAction&&sender.btn&&!sender.btn.disabled&&s.run) {
            workerNextAction=0;
            resetFailureObservation();
            sender.btn.click();
          }
          return;
        }
        failCurrent(s,item.id,msg);return;
      }
      resetFailureObservation();
      workerStatus=`⬆ ${item.title.slice(0,48)} — ${msg.slice(0,100)||'Starting…'}`;
    }
    render();
  }
  setInterval(()=>{
    const s=load();
    if(localWorkerId && s.workerId===localWorkerId) tickWorker();
    render();
  },CHECK_EVERY_MS);
  console.info('[Lecture queue] Worker-only auto-mute/pause ready; existing uploader unchanged.');
})();
