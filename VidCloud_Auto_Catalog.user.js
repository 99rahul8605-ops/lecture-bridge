// ==UserScript==
// @name         VidCloud Auto Catalog Sync (metadata only)
// @namespace    lecture-bridge/catalog
// @version      2.2.0
// @description  Queue navigator follows the site's own batch/subject/chapter cards (no guessed player security fields). Existing authorised uploader unchanged.
// @match        https://vidcloud.eu.org/*
// @run-at       document-start
// @grant        unsafeWindow
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @connect      ubuntu-production-9d09.up.railway.app
// ==/UserScript==
(() => {
  'use strict';
  const BRIDGE = 'https://ubuntu-production-9d09.up.railway.app';
  const w = typeof unsafeWindow !== 'undefined' ? unsafeWindow : window;
  const STORAGE = 'vc_auto_catalog_ctx';
  const keyId = 'catalogSyncKey';
  const readContext = () => {
    try { return JSON.parse(sessionStorage.getItem(STORAGE) || '{}'); }
    catch { return {}; }
  };
  let context = readContext();
  const saveContext = () => {
    try { sessionStorage.setItem(STORAGE, JSON.stringify(context)); } catch (_) {}
  };
  const validId = s => /^[a-f\d]{24}$/i.test(s || '');
  const text = (v, max = 160) => String(v || '').trim().replace(/\s+/g, ' ').slice(0,max);
  const unique = items => [...new Map(items.filter(x => validId(x.id)).map(x => [x.id,x])).values()];
  let sending = 0;
  const sent = new Map();

  GM_registerMenuCommand('Configure catalog sync key', () => {
    const previous = GM_getValue(keyId, '');
    const input = prompt('CATALOG_SYNC_KEY (must match Railway; minimum 24 characters)', previous);
    if (input === null) return;
    if (input.length < 24) { alert('Key must have at least 24 characters.'); return; }
    GM_setValue(keyId, input);
    alert('Catalog key saved. Reload website and browse your batch/chapter pages.');
  });
  GM_registerMenuCommand('Show auto-catalog status', () => {
    alert('Catalog sync key: ' + (GM_getValue(keyId, '').length >= 24 ? 'configured' : 'NOT configured') +
      '\nSelected context: ' + JSON.stringify(context));
  });

  function sync(payload) {
    const key = GM_getValue(keyId, '');
    if (key.length < 24) return;
    if (!payload.items || !payload.items.length) return;
    const signature = JSON.stringify(payload);
    const previously = sent.get(payload.kind + '|' + signature);
    if (previously && Date.now() - previously < 2 * 60 * 1000) return;
    sent.set(payload.kind + '|' + signature, Date.now());
    if (sent.size > 120) sent.clear();
    if (sending >= 5) { setTimeout(() => sync(payload), 750); return; }
    sending++;
    GM_xmlhttpRequest({
      method: 'POST',
      url: BRIDGE + '/api/catalog/sync',
      headers: {'X-Catalog-Key': key, 'Content-Type':'application/json'},
      data: signature,
      timeout: 15000,
      onload: r => {
        sending--;
        if (r.status !== 200) console.warn('[AutoCatalog] Sync HTTP', r.status, payload.kind);
      },
      onerror: () => { sending--; console.warn('[AutoCatalog] Railway sync failed'); },
      ontimeout: () => { sending--; console.warn('[AutoCatalog] Railway sync timeout'); }
    });
  }

  // Optional compatibility hook: the existing independent browser->Railway->Telegram
  // sender remains unchanged. We listen to its SUCCESSFUL job polling response
  // and mark that lecture as delivered in the catalog queue, not before.
  // This does not start exports, fetch media, inspect credentials or bypass DRM.
  const activePlayerVideoId = new URLSearchParams(location.search).get('video_id') || '';
  const relayStatusPath = /^\/api\/relay\/[^/]+\/status$/i;
  let completionNotified = false;
  function relayStatusSuccess(url, responsePromise) {
    let u;
    try { u = new URL(url, location.href); } catch (_) { return; }
    if (u.origin !== BRIDGE || !relayStatusPath.test(u.pathname)) return;
    Promise.resolve(responsePromise).then(r => {
      if (!r.ok) return;
      return r.clone().json().then(data => {
        if (!data || data.status !== 'sent' || completionNotified) return;
        if (!validId(activePlayerVideoId)) {
          console.warn('[AutoCatalog] Telegram sent, but play.php video_id is missing; not marking catalog item.');
          return;
        }
        const key = GM_getValue(keyId, '');
        if (key.length < 24) return;
        GM_xmlhttpRequest({
          method: 'POST',
          url: BRIDGE + '/api/catalog/delivered',
          headers: {'X-Catalog-Key': key, 'Content-Type': 'application/json'},
          data: JSON.stringify({videoId: activePlayerVideoId}),
          timeout: 15000,
          onload: resp => {
            if (resp.status !== 200) {
              console.warn('[AutoCatalog] Delivery status update failed:', resp.status);
              return;
            }
            completionNotified = true;
            let info = {};
            try { info = JSON.parse(resp.responseText); } catch (_) {}
            console.info('[AutoCatalog] Telegram send confirmed; queue items updated:', info.updated || 0);
          },
          onerror: () => console.warn('[AutoCatalog] Delivery status update failed'),
          ontimeout: () => console.warn('[AutoCatalog] Delivery status update timed out')
        });
      }).catch(() => {});
    }).catch(() => {});
  }

  function nearestWithId(node, keys) {
    while (node && node !== document) {
      if (node.getAttribute) {
        for (const attr of keys) {
          const id = node.getAttribute(attr);
          if (validId(id)) return {id,name:text(node.innerText || node.textContent,120),attr};
        }
      }
      node = node.parentElement;
    }
    return null;
  }
  // Batch labels are reliable ONLY inside the actual global batch search
  // results. Elsewhere, an ancestor may carry data-batch-id while its text
  // is the *current chapter* (which previously corrupted the catalog).
  function batchResults() {
    const container = document.querySelector('#globalSearchResults');
    if (!container) return [];
    const found = new Map();
    for (const el of container.querySelectorAll('[data-batch-id]')) {
      const id = el.getAttribute('data-batch-id');
      if (!validId(id)) continue;
      // Ignore wrappers containing results for different batches.
      if ([...el.querySelectorAll('[data-batch-id]')].some(child =>
        child.getAttribute('data-batch-id') !== id)) continue;
      const name = String(el.innerText || el.textContent || '')
        .split(/\r?\n/).map(x => x.trim()).filter(Boolean)[0] || '';
      if (name.length < 4 || /\d+\s+lectures?\s*[|•]/i.test(name)) continue;
      const previous = found.get(id);
      if (!previous || name.length > previous.name.length)
        found.set(id, {id, name: text(name,160)});
    }
    return [...found.values()];
  }
  function captureClick(e) {
    const resultContainer = document.querySelector('#globalSearchResults');
    const candidate = resultContainer && resultContainer.contains(e.target) ?
      nearestWithId(e.target,['data-batch-id']) : null;
    const batch = candidate && batchResults().find(b => b.id === candidate.id);
    if (batch) {
      context.batchId=batch.id;
      context.batchName=batch.name;
      context.subjectId='';context.chapterId='';
      saveContext();
      sync({kind:'batches',items:[{id:batch.id,name:batch.name}]});
      setTimeout(scanDOM,700);
      return;
    }
    const id = nearestWithId(e.target,['data-subject-id','data-chapter-id','data-topic-id','data-id']);
    if (!id) return;
    const m=id.name.match(/(\d+)\s*chapters?\s*[|•]\s*(\d+)\s*lectures?/i);
    if (m && context.batchId) {
      context.subjectId=id.id;context.subjectName=id.name.replace(/\d+\s*chapters?.*$/i,'').trim();context.chapterId='';
      saveContext();
      sync({kind:'subjects',batchId:context.batchId,items:[{id:id.id,name:context.subjectName,chapters:+m[1],lectures:+m[2]}]});
    } else if (/\d+\s*lectures?/i.test(id.name) && context.subjectId && !/\d+\s*chapters?/i.test(id.name)) {
      context.chapterId=id.id;
      saveContext();
    }
    setTimeout(scanDOM,700);
  }

  let scanTimer;
  function scheduleScan() {
    clearTimeout(scanTimer);
    scanTimer=setTimeout(scanDOM,800);
  }
  function scanDOM() {
    if (!document.body) return;
    // Never derive batch names from chapter/lecture page ancestors.
    const batches=batchResults();
    if (batches.length) sync({kind:'batches',items:batches.slice(0,250)});

    if (!validId(context.batchId)) return;
    const subjects=unique([...document.querySelectorAll('[data-subject-id],[data-id]')].map(el => {
      const name=text(el.innerText || el.textContent);
      const m=name.match(/(\d+)\s*chapters?\s*[|•]\s*(\d+)\s*lectures?/i);
      return m ? {id:el.getAttribute('data-subject-id') || el.getAttribute('data-id'),
                  name:name.slice(0,m.index).trim(),chapters:+m[1],lectures:+m[2]} : {id:'',name:''};
    }));
    if (subjects.length) sync({kind:'subjects',batchId:context.batchId,items:subjects.slice(0,250)});

    if (validId(context.subjectId)) {
      const chapters=unique([...document.querySelectorAll('[data-topic-id],[data-chapter-id],[data-id]')].map(el => {
        const name=text(el.innerText || el.textContent);
        if (/\d+\s*chapters?/i.test(name)) return {id:'',name:''};
        const m=name.match(/(\d+)\s*lectures?/i);
        return m ? {id:el.getAttribute('data-topic-id') || el.getAttribute('data-chapter-id') || el.getAttribute('data-id'),
                    name:name.slice(0,m.index).trim(),lectures:+m[1]} : {id:'',name:''};
      }));
      if (chapters.length) sync({kind:'chapters',batchId:context.batchId,subjectId:context.subjectId,items:chapters.slice(0,250)});
    }
    if (validId(context.subjectId) && validId(context.chapterId)) {
      const videos=unique([...document.querySelectorAll('[data-video-id]')].map(el=>({
        id:el.getAttribute('data-video-id'),
        title:text(el.innerText || el.textContent,220),
        contentId:el.getAttribute('data-type-id') || ''
      })).filter(x=>x.title));
      if (videos.length) sync({kind:'lectures',batchId:context.batchId,subjectId:context.subjectId,
        chapterId:context.chapterId,items:videos.map(v=>({videoId:v.id,contentId:v.contentId,title:v.title})).slice(0,250)});
    }
  }

  // The website itself issues these API requests with its existing authorization.
  // We only observe successful responses, and never retry 401s or inspect credentials.
  const contentsRE=/\/api\/v2\/batches\/([0-9a-f]{24})\/subject\/([0-9a-f]{24})\/contents/i;
  function captureResponse(url, data) {
    if (!data || data.success !== true || !Array.isArray(data.data)) return;
    let u;
    try { u=new URL(url, location.href); } catch (_) { return; }
    const m=u.pathname.match(contentsRE);
    if (!m) return;
    const tag=u.searchParams.get('tag');
    if (!validId(tag) || u.searchParams.get('contentType') !== 'videos') return;
    const batchId=context.batchId || m[1];
    const subjectId=m[2];
    context.subjectId=subjectId;
    context.chapterId=tag;
    saveContext();
    const items=data.data.filter(v=>v && v.isVideoLecture && v.videoDetails && validId(v.videoDetails._id)).map(v=>({
      videoId:v.videoDetails._id,
      contentId:v._id,
      title:text(v.topic || v.videoDetails.name,220),
      status:text(v.status,40),
      videoStatus:text(v.videoDetails.status,40),
      date:text(v.date,35),
      duration:text(v.videoDetails.duration,20)
    }));
    const chapterName=data.data.flatMap(v=>Array.isArray(v.tags)?v.tags:[]).find(t=>t._id===tag)?.name;
    if (chapterName) sync({kind:'chapters',batchId,subjectId,items:[{id:tag,name:text(chapterName),lectures:items.length}]});
    if (items.length) sync({kind:'lectures',batchId,subjectId,chapterId:tag,apiBatchId:m[1],items});
  }
  try {
    const original=w.fetch;
    if (typeof original==='function') {
      w.fetch=function(...args) {
        const result=original.apply(this,args);
        try {
          const url=typeof args[0]==='string' ? args[0] : args[0]?.url;
          if (url) relayStatusSuccess(url, result);
          if (url && contentsRE.test(new URL(url,location.href).pathname)) {
            Promise.resolve(result).then(response=>{
              if (response.ok) response.clone().json().then(data=>captureResponse(url,data)).catch(()=>{});
            }).catch(()=>{});
          }
        } catch (_) {}
        return result;
      };
    }
    const originalOpen=w.XMLHttpRequest.prototype.open;
    const originalSend=w.XMLHttpRequest.prototype.send;
    w.XMLHttpRequest.prototype.open=function(method,url,...args) {
      this.__catalogURL=String(url || '');
      return originalOpen.call(this,method,url,...args);
    };
    w.XMLHttpRequest.prototype.send=function(...args) {
      if (this.__catalogURL && contentsRE.test(this.__catalogURL)) {
        this.addEventListener('load',() => {
          if (this.status !== 200) return;
          try {
            const d=this.responseType==='json'?this.response:JSON.parse(this.responseText);
            captureResponse(this.__catalogURL,d);
          } catch (_) {}
        },{once:true});
      }
      return originalSend.apply(this,args);
    };
  } catch (e) { console.warn('[AutoCatalog] API observer failed',e); }

  document.addEventListener('click',captureClick,true);
  const init=()=>{
    scanDOM();
    const observer=new MutationObserver(scheduleScan);
    observer.observe(document.body,{childList:true,subtree:true});
    // Refresh DOM metadata while the user is already on an active page.
    setInterval(scanDOM,30000);
  };
  if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',init,{once:true});
  else init();

  // ---------------------------------------
  // Opt-in browser queue worker (no media code):
  // queue -> correct player -> existing authorised export button -> verified success.
  // ---------------------------------------
  const RUN_FLAG = 'vc_catalog_worker_in_this_tab';
  const TAB_ID = 'vc_catalog_worker_tab_id';
  const validJob = j => j && [j.batch_id,j.subject_id,j.chapter_id,j.video_id]
    .every(validId) && typeof j.title === 'string';
  const pause = ms => new Promise(resolve => setTimeout(resolve,ms));
  let workerBusy = false;
  let workerTimer = null;
  let workerUI = null;

  function tabId() {
    let id=sessionStorage.getItem(TAB_ID);
    if (!id) {
      id='tab_' + (crypto.randomUUID ? crypto.randomUUID().replace(/-/g,'') :
        Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join(''));
      sessionStorage.setItem(TAB_ID,id);
    }
    return id;
  }
  function running() { return sessionStorage.getItem(RUN_FLAG)==='1'; }
  function workerStatus(message) {
    if (!running() && !workerUI) return;
    if (!workerUI && document.body) {
      workerUI=document.createElement('div');
      workerUI.id='vc-catalog-worker-status';
      workerUI.style.cssText='position:fixed;bottom:12px;left:12px;z-index:2147483600;'+
        'background:#14263a;color:#fff;border-radius:8px;padding:8px 12px;'+
        'max-width:330px;font:12px system-ui;box-shadow:0 2px 12px #0006';
      document.body.appendChild(workerUI);
    }
    if (workerUI) workerUI.textContent='🤖 Queue worker: '+message;
    console.info('[AutoCatalogWorker]',message);
  }
  function workerCall(endpoint, data) {
    return new Promise((resolve,reject) => {
      const key=GM_getValue(keyId,'');
      if(key.length<24) return reject(new Error('Configure Catalog key in Tampermonkey'));
      GM_xmlhttpRequest({
        method:'POST',url:BRIDGE+'/api/catalog/worker/'+endpoint,
        headers:{'X-Catalog-Key':key,'Content-Type':'application/json'},
        data:JSON.stringify({browserId:tabId(),...data}),timeout:20000,
        onload:r => {
          let result={};
          try{result=JSON.parse(r.responseText||'{}');}catch(_){}
          if(r.status!==200) reject(new Error('Worker HTTP '+r.status+': '+(result.error||'request failed')));
          else resolve(result);
        },
        onerror:()=>reject(new Error('Railway worker unavailable')),
        ontimeout:()=>reject(new Error('Railway worker timed out'))
      });
    });
  }
  // A player opened from the site's own lecture card contains fields established
  // by the site (11 observed). Never guess, forge, replay, or collect security URLs.
  // Native card.click() lets the website perform its own access checks.
  const PLAYER_FIELDS = ['batch_id','program_id','subject_id','topic_id',
    'video_id','typeId','video_url','video_name','video_img','video_type','play_type'];
  let lastNavStep = '';
  let lastNavAt = 0;
  let searchStartedAt = 0;
  let nextTickDelay = 10000;
  const visible = el => !!el && el.getClientRects().length > 0 &&
    getComputedStyle(el).visibility !== 'hidden';
  const elementFor = (selector, attr, id, test = () => true) =>
    [...document.querySelectorAll(selector)].find(el => visible(el) &&
      el.getAttribute(attr) === id && test(el));

  function clickNative(el, action, job) {
    if (!el) return false;
    const step = job.video_id + '|' + action;
    if (lastNavStep === step && Date.now() - lastNavAt < 5000) {
      nextTickDelay = 1700;
      return true;
    }
    lastNavStep = step;
    lastNavAt = Date.now();
    workerStatus(action + ': ' + job.title.slice(0,34));
    nextTickDelay = 1700;
    el.click(); // invokes the website's real handler; no direct player URL
    return true;
  }

  function searchBatch(job) {
    // The site search UI can asynchronously render batch cards. We use its
    // regular input event, not undocumented API requests or auth headers.
    const input = document.querySelector('#globalSearchInput');
    const batchName = String(job.batch_name || '')
      .replace(/\s+NEW\b.*$/i,'').replace(/\s+Class\s+\d.*$/i,'').trim();
    if (!input || !batchName || /^Unlabelled batch /i.test(batchName)) return false;
    // A chapter title containing "9 Lectures | 10 Notes" is not a batch name.
    if (/\d+\s+(?:lectures?|notes?|exercises?)\s*[|•]/i.test(batchName) ||
        batchName === String(job.chapter_name || '').trim() ||
        batchName === String(job.subject_name || '').trim()) {
      workerStatus('Catalog batch label is invalid; fix the batch name before retry');
      nextTickDelay = 6000;
      return false;
    }
    const modal = document.querySelector('#globalSearchModal');
    if (modal && !modal.classList.contains('active')) {
      const opener = [...document.querySelectorAll('button,[role="button"]')]
        .find(b => visible(b) && /search/i.test(
          (b.getAttribute('aria-label') || '') + ' ' + (b.innerText || '') + ' ' + (b.id || '')));
      if (opener) return clickNative(opener,'Open website batch search',job);
      // Some site layouts keep the search input active without a separate button.
      if (!visible(input)) return false;
    }
    if (input.value !== batchName && Date.now() - searchStartedAt > 4000) {
      searchStartedAt = Date.now();
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype, 'value')?.set;
      if (setter) setter.call(input,batchName);
      else input.value = batchName;
      input.dispatchEvent(new Event('input',{bubbles:true}));
      workerStatus('Searching batch on website: ' + batchName.slice(0,50));
      nextTickDelay = 1800;
      return true;
    }
    return false;
  }

  function navigateByWebsite(job) {
    if (location.pathname !== '/index.php' && location.pathname !== '/') {
      if (location.pathname.startsWith('/verify-task/')) {
        sessionStorage.removeItem(RUN_FLAG);
        workerStatus('Website verification required; complete it manually, then resume');
        return true;
      }
      workerStatus('Returning to normal website course navigation');
      location.assign(new URL('/index.php',location.origin).href);
      return true;
    }
    // Start at the deepest currently visible course level, then walk backwards
    // through website UI. The lecture card alone can generate the valid URL.
    const video = elementFor('[data-video-id]','data-video-id',job.video_id);
    if (video) return clickNative(video,'Clicking authorised lecture card',job);

    const ch = elementFor('[data-topic-id],[data-chapter-id],[data-id]',
      'data-topic-id',job.chapter_id) ||
      elementFor('[data-topic-id],[data-chapter-id],[data-id]',
      'data-chapter-id',job.chapter_id) ||
      elementFor('[data-topic-id],[data-chapter-id],[data-id]',
      'data-id',job.chapter_id,
        e => /\d+\s*lectures?/i.test(e.innerText || ''));
    if (ch) return clickNative(ch,'Opening chapter via website',job);

    const subject = elementFor('[data-subject-id],[data-id]',
      'data-subject-id',job.subject_id) ||
      elementFor('[data-subject-id],[data-id]',
      'data-id',job.subject_id,
        e => /\d+\s*chapters?/i.test(e.innerText || ''));
    if (subject) return clickNative(subject,'Opening subject via website',job);

    const batch = elementFor('#globalSearchResults [data-batch-id]',
      'data-batch-id',job.batch_id);
    if (batch) return clickNative(batch,'Opening batch via website',job);

    // Do not type a chapter's label into the global batch-search modal when
    // already looking at subjects, chapters or lectures. This is a state
    // mismatch, not evidence that a different batch should be searched.
    const courseView = [...document.querySelectorAll(
      '#mainContent [data-video-id], #mainContent [data-subject-id], ' +
      '#mainContent [data-topic-id], #mainContent [data-chapter-id], ' +
      '#mainContent [data-id]'
    )].some(el => visible(el) && (
      el.hasAttribute('data-video-id') || el.hasAttribute('data-subject-id') ||
      el.hasAttribute('data-topic-id') || el.hasAttribute('data-chapter-id') ||
      /\d+\s+(?:lectures?|chapters?)\b/i.test(el.innerText || '')
    ));
    if (courseView) {
      workerStatus('Course view open but target card missing; not using global batch search');
      nextTickDelay = 5000;
      return false;
    }

    if (searchBatch(job)) return true;
    workerStatus('Waiting for normal website batch/subject UI; open batch search if needed');
    nextTickDelay = 3000;
    return false;
  }

  function hasWebsitePlayerContext() {
    const p = new URLSearchParams(location.search);
    return PLAYER_FIELDS.every(k => p.has(k));
  }
  function hasHlsManifest() {
    return performance.getEntriesByType('resource').some(r=> {
      try { return /\.(?:docx|m3u8)$/i.test(new URL(r.name).pathname); }
      catch(_) { return false; }
    });
  }
  async function completeJob(job,status,error='') {
    // A successful result is acknowledged only after the independent existing
    // uploader shows its real post-Telegram status: "✅ Sent to Telegram".
    const result=await workerCall('finish',{videoId:job.video_id,outcome:status,error:String(error).slice(0,220)});
    if (result.state!=='delivered' && result.state!=='failed')
      throw new Error('Worker finish not acknowledged: '+result.state);
    workerStatus(`${status}: ${job.title.slice(0,35)}. Next...`);
    await pause(1800);
    // Prefer browser Back: normal website navigation may restore the already-open
    // chapter, which keeps subsequent lecture-card clicks fast.
    if (document.referrer && new URL(document.referrer).origin===location.origin &&
        new URL(document.referrer).pathname==='/index.php' && history.length>1) {
      history.back();
    } else {
      location.assign(new URL('/index.php',location.origin).href);
    }
  }
  async function performJob(job) {
    let heartbeat;
    let leaseActive=true;
    let clicked=false;
    try {
      const initial=await workerCall('heartbeat',{videoId:job.video_id});
      if(!initial.active) throw new Error('Assignment expired; wait for next claim');
      heartbeat=setInterval(()=>workerCall('heartbeat',{videoId:job.video_id}).then(r=>{
        if(!r.active) leaseActive=false;
      }).catch(err=>console.warn('[AutoCatalogWorker] heartbeat:',err.message)),25000);
      workerStatus('Opening '+job.title.slice(0,48));
      const deadline=Date.now()+4*60*1000;
      let button=null;
      let playAttemptAt=0;
      while(Date.now()<deadline) {
        if(!leaseActive) throw new Error('Worker assignment lost');
        if(location.pathname!=='/play.php' ||
           new URLSearchParams(location.search).get('video_id')!==job.video_id)
          throw new Error('Wrong player lecture');
        const video=document.querySelector('video');
        if(video && video.paused && Date.now()-playAttemptAt>10000) {
          playAttemptAt=Date.now();
          // Muted HTML5 playback is normally allowed by browser autoplay policy.
          // This does not access encrypted frames or obtain a DRM licence.
          video.muted=true;
          video.play().catch(()=>{});
        }
        button=document.querySelector('#railway-lecture-link-v1 button');
        if(button && !button.disabled && hasHlsManifest()) break;
        workerStatus('Waiting for existing uploader + player playlist...');
        await pause(2500);
      }
      if(!button || !hasHlsManifest())
        throw new Error('Existing uploader or supported HLS playlist not available (autoplay may be blocked)');
      // Do not double-send after a transient catalog confirmation error.
      const existing=(document.querySelector('#railway-lecture-link-v1 .relay-message')?.textContent||'').trim();
      if(existing.startsWith('✅ Sent to Telegram:')) {
        await completeJob(job,'delivered');
        return;
      }
      if(existing.startsWith('Failed:')) {
        await completeJob(job,'failed',existing);
        return;
      }
      button.click();
      clicked=true;
      workerStatus('Uploader started for '+job.title.slice(0,45));
      const uploadDeadline=Date.now()+3*60*60*1000;
      let lastText='';
      while(Date.now()<uploadDeadline) {
        if(!leaseActive) throw new Error('Worker assignment lost during upload');
        const info=document.querySelector('#railway-lecture-link-v1 .relay-message');
        const txt=(info?.textContent||'').trim();
        if(txt.startsWith('✅ Sent to Telegram:')) {
          await completeJob(job,'delivered');
          return;
        }
        if(txt.startsWith('Failed:')) {
          await completeJob(job,'failed',txt);
          return;
        }
        if(txt && txt!==lastText) {
          lastText=txt;
          workerStatus(txt.slice(0,110));
        }
        await pause(2500);
      }
      throw new Error('Upload timeout after 3 hours');
    } catch(err) {
      workerStatus('Error: '+String(err.message||err).slice(0,95));
      console.error('[AutoCatalogWorker]',err);
      if(!clicked) {
        try { await completeJob(job,'failed',err.message||String(err)); }
        catch(e) { console.warn('[AutoCatalogWorker] finish failed:',e.message); }
      } else {
        // Stop to prevent accidentally sending the same video twice.
        sessionStorage.removeItem(RUN_FLAG);
        workerStatus('Upload state uncertain. Check Telegram/Railway before retry.');
      }
    } finally {
      if(heartbeat) clearInterval(heartbeat);
    }
  }
  async function workerTick() {
    if(!running() || workerBusy) return;
    workerBusy=true;
    try {
      const result=await workerCall('next',{});
      if(result.state==='assigned') {
        const job=result.lecture;
        if(!validJob(job)) throw new Error('Bad lecture metadata');
        const onCorrectPlayer=location.pathname==='/play.php' &&
          new URLSearchParams(location.search).get('video_id')===job.video_id;
        if (!onCorrectPlayer || !hasWebsitePlayerContext()) {
          // Crucial: incomplete 5-parameter URLs trigger "Security fields missing".
          // Never fabricate the remaining fields; navigate via the site's cards.
          navigateByWebsite(job);
          return;
        }
        await performJob(job);
        return;
      }
      workerStatus(result.state==='paused'?'Paused by Telegram /autostop':
        result.state==='empty'?'Queue empty; waiting for /done':
        result.state==='busy'?'Another worker tab busy':'Waiting');
    } catch(err) {
      workerStatus('Error: '+String(err.message||err).slice(0,95));
      console.warn('[AutoCatalogWorker]',err);
    } finally {
      workerBusy=false;
      if(running()) {
        workerTimer=setTimeout(workerTick,nextTickDelay);
        nextTickDelay=10000;
      }
    }
  }
  window.addEventListener('pageshow',e=> {
    if (e.persisted && running()) {
      clearTimeout(workerTimer);
      workerTimer=setTimeout(workerTick,1500);
    }
  });
  GM_registerMenuCommand('▶ Start Telegram queue worker IN THIS TAB',()=>{
    if(GM_getValue(keyId,'').length<24) {
      alert('First Configure catalog sync key in Tampermonkey.');return;
    }
    sessionStorage.setItem(RUN_FLAG,'1');
    tabId();
    workerStatus('Enabled in this tab; waiting for Telegram /done');
    clearTimeout(workerTimer);
    workerTimer=setTimeout(workerTick,100);
  });
  GM_registerMenuCommand('⏸ Stop worker AFTER current upload',()=>{
    sessionStorage.removeItem(RUN_FLAG);
    clearTimeout(workerTimer);
    workerStatus('Stopping after any current upload');
    alert('Auto worker disabled in this Chrome tab. Current transfer may finish.');
  });
  if(running()) {
    const resume=()=>{ workerStatus('Resuming queue worker'); workerTimer=setTimeout(workerTick,1500); };
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',resume,{once:true});
    else resume();
  }

})();
