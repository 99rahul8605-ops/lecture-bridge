# Lecture Cloud Queue v1 (PC OFF Pilot) + One-Command Deployment

## Kya mila hai

- **Railway Cloud Queue**: Browser mein authorized lecture kholkar player ki real, already-observed HTTPS CDN media-playlist queue mein add karo. Phir Railway (browser nahi) playlist aur unencrypted MPEG-TS VOD segments download karta hai, apne existing FFmpeg converter se MP4 banata hai, aur existing Telegram sender ko use karta hai. Ek video at a time; doosri waiting.
- **Server stats**: Waiting, Downloading, Processing, Telegram Sending, Sent, Failed, plus per-video received/total segments.
- **Telegram cleanup**: Purana bot progress-cleanup Python patch automatic install hota hai. Sirf bot ka khud ka temporary text status remove karta hai; final videos/captions delete nahi hote.
- **Single command**: Checks, tests, backup, safe patch, Python compile, Gunicorn reload (sirf jab ek master clearly mil jaye). Railway Dockerfile bhi diya hai taaki **future image builds** par cloud code automatically install ho.
- DRM/encrypted HLS, DASH/fMP4, cookies/session extraction, private-access bypass **nahi**. Server sirf `https://bunny-cdn-qbg-s6.testwave.cc` host accept karta hai. Different CDN website ke liye custom authorized integration chahiye.

## Step 1: GitHub mein ZIP ke ye files ROOT mein upload karo

`cloud_queue.py`, `Lecture_Cloud_Queue.user.js`, `setup_deploy.sh`, `one_command_deploy.py`, `install_progress_cleanup.py`, `test_progress_cleanup.py`, `test_cloud_queue.py`, aur **`Dockerfile` (existing Dockerfile replace)**. `app.py`, `relay_api.py`, Telegram bot credentials ya `Railway_Auto_Telegram.user.js` manually replace **mat** karo.

⚠️ GitHub update Railway par automatic redeploy trigger kar sakta hai. **Saare browser uploads aur Telegram sends idle hon tabhi GitHub commit ya deploy karo.**

## Step 2: Railway /lecture-bridge terminal mein ONE command

```bash
cd /lecture-bridge && git pull --ff-only && bash setup_deploy.sh
```

Script pehle offline tests chalati hai, phir `IDLE` type karne ko kahegi. Jab koi transfer active na ho tab `IDLE` likhkar Enter karo. Files auto-backup hoti hain. Reload sirf single identifiable Gunicorn master milne par; nahi toh Railway UI ka normal Redeploy/Restart karna hoga, **transfers idle hon tab**.

Terminal mein `PRIVATE Cloud Queue Key` printed hoga agar Railway `CLOUD_QUEUE_KEY` variable set nahi hai. **Key sirf Tampermonkey prompt mein paste karo, group/chat/GitHub mein nahi.**

### Railway image/volume permanence: bahut zaroori

- Railway service ka Dockerfile **same ZIP wala replace karna**. Ye image build ke waqt Python patch apply karta hai. Naye builds par patch automatic rahega; .env/GitHub mein secrets bake nahi hote.
- Railway service Variables mein ideally `CLOUD_QUEUE_KEY` (>=24-character random private key) aur `CLOUD_QUEUE_DIR=/data/lecture_cloud` rakho. Railway **Volume** `/data` par mount karo. `CLOUD_QUEUE_DIR` akela set kar dena volume banane ke barabar nahi hai.
- `CLOUD_QUEUE_KEY` ek baar Railway Variables mein set kiya hai toh wahi key Tampermonkey mein enter karo; installer secret print nahi karega. Bot token key ki jagah **kabhi mat** dena.
- Agar volume/key env set nahi: installer local private key banata hai aur current instance par cloud queue try ho sakti hai; **restart, replacement ya redeploy ke baad key/queue lose ho sakti hai**. Production PC-off queue ke liye Railway Volume + Railway Variables use karo. Railway par ephemeral disk mein signed playlist links DB mein rehte hain; GitHub par nahi.
- Existing media pipeline stores TS/MP4 in `RELAY_DATA_DIR` (often `/tmp/lecture_relay`) and cloud DB may be on volume. Railway unexpectedly restarts during processing: current video **interrupted** ho sakta hai; resume guaranteed nahi. Telegram duplicate avoid karne ke liye interrupted job auto-resend nahi hota.

