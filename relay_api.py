"""Authenticated, opt-in browser-to-Railway upload bridge for authorized MPEG-TS media.

No external video URL is fetched by the server. Browser sends video bytes it is
already authorized to read. No DRM support, signature forging, or cookie replay.
"""
from __future__ import annotations

import hmac
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


def _purge_loop():
    while True:
        time.sleep(120)
        current = time.time()
        with _jobs_lock:
            expired = [key for key, job in _jobs.items()
                       if job["status"] not in ("processing", "telegram_sending")
                       and current - job.get("updated", job["created"]) > TTL_SECONDS]
            for key in expired:
                job = _jobs.pop(key)
                shutil.rmtree(job["folder"], ignore_errors=True)


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


async def _telegram_send(job: dict[str, Any], file_path: Path):
    """Send large MP4 directly via the bot's MTProto session (no Bot API 50MB HTTP limit)."""
    # Import lazily: backend may still answer health checks before Telegram is configured.
    from telethon import TelegramClient
    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    chat_text = os.environ["TELEGRAM_CHAT_ID"].strip()
    chat_id = int(chat_text) if re.fullmatch(r"-?\d+", chat_text) else chat_text
    # Dedicated session file; don't share a .session file with a running bot.
    session = str(ROOT / "lecture_relay_bot")
    client = TelegramClient(session, api_id, api_hash)
    try:
        await client.start(bot_token=token)
        await client.send_file(
            chat_id,
            str(file_path),
            caption=job["title"],
            supports_streaming=True,
            force_document=False,
        )
    finally:
        await client.disconnect()


def _convert(job_id: str):
    job = _job(job_id)
    if not job:
        return
    src = job["folder"] / "input.ts"
    dest = job["folder"] / _safe_filename(job["title"])
    base = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "mpegts", "-i", str(src)]
    methods = [
        base + ["-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-movflags", "+faststart", str(dest)],
        base + ["-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)],
    ]
    try:
        err = ""
        converted = False
        for cmd in methods:
            try:
                dest.unlink(missing_ok=True)
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45 * 60)
                if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                    job["bytes_output"] = dest.stat().st_size
                    converted = True
                    break
                err = (proc.stderr or f"ffmpeg exited {proc.returncode}")[-500:]
            except (subprocess.TimeoutExpired, OSError) as exc:
                err = str(exc)
        if not converted:
            raise RuntimeError("MP4 conversion failed: " + err)
        src.unlink(missing_ok=True)
        job["status"] = "telegram_sending"
        job["updated"] = time.time()
        asyncio.run(_telegram_send(job, dest))
        job["status"] = "sent"
        job["updated"] = time.time()
        dest.unlink(missing_ok=True)  # Avoid holding the full MP4 after Telegram delivery.
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
        with _jobs_lock:
            if sum(job["status"] in ("uploading", "processing", "telegram_sending") for job in _jobs.values()) >= MAX_JOBS:
                return _error("Too many active jobs", 429)
            job_id = secrets.token_urlsafe(18)
            folder = ROOT / job_id
            folder.mkdir(parents=True, exist_ok=False)
            _jobs[job_id] = {"folder": folder, "created": time.time(), "updated": time.time(), "status": "uploading",
                             "expected": count, "next": 0, "bytes": 0,
                             "title": _clean_title(body.get("title")),
                             "download_token": secrets.token_urlsafe(24), "lock": threading.Lock()}
        return jsonify(job_id=job_id, status="uploading"), 201

    @app.post("/api/relay/<job_id>/segment/<int:index>")
    def relay_segment(job_id, index):
        if not _authorized():
            return _error("Invalid upload key", 401)
        job = _job(job_id)
        if not job:
            return _error("Unknown or expired job", 404)
        with job["lock"]:
            if job["status"] != "uploading" or index != job["next"]:
                return _error("Unexpected segment index or job already closed", 409)
            content_length = request.content_length
            if content_length is not None and (content_length <= 0 or content_length > MAX_CHUNK):
                return _error("Segment too large or empty", 413)
            if job["bytes"] >= MAX_TOTAL:
                return _error("Job size limit reached", 413)
            total = 0
            part = job["folder"] / "incoming.part"
            try:
                if shutil.disk_usage(ROOT).free < 250*1024**2:
                    return _error("Railway disk nearly full", 507)
                # Stage one segment first, so interrupted/oversized uploads cannot
                # corrupt already-accepted bytes in input.ts.
                with part.open("wb") as incoming:
                    first = True
                    while True:
                        chunk = request.stream.read(1024 * 1024)
                        if not chunk:
                            break
                        if first and index == 0 and not (len(chunk) >= 189 and chunk[0] == 0x47 and chunk[188] == 0x47):
                            return _error("Not MPEG-TS; HLS fMP4/DRM unsupported", 415)
                        first = False
                        total += len(chunk)
                        if total > MAX_CHUNK or job["bytes"] + total > MAX_TOTAL:
                            return _error("Job/segment exceeds size limit", 413)
                        incoming.write(chunk)
                if total == 0:
                    return _error("Empty segment", 400)
                with (job["folder"] / "input.ts").open("ab") as output, part.open("rb") as incoming:
                    shutil.copyfileobj(incoming, output, 1024 * 1024)
                job["next"] += 1
                job["bytes"] += total
                job["updated"] = time.time()
                return jsonify(received=job["next"], total=job["expected"], bytes=job["bytes"])
            except OSError:
                return _error("Disk write failed", 507)
            finally:
                part.unlink(missing_ok=True)

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
            if job["next"] != job["expected"]:
                return _error(f"Incomplete upload: {job['next']}/{job['expected']}", 409)
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
        res = {"status": job["status"], "received": job["next"], "total": job["expected"],
               "bytes": job["bytes"], "title": job["title"]}
        if job["status"] == "sent":
            res["message"] = "Video sent to your Telegram chat"
        if job["status"] == "failed":
            res["error"] = job.get("error", "Conversion failed")
        return jsonify(res)

    threading.Thread(target=_purge_loop, daemon=True).start()
