"""Authenticated, opt-in browser-to-Railway upload bridge for authorized MPEG-TS media.

No external video URL is fetched by the server. Browser sends video bytes it is
already authorized to read. No DRM support, signature forging, or cookie replay.
"""
from __future__ import annotations

import hmac
import html
import json
import urllib.parse
import urllib.request
import urllib.error
import asyncio
import re
import logging
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from flask import jsonify, request, send_file

log = logging.getLogger(__name__)
ROOT = Path(os.environ.get("RELAY_DATA_DIR", "/tmp/lecture_relay"))
ROOT.mkdir(parents=True, exist_ok=True)
MAX_TOTAL = int(float(os.environ.get("RELAY_MAX_GB", "1.7")) * 1024**3)
MAX_CHUNK = int(os.environ.get("RELAY_MAX_SEGMENT_MB", "128")) * 1024**2
TTL_SECONDS = int(float(os.environ.get("RELAY_TTL_HOURS", "3")) * 3600)
MAX_JOBS = max(1, int(os.environ.get("RELAY_MAX_JOBS", "1")))
# Concurrent HTTP uploads run in Gunicorn threads. Keep one Gunicorn worker:
# the job registry and Telegram progress are currently process-local.
MAX_PARALLEL_SEGMENTS = max(1, min(12, int(os.environ.get("RELAY_PARALLEL_UPLOADS", "8"))))
PROGRESS_ENABLED = os.environ.get("TELEGRAM_PROGRESS_ENABLED", "1").strip().lower() not in ("0", "false", "no")
PROGRESS_INTERVAL = max(5, min(60, int(os.environ.get("TELEGRAM_PROGRESS_INTERVAL", "7"))))
# Abandoned uploads should not occupy the single-job slot for hours.
UPLOAD_IDLE_SECONDS = int(
    max(1, min(120, float(os.environ.get("RELAY_UPLOAD_IDLE_MINUTES", "15")))) * 60
)
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _allowed_origin() -> str:
    return os.environ.get("RELAY_ALLOWED_ORIGIN", "https://vidcloud.eu.org").rstrip("/")


def _authorized() -> bool:
    # User-opted private-key-free mode. NOT secure for public Internet: any
    # HTTP client can upload, and CORS is only a browser-side safeguard.
    if os.environ.get("RELAY_REQUIRE_UPLOAD_KEY", "1").strip().lower() in ("0", "false", "no"):
        return True
    key = os.environ.get("RELAY_UPLOAD_KEY", "")
    provided = request.headers.get("X-Relay-Key", "")
    return len(key) >= 24 and hmac.compare_digest(key, provided)


def _error(message: str, status: int):
    return jsonify(error=message), status


def _job(job_id: str):
    with _jobs_lock:
        return _jobs.get(job_id)


def _reap_abandoned_uploads():
    """Reclaim idle uploads only; never touch conversion or Telegram sending.

    A nonblocking job lock prevents deleting files during a segment write.
    """
    now = time.time()
    folders = []
    with _jobs_lock:
        for job_id, job in list(_jobs.items()):
            if job["status"] != "uploading" or job.get("inflight"):
                continue
            if now - job.get("updated", job["created"]) <= UPLOAD_IDLE_SECONDS:
                continue
            if not job["lock"].acquire(blocking=False):
                continue
            try:
                if (job["status"] == "uploading" and not job.get("inflight") and
                        now - job.get("updated", job["created"]) > UPLOAD_IDLE_SECONDS):
                    job["status"] = "expired"
                    _jobs.pop(job_id, None)
                    folders.append(job["folder"])
            finally:
                job["lock"].release()
    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)
    return len(folders)


def _purge_loop():
    while True:
        time.sleep(30)
        _reap_abandoned_uploads()
        current = time.time()
        folders = []
        with _jobs_lock:
            expired = [key for key, job in _jobs.items()
                       if job["status"] in ("sent", "failed", "cancelled")
                       and current - job.get("updated", job["created"]) > TTL_SECONDS]
            for key in expired:
                job = _jobs.pop(key)
                folders.append(job["folder"])
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)



