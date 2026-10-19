#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
# Match the environment that the existing Railway bridge normally starts with.
if [[ -f .env ]]; then
  set -a
  # Existing trusted local .env only; do not upload/share it.
  source ./.env
  set +a
fi
echo '[1/4] Dependencies + offline tests'
command -v ffmpeg >/dev/null || { echo 'ffmpeg missing'; exit 1; }
python3 -c 'import flask; import relay_api' || { echo 'Missing Flask or backend; activate your existing venv'; exit 1; }
python3 test_cloud_queue.py
python3 test_nokey_public_smoke.py
python3 test_progress_cleanup.py
echo '[2/4] Safety: no browser upload, FFmpeg job or Telegram send should be active.'
read -r -p 'When all transfers are IDLE, type IDLE to install and reload: ' answer
if [[ "$answer" != 'IDLE' ]]; then echo 'Cancelled; no changes'; exit 2; fi
python3 one_command_deploy.py
python3 -m py_compile app.py relay_api.py cloud_queue.py
python3 test_cloud_queue.py
python3 test_nokey_public_smoke.py
echo '[3/4] Reloading the existing SINGLE Gunicorn service if unambiguous'
python3 - <<'PY'
import os,signal,subprocess
rows=[]
for line in subprocess.check_output(['ps','-e','-o','pid=,ppid=,args='],text=True).splitlines():
    try:
        pid,ppid,cmd=line.strip().split(None,2)
    except ValueError:continue
    if 'gunicorn' in cmd and ('app:app' in cmd or ':8090' in cmd) and 'grep' not in cmd:
        rows.append((int(pid),int(ppid),cmd))
ids={r[0] for r in rows}
masters=[r for r in rows if r[1] not in ids]
if len(masters)==1:
    pid=masters[0][0]
    os.kill(pid,signal.SIGHUP)
    print(f'Sent HUP reload to Gunicorn master {pid}.')
else:
    print('Auto-reload skipped: not exactly one identifiable app:app Gunicorn master.')
    print('Use your Railway service Redeploy/Restart ONLY WHEN IDLE.')
PY
echo '[4/4] Setup completed locally. Install Lecture_Cloud_Queue.user.js in Tampermonkey.'
echo 'WARNING: Without a Railway persistent volume, DB/keys disappear on instance replacement.'
