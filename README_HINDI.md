# Telegram Auto Catalog + existing MP4 relay success sync (v1.1)

## Kya **actually** automate hota hai?

- Website par normal browsing se Batch -> Subject -> Chapter IDs aur original **successful** chapter lecture-list API responses se lecture title, status, video ID, time sync hota hai.
- **Kisi token / cookie / signed MPD URL / DRM key ko upload ya store nahi karta.** `401` URL retry nahi hoti; browser ke successful normal requests ki metadata copy hoti hai.
- Telegram bot se `/batches`, `/batch`, `/subjects`, `/subject`, `/autochapters`, `/autochapter`, `/lectures`, `/add`, `/queueauto`, `/done` commands.
- `COMPLETED` + `Ready` lectures hi queue mein aate hain. Scheduled / non-ready items skip. Stable Video IDs se duplicates skip. Queue SQLite mein store.
- Existing `/txt`, `/importchapters`, old `/chapters`, legacy `/done` (TXT catalog) untouched rehte hain. Naye *pending auto queue* ke liye `/done` media-export-waiting status karta hai. **v1.1:** Existing browser sender jab Railway ka real `status: sent` result receive karta hai, tab catalog script us `video_id` ka queue status `delivered` set karta hai. Agar old uploader full MP4 already bhej raha hai, catalog use block nahi karta.
- `POST /api/catalog/delivered` can be called **only after** an authorized media-delivery integration confirms success, with JSON `{"videoId":"<24 hex ID>"}` and dedicated `X-Catalog-Key` header.

## Kya abhi nahi hota?

- Telegram `/done` **khud video download/send start nahi karta**. Browser ka existing **Send MP4 to Telegram** button pehle jaise manually trigger hoga. Is update mein Telegram success ka status-sync auto ho gaya hai, media export nahi.
- No DRM decryption, license extraction or signing-key bypass. `drmProtected: true` wala metadata flag apne aap working existing uploader ko block nahi karta; lekin har stream exportable hone ka proof bhi nahi hai.
- Browser must be open and signed in to load website pages/normal API responses. Railway server itself cannot list full protected catalog without proper provider-supported credentials/API. It is **not** always-on headless browsing.
- First-time catalog coverage requires navigating desired batch/subject/chapter screens. Future lectures sync when browser normally loads that chapter again. It does not silently force the site to open every chapter.
- Install/restart steps require server access; not deployed on your Railway service by this ZIP.

## Setup (no `.env`/token included)

1. ZIP unzip karo; existing Railway `app.py`, `bot.py` ki copy/backup rakho. **Do not replace existing relay_api.py**, upload script, bot token or existing server process.
2. Ek nayi **dedicated catalog key** banao, e.g. `python3 -c 'import secrets;print(secrets.token_urlsafe(32))'`. Railway bridge environment aur bot environment **dono** mein `CATALOG_SYNC_KEY=<same key>` set karo. Telegram bot environment mein `OWNER_ID=<your Telegram numeric user id>` should be set. Bridge mein bhi same `OWNER_ID` recommended. Kabhi chat mein key paste mat karna.
3. Railway bridge environment: `AUTO_CATALOG_DB=/data/auto_catalog.sqlite3` (agar persistent Railway volume `/data` mount ho). Agar volume nahi hai, writable persistent volume configure karo, warna deploy ke baad queue/catalog reset ho sakte hain. Existing `RELAY_ALLOWED_ORIGIN=https://vidcloud.eu.org` unchanged.
4. Telegram bot environment: `CATALOG_BASE_URL=https://ubuntu-production-9d09.up.railway.app` and the same `CATALOG_SYNC_KEY`. **Existing** `BOT_TOKEN`, `API_ID`, `API_HASH` etc. unchanged.
5. `auto_catalog.py`, `catalog_web.py`, `catalog_bot_client.py`, and `install_into_repo.py` files ek directory (e.g. `/tmp/auto-catalog`) mein rakho. Python3 se command chalao:

   ```bash
   python3 install_into_repo.py --bridge-dir /lecture-bridge --bot-dir /path/to/current/bot
   ```

   Agar bot aur bridge same folder mein hain, dono flags mein same path do. Agar sirf bridge update hai, `--bridge-dir` only. Installer original `app.py` aur `bot.py` ka `.catalog-bak` backup banata hai, **relay_api.py ko nahi chhoota**, aur syntax compile karta hai. Bot mein expected old `chapter_queue` code na mile toh modify nahi karta; version mismatch hone par manual review needed.
