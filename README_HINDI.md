# Video 1 Railway upload complete -> Video 2 start while Video 1 sends to Telegram

## Kya change hota hai

- **Browser:** saare valid, permitted HLS segments send karke `/finish` ke 202 response par instantly free. Ab Telegram ke final `sent` status ko poll/wait nahi karega. **Next lecture manually open** karke one-click Send dabaa sakte ho.
- **Server:** `RELAY_MAX_JOBS=1` ab **sirf active browser upload** limit karta hai. Background `queued`/`processing`/`telegram_sending` upload slot occupy nahi karte.
- **Telegram:** processing aur Telegram send **ek-ek karke** hote hain, jisse Pyrogram/Telethon session conflict aur FFmpeg overload avoid hota hai. Telegram group ka existing per-video progress notifier unchanged.
- **Safety:** `RELAY_BACKGROUND_JOBS=2` by default. 2 completed jobs queue mein hain toh naya `/start` 429 dega, taaki disk/RAM exhaust na hon. Disk ka kam-se-kam 512 MiB free check hota hai. Old upload retry/parallel lanes and MP4 conversion untouched.
- **One worker** Gunicorn configuration wahi rahegi.

## Install

1. **Wait until the current video finishes Telegram sending**, because Gunicorn reload kills RAM-based background jobs.
2. ZIP ko extract karo. Saari files GitHub `lecture-bridge` repo ke root mein upload karo; `tests/fixtures/` folders bhi preserve karo.
3. Railway Ubuntu (ya same `lecture-bridge` VPS) terminal:

```bash
cd /lecture-bridge         # VPS par ho sakta hai cd ~/lecture-bridge
# Agar ZIP files GitHub par uploaded hain:
git pull --ff-only
bash deploy_pipeline.sh
```

Installer `relay_api.py.before-pipeline-<timestamp>` backup banata hai aur existing code ko hi patch karta hai. Script pehle 6 offline tests chalata hai; `RELOAD` ke liye prompt karega. **Tabhi RELOAD type karna jab koi existing Telegram send/FFmpeg/upload running na ho.**

4. Chrome **Tampermonkey** mein ek hi `Lecture Auto Send to Telegram` userscript rakhna; is package wala `Railway_Auto_Telegram.user.js` replace karo. Agar VPS use kar rahe ho toh script ki `let RELAY` line VPS domain set karo; Railway ka default already set hai.
5. Lecture page reload karo. **Video 1: Send MP4 to Telegram**. All segments uploaded message ke baad next video kholkar **Video 2: Send** dabao. Video 1 ka processing + Telegram send background mein rahega. Telegram group mein delivery/progress dekho.

## Expected sequence

```
Video 1: Browser upload 25/25 ✅ -> /finish 202 (queued)
                         Telegram Video 1: converting -> sending -> sent
Video 2: Browser upload 25/25 ✅ -----------------------^
Video 2 Telegram: queued until Video 1 completes, then converting -> sending -> sent
```

**Important:** Jo /finish 202 batata hai, woh *server ne saare segments receive kiye hain* ki confirmation hai, **Telegram delivered** ki nahi. Browser band kar sakte ho, lekin Railway/VPS worker running rehna chahiye. In-process background jobs deployment/restart ke baad automatically resume nahi honge.

## Limitations

- Existing network `Failed to fetch`, slow uploads, short CDN session, CORS, Cloudflare connectivity is change se fix nahi hote.
- Server ki free disk zaroori hai: media input aur conversion output dono temporary jagah lete hain.
- DRM/signature bypass or key generation is package ka part nahi hai. Existing authorized media handling unchanged.
- Existing Telethon/Pyrogram sender ka function unchanged hai.
- Railway aur user ke browser par live upload/delivery test nahi chalaya gaya; offline simulations pass hue hain.

## Rollback

Jab jobs idle hon:

```bash
cd /lecture-bridge
ls -t relay_api.py.before-pipeline-* | head -1
cp "$(ls -t relay_api.py.before-pipeline-* | head -1)" relay_api.py
python3 -m py_compile relay_api.py
# Ab running Gunicorn master ka PID check karke HUP; manually:
ps -eo pid,ppid,args | grep '[g]unicorn.*8090'
# kill -HUP <MASTER_PID>
```

Tampermonkey mein pichhla uploader restore kar dena. `.env`, tunnel, credentials, queue catalog aur doosre bots change nahi hote.
