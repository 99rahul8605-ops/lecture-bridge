"""Authenticated, opt-in browser-to-Railway upload bridge for authorized MPEG-TS media.

No external video URL is fetched by the server. Browser sends video bytes it is
already authorized to read. No DRM support, signature forging, or cookie replay.
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
import shutil
import subprocess
import tempfile
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
                       if job["status"] != "processing" and current - job["created"] > TTL_SECONDS]
            for key in expired:
                job = _jobs.pop(key)
                shutil.rmtree(job["folder"], ignore_errors=True)


def _convert(job_id: str):
    job = _job(job_id)
    if not job:
        return
    src = job["folder"] / "input.ts"
    dest = job["folder"] / "lecture.mp4"
    base = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "mpegts", "-i", str(src)]
    methods = [
        base + ["-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-movflags", "+faststart", str(dest)],
        base + ["-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)],
    ]
    try:
        err = ""
        for cmd in methods:
            try:
                dest.unlink(missing_ok=True)
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45*60)
                if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                    job["bytes_output"] = dest.stat().st_size
                    job["status"] = "ready"
                    src.unlink(missing_ok=True)
                    return
                err = (proc.stderr or f"ffmpeg exited {proc.returncode}")[-500:]
            except (subprocess.TimeoutExpired, OSError) as exc:
                err = str(exc)
        job["status"] = "failed"
        job["error"] = "MP4 conversion failed: " + err
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
    finally:
        if job["status"] != "ready":
            log.warning("Relay conversion failed for %s: %s", job_id, job.get("error", "unknown"))


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
        body = request.get_json(silent=True) or {}
        count = body.get("segments")
        if type(count) is not int or not (1 <= count <= 10000):
            return _error("Invalid segment count", 400)
        with _jobs_lock:
            if sum(job["status"] in ("uploading", "processing") for job in _jobs.values()) >= MAX_JOBS:
                return _error("Too many active jobs", 429)
            job_id = secrets.token_urlsafe(18)
            folder = ROOT / job_id
            folder.mkdir(parents=True, exist_ok=False)
            _jobs[job_id] = {"folder": folder, "created": time.time(), "status": "uploading",
                             "expected": count, "next": 0, "bytes": 0,
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
               "bytes": job["bytes"]}
        if job["status"] == "ready":
            # request.host_url is set by Railway reverse proxy; avoid host header poisoning
            domain = os.environ.get("RELAY_PUBLIC_BASE_URL", "").rstrip("/")
            if not domain.startswith("https://"):
                return _error("RELAY_PUBLIC_BASE_URL must be set to your HTTPS public domain", 503)
            res["download_url"] = f"{domain}/relay-download/{job_id}/{job['download_token']}.mp4"
            res["bytes_output"] = job.get("bytes_output", 0)
        if job["status"] == "failed":
            res["error"] = job.get("error", "Conversion failed")
        return jsonify(res)

    @app.route("/relay-download/<job_id>/<token>.mp4", methods=["GET", "HEAD"])
    def relay_download(job_id, token):
        job = _job(job_id)
        if not job or job["status"] != "ready" or not hmac.compare_digest(job["download_token"], token):
            return _error("Link expired or not found", 404)
        file_path = job["folder"] / "lecture.mp4"
        if not file_path.is_file():
            return _error("File not found", 404)
        return send_file(file_path, as_attachment=True, download_name="lecture.mp4",
                         mimetype="video/mp4", conditional=True, max_age=0)

    threading.Thread(target=_purge_loop, daemon=True).start()