6. Existing Gunicorn/screen **bridge process restart** karo (1 worker recommended per old relay code). Existing bot service bhi restart karo. Don't launch duplicate bot sessions. `/health` pehle ki tarah valid hona chahiye.
7. Chrome Tampermonkey mein **updated v1.1** `VidCloud_Auto_Catalog.user.js` install/update karo. Existing "Send MP4 to Telegram" script ko replace nahi karna. Tampermonkey menu > **Configure catalog sync key** > dedicated key enter karo. Key extension mein stored hoti hai, website mein post nahi hoti. Agar v1.0 pehle installed hai to replace/update karo, duplicate scripts mat rakho.
8. Website open karo, global search -> batch select -> subject -> chapter list. Normal lecture API jab successful load hoti hai, `data[]` se status and titles sync ho jaate hain. Expected Chrome Console may log `[AutoCatalog]` errors on unauthorized key/bridge failures.
9. **Delivery tracking test:** Telegram bot mein lecture select karo aur `/done` bhejo **pehle**, phir usi lecture ke `play.php?video_id=...` page par existing **Send MP4 to Telegram** button use karo. Normal uploader ka Railway status `sent` aane ke baad `/queueauto` mein `delivered` hona chahiye. Script sirf Railway result observe karti hai; bina upload success ke delivered nahi mark hota.
10. Agar `play.php` ke query mein catalog wala 24-character `video_id` nahi hai, tracker ID match nahi kar sakta. Console warning aayegi; tab video still Telegram pahunch sakti hai, lekin automatic delivered marking nahi hogi.

## Telegram commands

```text
/syncstatus     # currently synced counts
/batches        # synced batch list
/batch 1        # choose batch
/subjects       # list subjects of chosen batch
/subject 2      # choose subject
/autochapters   # show chapter list
/autochapter 3  # choose chapter
/lectures       # list lectures; ✅ = Ready, ⏳ = not confirmed ready
/add 5          # first 5 chapter lectures
/add 2-4        # indices 2,3,4
/add 1,3,5      # specific lectures
/add all        # all Ready; duplicate and scheduled skip
/queueauto      # queued metadata status
/done           # prepare new auto queue entries for authorized export
/autoclear      # remove pending/waiting items, preserve delivered history
```

**Do not mistake `/done` for a finished Telegram MP4 upload.** It only marks catalog-selected items as `awaiting_export`. The existing browser uploader still performs the actual export on user action and its real successful Telegram send auto-confirms catalog status. Old TXT `/done` behavior remains intact when there is no pending auto queue.

## Troubleshooting

- `/syncstatus` empty: Tampermonkey key not configured; initial website pages not visited; wrong `CATALOG_BASE_URL`; or website changed HTML/API. `/health` and browser Console first.
- Site API `401` from manual Console fetch is irrelevant here; script does not manually retry authenticated video metadata requests.
- `batches` appears, but no subjects/chapters: select the batch using website card first (click tracking supplies GUI ID). Navigate subjects and chapters once. Refresh the chapter page if the extension script was just installed.
- `lectures` marked ⏳ despite playable video: HTML sees ID but lacks authoritative normal successful API status. Open chapter again, let its normal `/contents?tag=...&contentType=videos&page=1` load. No credentials required outside website.
- Bot and bridge in separate Railway deployments: don't rely on SQLite shared filesystem; bot uses the dedicated remote catalog API. Volume is for bridge only.
- If files in installed repo differ from the provided earlier bot version, test in backup staging before production deploy. Never delete existing bot or relay data.
- `drmProtected: true` is metadata, not a queue rejection check. A DRM license/authorization failure in a different stream cannot be fixed by this package; do not remove existing uploader safeguards.
- **Bot-only one-click playback/export is not provided.** Browser tab must stay open for the existing uploader, and its Send MP4 button still needs to be clicked.

## Tests

Run `PYTHONPATH=. python3 -m unittest discover -s tests -v` in ZIP folder. The standalone data-model tests cover selection, skipping scheduled items, duplicates, delivered state, and avoiding media URL storage. Live website/TG/Railway authentication was **not** tested by the packager.
