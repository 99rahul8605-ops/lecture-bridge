# One-click Telegram Uploader — Download + Upload Lane Statistics v2.5

Ye **sirf browser Tampermonkey UI update** hai. Ubuntu VPS ka `relay_api.py`, `.env`, Gunicorn, Cloudflare Tunnel aur Telegram sending unchanged rahenge.

## Install

1. Chrome Tampermonkey -> **Lecture Auto Send to Telegram** -> Edit.
2. Is package ki `Railway_Auto_Telegram.user.js` file se existing script **poora replace** karo, Ctrl+S save karo.
3. Duplicate `Lecture Auto Send to Telegram` copies disable karo.
4. `vidcloud.eu.org/play.php` player reload karo.
5. 1, 2, ya 3 upload lanes select karo aur **Send MP4 to Telegram** button dabao.
6. Stats panel button ke neeche dikhega.

## Stats panel

- `CDN download: X/N` — kitne segment fetch aur ArrayBuffer mein receive ho chuke hain.
- `VPS upload: X/N` — kitne segment VPS ne HTTP success ke saath receive kiye.
- `MiB` — un complete segments ka total size.
- `avg MiB/s` — job start se ab tak elapsed wall-time par based average *completed bytes throughput*. Ye current network link ki instant speed nahi hai.
- `Active now: ↓D / ↑U` — kitni fetch / POST requests filhaal in progress hain.
- `peak ↓P` — ek samay mein sabse zyada concurrent download requests.
- `L1`, `L2`, `L3` — lane status, current segment number, most recent finished segment ke download aur upload transfer rates.

**Stats segment complete hone ke baad update hoti hain.** Existing browser fetch `arrayBuffer()` method download progress byte-by-byte expose nahi karta; live byte-counting enable karne ke liye alag streaming-reader changes chahiye jo yahan intentionally nahi kiye.

## Important

- Script ka server base `https://upload.bingolink.site` hai. Agar tumhara URL alag hai, top par `let RELAY = ...` apne HTTPS bridge endpoint se badalna.
- API path, HLS parsing, segment indexing, finalization, MP4 conversion aur Telegram delivery behavior untouched.
- 2/3 lanes **faster hone ki guarantee nahi**. A/B speed compare karo.
- Existing security, encryption, authorized-access checks intact hain; no license / DRM bypass.
- **Koi GitHub pull, Railway/VPS restart, Python file replacement zaroori nahi** (jab tak VPS pe compatible parallel-lane relay API pehle se installed hai).

## Offline verification

- `node --check Railway_Auto_Telegram.user.js`
- `node test_stats_mock.cjs`
- 1/2/3 lane mock transfers: concurrent fetches and POSTs, correct original segment indexes, successful finish.
- Failure case: relay job cancelled and no finalize.
- Not tested end-to-end against your live browser/CDN/VPS/Telegram.
