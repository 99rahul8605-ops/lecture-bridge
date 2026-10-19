"""Opt-in cloud HLS queue for authorized, unencrypted MPEG-TS/VOD streams.

Server fetches ONLY the exact allowlisted HTTPS CDN host, NEVER browser cookies,
DRM material, private networks, arbitrary URLs or redirects. One worker, SQLite
queue, and original relay's FFmpeg/Telegram converter. No extra Telegram bot.
"""
from __future__ import annotations

from contextlib import contextmanager
import hmac
import ipaddress
import logging
import os
import re
import secrets
import shutil
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from flask import jsonify, request

import relay_api as relay

log = logging.getLogger(__name__)
HOST = "bunny-cdn-qbg-s6.testwave.cc"  # Strict SSRF allowlist for current authorized stream only
MAX_PLAYLIST = 2 * 1024 * 1024
MAX_SEGMENTS = 10000
MAX_QUEUE = 20
MAX_SEGMENT = min(int(os.getenv("CLOUD_MAX_SEGMENT_MB", "128")) * 1024**2, 128 * 1024**2)
MAX_TOTAL = relay.MAX_TOTAL
ROOT = Path(os.getenv("CLOUD_QUEUE_DIR", str(relay.ROOT / "cloud_queue")))
DB = ROOT / "queue.sqlite3"
WORKER_LOCK = ROOT / "worker.lock"
KEY_FILE = Path(os.getenv("CLOUD_QUEUE_KEY_FILE", str(ROOT / "cloud_queue.key")))
ROOT.mkdir(parents=True, exist_ok=True)


class CloudError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CloudError("CDN redirects not permitted; browser uploader may be required")


_OPENER = urllib.request.build_opener(NoRedirect())


def _validate_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        raise CloudError("Invalid playlist/segment URL")
    try:
        url = urllib.parse.urlsplit(value)
        if (url.scheme != "https" or url.hostname != HOST or url.port not in (None, 443)
                or url.username or url.password or not url.path.startswith("/") or url.fragment):
            raise CloudError("Only authorized HTTPS CDN host is supported")
        # Defend against SSRF if the trusted CDN hostname ever resolves internally.
        ips = socket.getaddrinfo(url.hostname, url.port or 443, type=socket.SOCK_STREAM)
        if not ips or any(not ipaddress.ip_address(item[4][0]).is_global for item in ips):
            raise CloudError("CDN DNS did not resolve to public Internet addresses")
    except (ValueError, OSError) as exc:
        raise CloudError("Invalid or unavailable CDN URL") from exc
    return value


def _request_url(url):
    _validate_url(url)
    # Server does not copy browser authentication headers or cookies.
    return urllib.request.Request(url, headers={"User-Agent": "LectureCloudQueue/1.0", "Accept": "*/*"})


def _fetch_bytes(url, cap):
    req = _request_url(url)
    with _OPENER.open(req, timeout=50) as response:
        if response.status != 200:
            raise CloudError(f"CDN returned HTTP {response.status}")
        size = response.headers.get("Content-Length")
        if size and int(size) > cap:
            raise CloudError("Media response exceeds configured limit")
        data = bytearray()
        while True:
            piece = response.read(min(1024 * 1024, cap + 1 - len(data)))
            if not piece:
                break
            data.extend(piece)
            if len(data) > cap:
                raise CloudError("Media response exceeds configured limit")
    return bytes(data)


def _get_playlist(url, depth=0):
    if depth > 3:
        raise CloudError("Too many nested playlists")
    content = _fetch_bytes(url, MAX_PLAYLIST)
    try:
        lines = [x.strip() for x in content.decode("utf-8-sig").splitlines() if x.strip()]
    except UnicodeDecodeError as exc:
        raise CloudError("CDN response is not an HLS playlist") from exc
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise CloudError("Not an HLS playlist (maybe expired URL)")
    for line in lines:
        if re.match(r"#EXT-X-(?:SESSION-)?KEY\s*:", line, re.I) and not re.search(r"\bMETHOD\s*=\s*NONE\b", line, re.I):
            raise CloudError("Encrypted/DRM playlist unsupported; use official export")
        if re.match(r"#EXT-X-(MAP|BYTERANGE)\s*:", line, re.I):
            raise CloudError("fMP4/byte-range HLS is unsupported")
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF:"):
            next_uri = next((x for x in lines[i + 1:] if not x.startswith("#")), None)
            if not next_uri:
                raise CloudError("Master playlist has no variant")
            dest = urllib.parse.urljoin(url, next_uri)
            _validate_url(dest)
            return _get_playlist(dest, depth + 1)
    if "#EXT-X-ENDLIST" not in lines:
        raise CloudError("Live HLS unsupported; only complete VOD")
    if any(x.startswith("#EXT-X-I-FRAMES-ONLY") for x in lines):
        raise CloudError("I-frame-only playlist unsupported")
    urls = []
    for line in lines:
        if not line.startswith("#"):
            target = urllib.parse.urljoin(url, line)
            _validate_url(target)
            urls.append(target)
    if not 1 <= len(urls) <= MAX_SEGMENTS:
        raise CloudError("Segment count out of allowed range")
    return urls


