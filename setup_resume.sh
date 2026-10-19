#!/usr/bin/env bash
# Apply only to the running Railway single-worker app; build-mode Dockerfile
# in this ZIP also installs this patch on future Railway image builds.
set -Eeuo pipefail
cd "$(dirname "$0")"

echo '===== [1/4] Inspect + offline tests (no running service touched) ====='
python3 -m py_compile install_resume_backend.py test_resume_backend.py
RESUME_TEST_FIXTURE="$PWD/relay_api.py" python3 test_resume_backend.py
command -v node >/dev/null 2>&1 && {
  node --check Railway_Auto_Telegram.user.js
  node --check Lecture_Two_Tab_Queue.user.js
  node test_queue.js
  node test_two_tabs.js
} || echo 'Node.js unavailable: browser script checks were run before packaging.'

echo '===== [2/4] Safety: all uploads, ffmpeg and Telegram sends must be IDLE ====='
read -r -p 'If all transfers are IDLE, type IDLE to patch+reload; else Ctrl+C: ' answer
[[ "$answer" == 'IDLE' ]] || { echo 'Cancelled without modifying relay_api.py'; exit 1; }

echo '===== [3/4] Install + backup + compile ====='
python3 install_resume_backend.py relay_api.py
python3 -m py_compile relay_api.py
RESUME_TEST_FIXTURE="$PWD/relay_api.py" python3 test_resume_backend.py

echo '===== [4/4] HUP reload only identifiable single 8090 Gunicorn master ====='
python3 - <<'PY'
import os, signal, subprocess
rows=[]
for line in subprocess.check_output(['ps','-e','-o','pid=,ppid=,args='],text=True).splitlines():
    try: pid,ppid,cmd=line.strip().split(None,2)
    except ValueError: continue
    if ('gunicorn' in cmd and 'app:app' in cmd and
        '--bind 0.0.0.0:8090' in cmd and
        not cmd.startswith(('SCREEN','bash','sh','grep'))):
        rows.append((int(pid),int(ppid),cmd))
ids={x[0] for x in rows}
masters=[x for x in rows if x[1] not in ids]
if len(masters)!=1:
    print('Auto reload skipped: could not safely identify one 8090 Gunicorn master.')
    print('Check the Railway app start command and reload only when IDLE.')
else:
    os.kill(masters[0][0],signal.SIGHUP)
    print('Reloaded Gunicorn master',masters[0][0]);print('Cache kept in current RELAY_DATA_DIR')
PY

echo 'Done. Replace BOTH Tampermonkey scripts from the ZIP and refresh tabs.'
echo 'Keep old Cloud Queue OFF. Never refresh worker tab during live upload.'
