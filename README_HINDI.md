# Cloud Queue — NO KEY v1.0.3

**Ab Cloud Queue mein koi key nahi hai.** Backend GET `/api/cloud/queue` aur POST `/api/cloud/enqueue` ko bina `X-Cloud-Queue-Key` accept karta hai. Tampermonkey mein `Change Key` button, key prompt aur key header sab hata diye gaye hain.

## Install (Railway + GitHub)

1. **Pehle sabhi browser uploads, TS→MP4 conversion aur Telegram sends finish hone do.** Deploy/restart running job interrupt kar sakta hai.
2. Is ZIP ki 6 files `cloud_queue.py`, `one_command_deploy.py`, `test_cloud_queue.py`, `test_nokey_public_smoke.py`, `setup_deploy.sh`, `Lecture_Cloud_Queue.user.js` GitHub repo `lecture-bridge` ke **root** mein upload karo aur **Commit** karo. Subfolder mein mat daalna. Railway Dockerfile pehle wala hi hai; use change karne ki zaroorat nahi. Agar Railway auto-redeploy enabled hai, uske successful hone ka wait karo. Local tests + controlled reload ke liye, jab saare transfers IDLE hon, terminal mein **ek command** `cd /lecture-bridge && git pull --ff-only && bash setup_deploy.sh` chalao; `IDLE` maange toh sirf sach mein sab jobs khatam hone par type karo. Docker deploy ke baad ek extra HUP usually zaroori nahi.
3. Existing Railway terminal se verify karo (single command):

   ```bash
   curl -sS -o /dev/null -w 'Cloud API HTTP: %{http_code}\n' --max-time 15 https://ubuntu-production-c8d8.up.railway.app/api/cloud/queue
   ```

   **200 = ready (public API).** 401 = old key-enforcing backend still running; Railway redeploy/latest code check. 404 = wrong service/domain/port or cloud routes unavailable. 000 = network error.
4. Tampermonkey mein **existing Cloud Queue** script replace karo (duplicate mat banana), `Ctrl+S`, page refresh. Naye version `1.0.3` mein **Change Key button nahi rahega**. `↻ Refresh stats` kaam karna chahiye. Old Local Two-Tab Queue script alag hai, use change nahi karna.
5. Lecture kholo → authorized unencrypted playlist load hone do → pause → **☁ Add this lecture**. Pehle ek video trial karo; CDN Railway ko access na de toh cloud processing fail ho sakti hai, key removal usko fix nahi karega.

## Important security tradeoff

**Public endpoint kisi ko bhi lecture titles/stats dekhne aur Railway se jobs enqueue karwane dega.** Railway resources aur Telegram group par unwanted traffic aa sakta hai. Existing 20 pending queue limit, single processing worker, CDN-host validation, URL/public-IP checks, TS and DRM safeguards unchanged hain. Railway `CLOUD_QUEUE_KEY` variable ab istemal nahi hoga; optionally dashboard se delete kar sakte ho. Public URL ko share mat karna, lekin URL guess/discover kiya ja sakta hai. Agar spam ho toh key protection wapas enable karna better hai.

Current Railway public domain used in JS: `https://ubuntu-production-c8d8.up.railway.app`. Tampermonkey `@connect` bhi isi domain par hai. Old `ubuntu-production-9d09...` domain use mat karna.

`test_cloud_queue.py` offline testing hai; browser-to-Railway/CDN-to-Telegram live test abhi nahi hua. Railway setup/deploy ka previous one-command script `bash setup_deploy.sh` ab bhi applicable hai when idle; naya installer koi key generate nahi karega. Existing uploads ke dauran isse mat chalana.
