#!/usr/bin/env bash
# Run on Ubuntu VPS only after no active video upload/conversion/Telegram send.
set -Eeuo pipefail
cd "$(dirname "$(readlink -f "$0")")" # Installer files reside in repo root.
echo '[1/5] Check existing bridge'
test -f relay_api.py && test -f requirements.txt && test -f .env || {
  echo 'Missing relay_api.py / requirements.txt / .env; nothing changed.'; exit 1;
}
VENV_PY=".venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then echo 'Missing .venv/bin/python'; exit 1; fi

echo '[2/5] Install Pyrogram + TgCrypto dependencies'
"$VENV_PY" -m pip install 'Pyrogram==2.0.106' 'TgCrypto>=1.2.5,<2.0'
"$VENV_PY" -c 'import asyncio; asyncio.set_event_loop(asyncio.new_event_loop()); import pyrogram, tgcrypto; print("Pyrogram and TgCrypto import OK")' 

echo '[3/5] Preserve old Telethon sender and install switch'
"$VENV_PY" install_pyrogram_sender.py "$(pwd)"
"$VENV_PY" -m py_compile relay_api.py relay_sender_pyrogram.py
"$VENV_PY" -m unittest discover -s . -p 'test_pyrogram_mock.py' -v

echo '[4/5] Check existing Gunicorn master and hot reload code'
# The patch defaults to Pyrogram unless TELEGRAM_SENDER=telethon is set in .env.
# HUP reloads Python code but not environment variables; no .env modification here.
if grep -Eq '^[[:space:]]*TELEGRAM_SENDER[[:space:]]*=[[:space:]]*telethon([[:space:]]*)$' .env; then
  echo 'WARN: .env sets TELEGRAM_SENDER=telethon: remove/change that to enable Pyrogram.'
fi
MASTER=$("$VENV_PY" - <<'PY'
from pathlib import Path
import subprocess
repo = Path.cwd().resolve()
rows = []
for line in subprocess.check_output(['ps','-eo','pid=,ppid=,args='], text=True).splitlines():
    fields = line.strip().split(maxsplit=2)
    if len(fields) != 3: continue
    try: pid, ppid = int(fields[0]), int(fields[1])
    except ValueError: continue
    argv = fields[2]
    if argv.startswith('SCREEN ') or 'gunicorn' not in argv or '8090' not in argv or 'app:app' not in argv:
        continue
    try:
        if Path(f'/proc/{pid}/cwd').resolve() != repo: continue
    except OSError:
        continue
    rows.append((pid, ppid))
pids = {p for p,_ in rows}
masters = [p for p,parent in rows if parent not in pids]
print(masters[0] if len(masters) == 1 else '')
PY
)
if [[ -n "$MASTER" ]]; then
  kill -HUP "$MASTER"
  echo "Reloaded lecture-bridge master PID $MASTER (without touching tunnel or other bots)."
  sleep 4
  curl -fsS http://127.0.0.1:8090/health; echo
else
  echo 'Could not identify unique lecture-bridge master. Not restarting anything.'
  echo "Find it with: ps -eo pid,ppid,args | grep '[g]unicorn.*8090'"
fi

echo '[5/5] Done. Telegram sending uses Pyrogram unless TELEGRAM_SENDER=telethon.'
