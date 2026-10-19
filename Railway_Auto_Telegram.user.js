// ==UserScript==
// @name         Lecture Auto Send to Telegram
// @namespace    lecture-bridge
// @version      2.7.0-background-next
// @description  Send one authorized lecture to relay, then immediately free browser for the next while Telegram sends in background.
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
  let RELAY = 'https://ubuntu-production-9d09.up.railway.app'; // Railway bridge (edit only if using another host)
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
    #${WIDGET_ID} .relay-stats { background:#0c2337!important;color:#d7f5ff!important;
      border-radius:8px!important;padding:10px 12px!important;box-shadow:0 3px 12px #0007!important;
      font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace!important;
      white-space:pre-line!important;text-align:left!important;max-width:min(420px,94vw)!important;
      font-variant-numeric:tabular-nums!important;overflow-wrap:anywhere!important; }
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
  lanes.value = '1'; // Stable default; switch to 2/3 only with parallel-capable server.
  const status = document.createElement('div');
  status.className = 'relay-message';
  status.hidden = true;
  const stats = document.createElement('div');
  stats.className = 'relay-stats';
  stats.hidden = true;
  root.append(lanes, button, status, stats);
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

  const RETRY_BACKOFF_MS = [2500, 7500, 20000]; // three retries, four total attempts
  const CDN_TIMEOUT_MS = 8 * 60 * 1000;
  const UPLOAD_TIMEOUT_MS = 8 * 60 * 1000;

  function shouldRetry(error) {
    if (error && error.transient) return true;
    const code = Number(error && error.httpStatus);
    return [408, 425, 429, 500, 502, 503, 504].includes(code) ||
      (code === 409 && /still in progress|duplicate segment/i.test(error.message || ''));
  }

  async function retrySegment(label, doAttempt, onRetry) {
    for (let attempt = 0; ; attempt++) {
      try { return await doAttempt(); }
      catch (error) {
        if (attempt >= RETRY_BACKOFF_MS.length || !shouldRetry(error)) throw error;
        onRetry(attempt + 1, RETRY_BACKOFF_MS.length, error);
        await wait(RETRY_BACKOFF_MS[attempt]);
      }
    }
  }

  async function fetchSegment(segmentUrl, index) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), CDN_TIMEOUT_MS);
    try {
      let res;
      try { res = await fetch(segmentUrl, {mode:'cors', credentials:'same-origin', signal:controller.signal}); }
      catch (error) {
        const err = new Error(`CDN segment ${index + 1} fetch failed: ${error.message}`);
        err.transient = true;
        throw err;
      }
      if (!res.ok) {
        const err = new Error(`CDN segment ${index + 1} HTTP ${res.status}`);
        err.httpStatus = res.status;
        throw err;
      }
      try { return await res.arrayBuffer(); }
      catch (error) {
        const err = new Error(`CDN segment ${index + 1} body failed: ${error.message}`);
        err.transient = true;
        throw err;
      }
    } finally { clearTimeout(timeout); }
  }

  async function api(path, secret, data, timeoutMs = 120000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const options = {
      method: 'POST', signal: controller.signal,
      headers: {...(secret ? {'X-Relay-Key': secret} : {}), 'Content-Type': data instanceof ArrayBuffer
        ? 'application/octet-stream' : 'application/json'},
      body: data instanceof ArrayBuffer ? data : JSON.stringify(data)
    };
    let response;
    try {
      try { response = await fetch(RELAY + path, options); }
      catch (error) {
        const err = new Error(`Relay request ${path.includes('/segment/') ? 'segment' : 'API'} failed: ${error.message}`);
        err.transient = true;
        throw err;
      }
      let body;
      try { body = await response.json(); } catch (_) { body = {}; }
      if (!response.ok) {
        const err = new Error(`Relay HTTP ${response.status}: ${body.error || 'API failed'}`);
        err.httpStatus = response.status;
        throw err;
      }
      return body;
    } finally { clearTimeout(timer); }
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
    let statsTimer = null;
    stats.hidden = true;
    stats.textContent = '';
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
      let downloaded = 0, uploaded = 0;
      let downloadBytes = 0, uploadBytes = 0;
      let activeDownloads = 0, activeUploads = 0, peakDownloads = 0;
      let failure = null;
      const laneInfo = Array.from({length: CONCURRENCY}, () => ({phase: 'idle', index: -1, downloadRate: null, uploadRate: null}));
      const mib = bytes => (bytes / 1048576).toFixed(1);
      const rate = (bytes, ms) => (bytes / 1048576 / Math.max(ms / 1000, 0.001)).toFixed(2);
      // These are averages over elapsed wall time; network bytes still in flight
      // cannot be measured with fetch().arrayBuffer() until a segment finishes.
      function renderStats() {
        const elapsedMs = Math.max(performance.now() - began, 1000);
        const perLane = laneInfo.map((lane, laneIndex) => {
          const current = lane.index < 0 ? '' : ` #${lane.index + 1}`;
          const lastDL = lane.downloadRate == null ? '—' : `${lane.downloadRate} MiB/s`;
          const lastUL = lane.uploadRate == null ? '—' : `${lane.uploadRate} MiB/s`;
          return `L${laneIndex + 1} ${lane.phase}${current} | last ↓${lastDL} ↑${lastUL}`;
        });
        stats.textContent = [
          `⬇ CDN download: ${downloaded}/${segments.length} | ${mib(downloadBytes)} MiB | avg ${rate(downloadBytes, elapsedMs)} MiB/s`,
          `⬆ VPS upload:   ${uploaded}/${segments.length} | ${mib(uploadBytes)} MiB | avg ${rate(uploadBytes, elapsedMs)} MiB/s`,
          `Active now: ↓${activeDownloads} download(s) / ↑${activeUploads} upload(s) | peak ↓${peakDownloads}`,
          ...perLane,
          'Note: speeds update when each segment completes.'
        ].join('\n');
      }
      stats.hidden = false;
      renderStats();
      // Update the active-lane indicator even if a segment takes several seconds.
      statsTimer = setInterval(renderStats, 800);
      say(`${CONCURRENCY} upload lane(s) running • ${segments.length} segments`);
      async function transferLane(laneIndex) {
        const lane = laneInfo[laneIndex];
        while (!failure) {
          const i = nextIndex++;
          if (i >= segments.length) { lane.phase = 'done'; renderStats(); return; }
          lane.index = i;
          lane.phase = 'downloading';
          activeDownloads++;
          peakDownloads = Math.max(peakDownloads, activeDownloads);
          renderStats();
          let data;
          const downloadBegan = performance.now();
          try {
            try {
              data = await retrySegment(`CDN segment ${i + 1}`,
                () => fetchSegment(segments[i], i),
                (attempt, limit, error) => {
                  lane.phase = `retry CDN ${attempt}/${limit}`;
                  say(`Retrying CDN segment ${i + 1} (${attempt}/${limit}): ${error.message}`);
                  renderStats();
                });
              if (i === 0) {
                const x = new Uint8Array(data);
                if (x.length < 189 || x[0] !== 0x47 || x[188] !== 0x47)
                  throw new Error('Not MPEG-TS segments; encrypted/fMP4 formats unsupported');
              }
              downloaded++;
              downloadBytes += data.byteLength;
              lane.downloadRate = rate(data.byteLength, performance.now() - downloadBegan);
            } finally {
              activeDownloads--;
              renderStats();
            }
            lane.phase = 'uploading';
            activeUploads++;
            renderStats();
            const uploadBegan = performance.now();
            try {
              await retrySegment(`Railway segment ${i + 1}`,
                () => api(`/api/relay/${encodeURIComponent(job.job_id)}/segment/${i}`, key, data, UPLOAD_TIMEOUT_MS),
                (attempt, limit, error) => {
                  lane.phase = `retry upload ${attempt}/${limit}`;
                  say(`Retrying VPS upload segment ${i + 1} (${attempt}/${limit}): ${error.message}`);
                  renderStats();
                });
              uploaded++;
              uploadBytes += data.byteLength;
              lane.uploadRate = rate(data.byteLength, performance.now() - uploadBegan);
            } finally {
              activeUploads--;
              renderStats();
            }
            lane.phase = 'saved';
            renderStats();
            say(`${CONCURRENCY} lanes • Uploaded ${uploaded}/${segments.length} • ${mib(uploadBytes)} MiB. Download/upload stats below.`);
          } catch (error) {
            lane.phase = 'failed';
            renderStats();
            failure ||= error;
            return;
          }
        }
      }
      // Wait for all current transfers to settle before finish or cancel.
      await Promise.all(Array.from({length: Math.min(CONCURRENCY, segments.length)}, (_, i) => transferLane(i)));
      if (statsTimer !== null) { clearInterval(statsTimer); statsTimer = null; }
      renderStats();
      if (failure) throw failure;
      await api(`/api/relay/${encodeURIComponent(job.job_id)}/finish`, key, {});
      transferFinished = true;
      // /finish returns only when the server has accepted all segments.
      // Conversion and Telegram delivery continue in the relay worker after
      // the browser navigates away. The Telegram group gets live progress.
      say(`✅ ${title}: all ${segments.length} segments safely uploaded. ` +
          'MP4 conversion + Telegram sending is running in background. ' +
          'You can open the NEXT lecture now; watch Telegram group for final delivery.');
      console.info('[Lecture bridge] background job accepted:', job.job_id);
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
      if (statsTimer !== null) clearInterval(statsTimer);
      button.disabled = false;
      lanes.disabled = false;
    }
  });

  console.info('Auto Telegram lecture button added.');
})();
