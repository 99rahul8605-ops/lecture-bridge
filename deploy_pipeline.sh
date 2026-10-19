#!/usr/bin/env bash
# Run only when current Chrome/Railway uploads AND Telegram sends are idle.
set -Eeuo pipefail
cd "$(dirname "$(readlink -f "$0")")"

[ -f relay_api.py ] || { echo 'ERROR: relay_api.py missing. Place files in lecture-bridge root.'; exit 1; }
[ -f app.py ] || { echo 'ERROR: app.py missing.'; exit 1; }

echo '[1/4] Syntax and offline tests (no active services touched)'
python3 -m py_compile install_pipeline.py test_pipeline.py
python3 test_pipeline.py
if command -v node >/dev/null 2>&1; then node --check Railway_Auto_Telegram.user.js; fi

echo '[2/4] Safe patch with timestamped backup'
python3 install_pipeline.py relay_api.py
python3 -m py_compile relay_api.py

echo '[3/4] Find current Gunicorn master on :8090'
MASTER=$(python3 - <<'PY'
from pathlib import Path
import subprocess
here=Path.cwd().resolve()
rows=[]
for line in subprocess.check_output(['ps','-eo','pid=,ppid=,args='],text=True).splitlines():
    f=line.strip().split(maxsplit=2)
    if len(f)!=3:continue
    try: pid,ppid=int(f[0]),int(f[1])
    except ValueError:continue
    argv=f[2]
    if argv.startswith('SCREEN ') or 'gunicorn' not in argv or '8090' not in argv or 'app:app' not in argv:continue
    try:
        if Path(f'/proc/{pid}/cwd').resolve()!=here:continue
    except OSError:continue
    rows.append((pid,ppid))
ids={p for p,_ in rows}
masters=[p for p,parent in rows if parent not in ids]
print(masters[0] if len(masters)==1 else '')
PY
)

if [ -z "$MASTER" ]; then
  echo 'WARN: Could not uniquely find Gunicorn master. No process was restarted.'
  echo "Check: ps -eo pid,ppid,args | grep '[g]unicorn.*8090'"
  exit 0
fi

echo "Gunicorn master PID: $MASTER"
echo 'ATTENTION: Reload interrupts any ongoing conversion/Telegram upload (RAM-backed jobs).'
read -rp 'Confirm ALL video uploads/Telegram sends finished. Type RELOAD to activate: ' ANSWER
if [ "$ANSWER" != RELOAD ]; then
  echo 'Patched files saved; reload deferred safely.'
  exit 0
fi

echo '[4/4] Hot-reload just lecture bridge (not tunnel or other bots)'
kill -HUP "$MASTER"
sleep 4
curl -fsS http://127.0.0.1:8090/health
echo
echo 'Backend active. Now update the Tampermonkey one-click userscript from this package.'
