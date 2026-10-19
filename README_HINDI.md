# 2/3 Parallel Segment Upload Test — Ubuntu VPS

Existing **one-click Send MP4 to Telegram** uploader ka speed test. No automatic batch selection, no new Telegram bot. Website player normal tarike se kholo. This is only for media you are authorized to export, and the script still refuses encrypted HLS/fMP4 unsupported streams.

## Files

- `relay_api.py`: same existing conversion + group progress, but accepts up to 3 out-of-order segment uploads; MPEG-TS assembled **original segment index 0,1,2...** using a bounded memory/disk buffer.
- `Railway_Auto_Telegram.user.js`: `https://upload.bingolink.site` preset, dropdown with **1 / 2 / 3 upload lanes** (default **2**); old one-click button preserved.
- `enable_parallel.sh`: configures VPS to allow 3 concurrent segment requests and restarts only port 8090's Gunicorn master (loads new .env). No other bots or Cloudflare Tunnel changes.
- `test_parallel_server.py` and `test_parallel_browser.cjs`: offline tests.

## Install

1. Stop/wait for any **ongoing video upload or Telegram send**. In the GitHub `lecture-bridge` repository, replace `relay_api.py`, `Railway_Auto_Telegram.user.js` and add `enable_parallel.sh`, `test_parallel_server.py` and (optionally) `test_parallel_browser.cjs`.
2. In Ubuntu VPS terminal:

```bash
cd ~/lecture-bridge
git pull --ff-only
bash enable_parallel.sh
curl -sS https://upload.bingolink.site/health
```

3. In Chrome Tampermonkey replace the **existing one-click uploader** with the new `Railway_Auto_Telegram.user.js`. Keep other auto-catalog worker scripts disabled. Refresh existing lecture player.
4. Test with **1 lane first**, then same video with **2 lanes** (when no existing transfer running), optionally 3. Compare the actual Chrome→VPS `MiB/s` and Telegram delivery. Default 2 lanes isn't guaranteed faster than 1.

## Safety & performance

- Keep `--workers 1 --threads 16`, `RELAY_MAX_JOBS=1`; `RELAY_PARALLEL_UPLOADS=3` only controls **segment POST concurrency**, not concurrent video jobs.
- Each segment is saved by index and only its contiguous playlist prefix is appended; no mix-up if segment 2 arrives before segment 0.
- `finish` returns `409` until all segments are present. A failed upload cancels the job. Original MP4 conversion, thumbnail, Telethon and Telegram progress paths unchanged.
- A video above your current `.env` `RELAY_MAX_GB=0.7` will still be rejected; that limit is unchanged.
- Cloudflare Tunnel can still be bottleneck; concurrency is a speed experiment, not a promised 2x/3x throughput.
- No-key mode (`RELAY_REQUIRE_UPLOAD_KEY=0`) exposes a public upload surface; switch to upload-key auth when possible. This package does not change authorization settings.
- Existing active jobs are in Gunicorn memory; **never reload while an upload or Telegram delivery is in progress**.

## Roll back if 2/3 slower

Select **`1 upload lane`** in the Tampermonkey dropdown. No VPS restart needed; the new server accepts 1-lane sequential requests too. To fully revert, restore the previous Git commit of these two files in the repository and reload Gunicorn outside an active transfer.

## Verified locally

- Node `--check`, Python `py_compile`.
- Tests: 1/2/3/6 segment reverse-order server assembly, duplicate/index/missing guards, conversion gated; browser mock for 1/2/3 parallel lanes and error cancellation.
- **NOT** verified against live VPS, CDN, Telegram network. Final speed and complete Telegram MP4 need user-side test.
