#!/usr/bin/env bash
# One command: cd /lecture-bridge && bash setup.sh
set -euo pipefail
cd "$(dirname "$0")"
echo 'Lecture Thumbnail Setup | one-command verification & reload'
python3 -m py_compile relay_api.py app.py
python3 - <<'PY'
from pathlib import Path
s=Path('relay_api.py').read_text()
a=Path('app.py').read_text()
assert 'LECTURE_SITE_THUMB_V1' in s and 'def relay_site_thumbnail(' in s
assert 'thumb = _lecture_site_thumbnail(' in s
assert 'install_relay_routes' in a
print('PASS: complete bot file, thumbnail route and Flask integration')
PY
if ! command -v ffmpeg >/dev/null 2>&1; then echo 'ERROR: ffmpeg missing. Stopping.'; exit 1; fi
if ! command -v ffprobe >/dev/null 2>&1; then echo 'ERROR: ffprobe missing. Stopping.'; exit 1; fi
if ! command -v gunicorn >/dev/null 2>&1; then echo 'ERROR: gunicorn missing. Stopping.'; exit 1; fi
printf '\nIMPORTANT: No Chrome upload / FFmpeg conversion / Telegram send should be active.\n'
read -r -p 'If ALL transfers are idle, type IDLE to reload (anything else cancels): ' confirm
if [[ "$confirm" != 'IDLE' ]]; then echo 'Cancelled. No processes changed.'; exit 0; fi
# Discover exactly one Gunicorn master bound to the established port 8090.
python3 - <<'PY'
import os,signal,subprocess,time,urllib.request
lines=subprocess.check_output(['ps','-eo','pid=,ppid=,args='],text=True).splitlines()
found={}
for l in lines:
    parts=l.strip().split(None,2)
    if len(parts)!=3: continue
    pid,ppid,cmd=parts
    if 'gunicorn' in cmd and '--bind 0.0.0.0:8090' in cmd and 'app:app' in cmd and not cmd.startswith(('SCREEN','bash','sh','grep')):
        found[int(pid)]=int(ppid)
masters=[pid for pid,ppid in found.items() if ppid not in found]
if len(masters)!=1:
    raise SystemExit(f'ERROR: Expected one 8090 Gunicorn master; found {len(masters)}. No reload attempted.')
pid=masters[0]
print('Reloading Gunicorn master:',pid)
os.kill(pid,signal.SIGHUP)
time.sleep(5)
try:
    with urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=8) as r:
        if r.status!=200: raise RuntimeError('Health HTTP '+str(r.status))
    print('SUCCESS: port 8090 healthy (HTTP 200)')
except Exception as exc:
    print('WARNING: health verification failed:',type(exc).__name__,str(exc))
    print('Check: tail -50 /tmp/lecture-bridge.log')
    raise SystemExit(1)
PY
printf '\nDONE. Update Tampermonkey with Railway_Auto_Telegram.user.js and refresh the player after all transfers finish.\n'
