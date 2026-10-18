RAILWAY VIDEO PREVIEW / THUMBNAIL UPGRADE
========================================

Original Google-drive-download bot ke upload code ko dekhkar ye patch banaya gaya:
- ffprobe se asli width, height, duration, video/audio codec.
- FFmpeg se <= 320px JPEG thumbnail (5s, 1s, 0.2s etc fallback).
- Telethon DocumentAttributeVideo(duration,w,h,supports_streaming=True).
- DocumentAttributeFilename(original-title.mp4) + MIME video/mp4.
- Proper formatted Telegram caption: original lecture title, file size,
  duration aur resolution; HTML-escaped titles.
- MP4 H.264 video + AAC audio for Telegram compatibility.
- Jab lecture audio missing ho toh silent AAC track add hota hai, taaki
  Telegram use animation/GIF jaisa classify na kare.
- H.264/AAC already correct ho toh fast stream copy (no quality loss).
- Chrome -> Railway / Telegram live progress, 429 idle cleanup,
  existing API endpoints aur current Tampermonkey userscript unchanged.
- Thumbnail aur temporary video successful delivery ke baad clean.

FILES
-----
relay_api.py                  UPDATE: main backend patch
app.py, requirements.txt     Unchanged; reference copies
Railway_Auto_Telegram.user.js Unchanged; already installed userscript

test_video_preview.py        Local/offline test (fake Telegram, real ffmpeg)

DEPLOY (Existing Ubuntu Railway container, Gunicorn screen at port 8090)
-----------------------------------------------------------------------
1) GitHub lecture-bridge repo ke ROOT mein relay_api.py REPLACE karo.
   Commit/push karo. Tampermonkey update ki zaroorat NAHI.

2) Container terminal:
   cd /lecture-bridge
   git pull
   python3 -m py_compile relay_api.py
   command -v ffmpeg
   command -v ffprobe

3) Important: Koi Chrome upload / Telegram send ACTIVE ho to restart MAT karna.
   screen -r lecture-bridge
   Ctrl + C   (existing Gunicorn ko stop)

   cd /lecture-bridge
   set -a
   . ./.env
   set +a
   if [ -x .venv/bin/gunicorn ]; then
     .venv/bin/gunicorn --workers 1 --threads 8 --timeout 600 --bind 0.0.0.0:8090 app:app
   else
     gunicorn --workers 1 --threads 8 --timeout 600 --bind 0.0.0.0:8090 app:app
   fi

   Listening at: http://0.0.0.0:8090  expect.
   Ctrl + A, then D (detach)

4) curl https://ubuntu-production-9d09.up.railway.app/health

5) Video page refresh; same "Send MP4 to Telegram" Tampermonkey button.
   Telegram mein title + thumbnail + duration ke saath playable MP4 video
   aana chahiye. Live Telegram test is required; only local mocks tested.

KYA NAHI BADLA
--------------
- Existing port 8080 / Ubuntu UI safe.
- Bridge remains 8090.
- TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_CHAT_ID, BOT_TOKEN same.
- RELAY_REQUIRE_UPLOAD_KEY=0 (agar aapne set kiya hai) same.
- CDN permissions / DRM / signature rules bypass nahi hoti.
- Cloud transfer/Telegram upload still consumes Railway bandwidth/storage.

SECURITY: Public no-key upload endpoint can consume Railway resources even if
         domain is not shared. Better to enable auth later.
