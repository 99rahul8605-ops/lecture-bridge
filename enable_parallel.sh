#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/4] Syntax + offline tests"
PYTHON=python3
[ -x .venv/bin/python ] && PYTHON=.venv/bin/python
"$PYTHON" -m py_compile relay_api.py
node --check Railway_Auto_Telegram.user.js >/dev/null 2>&1 || {
  if command -v node >/dev/null 2>&1; then
    echo 'JavaScript syntax check failed.'; exit 1
  else
    echo 'Node.js not installed, JS syntax test skipped on VPS (checked in package).'
  fi
}
"$PYTHON" test_parallel_server.py

if [ ! -f .env ]; then
  echo 'ERROR: .env missing. Existing Telegram credentials are required.' >&2
  exit 1
fi

echo "[2/4] Backup private VPS config (not uploaded to GitHub)"
BACKUP="/tmp/lecture-parallel-before-$(date +%Y%m%d-%H%M%S)"
mkdir -m 700 -p "$BACKUP"
cp .env "$BACKUP/env"; chmod 600 "$BACKUP/env"
cp relay_api.py "$BACKUP/relay_api.py"
cp Railway_Auto_Telegram.user.js "$BACKUP/Railway_Auto_Telegram.user.js"
echo "Backup: $BACKUP (note: code was already updated by git pull)"

printf '\n[3/4] Allow max 3 concurrent segment uploads, keep 1 video job\n'
"$PYTHON" - <<'PY'
from pathlib import Path
p=Path('.env')
lines=[line for line in p.read_text().splitlines() if not line.startswith('RELAY_PARALLEL_UPLOADS=')]
lines.append('RELAY_PARALLEL_UPLOADS=3')
p.write_text('\n'.join(lines)+'\n')
p.chmod(0o600)
print('RELAY_PARALLEL_UPLOADS=3 (server maximum; browser defaults to 2)')
PY

echo '[4/4] Restart ONLY lecture bridge so Gunicorn inherits new .env (not just HUP)'
GUNICORN="$PWD/.venv/bin/gunicorn"
if [ ! -x "$GUNICORN" ]; then
  GUNICORN="$(command -v gunicorn || true)"
fi
if [ -z "$GUNICORN" ] || [ ! -x "$GUNICORN" ]; then
  echo 'ERROR: Gunicorn not installed; do not stop existing process.' >&2; exit 1
fi
command -v screen >/dev/null || { echo 'ERROR: screen missing; do not stop existing process.' >&2; exit 1; }

MASTER=$("$PYTHON" - <<'PYCODE'
import subprocess
lines=subprocess.check_output(['ps','-eo','pid=,ppid=,comm=,args='],text=True).splitlines()
found=[]
for line in lines:
    fields=line.strip().split(None,3)
    if len(fields)<4:continue
    pid,ppid,comm,args=fields
    if comm!='gunicorn' or '0.0.0.0:8090' not in args or 'app:app' not in args:
        continue
    found.append((int(pid),int(ppid)))
pids={pid for pid,_ in found}
masters=[pid for pid,ppid in found if ppid not in pids]
if len(masters)!=1: raise SystemExit(f'ERROR: Cannot identify one Gunicorn master on 8090; found {found}')
print(masters[0])
PYCODE
)

echo "Stopping only Gunicorn master PID $MASTER on port 8090..."
kill -TERM "$MASTER"
"$PYTHON" - <<'PYCODE'
import socket, time
for _ in range(50):
    s=socket.socket()
    s.settimeout(.25)
    try: is_free = s.connect_ex(('127.0.0.1',8090)) != 0
    finally: s.close()
    if is_free: break
    time.sleep(.2)
else: raise SystemExit('ERROR: Port 8090 still occupied; NOT starting another server')
print('Port 8090 free')
PYCODE

printf -v BDIR '%q' "$PWD"
printf -v GUN '%q' "$GUNICORN"
CMD="cd $BDIR && set -a && . ./.env && set +a && exec $GUN --workers 1 --threads 16 --timeout 600 --bind 0.0.0.0:8090 app:app"
screen -dmS lecture-bridge bash -lc "$CMD"
sleep 4
printf 'VPS health: '
curl -fsS http://127.0.0.1:8090/health
echo
printf 'Public URL: '
curl -fsS https://upload.bingolink.site/health || echo 'Public tunnel unreachable (check Cloudflare)'
echo
echo 'Done: 1 Gunicorn worker, server allows 3 lanes, Chrome UI defaults to 2.'
