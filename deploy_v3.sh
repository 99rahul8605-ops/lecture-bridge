#!/usr/bin/env bash
# Apply already-pulled v3 code to the CURRENT lecture-bridge only.
# Do not run while a media upload is active: HUP reloads Gunicorn workers.
set -euo pipefail
cd /lecture-bridge

echo '[1/4] Check files and backup previous committed code'
for file in auto_catalog.py VidCloud_Auto_Catalog.user.js; do
  test -s "$file" || { echo "Missing file $file"; exit 1; }
done
backup="/tmp/lecture-worker-v3-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
for file in auto_catalog.py VidCloud_Auto_Catalog.user.js; do
  if git rev-parse 'HEAD^' >/dev/null 2>&1; then
    git show "HEAD^:$file" > "$backup/$file" 2>/dev/null || cp "$file" "$backup/$file"
  else
    cp "$file" "$backup/$file"
  fi
done
echo "Backup: $backup"

echo '[2/4] Syntax and catalog worker tests'
python3 -m py_compile auto_catalog.py
python3 -m unittest -q test_worker test_catalog_existing
if command -v node >/dev/null 2>&1; then
  node --check VidCloud_Auto_Catalog.user.js
  node test_navigation.cjs
  node test_search.cjs
else
  echo 'node unavailable on Railway: local browser tests were run before packaging.'
fi

echo '[3/4] Reload ONLY lecture Gunicorn master (0.0.0.0:8090)'
python3 - <<'PY'
import os, signal, subprocess, sys
rows=[]
for line in subprocess.check_output(['ps','-eo','pid=,ppid=,args='], text=True).splitlines():
    parts=line.split(None,2)
    if len(parts)<3:continue
    pid,ppid,args=parts
    # Select only our executable, not SCREEN's copied command text.
    if ('/usr/local/bin/gunicorn' in args and
        '--bind 0.0.0.0:8090' in args and
        args.strip().endswith('app:app') and
        not args.startswith(('SCREEN','bash','sh','/bin/sh','/bin/bash'))):
        rows.append((int(pid),int(ppid)))
pids={pid for pid,_ in rows}
masters=[pid for pid,ppid in rows if ppid not in pids]
if len(masters)!=1:
    sys.exit('Could not identify unique 8090 Gunicorn master; not reloading. Found: '+str(rows))
master=masters[0]
os.kill(master,signal.SIGHUP)
print(f'Reloaded PID {master} with HUP; port stays bound.')
PY

sleep 3
echo '[4/4] Health check'
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8090/health
echo
echo '✅ Server patched. Update ONLY the existing Auto Catalog Tampermonkey script to v3.0.0.'
echo '   Do not replace the existing Send MP4 to Telegram script.'
