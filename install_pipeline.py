#!/usr/bin/env python3
"""Safely patch lecture relay to decouple upload slots from Telegram sending.

Only upload-job scheduling/status handling changes. No media access logic changes.
Compatible with base/parallel relay and the optional Pyrogram sender patch.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import re
import sys

MARKER = "# PIPELINE_UPLOAD_NEXT_V1"


def patch_text(src: str) -> str:
    if MARKER in src:
        return src
    original = src

    # 1. Keep exactly one active browser->relay upload, while permitting a
    #    bounded background backlog. A single conversion lock serializes
    #    conversion & Telegram sends (important for Telethon/Pyrogram sessions).
    anchor = '_jobs_lock = threading.Lock()'
    assert src.count(anchor) == 1, "Cannot locate _jobs_lock"
    src = src.replace(anchor, anchor + '''\n# PIPELINE_UPLOAD_NEXT_V1
MAX_BACKGROUND_JOBS = max(1, min(4, int(os.environ.get("RELAY_BACKGROUND_JOBS", "2"))))
_conversion_lock = threading.Lock()\n''', 1)

    # 2. Change only relay_start concurrency check, not upload segment limits.
    start = src.index('    @app.post("/api/relay/start")')
    end = src.index('    @app.post("/api/relay/<job_id>/cancel")', start)
    section = src[start:end]
    pattern = re.compile(r'''            if sum\(job\["status"\] in \("uploading", "processing", "telegram_sending"\) for job in _jobs\.values\(\)\) >= MAX_JOBS:\n                active = next\(\n                    \(job\["status"\] for job in _jobs\.values\(\)\n                     if job\["status"\] in \("uploading", "processing", "telegram_sending"\)\), "busy"\n                \)\n                return jsonify\(error=f"Job busy \(\{active\}\)\. Wait for it to finish, or cancel an abandoned upload\.",\n                               active_status=active\), 429''')
    repl = '''            if sum(j["status"] == "uploading" for j in _jobs.values()) >= MAX_JOBS:
                return jsonify(error="Another browser upload is active; finish it first.",
                               active_status="uploading"), 429
            pending = sum(j["status"] in ("queued", "processing", "telegram_sending")
                          for j in _jobs.values())
            if pending >= MAX_BACKGROUND_JOBS:
                return jsonify(error="Telegram processing backlog is full; wait for a previous video.",
                               active_status="background_full"), 429
            # Avoid accepting another large job if the filesystem is nearly full.
            if shutil.disk_usage(ROOT).free < 512 * 1024**2:
                return _error("Insufficient free disk for another video", 507)'''
    section, n = pattern.subn(lambda _: repl, section, count=1)
    assert n == 1, "Cannot safely identify old MAX_JOBS check"
    src = src[:start] + section + src[end:]

    # 3. Background jobs are serialized under a non-upload lock. This avoids
    #    concurrent SQLite Telegram sessions and FFmpeg CPU overload.
    pattern = r'\ndef _convert\(job_id: str\):\n'
    assert len(re.findall(pattern, src)) == 1, "Cannot locate _convert entrypoint"
    src = re.sub(pattern, '''
def _convert(job_id: str):
    """Background pipeline: queued jobs execute in one-at-a-time FIFO-ish order."""
    with _conversion_lock:
        job = _job(job_id)
        if not job or job["status"] not in ("queued", "processing"):
            return
        job["status"] = "processing"
        job["updated"] = time.time()
        return _convert_serial(job_id)


def _convert_serial(job_id: str):
''', src, count=1)

    # 4. Transfer completes -> queued, not yet Telegram-delivered.
    begin = src.index('    @app.post("/api/relay/<job_id>/finish")')
    stop = src.index('    @app.get("/api/relay/<job_id>/status")', begin)
    finish = src[begin:stop]
    assert finish.count('job["status"] = "processing"') == 1
    assert finish.count('return jsonify(status="processing"), 202') == 1
    finish = finish.replace('job["status"] = "processing"', 'job["status"] = "queued"', 1)
    finish = finish.replace('return jsonify(status="processing"), 202',
                            'return jsonify(status="queued", job_id=job_id), 202', 1)
    src = src[:begin] + finish + src[stop:]

    # 5. Per-job Telegram group progress remains, while queued jobs have a clear status.
    progress = src.index('def _progress_text(')
    progress_end = src.index('def _progress_notifier(', progress)
    p = src[progress:progress_end]
    anchor = '    if stage == "processing":'
    assert p.count(anchor) == 1
    p = p.replace(anchor,
                  '    if stage == "queued":\n'
                  '        return f"{header}\\n\\n✅ All segments on server; waiting for Telegram turn."\n'
                  + anchor, 1)
    src = src[:progress] + p + src[progress_end:]

    assert src != original
    ast.parse(src)
    return src


def install(p: Path) -> None:
    if not p.is_file():
        raise RuntimeError(f"Missing {p}; no change")
    src = p.read_text(encoding="utf-8")
    patched = patch_text(src)
    if patched == src:
        print("✅ Pipeline already installed; nothing changed")
        return
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    backup = p.with_name(p.name + '.before-pipeline-' + stamp)
    backup.write_text(src, encoding="utf-8")
    p.write_text(patched, encoding="utf-8")
    print("✅ Upload-next / Telegram-background pipeline installed")
    print("Backup:", backup)
    print("IMPORTANT: Only reload Gunicorn when no active upload or Telegram send is in progress")


if __name__ == "__main__":
    install(Path(sys.argv[1] if len(sys.argv) > 1 else "relay_api.py"))
