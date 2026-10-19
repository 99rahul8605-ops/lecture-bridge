#!/usr/bin/env python3
"""Fail-closed, idempotent source patch for the existing SINGLE-WORKER relay_api.py.
Keeps accepted TS chunks on failed browser transfer; queued retries reuse them.
No media source URL is fetched by this backend. No changes to Telethon file send.
"""
from __future__ import annotations
import ast
from datetime import datetime, timezone
from pathlib import Path
import os
import sys

MARKER = '# LECTURE_RESUME_CACHE_V1'
HELPERS = r'''# LECTURE_RESUME_CACHE_V1
# Keep resumable upload segments for up to 48 hours if disk space permits.
RESUME_TTL_SECONDS = max(3600, min(7 * 86400, int(float(os.getenv("RELAY_RESUME_TTL_HOURS", "48")) * 3600)))

def _save_resume(job_id: str, job: dict[str, Any]) -> None:
    if not job.get("resume_key"):
        return
    # File is never a source for URLs, cookies or DRM data.
    payload = {
        "job_id": job_id, "resume_key": job["resume_key"],
        "source_fingerprint": job["source_fingerprint"],
        "expected": job["expected"], "title": job["title"],
        "created": job["created"], "updated": job["updated"],
        "next": job["next"], "received": sorted(job["received"]),
        "bytes": job["bytes"], "status": job["status"],
        "prefix_bytes": (job["folder"] / "input.ts").stat().st_size
            if (job["folder"] / "input.ts").is_file() else 0,
    }
    temporary = job["folder"] / "resume.json.tmp"
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, job["folder"] / "resume.json")


def _restore_resumable_jobs() -> None:
    # Imported once by the single Gunicorn worker. Verify committed input length
    # and waiting indexed TS files before reusing on-disk state.
    for manifest in ROOT.glob("*/resume.json"):
        try:
            obj = json.loads(manifest.read_text(encoding="utf-8"))
            folder = manifest.parent
            jid = obj["job_id"]
            if folder.name != jid or not re.fullmatch(r"[A-Za-z0-9_-]{12,64}", jid):
                continue
            if obj.get("status") not in ("paused", "uploading", "queued", "processing", "telegram_sending", "sent"):
                continue
            if time.time() - float(obj["updated"]) > RESUME_TTL_SECONDS:
                continue
            count = int(obj["expected"])
            nxt = int(obj["next"])
            received = set(int(i) for i in obj["received"])
            if not (1 <= count <= 10000 and 0 <= nxt <= count and
                    all(0 <= i < count for i in received) and
                    all(i in received for i in range(nxt))):
                continue
            ts = folder / "input.ts"
            if obj["status"] in ("uploading", "paused") and (
                (ts.stat().st_size if ts.is_file() else 0) != int(obj.get("prefix_bytes", -1))):
                continue
            # For already-queued/sending/sent videos the FFmpeg input may have
            # been intentionally deleted, but the tombstone must remain to avoid
            # delivering a duplicate MP4 after an acknowledged upload.
            if obj["status"] in ("uploading", "paused"):
                if nxt > 0 and (not ts.is_file() or ts.stat().st_size == 0):
                    continue
                if any(not (folder / "segments" / f"{i:06d}.ts").is_file()
                       for i in received if i >= nxt):
                    continue
            fingerprint = obj.get("source_fingerprint", "")
            resume_key = obj.get("resume_key", "")
            # Interrupted request temporary files are never accepted/cache hits.
            if obj["status"] in ("uploading", "paused"):
                for part in (folder / "segments").glob("*.part"):
                    part.unlink(missing_ok=True)
            if not (re.fullmatch(r"[a-f0-9]{64}", fingerprint) and
                    re.fullmatch(r"[a-zA-Z0-9_-]{20,100}", resume_key)):
                continue
            job = {"folder": folder, "created": float(obj["created"]),
                   "updated": float(obj["updated"]), "status": (
                       "paused" if obj["status"] in ("paused", "uploading")
                       else obj["status"]),
                   "expected": count, "next": nxt, "bytes": int(obj["bytes"]),
                   "inflight_bytes": 0, "inflight": set(), "received": received,
                   "received_count": len(received), "title": str(obj["title"])[:140],
                   "download_token": secrets.token_urlsafe(24), "lock": threading.Lock(),
                   "resume_key": resume_key, "source_fingerprint": fingerprint}
            _jobs[jid] = job
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("Skipped corrupt cached upload state (%s)", type(exc).__name__)

_restore_resumable_jobs()

'''
REAPER = '''def _reap_abandoned_uploads():
    """Paused cached jobs release browser slot, WITHOUT deleting received bytes."""
    now = time.time()
    folders = []
    with _jobs_lock:
        for jid, job in list(_jobs.items()):
            if job["status"] != "uploading" or job.get("inflight"):
                continue
            if now - job.get("updated", job["created"]) <= UPLOAD_IDLE_SECONDS:
                continue
            if not job["lock"].acquire(blocking=False):
                continue
            try:
                if job["status"] != "uploading" or job.get("inflight"):
                    continue
                if job.get("resume_key"):
                    job["status"] = "paused"
                    job["updated"] = now
                    _save_resume(jid, job)
                else:
                    job["status"] = "expired"
                    _jobs.pop(jid, None)
                    folders.append(job["folder"])
            finally:
                job["lock"].release()
    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)
    return len(folders)

'''
PURGER = '''def _purge_loop():
    while True:
        time.sleep(30)
        _reap_abandoned_uploads()
        now = time.time()
        folders = []
        with _jobs_lock:
            for jid, job in list(_jobs.items()):
                if job.get("inflight"):
                    continue
                state = job["status"]
                age = now - job.get("updated", job["created"])
                if (state == "paused" and age > RESUME_TTL_SECONDS) or (
                    state in ("sent", "failed", "cancelled") and age > TTL_SECONDS):
                    _jobs.pop(jid, None)
                    folders.append(job["folder"])
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)

'''
NOTIFIER = '''def _progress_notifier(job: dict[str, Any]) -> None:
    """Only edit/delete the own temporary progress text; never Telegram video."""
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        return
    message_id = None
    last_message = None
    last_at = -float("inf")
    terminal = {"paused", "expired", "cancelled", "failed", "sent"}
    while True:
        state = job.get("status", "uploading")
        if state in terminal:
            if message_id is not None:
                # On pause/expiration: remove status immediately. No spam saying
                # 'Upload expired; job slot released' in the group.
                if state == "failed":
                    time.sleep(10)
                elif state == "sent":
                    time.sleep(3)
                for attempt in range(3):
                    try:
                        _bot_api("deleteMessage", {"chat_id": chat_id,
                                                   "message_id": message_id})
                        break
                    except Exception as exc:
                        log.warning("Temporary progress delete failed (%s)", type(exc).__name__)
                        if attempt < 2:
                            time.sleep(2)
            return
        text = _progress_text(job)
        now = time.monotonic()
        if text != last_message and now - last_at >= PROGRESS_INTERVAL:
            last_at = now
            try:
                if message_id is None:
                    message_id = _bot_api("sendMessage", {"chat_id": chat_id,
                        "text": text, "disable_web_page_preview": "true"})["message_id"]
                else:
                    _bot_api("editMessageText", {"chat_id": chat_id,
                        "message_id": message_id, "text": text})
                last_message = text
            except Exception as exc:
                log.warning("Temporary progress update failed (%s)", type(exc).__name__)
        time.sleep(2)

'''
START = '''    @app.post("/api/relay/start")
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
        resume_key = body.get("resume_key", "")
        fingerprint = body.get("source_fingerprint", "")
        if resume_key or fingerprint:
            if not isinstance(resume_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", resume_key):
                return _error("Invalid resume key", 400)
            if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
                return _error("Invalid media fingerprint", 400)
        _reap_abandoned_uploads()
        with _jobs_lock:
            if resume_key:
                old = next(((jid, job) for jid, job in _jobs.items()
                           if job.get("resume_key") == resume_key), None)
                if old:
                    jid, job = old
                    if job.get("source_fingerprint") != fingerprint or job["expected"] != count:
                        return _error("Playlist changed; refusing unsafe partial resume", 409)
                    if job["status"] in ("queued", "processing", "telegram_sending", "sent"):
                        return jsonify(job_id=jid, status=job["status"],
                                       received_indices=sorted(job["received"]),
                                       resumed=True, already_finished=True), 200
                    if job["status"] not in ("paused",):
                        return _error("Existing upload still active; wait before retry", 429)
                    if any(j["status"] in (("uploading",) if "_conversion_lock" in globals()
                                                 else ("uploading", "processing", "telegram_sending"))
                           for j in _jobs.values() if j is not job):
                        return _error("Another job occupies upload slot", 429)
                    job["status"] = "uploading"
                    job["updated"] = time.time()
                    _save_resume(jid, job)
                    if PROGRESS_ENABLED:
                        threading.Thread(target=_progress_notifier, args=(job,), daemon=True).start()
                    return jsonify(job_id=jid, status="uploading", resumed=True,
                                   received_indices=sorted(job["received"]),
                                   bytes_cached=job["bytes"]), 200
            # Preserve existing background Telegram pipeline when installed.
            if "_conversion_lock" in globals():
                if sum(j["status"] == "uploading" for j in _jobs.values()) >= MAX_JOBS:
                    return jsonify(error="Another browser upload is active; finish it first.",
                                   active_status="uploading"), 429
                if sum(j["status"] in ("queued", "processing", "telegram_sending")
                       for j in _jobs.values()) >= MAX_BACKGROUND_JOBS:
                    return jsonify(error="Telegram processing backlog is full",
                                   active_status="background_full"), 429
                if shutil.disk_usage(ROOT).free < 512 * 1024**2:
                    return _error("Insufficient free disk for another video", 507)
            elif sum(job["status"] in ("uploading", "processing", "telegram_sending")
                     for job in _jobs.values()) >= MAX_JOBS:
                active = next((job["status"] for job in _jobs.values()
                               if job["status"] in ("uploading", "processing", "telegram_sending")), "busy")
                return jsonify(error=f"Job busy ({active}). Wait for it to finish.",
                               active_status=active), 429
            job_id = secrets.token_urlsafe(18)
            folder = ROOT / job_id
            folder.mkdir(parents=True, exist_ok=False)
            (folder / "segments").mkdir()
            _jobs[job_id] = {
                "folder": folder, "created": time.time(), "updated": time.time(),
                "status": "uploading", "expected": count, "next": 0,
                "bytes": 0, "inflight_bytes": 0, "inflight": set(),
                "received": set(), "received_count": 0,
                "title": _clean_title(body.get("title")),
                "download_token": secrets.token_urlsafe(24), "lock": threading.Lock(),
                "resume_key": resume_key, "source_fingerprint": fingerprint,
            }
            job = _jobs[job_id]
            _save_resume(job_id, job)
        if PROGRESS_ENABLED:
            threading.Thread(target=_progress_notifier, args=(job,), daemon=True).start()
        return jsonify(job_id=job_id, status="uploading", resumed=False,
                       received_indices=[]), 201

    @app.post("/api/relay/<job_id>/pause")
    def relay_pause(job_id):
        if not _authorized():
            return _error("Invalid upload key", 401)
        job = _job(job_id)
        if not job:
            return _error("Unknown job", 404)
        with job["lock"]:
            if job["status"] == "paused":
                return jsonify(status="paused", received_indices=sorted(job["received"])), 200
            if job["status"] != "uploading":
                return _error("Cannot pause already processed job", 409)
            if job.get("inflight"):
                return _error("Segment uploads still in progress; retry pause shortly", 409)
            if not job.get("resume_key"):
                return _error("This upload was started without resumable caching", 409)
            job["status"] = "paused"
            job["updated"] = time.time()
            _save_resume(job_id, job)
            return jsonify(status="paused", received_indices=sorted(job["received"])), 200

'''