## Step 3: Tampermonkey mein CLOUD script add karo

`Lecture_Cloud_Queue.user.js` ko naya **separate** userscript install karo. Old manual uploader OFF karna zaroori nahi, par PC-OFF test ke dauraan old two-tab local worker **Stop after current** rakho. Ek hi lecture ko dono queues mein mat dalo.

1. Browse tab mein chapter card se real `play.php` kholkar **Play** karo jab tak `.docx`/`.m3u8` manifest Network mein load ho. Phir Pause.
2. **☁ Add this lecture** dabao. Pehli baar private `Cloud Queue Key` prompt hoga.
3. Cloud stats mein `Waiting` / `Downloading` dikhega. Lecture 2,3 ke liye repeat. Browser back okay: server already job accept kar chuka hai.
4. **Trial:** Pehle ek lecture queue karo; verify Railway se **segment progress** badhta hai. Tab PC OFF karke Telegram mein final MP4 aaye ya nahi check karo.
5. Agar `Failed` aa jaye, Railway log aur dashboard error dekho: server ko CDN `403`/URL expired mil sakta hai. Aise source ke liye existing browser uploader use karo; `403` bypass ya signature renew nahi kiya jaata.

### Purani browser queue ke 8 videos ka kya?

**Purana `Waiting 8` automatically cloud queue mein transfer nahi ho sakta.** Local queue mein player page URLs hain; cloud queue ko actual already-loaded valid manifest chahiye. Un lectures ko browser mein dubara kholkar **☁ Add this lecture** karna padega. Naaya cloud queue panel alag server-side stats dikhayega.

## Limits aur safety

- Queue maximum **20 pending** lectures; one active cloud job; max 10,000 segments; relay ka video-size/disk cap apply.
- VOD (`#EXT-X-ENDLIST`) + clear unencrypted MPEG-TS only; `#EXT-X-KEY` encryption, DASH/DRM, signed URL refresh, CORS/session copying, redirect, SSRF/private IP blocked.
- Doosri website/CDN se video fetch karne ki koshish mat karo; additional host supported nahi.
- Signed playlist data sirf waiting/download ke waqt private SQLite DB mein stored hota hai; successful/failed/interrupted jobs ka playlist field clear hota hai.
- CDN source expires ho sakta hai, especially long queue; 8–10 lectures **guaranteed nahi**. Agar source Railway se accessible nahi hua toh PC-OFF solution is site ke liye kaam nahi karega.
- Telegram send is existing `relay_api.py` sender; credentials same; `bot.py` untouched. Temporary text cleanup new jobs par; purane text messages manually delete.
- Browser/Railway live end-to-end verification abhi **pending**. Offline tests network/Telegram ko contact nahi karte.

## Troubleshooting

- `401 Cloud queue key not configured or invalid`: Setup output ka CLOUD KEY paste karo (BOT_TOKEN nahi), Railway env var / keyfile persist check karo.
- `403 CDN`: Server has no authorized source access. Browser upload method use karo.
- `Waiting` endlessly: Gunicorn worker active hai? Railway log mein CloudQueue errors dekho; old uploader job busy ho toh pehle finish hone do.
- `Failed: Encrypted/DRM playlist`: Not supported.
- `interrupted`: Railway restarted. Verify Telegram group before manually retrying; this pilot intentionally won't auto-resend.
- Docker auto-deploy ke liye Railway service correct Dockerfile se build ho rahi hai ya nahi verify karo. Gunicorn workers **1** preferred.
