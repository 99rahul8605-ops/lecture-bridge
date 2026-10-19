# Telegram Queue → Chrome → Existing MP4 Uploader (v2)

**Ye Chrome browser-worker integration hai, direct Railway downloader nahi.** Web player ke existing, authorised media export button ko auto-trigger karta hai. DRM decryption, licence fetching, signature forgery, access restrictions bypass, ya encrypted stream conversion nahi karta. Chrome tab khula/signed-in rehna zaroori hai. Har course ke liye media-export permission zaroori hai.

## Files to update on GitHub (ZIP extract karke, existing `lecture-bridge` repo root mein)

1. `auto_catalog.py`
2. `catalog_web.py`
3. `catalog_bot_client.py`
4. `catalog_control_bot.py`
5. `VidCloud_Auto_Catalog.user.js`
6. `deploy_worker.sh` (optional: one-command deploy)

`app.py`, `relay_api.py`, existing `Railway_Auto_Telegram.user.js`, `.env`, token, or original video uploader **replace mat karo**. Existing SQLite catalog same database par continue karega. `test_worker.py` optional hai.

## Railway Ubuntu: after GitHub upload

**Fast way (ZIP extract ki saari 6 files GitHub repo root par upload karne ke baad):**

```bash
cd /lecture-bridge
bash deploy_worker.sh
```

Manual commands below are an alternative, not additional steps.


```bash
cd /lecture-bridge
git pull --ff-only
python3 -m py_compile auto_catalog.py catalog_web.py catalog_bot_client.py catalog_control_bot.py

# Gunicorn worker reload, same port 8090, no conflicting second start.
MASTER=$(pgrep -of '[g]unicorn.*0.0.0.0:8090 app:app')
if [ -n "$MASTER" ]; then
  kill -HUP "$MASTER"
  sleep 3
  curl -sS http://127.0.0.1:8090/health
else
  echo 'No running lecture-bridge Gunicorn found: do not blindly kill other services.'
fi

# Restart only this project's Telegram catalog-control listener.
screen -S catalog-control -X quit 2>/dev/null || true
sleep 2
screen -dmS catalog-control bash -lc 'cd /lecture-bridge && set -a && . ./.env && set +a && exec python3 -u catalog_control_bot.py > /tmp/catalog-control.log 2>&1'
sleep 3
tail -n 20 /tmp/catalog-control.log
```

For existing catalog data to survive redeploy, set `AUTO_CATALOG_DB` to a path on a persistent volume. A path under `/tmp` may disappear on restart/redeploy. Do not change it casually or existing queued lectures may appear to vanish.

## Chrome: ONE worker tab

- Old **Send MP4 to Telegram** Tampermonkey userscript must stay ON, unchanged.
- Open existing `VidCloud Auto Catalog Sync (metadata only)` userscript and **replace its entire code** with the new `VidCloud_Auto_Catalog.user.js`. It retains the same `@name` and `@namespace` so Tampermonkey storage for catalog key should remain. Keep exactly **one** Auto Catalog script ON.
- Open `https://vidcloud.eu.org/index.php` while signed in.
- Tampermonkey menu: **▶ Start Telegram queue worker IN THIS TAB** (one-time per working tab). Keep that one Chrome tab open. Browser may need permission to play video; if playback is blocked, worker reports error rather than bypassing protection.
- Use only one worker tab at a time. Do not start a separate worker in additional tabs.

## Telegram: existing 9 waiting items

```text
/autostatus
/autostart
/queueauto
```

For a fresh selection:

```text
/batches
/batch 1
/subjects
/subject 1
/autochapters
/autochapter 1
/lectures
/add all
/done
/queueauto
```

`/done` turns on queue processing; the **Chrome worker automatically visits the next lecture**. It attempts muted player playback to load the authorised manifest, then clicks the existing upload button on the matched `play.php` page. The separate existing uploader's own checks still apply (encrypted/unsupported media stays unsupported). The worker only marks `delivered` after the existing uploader visibly reports `✅ Sent to Telegram:`; next lecture starts after confirmation.

New control commands:

- `/autostatus`: ON/OFF and active lecture
- `/autostop`: pause picking **new** lectures (current transfer may complete)
- `/autostart`: resume next lectures
- `/autoretry`: requeue failed lectures for another authorised attempt
- `/queueauto`: show pending / waiting / delivered / failed totals

Tampermonkey **⏸ Stop worker AFTER current upload** disables queue processing in this specific browser tab; it does not cancel an ongoing upload. `/autostop` additionally pauses server queue. If browser closes during an upload, lease expires and worker may later retry, so **always check whether Telegram already received that lecture** before enabling automated retry.

## Safe smoke test

1. Prefer one small, authorised, non-DRM-export-restricted lecture for the first live test.
2. Open Chrome worker tab and enable it through Tampermonkey menu.
3. Telegram `/done` (or `/autostart` if queue already prepared).
4. Watch `🤖 Queue worker` small status badge and Railway upload status. Avoid manually clicking sender during automated run.
5. `/queueauto`: lecture should move from `awaiting_export` to `delivered` only when Telegram upload really succeeded.
6. If error, inspect Chrome Console `[AutoCatalogWorker]`; `/queueauto` shows `failed`. Use `/autoretry` only after confirming no duplicate file is already in Telegram.

## Limitations

- Local tests: Python DB queue/locking/retry plus JavaScript syntax. **No live VidCloud/Railway/Telegram test has been run.** No claim of guaranteed complete batch MP4 export.
- Existing media uploader still requires a supported authorised HLS playlist; doesn't export encrypted DRM DASH content. Server cannot magically create DRM licence keys.
- Chrome must stay open/signed-in and have a stable connection. Browser autoplay policy, session expiry, expired signed URLs, website UI changes, 429/403, or Telegram rate limits may stop a lecture. Worker reports failure/pauses on uncertain outcome rather than blindly marking delivered.
- The bridge key secures metadata controls. Do not paste `.env`, BOT_TOKEN or catalog key in chat or GitHub.