def _bot_api(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Telegram Bot API is only used for editing progress text messages.

    MTProto upload stays in Telethon, so there is no duplicate Telethon
    session or interference with the running Telegram bot.
    """
    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Bot token not configured")
    url = f"https://api.telegram.org/bot{token}/{method}"
    request_data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=request_data, method="POST")
    # Never log the URL, because it contains the bot token.
    with urllib.request.urlopen(req, timeout=9) as response:
        obj = json.load(response)
    if not obj.get("ok"):
        raise RuntimeError(f"Telegram Bot API {method} failed")
    return obj["result"]


def _progress_text(job: dict[str, Any]) -> str:
    title = job.get("title", "Lecture")
    stage = job["status"]
    expected = max(1, job.get("expected", 1))
    received = job.get("received_count", job.get("next", 0))
    uploaded = job.get("bytes", 0) + job.get("inflight_bytes", 0)
    uploaded_mib = uploaded / 1048576
    elapsed = max(1.0, time.time() - job.get("created", time.time()))
    rate = uploaded_mib / elapsed
    header = f"🎬 {title}"
    if stage == "uploading":
        progress_pct = int(received * 100 / expected)
        return (f"{header}\n\n⬇️ Download + Railway upload: {received}/{expected} segments ({progress_pct}%)"
                f"\n📦 {uploaded_mib:.1f} MiB received • {rate:.2f} MiB/s"
                "\n⏳ Chrome tab khula rakho")
    if stage == "processing":
        return (f"{header}\n\n✅ Railway upload: {expected}/{expected} segments"
                f"\n🎞️ Converting to MP4… ({int(elapsed)}s elapsed)")
    if stage == "telegram_sending":
        current = max(0, int(job.get("telegram_uploaded", 0)))
        total = max(0, int(job.get("telegram_total", 0)))
        send_elapsed = max(1, time.time() - job.get("telegram_started", time.time()))
        mibs = (current / 1048576) / send_elapsed
        pct = min(100, round(100 * current / total)) if total else 0
        remaining = int(max(0, total - current) / max(current / send_elapsed, 1)) if total else None
        eta = f" • ETA ~{remaining}s" if remaining is not None and current > 0 else ""
        return (f"{header}\n\n✅ Download + Railway conversion complete"
                f"\n⬆️ Telegram upload: {pct}% ({current/1048576:.1f}/{total/1048576:.1f} MiB)"
                f"\n🚀 {mibs:.2f} MiB/s{eta}")
    if stage == "sent":
        return f"{header}\n\n✅ Telegram par MP4 successfully send ho gayi!"
    if stage == "failed":
        return f"{header}\n\n❌ Failed: {str(job.get('error', 'Unknown error'))[:290]}"
    if stage in ("expired", "cancelled"):
        return f"{header}\n\n⛔ Upload {stage}; job slot released."
    return f"{header}\n\n⏳ Processing…"


def _progress_notifier(job: dict[str, Any]) -> None:
    """One Telegram message per job, edited at a safe, throttled interval.

    Failing to send progress must never abort a video transfer.
    """
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        return
    message_id = None
    last_message = None
    last_edit_at = 0.0
    last_attempt_at = 0.0
    final_states = {"sent", "failed", "expired", "cancelled"}
    while True:
        state = job["status"]
        text = _progress_text(job)
        now = time.monotonic()
        if text != last_message and now - last_attempt_at >= PROGRESS_INTERVAL:
            last_attempt_at = now
            try:
                if message_id is None:
                    result = _bot_api("sendMessage", {"chat_id": chat_id, "text": text,
                                                      "disable_web_page_preview": "true"})
                    message_id = result["message_id"]
                elif now - last_edit_at >= PROGRESS_INTERVAL:
                    _bot_api("editMessageText", {"chat_id": chat_id,
                                                  "message_id": message_id, "text": text})
                    last_edit_at = time.monotonic()
                else:
                    time.sleep(1)
                    continue
                last_message = text
            except Exception as exc:
                # Do not expose tokens or message text to logs.
                log.warning("Telegram progress notification failed (%s)", type(exc).__name__)
        if state in final_states:
            # If a final update was throttled, wait and edit it once before exiting.
            if last_message == text or message_id is None:
                return
        time.sleep(2)


def _telegram_setup_error() -> str | None:
    if not (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")):
        return "Missing BOT_TOKEN in server .env"
    if not os.getenv("TELEGRAM_API_ID", "").isdigit():
        return "Missing/invalid TELEGRAM_API_ID in server .env"
    if not os.getenv("TELEGRAM_API_HASH"):
        return "Missing TELEGRAM_API_HASH in server .env"
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not (re.fullmatch(r"-?\d+", chat) or re.fullmatch(r"@[a-zA-Z0-9_]{5,32}", chat)):
        return "Missing/invalid TELEGRAM_CHAT_ID (numeric ID or @username) in server .env"
    return None


def _clean_title(raw: Any) -> str:
    """Preserve page video_name for captions; don't trust it for filesystem paths."""
    if not isinstance(raw, str):
        return "Lecture"
    title = re.sub(r"[\x00-\x1f\x7f]", " ", raw).strip()
    return (title[:140].strip() or "Lecture")


def _safe_filename(title: str) -> str:
    base = re.sub(r'[\\/:*?"<>|]', "-", title)
    base = re.sub(r"\s+", " ", base).strip(" .-")
    return (base[:115].strip(" .-") or "Lecture") + ".mp4"


def _probe_video(path: Path) -> dict[str, Any]:
    """Metadata from the actual MP4, with container-duration fallback.

    Telegram video attributes need correct width/height/duration; setting only
    supports_streaming=True is not enough for consistent native video preview.
    """
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
         "-show_format", str(path)], capture_output=True, text=True, timeout=25,
    )
    if result.returncode:
        raise RuntimeError("ffprobe could not inspect final MP4")
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Invalid ffprobe metadata") from exc
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("MP4 contains no video stream")
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    duration = video.get("duration") or data.get("format", {}).get("duration") or 0
    try:
        duration = max(0, int(round(float(duration))))
    except (ValueError, TypeError, OverflowError):
        duration = 0
    return {
        "width": max(1, int(video.get("width") or 1)),
        "height": max(1, int(video.get("height") or 1)),
        "duration": duration,
        "video_codec": str(video.get("codec_name") or ""),
        "audio_codec": str(audio[0].get("codec_name") or "") if audio else "",
        "has_audio": bool(audio),
    }


