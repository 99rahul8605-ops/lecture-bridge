// ==UserScript==
// @name         VidCloud Auto Catalog Sync (metadata only)
// @namespace    lecture-bridge/catalog
// @version      1.1.0
// @description  Sync lecture metadata; mark queued items delivered when the EXISTING browser uploader confirms Telegram success. No media download or DRM changes.
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
  function captureClick(e) {
    const batch = nearestWithId(e.target,['data-batch-id']);
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
    const batches=unique([...document.querySelectorAll('[data-batch-id]')].map(el=>({
      id:el.getAttribute('data-batch-id'),
      name:text(el.innerText || el.textContent)
    })).filter(x => x.name.length>3));
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
})();
