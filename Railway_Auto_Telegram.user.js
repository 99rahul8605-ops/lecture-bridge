// ==UserScript==
// @name         Lecture Auto Send to Telegram
// @namespace    lecture-bridge
// @version      2.4.0
// @description  Upload authorized lecture to Railway, convert to MP4 and send automatically via Telegram bot.
// @match        https://vidcloud.eu.org/play.php*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/*
 * Chrome DevTools > Sources > Snippets > Ctrl+Enter
 * Browser -> Railway -> MP4 -> direct Telegram delivery (no copied URL).
 * 1/2/3 parallel browser-to-VPS lanes; VPS commits original HLS segment order. No DRM bypass.
 * Only for media the user is allowed to export. No DRM/signature bypass.
 * Existing signed HLS links must already work in the page via normal CORS fetch.
 */
(() => {
  'use strict';
  let RELAY = 'https://upload.bingolink.site'; // Ubuntu VPS Cloudflare Tunnel
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
    #${WIDGET_ID} select { font:13px system-ui!important; border-radius:7px!important;
      padding:6px 10px!important; color:#111!important; background:#fff!important; }
  `;

  const root = document.createElement('div');
  root.id = WIDGET_ID;
  const button = document.createElement('button');
  button.textContent = '🔗 📤 Send MP4 to Telegram';
  const lanes = document.createElement('select');
  lanes.title = 'Parallel upload lanes';
  for (const n of [1, 2, 3]) {
    const opt = document.createElement('option');
    opt.value = String(n);
    opt.textContent = `${n} upload lane${n === 1 ? '' : 's'}`;
    lanes.appendChild(opt);
  }
  lanes.value = '2'; // Default: test 2 vs old 1; 3 is optional.
  const status = document.createElement('div');
  status.className = 'relay-message';
  status.hidden = true;
  root.append(lanes, button, status);
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
    lanes.disabled = true;
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
      // Bounded parallel pipeline. Each lane fetches and posts one segment at a time.
      // Server accepts out-of-order indexes, but commits input.ts strictly 0,1,2,...
      const CONCURRENCY = Math.max(1, Math.min(3, Number(lanes.value) || 2));
      const began = performance.now();
      let nextIndex = 0;
      let completed = 0;
      let bytes = 0;
      let failure = null;
      say(`${CONCURRENCY} upload lane(s) running • ${segments.length} segments`);
      async function transferLane() {
        while (!failure) {
          const i = nextIndex++;
          if (i >= segments.length) return;
          try {
            const response = await fetch(segments[i], {mode: 'cors', credentials: 'same-origin'});
            if (!response.ok) throw new Error(`Video segment ${i + 1} HTTP ${response.status}`);
            const data = await response.arrayBuffer();
            if (i === 0) {
              const x = new Uint8Array(data);
              if (x.length < 189 || x[0] !== 0x47 || x[188] !== 0x47)
                throw new Error('Not MPEG-TS segments; encrypted/fMP4 formats unsupported');
            }
            await api(`/api/relay/${encodeURIComponent(job.job_id)}/segment/${i}`, key, data);
            completed++;
            bytes += data.byteLength;
            const secs = Math.max((performance.now() - began) / 1000, 1);
            const rate = (bytes / 1048576 / secs).toFixed(2);
            say(`${CONCURRENCY} lanes • Upload ${completed}/${segments.length} • ${(bytes/1048576).toFixed(1)} MiB • ${rate} MiB/s. Keep tab open.`);
          } catch (error) {
            failure ||= error;
            return;
          }
        }
      }
      // Wait for all current transfers to settle before finish or cancel.
      await Promise.all(Array.from({length: Math.min(CONCURRENCY, segments.length)}, transferLane));
      if (failure) throw failure;
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
      lanes.disabled = false;
    }
  });

  console.info('Auto Telegram lecture button added.');
})();
