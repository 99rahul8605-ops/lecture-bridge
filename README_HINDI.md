# Old manual one-click uploader restored (NOT auto-navigation)

Ye **purana verified uploader code** hai: `Railway_Auto_Telegram.user.js` (version 2.2.0) aur original `app.py`. Ye v3/v2 navigation/catalog code nahi hai. `relay_api.py` ko **change nahi** karta, isliye existing working Telegram group, progress, thumbnail aur conversion implementation jaisi hai waisi rahegi.

## Install

1. GitHub ke `/lecture-bridge` project ke root mein is bundle se **`app.py`, `Railway_Auto_Telegram.user.js`, `restore_manual.sh`** upload/replace karo. Repository ka `.env` GitHub par kabhi upload mat karo.
2. Railway Ubuntu terminal mein (koi upload/Telegram send currently chal nahi raha ho):

```bash
cd /lecture-bridge
git pull --ff-only
bash restore_manual.sh
```

3. Chrome Tampermonkey mein **VidCloud Auto Catalog Sync** ko **Disable** ya uninstall karo. Script `Lecture Auto Send to Telegram` ka content bundle ke `Railway_Auto_Telegram.user.js` se replace karo; uski sirf ek copy ON rakho.
4. Website mein lecture normal card se kholkar player play karo. **Send MP4 to Telegram** button ek baar dabao. Telegram group mein progress aur actual video delivery dekho.

## Kya off hua

- `catalog-control` GNU Screen session stop hota hai; alag bots ko touch nahi karte.
- `app.py` mein se catalog routes hat jaate hain. Data files, `.env`, `relay_api.py`, secrets, existing queue DB **delete nahi** hote.
- `VidCloud_Auto_Catalog.user.js` disable **browser mein manually** karna hai. Railway se browser Tampermonkey automatically disable nahi ho sakta.

## Speed / 'straight download and upload'

Purana manual userscript **full video pehle PC par save nahi** karta. Authorized unencrypted HLS segments browser mein fetch hote hain aur ek-ek karke Railway ko upload hote rehte hain. Up to 3 segment fetches prefetch hote hain, aur **Railway upload lane 1 hi hoti hai**. Browser tab open rehna hai. Uske baad Railway MPEG-TS ko FFmpeg se MP4 remux/convert karke Telegram ko bhejta hai. True CDN→Telegram direct end-to-end streaming nahi hai. Chrome→Railway ke saath CDN downloading overlap already hota hai. Higher concurrency speed badhane ki guarantee nahi; pehle user tests mein 1 lane better thi.

`-c copy` H.264/AAC ke liye already employed by the existing relay conversion; forced re-encoding slow ho sakti hai. Is restoration mein sirf proven manual flow rakha gaya hai, new uncertain optimizations nahi. DRM/license protections bypass nahi kiye gaye.

## Tests actually run here

- `node --check Railway_Auto_Telegram.user.js` — JavaScript syntax pass.
- `python3 -m unittest discover -s . -p test_restore.py -v` — 3 offline rollback checks pass.
- `node test_pipeline.cjs` — simulated manual click, HLS metadata, four MPEG-TS segments, sequential Railway upload, finish aur `sent` confirmation pass. Ye sirf offline mock hai; real CDN/Railway/Telegram speed is environment se verify nahi hui.

## Quick tests

```bash
curl -sS http://127.0.0.1:8090/health
screen -ls
```

Agar `screen -ls` mein `catalog-control` nahi aur `lecture-bridge` hai, expected. Ensure `app.py` simple `install_relay_routes(app)` wala hai.

## Rollback installer

Script start mein previous `app.py` aur user script ka backup `/tmp/lecture-manual-restore-YYYYMMDD-HHMMSS/` mein banata hai. Agar alag project folders hon toh is installer ko bina change kiye mat chalana. Kabhi running media job ke dauraan Gunicorn HUP mat karna; jobs in-memory hain, transfer interrupt ho sakta hai.
