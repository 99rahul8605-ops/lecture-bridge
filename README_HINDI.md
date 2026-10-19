# Lecture Segment Retry v2.6 — Railway / Ubuntu VPS

This patch is for the existing **one-click Send MP4 to Telegram** relay. It does not perform DRM decryption or bypass website/CDN authorization. Use it only for media you are authorized to export.

## What is fixed

- One stuck CDN segment GET or browser→bridge POST is no longer allowed to wait forever: **8-minute timeout per attempt**.
- Transient network errors, HTTP 408/425/429/5xx: **up to 3 retries**, wait 2.5 s, 7.5 s, 20 s.
- **No retry** on HTTP 401/403/413, because those require fixing authorization/size instead.
- Railway receives a safe idempotent ACK if an earlier segment POST was committed but the response was lost. The same segment is **not appended twice**.
- 1, 2, 3 lane selections remain; **default 1** for stability. MP4 conversion and Telegram upload unchanged.
- The browser shows retries in the progress panel. Download/upload averages still reflect completion, not real-time byte counters.

## Install: currently active Railway server

**Do not install/reload while a video upload is active.** A stuck old upload cannot resume automatically after a page reload. If it is irrecoverably pending, reload the player to stop browser work, and let stale job cleanup release the previous server job slot before starting the new test.

1. Extract ZIP and upload these two files to **the same GitHub `lecture-bridge` repository**:
   - `Railway_Auto_Telegram.user.js`
   - `install_retry_backend.py`
2. On Railway Ubuntu terminal:

```bash
cd /lecture-bridge
git pull --ff-only
python3 install_retry_backend.py relay_api.py
python3 -m py_compile relay_api.py
ps -eo pid,ppid,args | grep '[g]unicorn.*8090'
```

3. Identify the Gunicorn **master PID**: it is the process whose parent is SCREEN and which has a child Gunicorn worker. After ongoing transfers are finished, reload only that master:

```bash
kill -HUP YOUR_MASTER_PID
sleep 3
curl -sS http://127.0.0.1:8090/health
```

   **Do not** use `kill -9` or `fuser -k` just to reload code. The installer makes a timestamped `relay_api.py.retry-backup-*` file.

4. In Chrome Tampermonkey **replace the current** `Lecture Auto Send to Telegram` userscript with this v2.6 file (disable any duplicates). Its default domain is `https://ubuntu-production-9d09.up.railway.app`; edit only if using another server. Save, reload lecture player.
5. Start **one new upload with 1 lane**. Watch for `Retrying CDN segment #` or `Retrying VPS upload segment #`. If this succeeds, later try 2 lanes if Railway has parallel-capable relay_api.py.

## If you are using VPS instead

Same install steps in `~/lecture-bridge`, but set `RELAY` in the userscript to your HTTPS VPS hostname and reload the VPS Gunicorn master.

## Limits

- A new script version cannot attach to/continue a stuck upload already running in the old script.
- The server must be patched too for safe retries after **lost upload acknowledgements**. Without the server patch, duplicate 409 errors may still fail.
- Retrying does not fix blocked authorization, unavailable CDN links, a broken Cloudflare tunnel, or insufficient upstream bandwidth.
- Python and JavaScript syntax and offline mock scenarios are tested; no live authenticated site / Railway / Telegram end-to-end test was run here.
