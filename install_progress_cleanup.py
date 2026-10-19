#!/usr/bin/env python3
"""Patch ONLY the relay's Bot API progress notifier.

This does not change media transfer, Telegram file send, routing, queue,
Telethon/Pyrogram, uploads, or credentials. Progress messages are deleted using
only the exact message_id returned by this bot's own sendMessage request.
"""
from __future__ import annotations

import ast
import os
from datetime import datetime, timezone
from pathlib import Path
import sys

MARKER = "# LECTURE_BOT_PROGRESS_CLEANUP_V1"

NEW_NOTIFIER = '''# LECTURE_BOT_PROGRESS_CLEANUP_V1

def _progress_notifier(job: dict[str, Any]) -> None:
    """Maintain ONE short-lived group progress message for this exact job.

    Only ever delete the message_id we received from our own sendMessage call.
    Final Telegram MP4 and any message written by anyone else are untouched.
    """
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        return
    # Old behavior can be restored without editing code or sending a message.
    cleanup = os.getenv("TELEGRAM_PROGRESS_CLEANUP", "1").lower() not in ("0", "false", "no")
    cleanup_failures = os.getenv("TELEGRAM_PROGRESS_CLEANUP_FAILURES", "1").lower() not in ("0", "false", "no")
    message_id = None
    last_message = None
    last_attempt_at = -float("inf")
    last_edit_at = -float("inf")
    terminal = {"sent", "failed", "expired", "cancelled"}

    def _delete_own_progress():
        if not cleanup or message_id is None:
            return
        # A transient Telegram 429 / timeout should not leave extra texts forever.
        for attempt in range(3):
            try:
                _bot_api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
                return
            except Exception as exc:
                log.warning("Progress cleanup attempt %s failed (%s)", attempt + 1,
                            type(exc).__name__)
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))

    while True:
        state = job.get("status", "uploading")
        now = time.monotonic()
        current_text = _progress_text(job)

        if state in terminal:
            if message_id is None:
                # Job completed before a progress notification was ever posted.
                return
            if state != "sent":
                # Show a terminal error briefly; do not leave status spam forever.
                try:
                    if current_text != last_message:
                        _bot_api("editMessageText", {"chat_id": chat_id,
                                   "message_id": message_id, "text": current_text})
                        last_message = current_text
                except Exception as exc:
                    log.warning("Terminal progress edit failed (%s)", type(exc).__name__)
                if cleanup and cleanup_failures:
                    time.sleep(30)
                    _delete_own_progress()
                return
            if cleanup:
                # Telegram video has already been acknowledged by send_file; remove
                # only its redundant text status (never the video).
                time.sleep(3)
                _delete_own_progress()
                return
            try:
                if current_text != last_message:
                    _bot_api("editMessageText", {"chat_id": chat_id,
                               "message_id": message_id, "text": current_text})
            except Exception as exc:
                log.warning("Final progress edit failed (%s)", type(exc).__name__)
            return

        if current_text != last_message and now - last_attempt_at >= PROGRESS_INTERVAL:
            last_attempt_at = now
            try:
                if message_id is None:
                    result = _bot_api("sendMessage", {
                        "chat_id": chat_id, "text": current_text,
                        "disable_web_page_preview": "true"})
                    # Telegram returns int; never infer an ID from a chat or video.
                    message_id = result["message_id"]
                elif now - last_edit_at >= PROGRESS_INTERVAL:
                    _bot_api("editMessageText", {"chat_id": chat_id,
                              "message_id": message_id, "text": current_text})
                    last_edit_at = now
                else:
                    time.sleep(1)
                    continue
                last_message = current_text
            except Exception as exc:
                log.warning("Telegram progress notification failed (%s)",
                            type(exc).__name__)
        time.sleep(2)
'''


def patch_text(source: str) -> str:
    if MARKER in source:
        return source
    start_pattern = "def _progress_notifier("
    next_pattern = "\ndef _telegram_setup_error("
    if source.count(start_pattern) != 1:
        raise ValueError("Expected exactly one _progress_notifier; no changes")
    start = source.index(start_pattern)
    end = source.find(next_pattern, start)
    if end == -1:
        raise ValueError("Unrecognized notifier layout (_telegram_setup_error missing); no changes")
    # Only known bridge layouts. Never mutate a different bot.py implementation.
    existing = source[start:end]
    for required in ('_bot_api("sendMessage"', '_bot_api("editMessageText"', 'PROGRESS_INTERVAL'):
        if required not in existing:
            raise ValueError(f"Notifier differs from expected layout: {required}; no changes")
    result = source[:start] + NEW_NOTIFIER + source[end:]
    ast.parse(result)
    return result


def install(filename: Path) -> None:
    if not filename.is_file():
        raise SystemExit(f"File missing: {filename}. No change made.")
    original = filename.read_text(encoding="utf-8")
    patched = patch_text(original)
    if patched == original:
        print("✅ Telegram progress cleanup already installed; no changes")
        return
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    backup = filename.with_name(filename.name + ".before-progress-cleanup-" + stamp)
    backup.write_text(original, encoding="utf-8")
    temp = filename.with_name(filename.name + '.progress-cleanup-tmp')
    try:
        temp.write_text(patched, encoding='utf-8')
        os.replace(temp, filename)
    finally:
        temp.unlink(missing_ok=True)
    print("✅ Cleanup installed. Backup:", backup)
    print("ℹ️ Restart ONLY when all browser uploads/conversion/Telegram sends are idle.")


if __name__ == "__main__":
    install(Path(sys.argv[1] if len(sys.argv) > 1 else 'relay_api.py'))