def _extract_thumbnail(video_path: Path, folder: Path, duration: int) -> Path | None:
    """Use the original bot's FFmpeg-frame thumbnail strategy (<=320px JPG)."""
    thumb = folder / "thumb.jpg"
    # Seek to actual content, rather than blindly to 5 s on a short lecture.
    times = [min(5.0, duration * 0.15) if duration else 2.0, 1.0, 0.2, 0.0]
    for pos in dict.fromkeys(times):
        thumb.unlink(missing_ok=True)
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                 "-ss", str(max(0, pos)), "-i", str(video_path),
                 "-frames:v", "1", "-vf",
                 "scale=320:320:force_original_aspect_ratio=decrease",
                 "-q:v", "4", str(thumb)],
                capture_output=True, timeout=45,
            )
            if (result.returncode == 0 and thumb.is_file()
                    and 1000 < thumb.stat().st_size <= 200 * 1024):
                return thumb
        except (OSError, subprocess.TimeoutExpired):
            pass
    thumb.unlink(missing_ok=True)
    return None


def _telegram_caption(title: str, meta: dict[str, Any], size: int) -> str:
    # HTML escaping avoids malformed Telegram captions if lecture titles contain markup.
    seconds = int(meta["duration"])
    time_str = f"{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}"
    return (f"🎬 <b>{html.escape(title)}</b>\n"
            f"📦 {size / 1048576:.1f} MiB  •  ⏱ {time_str}\n"
            f"📺 {meta['width']}×{meta['height']}  •  MP4")


