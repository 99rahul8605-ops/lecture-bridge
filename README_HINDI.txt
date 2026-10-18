STANDALONE RAILWAY LECTURE BRIDGE (Chrome -> Railway -> MP4 link -> existing TG bot)

Only for media you own or are authorized to export. Never attempts to bypass CDN signatures/DRM.
Do NOT deploy this as a second Telegram bot! It runs ONLY Flask API.
It does NOT need your Telegram BOT_TOKEN.
It does NOT use your AWS VPS.

1. Install Railway CLI on WINDOWS PC (requires Node.js):
   npm install -g @railway/cli
   railway login
2. Extract ZIP. In PowerShell cd into folder Lecture_Bridge_Standalone.
3. Link to your existing Railway project (select from menu):
   railway link
4. Create a NEW empty service named lecture-bridge:
   railway add --service lecture-bridge
5. Make new random upload key on your PC:
   $bytes = New-Object byte[] 32
   [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
   $secret = [Convert]::ToBase64String($bytes)
6. Configure variables FOR THE NEW SERVICE only:
   railway variable set PORT=8080 RELAY_ALLOWED_ORIGIN=https://vidcloud.eu.org RELAY_MAX_JOBS=1 RELAY_MAX_GB=0.7 RELAY_TTL_HOURS=2 --service lecture-bridge
   railway variable set "RELAY_UPLOAD_KEY=$secret" --service lecture-bridge
   # If Railway Paid with enough storage, use RELAY_MAX_GB=1.7 or greater.
   # NOTE on Free plan the file may need >2x size (input TS + MP4), max ephemeral storage 1 GB.
7. Deploy:
   railway up --service lecture-bridge
8. Add HTTP domain (on 8080, NOT TCP proxy):
   railway domain --service lecture-bridge --port 8080
9. Copy the NEW https://xxxx.up.railway.app shown by Railway.
10. Set new domain variable; replace the example with YOUR actual new bridge domain:
   railway variable set RELAY_PUBLIC_BASE_URL=https://YOUR-NEW-DOMAIN.up.railway.app --service lecture-bridge
   railway redeploy --service lecture-bridge --yes
11. Visit https://YOUR-NEW-DOMAIN.up.railway.app/health; should read OK - lecture-bridge is running.
12. In Chrome DevTools Snippets run Railway_Link_Snippet.js (NOT old Downloader snippet).
    Click Create Telegram MP4 link; enter NEW bridge domain; then enter RELAY_UPLOAD_KEY.
    Allow browser transfer to complete; final MP4 URL appears. Send that URL to your EXISTING Telegram bot.

IMPORTANT NOTES:
- Existing Ubuntu login website stays unchanged. Do not use its public domain for the bridge.
- Browser must be legally allowed to export the whole media stream.
- Some sites forbid cross-origin uploads via CSP; this tool does not bypass that policy.
- Browser sends actual video bytes to Railway. PC does not save the lecture file; internet/Railway temporary disk and CPU are still used.
- The URL CANNOT be issued before the upload/conversion is done, and expires after TTL; redeploy/restart invalidates it.
- On Railway Free plan, storage is especially limited; multi-hour lecture may not fit.
- Service network egress billed for MP4 download by bot; bot forwarding to Telegram may add more Railway egress.
- Browser page scripts may potentially read prompts entered in that page. Use a separate limited key, rotate it, never use bot tokens.
- Keep max 1 active job to reduce cost. Monitor Railway usage and set a workspace hard spending limit if needed.

DIAGNOSTICS
  railway logs --service lecture-bridge
  railway usage
  railway usage limit status

SECURITY
  Do not send secret upload key in chat. Anyone with a valid temporary MP4 download URL can access it before expiry.
