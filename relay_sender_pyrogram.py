"""Pyrogram sender for the existing one-click lecture bridge.

Receives an already converted, local MP4 path from relay_api; does not fetch,
access, or decrypt website media. The original Telethon implementation remains
available as TELEGRAM_SENDER=telethon.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any


def _upload_transmissions() -> int:
    try:
        value = int(os.getenv("PYROGRAM_MAX_TRANSMISSIONS", "2"))
    except ValueError as exc:
        raise RuntimeError("PYROGRAM_MAX_TRANSMISSIONS must be an integer") from exc
    return max(1, min(4, value))


async def send_telegram_mp4(job: dict[str, Any], file_path: Path) -> None:
    """Send a Telegram video with original filename, thumb and live progress."""
    try:
        from pyrogram import Client
        from pyrogram.enums import ParseMode
        from pyrogram.errors import PeerIdInvalid
    except ImportError as exc:
        raise RuntimeError(
            "Pyrogram not installed. Install Pyrogram and TgCrypto in the bridge venv."
        ) from exc

    # Imported lazily so relay_api can call us without circular imports.
    import relay_api

    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    api_id_raw = os.getenv("TELEGRAM_API_ID", "")
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    chat_raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not api_id_raw.isdigit() or not api_hash or not chat_raw:
        raise RuntimeError("Telegram credentials missing in server .env")
    chat_id = int(chat_raw) if re.fullmatch(r"-?\d+", chat_raw) else chat_raw
    file_path = Path(file_path)
    if not file_path.is_file() or file_path.stat().st_size == 0:
        raise RuntimeError("Converted MP4 missing or empty")

    metadata = relay_api._probe_video(file_path)
    thumb = relay_api._extract_thumbnail(file_path, job["folder"], metadata["duration"])
    size = file_path.stat().st_size

    job["telegram_uploaded"] = 0
    job["telegram_total"] = size
    job["telegram_started"] = time.time()
    job["media_duration"] = metadata["duration"]
    job["media_width"] = metadata["width"]
    job["media_height"] = metadata["height"]

    # Pyrogram's official docs show an async progress callback.
    # Bot API progress notifier in relay_api reads these numeric counters.
    async def progress(current: int, total: int) -> None:
        job["telegram_uploaded"] = int(current)
        job["telegram_total"] = int(total)

    # Session stays independent of other bots / the old Telethon session.
    session_dir = Path(os.getenv("RELAY_DATA_DIR", "/tmp/lecture_relay"))
    session_dir.mkdir(parents=True, exist_ok=True)
    client = Client(
        "lecture_relay_pyrogram",
        api_id=int(api_id_raw),
        api_hash=api_hash,
        bot_token=token,
        workdir=str(session_dir),
        workers=1,  # No incoming message handlers in this upload-only client.
        no_updates=True,
        max_concurrent_transmissions=_upload_transmissions(),
    )

    try:
        async with client:
            # New Pyrogram bot sessions may not have cached a private supergroup.
            # Official MTProto bot accounts may use access_hash=0 for their own
            # channels/supergroups when the hash is not known (no access bypass).
            if isinstance(chat_id, int) and str(chat_id).startswith("-100"):
                try:
                    await client.resolve_peer(chat_id)
                except PeerIdInvalid:
                    await client.storage.update_peers(
                        [(chat_id, 0, "supergroup", None, None)]
                    )
            sent_message = await client.send_video(
                chat_id=chat_id,
                video=str(file_path),
                caption=relay_api._telegram_caption(job["title"], metadata, size),
                parse_mode=ParseMode.HTML,
                duration=int(metadata["duration"]),
                width=int(metadata["width"]),
                height=int(metadata["height"]),
                thumb=str(thumb) if thumb else None,
                file_name=file_path.name,
                supports_streaming=True,
                progress=progress,
            )
            if sent_message is None:
                raise RuntimeError("Pyrogram upload interrupted; Telegram did not confirm delivery")
            job["telegram_uploaded"] = size
            job["telegram_total"] = size
    finally:
        if thumb is not None:
            Path(thumb).unlink(missing_ok=True)
