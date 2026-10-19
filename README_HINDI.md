# Lecture Bridge — Pyrogram + TgCrypto sender (v1)

Ye **purane one-click `Send MP4 to Telegram`** setup ke liye focused update hai. Koi Telegram auto-catalog / auto-navigation worker install nahi karta. Existing Chrome → VPS parallel segment uploads, index ordering, FFmpeg conversion, thumbnail extraction, group progress notifications same rehte hain. Sirf FFmpeg ke baad **VPS → Telegram** sender Telethon se Pyrogram hota hai.

## Kya verify hua

- Tumhare uploaded `Google-drive-download--main (3).zip` ke `bot.py` me `run_pyrogram()`, `Client(..., max_concurrent_transmissions=workers)` aur `send_video(..., supports_streaming=True, progress=...)` milta hai. `requirements.txt` me `pyrogram` aur `TgCrypto` bhi hain.
- Lecture bridge ke current parallel-server `relay_api.py` me `_telegram_send()` Telethon use karta tha. Is package ka installer **original Telethon function ko delete nahi karta**; function ke beginning me Pyrogram call add hoti hai.
- Offline **7 mocked tests pass** (metadata, duration/size/thumbnail, progress, cleanup on error, first supergroup peer cache, idempotent installer). **Live Telegram speed/delivery test nahi hua**. Pyrogram ka faster hona guaranteed nahi; TgCrypto encryption overhead reduce kar sakta hai.

## Files

- `relay_sender_pyrogram.py` — naya Pyrogram Telegram MP4 sender, original title, video thumb, native streamable video and progress.
- `install_pyrogram_sender.py` — existing relay API ko carefully patch karta hai; original backup as `relay_api.py.before-pyrogram`.
- `deploy_pyrogram.sh` — requirements install, tests, one-worker Gunicorn code reload on port 8090 (without restarting Cloudflare Tunnel or other bots).
- `test_pyrogram_mock.py` — network-free mock tests.

## VPS deployment

**Pehle active video upload / MP4 conversion / Telegram send finish hone do**; Gunicorn reload in-memory jobs interrupt karega.

1. ZIP extract karke 4 files (`relay_sender_pyrogram.py`, `install_pyrogram_sender.py`, `deploy_pyrogram.sh`, `test_pyrogram_mock.py`) GitHub **lecture-bridge repo ke root** me upload karo. ZIP khud upload mat karo.
2. Ubuntu VPS terminal:

```bash
cd ~/lecture-bridge
git pull --ff-only
bash deploy_pyrogram.sh
curl -sS https://upload.bingolink.site/health
```

3. Chrome me existing one-click script **unchanged** rakho; 1/2/3 download+upload lanes as preferred. Ek short authorized MP4 test karo. Telegram group me **playable video + thumbnail + progress + `sent`** verify karo.
4. Comparison **VPS → Telegram** ki actual MiB/s se karo, Chrome → VPS upload speed se nahi.

## Settings

- **Gunicorn 1 worker** aur `RELAY_MAX_JOBS=1` same.
- `TELEGRAM_SENDER=pyrogram` optional; patch ka default Pyrogram hai. Agar `.env` me already `TELEGRAM_SENDER=telethon` hai toh Pyrogram tab tak active nahi hogi jab tak tum usko change karke **full bridge process restart** nahi karte. Sirf HUP old master ki environment variables nahi badalta.
- `PYROGRAM_MAX_TRANSMISSIONS=2` default (bounded 1–4). Ye total concurrent file transmissions ka bound hai; **one video** upload ki speed ko 2x nahi banata. `TgCrypto` available hona more relevant hai.
- Pyrogram ka session `/tmp/lecture_relay/lecture_relay_pyrogram.session` me independently save hota hai; bot token, API ID/hash aur group ID existing `.env` se hi liye jate hain.
- Private groups me fresh Pyrogram session ko cached peer access hash na mila toh sender bot-authorized supergroup peer ko `access_hash=0` se resolve karne ki koshish karega (Telegram ka documented bot mechanism). Bot ko group ka member/admin hona zaroori hai.

## Rollback: previous working Telethon

Agar koi send fail ho toh **new upload start mat karo**. VPS me:

```bash
cd ~/lecture-bridge
cp relay_api.py.before-pyrogram relay_api.py
python3 -m py_compile relay_api.py
ps -eo pid,ppid,args | grep '[g]unicorn.*8090'
```

Output me Gunicorn **master** ka PID (worker ka parent, SCREEN nahi) pehchaano; koi job active nahi ho tab:

```bash
kill -HUP MASTER_PID
curl -sS http://127.0.0.1:8090/health
```

Backup revert karne se original Telethon sender wapas aa jayega; `relay_sender_pyrogram.py` aur installed packages leave kiye ja sakte hain. **`fuser -k` ya sab processes kill na karo.**

## Troubleshooting

- `Pyrogram not installed`: `.venv/bin/python -m pip install 'Pyrogram==2.0.106' 'TgCrypto>=1.2.5,<2.0'`
- Python 3.12 me standalone `import pyrogram` ke liye asyncio loop issue aa sakta hai; upload sender async context me import karta hai, startup check explicit loop create karta hai.
- `PEER_ID_INVALID` / `CHAT_ADMIN_REQUIRED`: bot ko Telegram destination group/channel me membership/posting permission check karo; valid `TELEGRAM_CHAT_ID` use karo.
- `MEDIA_EMPTY` or Telegram 400: generated MP4 probe, file size, Telegram account/API limits check karo.
- Group me progress message aa raha hai but MP4 slow: `Telegram upload ... MiB/s` metrics compare karo. Cloudflare Tunnel browser→VPS side pe hai, **VPS→Telegram sender path** par nahi.
- `.env`/BOT_TOKEN/API_HASH/tunnel JSON ko GitHub ya chat me post mat karo.

**Boundary:** Ye sirf already authorized/exported MP4 ko Telegram par bhejta hai; DRM/session/access protection ko defeat karne wala code nahi hai.
