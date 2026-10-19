# Lecture Queue Resume + Skip Failed + Telegram Progress Cleanup v1

Browser uploader and Queue Manager are TWO separate Tampermonkey scripts. This
release does not enable the PC-off Cloud Queue (Railway→CDN got HTTP 403).

## What changed

- If a browser upload fails, `/pause` releases its Railway upload slot **without**
  deleting committed MPEG-TS segments.
- User can keep browsing/queueing lectures. Worker marks a real failed lecture
  as **Failed**, proceeds to the next queued lecture (after current sender is idle).
- **↻ Retry failed** requeues failed/short-lived URLs. Server returns exactly
  which segment indices it already received; Chrome skips them when retrying.
- Same lecture's media identity is SHA-256 of the video ID + ordered media
  segment URL **paths only** (no signed query strings are recorded in cache).
  If media playlist paths/count change, unsafe reuse is refused.
- Only the interrupted segment must restart from the beginning; already
  **accepted** complete segments are reused from Railway's disk.
- Temporary Telegram progress text is deleted using ONLY `message_id` obtained
  from that bot's `sendMessage`. It no longer leaves "Upload expired; job slot
  released" in the group. Final MP4 videos/captions stay untouched.
- Resume state saved in `RELAY_DATA_DIR` as `resume.json` and `input.ts` / TS
  files, restored after Gunicorn worker restart *if disk still exists*.
  Default paused cache retention 48h (env `RELAY_RESUME_TTL_HOURS`).

## Install / Deploy — 1 terminal command

1. Make sure **all ongoing uploads/Telegram deliveries are complete**.
2. Extract ZIP and upload these files to GitHub repo `lecture-bridge` **root**:
   `install_resume_backend.py`, `setup_resume.sh`, `test_resume_backend.py`,
   `test_queue.js`, `test_two_tabs.js`, `Railway_Auto_Telegram.user.js`,
   `Lecture_Two_Tab_Queue.user.js`, and `Dockerfile` (replace old Dockerfile).
   Keep `fixtures/parallel_relay.py` if it is easy to upload too; tests can use
   current `relay_api.py` from repo root without it.
3. Commit changes. Railway auto-deploy may restart the service, so this MUST
   happen only when it is idle.
4. Railway terminal:

   ```bash
   cd /lecture-bridge && git pull --ff-only && bash setup_resume.sh
   ```

   Script tests first, asks to type **IDLE**, creates a timestamp backup of
   `relay_api.py`, installs patch and checks syntax. If exactly one 8090
   Gunicorn master is identifiable, it reloads it. If not, it tells you and
   **does not** kill/restart an uncertain process.
5. In Chrome Tampermonkey: replace existing uploader's source with
   `Railway_Auto_Telegram.user.js` and existing Queue Manager's source with
   `Lecture_Two_Tab_Queue.user.js`. **Do not create duplicate copies.**
   `Ctrl+S`, refresh BOTH tabs. Keep Cloud Queue script **OFF**.
6. Keep one worker tab open; browse in second tab. Start worker ONCE.

## Testing safely

- Add two short authorized lectures. Intentionally disconnect Wi-Fi after one
  segment has reached Railway, then reconnect. Browser will show failure, and
  Queue Manager should move on. Use **↻ Retry failed** when ready.
- Check uploader stats: `N cached` should equal accepted segments from the
  previous attempt. Final Telegram MP4 should arrive only once.
- Never refresh worker tab during an active transfer. If URL/session expired,
  open actual lecture from its chapter card and `+ Add this lecture` again.
- An HTTP 429 (busy job) still causes bounded retries. If another process
  hogs the upload slot, the queue may defer until that server job releases.

## Limitations / warnings

- Source must be authorized unencrypted MPEG-TS/HLS that the browser normally
  can access. No DRM, signed URL bypass, cookie replay, CDN direct Railway fetch.
- No guarantee across a Railway **redeploy** without a persistent Volume for
  `RELAY_DATA_DIR`. Mount persistent Volume at `/data` and set
  `RELAY_DATA_DIR=/data/lecture_relay` if restart-proof caching is needed.
- Resume can reuse **only complete server-acknowledged segments**, not the
  middle of an incomplete HTTP POST. The previous code would cancel/delete;
  old failed uploads from before this patch can't be restored.
- Source playlist must still be accessible from Chrome at retry time.
- Paused cache consumes Railway storage up to 48 hours. Ensure disk quota.
- Public no-key relay is still public; any party who knows its endpoint can
  submit uploads. This patch does not introduce new authentication.
- Nothing is automatically deleted from the Telegram group except the exact
  temporary progress text that the relay bot itself created; earlier old texts
  must be removed manually.
- This is OFFLINE-tested against the known parallel relay (also with optional
  background Telegram pipeline/progress patch). Live Railway delivery not yet
  tested; retain timestamp backup for rollback.

## Rollback

Stop transfers. To restore backend, choose the latest
`relay_api.py.before-resume-<timestamp>` backup and replace `relay_api.py` with
it, then reload the correct Gunicorn master. Replace the 2 Tampermonkey scripts
with their preceding versions. Never roll back mid-upload.