def _secret():
    key = os.getenv("CLOUD_QUEUE_KEY", "")
    if len(key) >= 24:
        return key
    # File generated by one_command_deploy.py, private to the Railway service.
    try:
        key = KEY_FILE.read_text().strip()
    except FileNotFoundError:
        key = ""
    return key if len(key) >= 24 else ""


def _auth():
    key = _secret()
    given = request.headers.get("X-Cloud-Queue-Key", "")
    return bool(key) and hmac.compare_digest(key, given)


@contextmanager
def _open_db():
    db = sqlite3.connect(str(DB), timeout=20)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=20000")
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, video_id TEXT NOT NULL, title TEXT NOT NULL,
            manifest TEXT NOT NULL, state TEXT NOT NULL, received INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0, nbytes INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL
        )""")
        db.commit()
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _update(jobid, state=None, **fields):
    fields = dict(fields)
    if state is not None:
        fields["state"] = state
    fields["updated"] = time.time()
    with _open_db() as db:
        db.execute("UPDATE jobs SET " + ", ".join(f"{field}=?" for field in fields)
                   + " WHERE id=?", (*fields.values(), jobid))


def _public(row):
    return {k: row[k] for k in ("id", "title", "state", "received", "total", "nbytes", "error", "created")}


def _idle():
    with relay._jobs_lock:
        return not any(j["status"] in ("uploading", "queued", "processing", "telegram_sending")
                       for j in relay._jobs.values())


def _download_part(url, temp, max_bytes):
    """Stream into a bounded temp file, retrying only the failed segment."""
    last = None
    for attempt in range(4):
        last = None
        try:
            req = _request_url(url)
            with _OPENER.open(req, timeout=50) as response:
                if response.status != 200:
                    raise CloudError(f"CDN returned HTTP {response.status}")
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise CloudError("Segment exceeds configured size limit")
                total = 0
                with temp.open("wb") as f:
                    while True:
                        data = response.read(1024 * 1024)
                        if not data:
                            break
                        total += len(data)
                        if total > max_bytes:
                            raise CloudError("Segment exceeds configured size limit")
                        f.write(data)
            if not total:
                raise CloudError("Empty TS segment")
            return total
        except urllib.error.HTTPError as exc:
            # Respect 401/403/404; never try to obtain a different signature.
            if exc.code not in (408, 425, 429, 500, 502, 503, 504):
                raise CloudError(f"CDN HTTP {exc.code}; Railway may lack access") from exc
            last = CloudError(f"CDN temporarily returned HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = CloudError(f"CDN network request failed: {type(exc).__name__}")
        finally:
            if last:
                temp.unlink(missing_ok=True)
        if attempt < 3:
            time.sleep((2, 5, 10)[attempt])
    raise last or CloudError("CDN download failed")


def _process(row):
    jid = row["id"]
    folder = relay.ROOT / ("cloud-" + jid)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    part = folder / "incoming.part"
    full = folder / "input.ts"
    original_job = None
    try:
        # No cookies, proxy-auth, DRM keys or signed-URL renewal.
        segments = _get_playlist(row["manifest"])
        _update(jid, "downloading", total=len(segments))
        with relay._jobs_lock:
            # Guard race against concurrently starting a new browser transfer.
            if any(j["status"] in ("uploading", "queued", "processing", "telegram_sending")
                   for j in relay._jobs.values()):
                _update(jid, "waiting")
                return
            original_job = {"folder": folder, "created": time.time(), "updated": time.time(),
                            "status": "uploading", "expected": len(segments), "next": 0,
                            "bytes": 0, "inflight_bytes": 0, "title": row["title"],
                            "download_token": secrets.token_urlsafe(20), "lock": threading.Lock()}
            relay._jobs["cloud-" + jid] = original_job
        if shutil.disk_usage(relay.ROOT).free < 350 * 1024**2:
            raise CloudError("Railway disk space too low")
        with full.open("wb") as out:
            for index, url in enumerate(segments):
                remaining = min(MAX_TOTAL - original_job["bytes"], MAX_SEGMENT)
                if remaining <= 0:
                    raise CloudError("Video exceeds Railway size limit")
                n = _download_part(url, part, remaining)
                with part.open("rb") as inp:
                    first = inp.read(376) if index == 0 else b""
                    if index == 0 and (len(first) < 376 or first[0] != 0x47 or first[188] != 0x47):
                        raise CloudError("Not MPEG-TS video; DRM/fMP4 unsupported")
                    if first:
                        out.write(first)
                    shutil.copyfileobj(inp, out, 1024 * 1024)
                part.unlink(missing_ok=True)
                original_job["next"] = index + 1
                original_job["bytes"] += n
                original_job["updated"] = time.time()
                _update(jid, received=index + 1, nbytes=original_job["bytes"])
        original_job["status"] = "processing"
        original_job["updated"] = time.time()
        _update(jid, "processing")
        # Reuse existing MP4/Telegram pipeline. No second Telegram login/bot code.
        relay._convert("cloud-" + jid)
        result = original_job.get("status")
        if result == "sent":
            _update(jid, "sent", manifest="")
        else:
            raise CloudError("MP4/Telegram delivery failed; check Railway logs and Telegram group")
    except (CloudError, urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as exc:
        # Never include raw signed URL, headers, cookies, tokens or ffmpeg stdout in API.
        _update(jid, "failed", error=str(exc)[:220], manifest="")
        log.warning("Cloud queue job %s failed (%s)", jid, type(exc).__name__)
    except Exception as exc:
        _update(jid, "failed", error="Unexpected backend error; check Railway logs", manifest="")
        log.exception("Cloud queue internal error for %s", jid)
    finally:
        if original_job is not None:
            with relay._jobs_lock:
                relay._jobs.pop("cloud-" + jid, None)
        shutil.rmtree(folder, ignore_errors=True)


def _run_worker():
    # Gunicorn may fork more than one worker. Lock ensures only one cloud sender.
    import fcntl
    with WORKER_LOCK.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        with _open_db() as db:
            # Restarting during Telegram send is ambiguous; do NOT blindly resend.
            db.execute("""UPDATE jobs SET state='interrupted',
                error='Railway restarted; verify Telegram before retrying', manifest='', updated=?
                WHERE state IN ('downloading','processing')""", (time.time(),))
            db.commit()
        while True:
            try:
                if _idle():
                    with _open_db() as db:
                        row = db.execute("SELECT * FROM jobs WHERE state='waiting' ORDER BY created LIMIT 1").fetchone()
                        if row:
                            # Atomic claim before starting download.
                            db.execute("UPDATE jobs SET state='downloading', updated=? WHERE id=? AND state='waiting'",
                                       (time.time(), row["id"]))
                            db.commit()
                    if row:
                        _process(row)
                        continue
            except Exception:
                log.exception("Cloud queue worker exception")
            time.sleep(3)


_started = False
_start_guard = threading.Lock()


def install_cloud_routes(app):
    global _started

    @app.after_request
    def cloud_cors(res):
        if request.path.startswith("/api/cloud/"):
            if request.headers.get("Origin") == relay._allowed_origin():
                res.headers["Access-Control-Allow-Origin"] = relay._allowed_origin()
                res.headers["Vary"] = "Origin"
                res.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
                res.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Cloud-Queue-Key"
            res.headers["Cache-Control"] = "no-store"
        return res

    @app.route("/api/cloud/<path:tail>", methods=["OPTIONS"])
    def cloud_options(tail):
        return ("", 204) if request.headers.get("Origin") == relay._allowed_origin() else ("", 403)

    @app.post("/api/cloud/enqueue")
    def cloud_enqueue():
        if not _auth():
            return jsonify(error="Cloud queue key not configured or invalid"), 401
        if not request.is_json or request.content_length and request.content_length > 15000:
            return jsonify(error="Invalid request"), 400
        body = request.get_json(silent=True) or {}
        url = body.get("manifest")
        title = relay._clean_title(body.get("title"))
        video_id = body.get("videoId")
        if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", video_id):
            return jsonify(error="Invalid video ID"), 400
        try:
            _validate_url(url)
        except CloudError as exc:
            return jsonify(error=str(exc)), 400
        with _open_db() as db:
            existing = db.execute("SELECT * FROM jobs WHERE video_id=? AND state NOT IN ('failed','interrupted') ORDER BY created DESC LIMIT 1",
                                  (video_id,)).fetchone()
            if existing:
                return jsonify(job=_public(existing), duplicate=True), 200
            count = db.execute("SELECT count(*) FROM jobs WHERE state IN ('waiting','downloading','processing')").fetchone()[0]
            if count >= MAX_QUEUE:
                return jsonify(error=f"Cloud queue full ({MAX_QUEUE} jobs); try later"), 429
            jid = secrets.token_urlsafe(16)
            now = time.time()
            db.execute("INSERT INTO jobs (id,video_id,title,manifest,state,created,updated) VALUES (?,?,?,?,?,?,?)",
                       (jid, video_id, title, url, "waiting", now, now))
            db.commit()
            row = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        return jsonify(job=_public(row), duplicate=False), 201

    @app.get("/api/cloud/queue")
    def cloud_list():
        if not _auth():
            return jsonify(error="Cloud queue key not configured or invalid"), 401
        with _open_db() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 50").fetchall()
        public = []
        for row in rows:
            obj = _public(row)
            if obj["state"] == "processing":
                live = relay._job("cloud-" + obj["id"])
                if live and live.get("status") == "telegram_sending":
                    obj["state"] = "telegram_sending"
                    obj["telegram_uploaded"] = live.get("telegram_uploaded", 0)
                    obj["telegram_total"] = live.get("telegram_total", 0)
            public.append(obj)
        return jsonify(jobs=public, persistent=bool(os.getenv("CLOUD_QUEUE_DIR"))), 200

    with _start_guard:
        if not _started:
            _started = True
            threading.Thread(target=_run_worker, name="LectureCloudQueue", daemon=True).start()
