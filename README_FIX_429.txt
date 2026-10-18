Railway Lecture Bridge - 429 STUCK JOB FIX
=========================================

Two files need updating:
1) relay_api.py - Python API, idle job cleanup & cancellation
2) Railway_Auto_Telegram.user.js - Tampermonkey user script; cancel upload if an error occurs

What changed:
- Keeps RELAY_MAX_JOBS=1 and one Gunicorn worker to avoid in-memory job inconsistency.
- A failed browser upload calls POST /api/relay/<job_id>/cancel to free its slot.
- An abandoned uploading job is released after 15 minutes without completed segment progress.
  This check also runs whenever a new job is requested, so there is no extra wait.
- An upload still writing a segment, a conversion job, or Telegram sending is never auto-cancelled.
- 429 message now includes the active job phase: uploading / processing / telegram_sending.

Steps with your Railway Ubuntu container:
1) Update those files in the GitHub repo used by /lecture-bridge and commit/push.
2) Ubuntu terminal: cd /lecture-bridge && git pull
   (If you have uncommitted edits, back them up and resolve git pull conflicts safely.)
3) (Optional) In /lecture-bridge/.env add:
   RELAY_UPLOAD_IDLE_MINUTES=15
4) If NO lecture is uploading/converting/sending, restart the bridge only:
   screen -r lecture-bridge
   Ctrl+C to stop Gunicorn inside the screen
   cd /lecture-bridge
   set -a
   . ./.env                # /bin/sh uses dot, NOT source
   set +a
   .venv/bin/gunicorn --workers 1 --threads 8 --timeout 600 --bind 0.0.0.0:8090 app:app
   (If no .venv exists, use gunicorn instead of .venv/bin/gunicorn.)
   Ctrl+A then D to detach
5) Test from another terminal:
   curl -i http://127.0.0.1:8090/health
   Expect 200.
6) In Tampermonkey replace the old script with Railway_Auto_Telegram.user.js,
   save/enable it, reload the lecture page, and click Send MP4 to Telegram.

NOTE:
- Restarting Gunicorn clears the in-memory jobs. Do not restart during a genuine transfer.
- Files already abandoned from before the restart may remain on the Railway disk;
  check /tmp/lecture_relay carefully before removing old directories. Never delete
  lecture_relay_bot.session files while Telegram sender is running.
- The bridge still needs authorized media access; this patch does not bypass DRM/CDN controls.
- With RELAY_REQUIRE_UPLOAD_KEY=0 your public API can be abused and may incur Railway charges.
- GNU Screen does not automatically recover from a Railway container restart/redeploy.