def replace_top_function(src: str, name: str, replacement: str) -> str:
    start_token = f'def {name}('
    n = src.count(start_token)
    if n != 1:
        raise ValueError(f'{name}: expected one definition, found {n}')
    begin = src.index(start_token)
    # Top-level function ends when next top-level def/async def follows.
    import re
    next_def = re.search(r'(?m)^(?:async )?def [A-Za-z_]\w*\(', src[begin+len(start_token):])
    if not next_def:
        raise ValueError(f'{name}: cannot find end')
    end = begin + len(start_token) + next_def.start()
    return src[:begin] + replacement + src[end:]


def patch_text(src: str) -> str:
    if MARKER in src:
        return src
    for token in ['_jobs: dict[str, dict[str, Any]]', 'def _reap_abandoned_uploads():',
                  'def _purge_loop():', 'def _progress_notifier(',
                  'def relay_start():', 'def relay_segment(',
                  'def relay_finish(', 'def relay_status(']:
        if token not in src:
            raise ValueError(f'Unexpected relay_api layout, missing: {token}')
    if '"received": set()' not in src or '(folder / "segments").mkdir()' not in src:
        raise ValueError('Not the expected parallel TS relay. Refusing to patch.')
    src = src.replace('def _allowed_origin()', HELPERS + 'def _allowed_origin()', 1)
    src = replace_top_function(src, '_reap_abandoned_uploads', REAPER)
    src = replace_top_function(src, '_purge_loop', PURGER)
    src = replace_top_function(src, '_progress_notifier', NOTIFIER)
    a = src.index('    @app.post("/api/relay/start")')
    b = src.index('    @app.post("/api/relay/<job_id>/cancel")', a)
    src = src[:a] + START + src[b:]
    # Safe duplicate ACK after a response-loss; never write a segment twice.
    old = '''            if index in job["inflight"] or index in job["received"]:
                return _error("Duplicate segment", 409)'''
    new = '''            if index in job["received"]:
                return jsonify(received=job["received_count"], total=job["expected"],
                               already_received=True), 200
            if index in job["inflight"]:
                return _error("Duplicate segment still in progress", 409)'''
    if src.count(old) == 1:
        src = src.replace(old, new, 1)
    elif 'if index in job["received"]:' not in src or 'already_received=True' not in src:
        raise ValueError('Expected known duplicate segment guard; refusing to patch')
    # Segment acknowledged only after on-disk resume manifest committed.
    anchor = '''                committed = True
                return jsonify(received=job["received_count"]'''
    if src.count(anchor) != 1:
        raise ValueError('Could not locate the segment commit ACK, refusing to patch')
    src = src.replace(anchor, '''                committed = True
                _save_resume(job_id, job)
                return jsonify(received=job["received_count"]''', 1)
    # Persist phase transitions to avoid mistakenly resending a completed video
    # when a Gunicorn worker restarts between conversion and browser retry.
    finish_anchor = next((t for t in (
        '            job["status"] = "processing"\n            job["updated"] = time.time()',
        '            job["status"] = "queued"\n            job["updated"] = time.time()')
        if src.count(t) == 1), None)
    if not finish_anchor:
        raise ValueError('Could not locate processing/queued transition')
    src = src.replace(finish_anchor, finish_anchor + '\n            _save_resume(job_id, job)', 1)
    for stage in ('telegram_sending', 'sent', 'failed'):
        needle = f'        job["status"] = "{stage}"\n        job["updated"] = time.time()'
        if needle in src:
            src = src.replace(needle, needle + '\n        _save_resume(job_id, job)', 1)
    # Expose indices without exposing sensitive video URLs.
    anchor = '''        if job["status"] == "telegram_sending":
            res["telegram_uploaded"]'''
    if src.count(anchor) != 1:
        raise ValueError('Could not locate status output')
    src = src.replace(anchor, '''        res["received_indices"] = sorted(job["received"])
        if job["status"] == "telegram_sending":
            res["telegram_uploaded"]''', 1)
    ast.parse(src)
    return src


def install(p: Path) -> None:
    original = p.read_text(encoding='utf-8')
    patched = patch_text(original)
    if patched == original:
        print('Resume backend already installed; unchanged')
        return
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    backup = p.with_name(p.name + f'.before-resume-{stamp}')
    backup.write_text(original, encoding='utf-8')
    temp = p.with_name(p.name+'.resume.tmp')
    try:
        temp.write_text(patched, encoding='utf-8')
        os.replace(temp, p)
    finally:
        temp.unlink(missing_ok=True)
    print('Resume backend installed; backup:', backup)

if __name__ == '__main__':
    install(Path(sys.argv[1] if len(sys.argv)>1 else 'relay_api.py'))
