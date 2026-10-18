AUTO SEND LECTURE VIDEO TO TELEGRAM (no manual MP4 link)
=====================================================

HOW IT WORKS
On vidcloud.eu.org/play.php, Tampermonkey shows 'Send MP4 to Telegram'.
After one click the browser fetches HLS segments it is authorized to read,
streams segments to your Railway Ubuntu bridge, the bridge uses ffmpeg to
convert to MP4, then sends the MP4 to the specified chat via your existing
Telegram bot using Telethon (MTProto). No URL is manually pasted into the bot.
The MP4 filename + Telegram caption come from the page's video_name parameter.
Example: 'Accounting Standards 01 - AS.mp4' / caption 'Accounting Standards 01 : AS'.

NO DRM bypass, no CDN signed URL forging. Only for videos you own or have
permission to export. CDN segments must be browser-accessible already.

WHAT IS INSIDE
  relay_api.py                backend: supports title metadata and Telegram sending
  app.py                      existing Flask entrypoint (unchanged)
  requirements.txt            adds Telethon
  Railway_Auto_Telegram.user.js  Tampermonkey autoloads on lecture pages
  Dockerfile                  standalone Railway service option
  .env.example                sample configuration; NOT actual credentials

UPDATE YOUR EXISTING UBUNTU /lecture-bridge SETUP
1. Upload relay_api.py and requirements.txt to GitHub, replacing repo copies.
   Add Railway_Auto_Telegram.user.js to repo or download it and paste into Tampermonkey.
   DON'T commit .env.
2. In Railway Ubuntu terminal: cd /lecture-bridge && git pull
3. Dependencies (preferred use a virtual environment):
   cd /lecture-bridge
   python3 -m venv .venv        # install python3-venv if needed
   . .venv/bin/activate
   pip install -r requirements.txt
4. Edit PRIVATE /lecture-bridge/.env and set the keys in .env.example:
   BOT_TOKEN=...               # same token as your EXISTING bot, not new bot
   TELEGRAM_API_ID=...         # from your existing bot's config
   TELEGRAM_API_HASH=...       # from your existing bot's config
   TELEGRAM_CHAT_ID=...        # numeric chat ID or @yourusername
   RELAY_REQUIRE_UPLOAD_KEY=0  # user chose no browser upload key (not secure)
   RELAY_ALLOWED_ORIGIN=https://vidcloud.eu.org
   RELAY_PUBLIC_BASE_URL=https://ubuntu-production-9d09.up.railway.app
   RELAY_MAX_JOBS=1
   RELAY_MAX_GB=0.7
   chmod 600 /lecture-bridge/.env
5. Send /start to your EXISTING Telegram bot from the destination chat.
   It cannot send a private message unless you started it.
6. Restart Gunicorn under existing GNU Screen:
   screen -r lecture-bridge
   # Press Ctrl+C to stop old gunicorn
   cd /lecture-bridge
   . .venv/bin/activate
   set -a
   . ./.env
   set +a
   gunicorn --workers 1 --threads 8 --timeout 600 --bind 0.0.0.0:8090 app:app
   # Press Ctrl+A then D to detach. Existing Ubuntu interface on 8080 remains intact.
7. Test API health:
   curl https://ubuntu-production-9d09.up.railway.app/health
   Expected: OK - lecture-bridge is running
8. Test credentials status WITHOUT UPLOADING DATA:
   curl -s -H 'Content-Type: application/json' -d '{"segments":1,"title":"Test"}' \\
     http://127.0.0.1:8090/api/relay/start
   Expected: HTTP 201 + job_id if configured (a tiny empty test job will expire).
   If HTTP 401: old code or missing RELAY_REQUIRE_UPLOAD_KEY=0.
   If HTTP 503: missing Telegram credentials.
9. Tampermonkey Create new script and paste ALL Railway_Auto_Telegram.user.js.
   Save and enable, reload lecture page; old fast snippet/other userscripts
   MUST be disabled to avoid duplicate buttons.
10. Click 'Send MP4 to Telegram'. Wait for 'Sent to Telegram'.

IMPORTANT
- This sends using the SAME bot token, via a separate Telethon session;
  your existing bot process can remain running. No need to modify that bot.
- If Telethon reports 'Could not find input entity' with a numeric user ID,
  try the destination's public @username (after starting the bot). MTProto
  requires resolvable destination access; new bot sessions may not have the
  existing bot's peer cache. A private destination without a username may
  require an existing Telegram bot session or bot-side integration instead.
- To handle large video files, this uses MTProto / Telethon, not the
  smaller cloud Telegram Bot API multipart upload limit.
  Telegram account/bot file-size limits still apply.
- PC disk does not store the MP4. Browser upload + Railway temp storage and
  CPU/network/Telegram upload do use resources. Chrome tab must remain open
  until upload finishes; server will continue sending after conversion.
- ffmpeg MP4 conversion must succeed; source HLS must have clear MPEG-TS segments.
- Railway container restarts stop GNU Screen process; re-launch manually or
  set up an actual service manager/entrypoint for production.
- With RELAY_REQUIRE_UPLOAD_KEY=0 your public Railway API is NOT secure.
  Anyone discovering the URL could trigger uploads that consume credits or
  send unwanted media to your Telegram chat. Use RELAY_MAX_JOBS=1,
  bandwidth quotas, and preferably a real authentication layer.
- Don't share BOT_TOKEN, TELEGRAM_API_HASH, or .env; rotate exposed secrets.
- Live CDN, browser CORS/CSP and Telegram deliveries were not tested here.
