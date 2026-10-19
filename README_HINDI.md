# Telegram Auto Worker v3.0 — tested navigation & queue patch

**Existing project:** `/lecture-bridge` port `8090`, Telegram bot `catalog-control`. **No separate bot.py required.**

## Kya fix hai (pichhle screenshot se)

- `index.php#batches` par **Recently Watched** lecture card hone se worker galat course page nahi samjhega. Home pe **batch global search** hi karega.
- Railway `worker/next` response mein `batch_name`, `subject_name`, `chapter_name` bhejega. Ye values previous v2.0 mein missing thi; batch search ko `batch_name` chahiye.
- Batch sirf batch results mein, subject `data-subject-id`/`data-id`, chapter `data-chapter-id`/`data-topic-id`/`data-id`, lecture `data-video-id` se choose hoga. **Lecture name global batch search mein kabhi nahi dala jayega.**
- Agar screen already kisi unrelated course par hai, worker thoda wait karke normal site home par jayega aur correct batch select karega.
- Website ke **normal lecture-card click** se `play.php` open hoga. Missing PHP/security parameters create/forge nahi karta.
- Navigation 4 minute mein na ho toh worker pause; queue preserved. Success sirf purane uploader ke **`✅ Sent to Telegram:`** text ke baad mark hoti hai. Existing permitted uploader bilkul unchanged hai.
- A 10-minute Railway navigation lease can be renewed by the active browser tab. One worker at a time.

## Setup (sirf ek baar)

**Koi upload chal raha ho toh complete hone do.** ZIP extract karke GitHub `lecture-bridge` repository root mein ye files upload/replace karo:

- `auto_catalog.py`
- `VidCloud_Auto_Catalog.user.js`
- `deploy_v3.sh`
- `test_worker.py`, `test_catalog_existing.py`, `test_navigation.cjs`, `test_search.cjs` (local testing + deployment checks)

Railway terminal:

```bash
cd /lecture-bridge
git pull --ff-only
bash deploy_v3.sh
```

Ye script previous Git commit se temporary backup banata hai, Python tests chalata hai, agar `node` installed hai toh JS mock tests bhi, phir **sirf port 8090 ke ek Gunicorn master** ko HUP karta hai. Dusre bots ko nahi chhedta. Existing `catalog-control` Telegram bot restart **required nahi**. `.env` aur SQLite queue delete nahi kiye jaate.

Tampermonkey dashboard mein *existing* `VidCloud Auto Catalog Sync` script ko EDIT karke naye `VidCloud_Auto_Catalog.user.js` ka **poora text paste, save** karo. Duplicate v2.1/v2.2/v2.3 copies OFF rakho. Existing `Railway_Auto_Telegram.user.js` yani **Send MP4 to Telegram** script ON hi rakho. Purani saved catalog key same script namespace mein rehni chahiye; agar missing ho, Tampermonkey menu se Configure key karo (Railway secret chat mein share nahi karna).

## Single-lecture end-to-end acceptance test

1. Pehle Telegram `/autostop`, phir `/autoretry` (old failures/retry counters reset) aur `/queueauto`. Agar selected lectures awaiting_export mein hain toh re-add ki zaroorat nahi.
2. Chrome **same logged-in tab** par `https://vidcloud.eu.org/index.php#batches` kholo, global search modal close kar do.
3. Telegram `/autostart`, Chrome Tampermonkey > **Start Telegram queue worker IN THIS TAB**.
4. Bottom-left `v3.0.0` dikhe. **Expected:** correct CA Inter batch name search → exact batch ID click → correct subject ID → chapter ID → lecture video ID → site-generated 11-parameter `play.php` → existing upload button → Telegram MP4 delivery → `/queueauto` shows delivered.
5. **Sirf pehla lecture test karna hai toh** Chrome jab uska player khol de, Telegram par `/autostop` bhejo. Current upload complete ho sakta hai; agla lecture assign nahi hoga. Delivery verify hone par `/autostart` se baki queue resume karo. Chrome tab open/logged-in rehna hai.

Agar page par **`Security fields missing`**, verification, ya encrypted/unsupported media ka error aaye, worker access control bypass nahi karta. Authorization site ke normal flow se chahiye.

## Test evidence

- Python tests: 6/6 passed (queue states, duplicate, scheduled, lock, metadata, names).
- Mocked JS navigation cases: 9/9 passed (home recently-watched, search result, subject/chapter/lecture ID, stale URL hash, modal close, wrong-course recovery).
- JS search tests: 4/4 passed (open batch search, fill correct batch name, reject chapter/subject text).
- `node --check` passed.
- **Not live-tested:** private logged-in vidcloud.eu.org browser, actual Railway deployment, CDN video conversion and Telegram delivery. Cannot truthfully guarantee 100% success before that acceptance test.

## Safe rollback

Old code saved under `/tmp/lecture-worker-v3-YYYYMMDD-HHMMSS/` by deploy script, and previous Git commit. Chrome script rollback via Tampermonkey editor/previous saved version; server rollback requires restoring previous `auto_catalog.py` and HUP reloading *only the 8090 master*. Never run `fuser -k 8090/tcp` just to reload code.