async def _telegram_send(job: dict[str, Any], file_path: Path):
    """Send real Telegram VIDEO (thumbnail + video metadata), not generic document.

    Modelled after the original downloader bot's tl_send() logic. Progress
    callback remains compatible with Telegram live progress message editing.
    """
    from telethon import TelegramClient
    from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeFilename

    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    chat_text = os.environ["TELEGRAM_CHAT_ID"].strip()
    chat_id = int(chat_text) if re.fullmatch(r"-?\d+", chat_text) else chat_text
    # Use its own Telethon session, not the running downloader bot's session.
    session = str(ROOT / "lecture_relay_bot")
    client = TelegramClient(session, api_id, api_hash)
    meta = _probe_video(file_path)
    thumb = _extract_thumbnail(file_path, job["folder"], meta["duration"])
    job["telegram_uploaded"] = 0
    job["telegram_total"] = file_path.stat().st_size
    job["telegram_started"] = time.time()
    job["media_duration"] = meta["duration"]
    job["media_width"] = meta["width"]
    job["media_height"] = meta["height"]

    def on_progress(current, total):
        job["telegram_uploaded"] = int(current)
        job["telegram_total"] = int(total)

    attributes = [
        DocumentAttributeVideo(
            duration=meta["duration"], w=meta["width"], h=meta["height"],
            supports_streaming=True,
        ),
        DocumentAttributeFilename(file_name=file_path.name),
    ]
    try:
        await client.start(bot_token=token)
        await client.send_file(
            chat_id,
            str(file_path),
            caption=_telegram_caption(job["title"], meta, file_path.stat().st_size),
            parse_mode="html",
            attributes=attributes,
            thumb=str(thumb) if thumb else None,
            mime_type="video/mp4",
            supports_streaming=True,
            force_document=False,
            part_size_kb=512,
            progress_callback=on_progress,
        )
    finally:
        try:
            await client.disconnect()
        finally:
            if thumb:
                thumb.unlink(missing_ok=True)


def _convert(job_id: str):
    job = _job(job_id)
    if not job:
        return
    src = job["folder"] / "input.ts"
    dest = job["folder"] / _safe_filename(job["title"])
    base = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-fflags", "+genpts", "-f", "mpegts", "-i", str(src)]
    try:
        source_meta = _probe_video(src)
        # Preserve speed/quality whenever H.264/AAC is already present; otherwise
        # normalize to Telegram-friendly AVC/AAC. Add silent AAC when audio is absent.
        need_video_encode = source_meta["video_codec"] != "h264"
        if source_meta["has_audio"]:
            maps = ["-map", "0:v:0", "-map", "0:a:0"]
        else:
            base += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
            maps = ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
        video_opts = (["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
                      if need_video_encode else ["-c:v", "copy"])
        audio_opts = (["-c:a", "copy"] if source_meta["audio_codec"] == "aac"
                      else ["-c:a", "aac", "-b:a", "128k"])
        common = ["-movflags", "+faststart", "-f", "mp4", str(dest)]
        methods = [base + maps + video_opts + audio_opts + common]
        # If remux fails because of timing or nonstandard streams, re-encode as fallback.
        if not need_video_encode:
            methods.append(base + maps + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
                           + ["-c:a", "aac", "-b:a", "128k"] + common)
        err = ""
        converted = False
        for cmd in methods:
            try:
                dest.unlink(missing_ok=True)
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45 * 60)
                if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                    # Validate the final file, not merely the FFmpeg exit code.
                    final_meta = _probe_video(dest)
                    if final_meta["video_codec"] != "h264" or final_meta["audio_codec"] != "aac":
                        raise RuntimeError("Output is not Telegram-compatible H.264/AAC MP4")
                    job["bytes_output"] = dest.stat().st_size
                    converted = True
                    break
                err = (proc.stderr or f"ffmpeg exited {proc.returncode}")[-500:]
            except (subprocess.TimeoutExpired, OSError, RuntimeError) as exc:
                err = str(exc)
        if not converted:
            raise RuntimeError("MP4 conversion failed: " + err)
        src.unlink(missing_ok=True)
        job["status"] = "telegram_sending"
        job["updated"] = time.time()
        asyncio.run(_telegram_send(job, dest))
        job["telegram_uploaded"] = job.get("telegram_total", 0)
        job["status"] = "sent"
        job["updated"] = time.time()
        dest.unlink(missing_ok=True)
    except Exception as exc:
        job["status"] = "failed"
        job["updated"] = time.time()
        job["error"] = ("Telegram delivery / conversion failed: " + str(exc))[:500]
        log.exception("Lecture transfer failed for %s", job_id)


