#!/usr/bin/env bash
# Restore the earlier manual one-click browser uploader; no catalog bot.
# Does not touch relay_api.py, .env, Telegram tokens, queue DB, or unrelated bots.
set -euo pipefail
ROOT=/lecture-bridge
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

for file in app.py Railway_Auto_Telegram.user.js; do
  if [[ ! -f "$SOURCE_DIR/$file" ]]; then
    echo "ERROR: $SOURCE_DIR/$file missing; extract/upload the full restore bundle."
    exit 1
  fi
done

# Check source files BEFORE touching running services.
python3 -m py_compile "$SOURCE_DIR/app.py"
if command -v node >/dev/null 2>&1; then
  node --check "$SOURCE_DIR/Railway_Auto_Telegram.user.js"
fi
if [[ ! -f relay_api.py ]]; then
  echo 'ERROR: relay_api.py missing; no files were modified.' >&2
  exit 1
fi

backup="/tmp/lecture-manual-restore-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
for file in app.py Railway_Auto_Telegram.user.js; do
  if [[ -f "$ROOT/$file" ]]; then cp -p "$ROOT/$file" "$backup/$file"; fi
done
printf 'Backup: %s\n' "$backup"

for file in app.py Railway_Auto_Telegram.user.js; do
  if [[ "$SOURCE_DIR/$file" -ef "$ROOT/$file" ]]; then
    echo "Already in place: $file"
  else
    cp "$SOURCE_DIR/$file" "$ROOT/$file"
  fi
done

echo '[1/3] Manual-only Flask app restored; existing relay_api.py unchanged'
# Stop ONLY the catalog Telegram listener. Don't touch /ads, /vid, /gram.
if screen -ls 2>/dev/null | grep -Eq '[.]catalog-control[[:space:]]'; then
  screen -S catalog-control -X quit || true
  echo '[2/3] Catalog-control bot stopped'
else
  echo '[2/3] Catalog-control bot not running'
fi

# HUP reload the EXISTING gunicorn master. Do not kill port 8090 or start a second instance.
python3 - <<'PY'
import os, signal, subprocess
pids=[]
for line in subprocess.check_output(['ps','-eo','pid=,ppid=,args='], text=True).splitlines():
    parts=line.split(None,2)
    if len(parts)!=3: continue
    pid, ppid, cmd=parts
    if ('gunicorn' in cmd and '--bind 0.0.0.0:8090' in cmd
        and cmd.strip().endswith('app:app')
        and not cmd.startswith(('SCREEN', 'bash', 'sh', '/bin/bash', '/bin/sh'))):
        pids.append((int(pid),int(ppid)))
pidset={pid for pid,_ in pids}
masters=[pid for pid,ppid in pids if ppid not in pidset]
if len(masters)==1:
    os.kill(masters[0], signal.SIGHUP)
    print(f'[3/3] Existing bridge reloaded (Gunicorn master PID {masters[0]})')
elif not masters:
    print('[3/3] Bridge not running: run your existing Gunicorn start command when ready.')
else:
    print('[3/3] Multiple port-8090 Gunicorn masters found; NOT reloading. Check: ps aux | grep gunicorn')
PY
sleep 3
printf '\nBridge health: '
curl --max-time 8 -fsS http://127.0.0.1:8090/health || echo 'Bridge not reachable; check /tmp/lecture-bridge.log'
printf '\nBrowser: disable VidCloud Auto Catalog userscript; enable ONLY Lecture Auto Send to Telegram.\n'
printf 'One click on the video page starts segment-by-segment browser->Railway upload.\n'
