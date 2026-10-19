"""Non-destructive, idempotent patch for the existing relay_api.py sender."""
from __future__ import annotations

import ast
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
RELAY = DEST / "relay_api.py"
REQUIREMENTS = DEST / "requirements.txt"
SENDER = DEST / "relay_sender_pyrogram.py"
BACKUP = DEST / "relay_api.py.before-pyrogram"
REQ_BACKUP = DEST / "requirements.txt.before-pyrogram"
MARKER = "# PYROGRAM_SENDER_SWITCH_V1"
ANCHOR = "    from telethon import TelegramClient\n"
ADDITION = '''    # PYROGRAM_SENDER_SWITCH_V1
    if os.getenv("TELEGRAM_SENDER", "pyrogram").strip().lower() == "pyrogram":
        from relay_sender_pyrogram import send_telegram_mp4
        return await send_telegram_mp4(job, file_path)

'''


def install() -> None:
    if not RELAY.exists() or not REQUIREMENTS.exists():
        raise RuntimeError("relay_api.py or requirements.txt missing; run in lecture-bridge directory")
    src_sender = ROOT / "relay_sender_pyrogram.py"
    if not src_sender.exists():
        raise RuntimeError("relay_sender_pyrogram.py missing beside installer")

    old = RELAY.read_text(encoding="utf-8")
    tree = ast.parse(old, filename=str(RELAY))
    fn = next((n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
               and n.name == "_telegram_send"), None)
    if fn is None:
        raise RuntimeError("_telegram_send() absent; refusing to patch unknown relay_api.py")
    if MARKER not in old:
        function_text = "\n".join(old.splitlines()[fn.lineno - 1:fn.end_lineno])
        if function_text.count(ANCHOR) != 1:
            raise RuntimeError("Expected Telethon sender not found; refusing unsafe patch")
        if not BACKUP.exists():
            shutil.copy2(RELAY, BACKUP)
        updated = old.replace(ANCHOR, ADDITION + ANCHOR, 1)
        ast.parse(updated, filename=str(RELAY))
        RELAY.write_text(updated, encoding="utf-8")
        print("OK: Pyrogram toggle installed; Telethon code retained")
    else:
        print("OK: Pyrogram toggle already installed")

    if src_sender.resolve() != SENDER.resolve():
        shutil.copy2(src_sender, SENDER)
    ast.parse(SENDER.read_text(encoding="utf-8"), filename=str(SENDER))

    reqs = REQUIREMENTS.read_text(encoding="utf-8")
    new_lines = []
    if not any(x.strip().lower().startswith(("pyrogram", "pyrogram==")) for x in reqs.splitlines()):
        new_lines.append("Pyrogram==2.0.106")
    if not any(x.strip().lower().startswith("tgcrypto") for x in reqs.splitlines()):
        new_lines.append("TgCrypto>=1.2.5,<2.0")
    if new_lines:
        if not REQ_BACKUP.exists():
            shutil.copy2(REQUIREMENTS, REQ_BACKUP)
        REQUIREMENTS.write_text(reqs.rstrip("\n") + "\n" + "\n".join(new_lines) + "\n", encoding="utf-8")
    print("OK: Requirements updated / checked")
    print("Backup:", BACKUP.name, "(if first install)")


if __name__ == "__main__":
    install()