def install_relay_routes(app):
    """Attach API to existing Flask health app; no second HTTP server required."""
    if os.environ.get("RELAY_UPLOAD_KEY", "") and len(os.environ["RELAY_UPLOAD_KEY"]) < 24:
        log.warning("RELAY_UPLOAD_KEY is too short; relay will reject requests")

    @app.after_request
    def _cors(res):
        if request.path.startswith("/api/relay/") and request.headers.get("Origin") == _allowed_origin():
            res.headers["Access-Control-Allow-Origin"] = _allowed_origin()
            res.headers["Vary"] = "Origin"
            res.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            res.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Relay-Key"
            res.headers["Access-Control-Max-Age"] = "600"
        res.headers["X-Content-Type-Options"] = "nosniff"
        return res

    @app.route("/api/relay/<path:tail>", methods=["OPTIONS"])
    def relay_preflight(tail):
        if request.headers.get("Origin") != _allowed_origin():
            return _error("Origin not allowed", 403)
        return "", 204

    @app.post("/api/relay/start")
    def relay_start():
        if not _authorized():
            return _error("Invalid upload key or RELAY_UPLOAD_KEY not configured", 401)
        error = _telegram_setup_error()
        if error:
            return _error(error, 503)
        body = request.get_json(silent=True) or {}
        count = body.get("segments")
        if type(count) is not int or not (1 <= count <= 10000):
            return _error("Invalid segment count", 400)
        # Free abandoned upload slots immediately, instead of waiting for the timer.
        _reap_abandoned_uploads()
        with _jobs_lock:
            if sum(job["status"] in ("uploading", "processing", "telegram_sending") for job in _jobs.values()) >= MAX_JOBS:
                active = next(
                    (job["status"] for job in _jobs.values()
                     if job["status"] in ("uploading", "processing", "telegram_sending")), "busy"
                )
                return jsonify(error=f"Job busy ({active}). Wait for it to finish, or cancel an abandoned upload.",
                               active_status=active), 429
            job_id = secrets.token_urlsafe(18)
            folder = ROOT / job_id
            folder.mkdir(parents=True, exist_ok=False)
            (folder / "segments").mkdir()
            _jobs[job_id] = {"folder": folder, "created": time.time(), "updated": time.time(), "status": "uploading",
                             "expected": count, "next": 0, "bytes": 0, "inflight_bytes": 0,
                             "inflight": set(), "received": set(), "received_count": 0,
                             "title": _clean_title(body.get("title")),
                             "download_token": secrets.token_urlsafe(24), "lock": threading.Lock()}
        if PROGRESS_ENABLED:
            threading.Thread(target=_progress_notifier, args=(_jobs[job_id],), daemon=True).start()
        return jsonify(job_id=job_id, status="uploading"), 201

    @app.post("/api/relay/<job_id>/cancel")
    def relay_cancel(job_id):
        """Job holder can cancel an upload, not conversion or Telegram sending."""
        if not _authorized():
            return _error("Invalid upload key", 401)
        folder = None
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job:
                return _error("Unknown or expired job", 404)
            if not job["lock"].acquire(blocking=False):
                return _error("Segment upload in progress; try again", 409)
            try:
                if job["status"] != "uploading":
                    return _error("Cannot cancel a job already converting or sending", 409)
                if job.get("inflight"):
                    return _error("Segment requests still in progress; retry shortly", 409)
                job["status"] = "cancelled"
                _jobs.pop(job_id, None)
                folder = job["folder"]
            finally:
                job["lock"].release()
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
        return jsonify(status="cancelled"), 200

    @app.post("/api/relay/<job_id>/segment/<int:index>")
    def relay_segment(job_id, index):
        """Parallel, out-of-order TS segments, serialized *only* for final append.

        Each request has a distinct .part file; counters and append pointer are
        protected by the job lock. No writes to input.ts occur out of order.
        """
        if not _authorized():
            return _error("Invalid upload key", 401)
        job = _job(job_id)
        if not job:
            return _error("Unknown or expired job", 404)
        content_length = request.content_length
        if content_length is not None and (content_length <= 0 or content_length > MAX_CHUNK):
            return _error("Segment too large or empty", 413)
        if index < 0 or index >= job["expected"]:
            return _error("Segment index out of range", 400)

        # Reserve an index, preventing concurrent duplicates. Do NOT hold this
        # lock during a network upload or it would serialize all 6 requests.
        with job["lock"]:
            if job["status"] != "uploading":
                return _error("Job is not uploading", 409)
            if index in job["inflight"] or index in job["received"]:
                return _error("Duplicate segment", 409)
            if len(job["inflight"]) >= MAX_PARALLEL_SEGMENTS:
                return _error("Too many concurrent segment requests", 429)
            job["inflight"].add(index)
            job["updated"] = time.time()

        part = job["folder"] / "segments" / f"{index:06d}.part"
        ready = job["folder"] / "segments" / f"{index:06d}.ts"
        total = 0
        held = 0
        committed = False
        try:
            if shutil.disk_usage(ROOT).free < 250 * 1024**2:
                return _error("Railway disk nearly full", 507)
            with part.open("xb") as incoming:
                first = True
                while True:
                    chunk = request.stream.read(1024 * 1024)
                    if not chunk:
                        break
                    if first and index == 0 and (len(chunk) < 189 or
                                                    chunk[0] != 0x47 or chunk[188] != 0x47):
                        return _error("Not MPEG-TS; fMP4/DRM unsupported", 415)
                    first = False
                    with job["lock"]:
                        if (total + len(chunk) > MAX_CHUNK or
                                job["bytes"] + job["inflight_bytes"] + len(chunk) > MAX_TOTAL):
                            return _error("Job/segment size limit exceeded", 413)
                        job["inflight_bytes"] += len(chunk)
                        job["updated"] = time.time()
                        held += len(chunk)
                    incoming.write(chunk)
                    total += len(chunk)
            if total == 0:
                return _error("Empty segment", 400)
            part.replace(ready)

            with job["lock"]:
                if job["status"] != "uploading":
                    return _error("Job closed while uploading", 409)
                # Account for this segment exactly once; shared in-flight byte
                # budget is released after acceptance.
                job["inflight_bytes"] -= held
                held = 0
                job["bytes"] += total
                job["received"].add(index)
                job["received_count"] = len(job["received"])

                # Append only the contiguous prefix. Out-of-order segments
                # remain on disk until missing earlier ones arrive.
                with (job["folder"] / "input.ts").open("ab") as output:
                    while job["next"] in job["received"]:
                        segment_file = job["folder"] / "segments" / f"{job['next']:06d}.ts"
                        with segment_file.open("rb") as segment_data:
                            shutil.copyfileobj(segment_data, output, 1024 * 1024)
                        segment_file.unlink(missing_ok=True)
                        job["next"] += 1
                job["updated"] = time.time()
                committed = True
                return jsonify(received=job["received_count"], total=job["expected"],
                               bytes=job["bytes"], contiguous=job["next"])
        except OSError:
            with job["lock"]:
                job["status"] = "failed"
                job["error"] = "Disk write failed during segment transfer"
                job["updated"] = time.time()
            return _error("Disk write failed", 507)
        finally:
            with job["lock"]:
                job["inflight"].discard(index)
                if held:
                    job["inflight_bytes"] = max(0, job["inflight_bytes"] - held)
            part.unlink(missing_ok=True)
            if not committed:
                ready.unlink(missing_ok=True)

    @app.post("/api/relay/<job_id>/finish")
    def relay_finish(job_id):
        if not _authorized():
            return _error("Invalid upload key", 401)
        job = _job(job_id)
        if not job:
            return _error("Unknown/expired job", 404)
        with job["lock"]:
            if job["status"] != "uploading":
                return _error("Job is not uploading", 409)
            if (job["received_count"] != job["expected"] or
                    job["next"] != job["expected"] or job["inflight"]):
                return _error(f"Incomplete upload: {job['received_count']}/{job['expected']}", 409)
            job["status"] = "processing"
            job["updated"] = time.time()
            threading.Thread(target=_convert, args=(job_id,), daemon=True).start()
        return jsonify(status="processing"), 202

    @app.get("/api/relay/<job_id>/status")
    def relay_status(job_id):
        if not _authorized():
            return _error("Invalid upload key", 401)
        job = _job(job_id)
        if not job:
            return _error("Unknown/expired job", 404)
        res = {"status": job["status"], "received": job.get("received_count", job["next"]), "total": job["expected"],
               "bytes": job["bytes"], "title": job["title"]}
        if job["status"] == "telegram_sending":
            res["telegram_uploaded"] = job.get("telegram_uploaded", 0)
            res["telegram_total"] = job.get("telegram_total", 0)
        if job["status"] == "sent":
            res["message"] = "Video sent to your Telegram chat"
        if job["status"] == "failed":
            res["error"] = job.get("error", "Conversion failed")
        return jsonify(res)

    threading.Thread(target=_purge_loop, daemon=True).start()
