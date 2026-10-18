// ==UserScript==
// @name         Lecture Auto Send to Telegram
// @namespace    lecture-bridge
// @version      2.2.0
// @description  Upload authorized lecture to Railway, convert to MP4 and send automatically via Telegram bot.
// @match        https://vidcloud.eu.org/play.php*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/*
 * Chrome DevTools > Sources > Snippets > Ctrl+Enter
 * Browser -> Railway -> MP4 -> direct Telegram delivery (no copied URL).
 * Optimized: up to 3 concurrent browser fetches and ordered upload. No DRM bypass.
 * Only for media the user is allowed to export. No DRM/signature bypass.
 * Existing signed HLS links must already work in the page via normal CORS fetch.
 */
(() => {
  'use strict';
  let RELAY = 'https://ubuntu-production-9d09.up.railway.app'; // Your public bridge domain
  const WIDGET_ID = 'railway-lecture-link-v1';
  if (document.getElementById(WIDGET_ID)) return;

  const css = document.createElement('style');
  css.textContent = `
    #${WIDGET_ID} { position:fixed!important;z-index:2147483647!important;
      top:90px!important;right:15px!important;display:flex!important;
      flex-direction:column!important;align-items:flex-end!important;gap:7px!important;
      max-width:min(370px,90vw);font:13px system-ui,Arial,sans-serif!important; }
    #${WIDGET_ID} button { display:block!important;background:#1769e0!important;
      color:#fff!important;font-size:14px!important;border:0!important;border-radius:9px!important;
      padding:10px 14px!important;cursor:pointer!important;box-shadow:0 3px 10px #0005!important; }
    #${WIDGET_ID} button:disabled { opacity:.65!important;cursor:wait!important; }
    #${WIDGET_ID} .relay-message { background:#122139!important;color:white!important;
      border-radius:8px!important;padding:9px!important;overflow-wrap:anywhere!important;
      max-width:370px!important; }
    #${WIDGET_ID} textarea { width:340px!important;max-width:85vw!important;height:65px!important;
      padding:7px!important;background:white!important;color:#111!important;border-radius:5px!important; }
  `;

  const root = document.createElement('div');
  root.id = WIDGET_ID;
  const button = document.createElement('button');
  button.textContent = '🔗 📤 Send MP4 to Telegram';
  const status = document.createElement('div');
  status.className = 'relay-message';
  status.hidden = true;
  root.append(button, status);
  (document.head || document.documentElement).appendChild(css);
  document.body.appendChild(root);

  const say = (message) => { status.hidden = false; status.textContent = message; };
  const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const isCandidate = (text) => {
    try {
      const u = new URL(text);
      return ['http:', 'https:'].includes(u.protocol)
        && /\.(?:docx|m3u8)(?:$)/i.test(u.pathname)
        && (u.hostname === 'bunny-cdn-qbg-s6.testwave.cc' || u.origin === location.origin);
    } catch { return false; }
  };

  async function findManifest() {
    const candidates = [...new Set(performance.getEntriesByType('resource')
      .map(x => x.name).filter(isCandidate))].reverse();
    if (!candidates.length) throw new Error('Playlist not found. Play lecture, then click again.');
    let reason = '';
    for (const url of candidates) {
      try {
        const response = await fetch(url, {mode: 'cors', credentials: 'same-origin'});
        if (!response.ok) { reason = `Playlist HTTP ${response.status}`; continue; }
        const body = await response.text();
        if (body.trimStart().startsWith('#EXTM3U')) return {url, body};
      } catch (e) { reason = e.message; }
    }
    throw new Error(reason || 'No usable HLS manifest found');
  }

  async function mediaSegments(url, body, nesting = 0) {
    if (nesting > 3) throw new Error('Too many nested HLS playlists');
    const lines = body.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
    if (!lines[0].startsWith('#EXTM3U')) throw new Error('Not HLS playlist');
    if (lines.some(x => /^#EXT-X-(?:SESSION-)?KEY\s*:/i.test(x)
           && !/\bMETHOD\s*=\s*NONE\b/i.test(x))) {
      throw new Error('Encrypted HLS unsupported; use an official export');
    }
    if (lines.some(x => /^#EXT-X-(MAP|BYTERANGE)\s*:/i.test(x))) {
      throw new Error('fMP4 or byte-range stream unsupported by this uploader');
    }
    const variantAt = lines.findIndex(x => x.startsWith('#EXT-X-STREAM-INF:'));
    if (variantAt !== -1) {
      const next = lines.slice(variantAt + 1).find(x => !x.startsWith('#'));
      if (!next) throw new Error('No variant URI');
      const nextURL = new URL(next, url).href;
      const r = await fetch(nextURL, {mode:'cors', credentials:'same-origin'});
      if (!r.ok) throw new Error(`Variant HTTP ${r.status}`);
      return mediaSegments(nextURL, await r.text(), nesting + 1);
    }
    if (!lines.includes('#EXT-X-ENDLIST')) throw new Error('Live HLS cannot be exported as complete MP4');
    const segments = lines.filter(x => !x.startsWith('#')).map(x => new URL(x, url).href);
    if (!segments.length) throw new Error('No video segments found');
    return segments;
  }

  async function api(path, secret, data) {
    const options = {
      method: 'POST',
      headers: {...(secret ? {'X-Relay-Key': secret} : {}), 'Content-Type': data instanceof ArrayBuffer
        ? 'application/octet-stream' : 'application/json'},
      body: data instanceof ArrayBuffer ? data : JSON.stringify(data)
    };
    let response;
    try { response = await fetch(RELAY + path, options); }
    catch (e) { throw new Error('Railway API unreachable (deploy, CORS, or website CSP): ' + e.message); }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(`Railway ${response.status}: ${body.error || 'API failed'}`);
    return body;
  }

  async function poll(jobId, key) {
    for (let attempt = 0; attempt < 540; attempt++) {
      await wait(2000);
      const r = await fetch(`${RELAY}/api/relay/${encodeURIComponent(jobId)}/status`, {
        headers: key ? {'X-Relay-Key': key} : {}
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.error || `Status HTTP ${r.status}`);
      if (body.status === 'sent') return body;
      if (body.status === 'failed') throw new Error(body.error || 'Railway conversion / Telegram send failed');
      if (body.status === 'telegram_sending') {
        const sent = body.telegram_uploaded || 0;
        const total = body.telegram_total || 0;
        const pct = total ? Math.round(sent / total * 100) : 0;
        say(`Telegram upload ${pct}% • ${(sent / 1048576).toFixed(1)} / ${(total / 1048576).toFixed(1)} MiB. Telegram chat mein bhi live progress dekho.`);
      }
      else if (attempt % 5 === 0) say('Railway converting media to MP4…');
    }
    throw new Error('Timed out waiting for MP4 conversion');
  }

  button.addEventListener('click', async () => {
    const key = ''; // No-key mode. Server must set RELAY_REQUIRE_UPLOAD_KEY=0.
    button.disabled = true;
    let uploadJobId = null;
    let transferFinished = false;
    try {
      say('Finding authorized HLS playlist…');
      const {url, body} = await findManifest();
      const segments = await mediaSegments(url, body);
      const title = (new URLSearchParams(location.search).get('video_name') ||
        document.querySelector('h1')?.textContent || document.title || 'Lecture').trim().slice(0, 140);
      say(`Found ${segments.length} segments for ${title}. Starting Railway upload…`);
      const job = await api('/api/relay/start', key, {segments: segments.length, title});
      uploadJobId = job.job_id;
      // Pipeline: fetch up to 3 authorized segments concurrently, upload to Railway
      // in HLS playlist order (the Railway backend requires index 0,1,2,...).
      // This overlaps CDN fetching with upload. It does not bypass CDN authorization.
      const PREFETCH = 3;
      const pending = new Map();
      const began = performance.now();
      let bytes = 0;

      const schedule = (index) => {
        if (index >= segments.length || pending.has(index)) return;
        // Always handle early failures to avoid unhandled promise rejections.
        const p = fetch(segments[index], {mode: 'cors', credentials: 'same-origin'})
          .then(async response => {
            if (!response.ok) throw new Error(`Video segment ${index + 1} HTTP ${response.status}`);
            return response.arrayBuffer();
          })
          .then(data => ({ok: true, data}), error => ({ok: false, error}));
        pending.set(index, p);
      };

      for (let i = 0; i < Math.min(PREFETCH, segments.length); i++) schedule(i);
      for (let i = 0; i < segments.length; i++) {
        const result = await pending.get(i);
        pending.delete(i);
        if (!result.ok) throw result.error;
        const data = result.data;
        if (i === 0) {
          const x = new Uint8Array(data);
          if (x.length < 189 || x[0] !== 0x47 || x[188] !== 0x47) {
            throw new Error('Not MPEG-TS segments; other container formats unsupported');
          }
        }
        // Start fetching a future segment while we upload this one.
        schedule(i + PREFETCH);
        await api(`/api/relay/${encodeURIComponent(job.job_id)}/segment/${i}`, key, data);
        bytes += data.byteLength;
        const secs = Math.max((performance.now() - began) / 1000, 1);
        const rate = (bytes / 1048576 / secs).toFixed(2);
        say(`Upload ${i + 1}/${segments.length} • ${(bytes/1048576).toFixed(1)} MiB • ${rate} MiB/s. Do not close tab.`);
      }
      await api(`/api/relay/${encodeURIComponent(job.job_id)}/finish`, key, {});
      transferFinished = true;
      say('All segments uploaded. Railway converting to MP4…');
      const result = await poll(job.job_id, key);
      say(`✅ Sent to Telegram: ${result.title}.mp4 (check your bot chat)`);
    } catch (e) {
      // Free the server job slot on a failed browser upload.
      // Never cancel once conversion or Telegram delivery has started.
      if (uploadJobId && !transferFinished) {
        try {
          await api(`/api/relay/${encodeURIComponent(uploadJobId)}/cancel`, key, {});
        } catch (cancelErr) {
          console.warn('[Railway upload cleanup]', cancelErr);
        }
      }
      say(`Failed: ${e.message || e}`);
      console.error('[Railway MP4 link]', e);
    } finally {
      button.disabled = false;
    }
  });

  console.info('Auto Telegram lecture button added.');
})();
