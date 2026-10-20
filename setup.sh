#!/usr/bin/env bash
set -Eeuo pipefail
cd /lecture-bridge
printf 'Lecture thumbnail deploy (existing Railway backend).\n'
printf 'WARNING: wait for browser uploads, FFmpeg conversion AND Telegram sends to finish.\n'
read -r -p 'All transfers idle? Type IDLE to deploy: ' answer </dev/tty
[[ "$answer" == 'IDLE' ]] || { echo 'No change made.'; exit 2; }
command -v git >/dev/null
command -v python3 >/dev/null
command -v gunicorn >/dev/null
# Download staged source from the GitHub main branch without overwriting local edits with git pull.
git fetch --quiet origin main
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
for name in relay_api.py relay_sender_pyrogram.py Railway_Auto_Telegram.user.js; do
  git show "origin/main:$name" > "$stage/$name" || { echo "Missing GitHub file: $name"; exit 3; }
done
python3 -m py_compile "$stage/relay_api.py" "$stage/relay_sender_pyrogram.py"
python3 - "$stage" <<'PY'
import pathlib,sys
root=pathlib.Path(sys.argv[1]); api=(root/'relay_api.py').read_text(); py=(root/'relay_sender_pyrogram.py').read_text(); js=(root/'Railway_Auto_Telegram.user.js').read_text()
for symbol in ('_save_resume(', '/pause', '_lecture_site_thumbnail(', '/site-thumbnail', '_conversion_lock'):
    assert symbol in api, f'Backend compatibility missing: {symbol}'
assert '_lecture_site_thumbnail(' in py and 'send_video(' in py
assert '/site-thumbnail' in js and 'video_img' in js
print('Compatibility checks passed.')
PY
mkdir -p .thumbnail_backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
for name in relay_api.py relay_sender_pyrogram.py; do
  if [[ -f "$name" ]]; then cp -p "$name" ".thumbnail_backups/${name}.${stamp}"; fi
done
cp "$stage/relay_api.py" ./relay_api.py
cp "$stage/relay_sender_pyrogram.py" ./relay_sender_pyrogram.py
# Reload master of only the 8090 Gunicorn service. Never start duplicate workers.
master=$(ps -eo pid=,ppid=,args= | awk '
 /gunicorn/ && /--bind 0\.0\.0\.0:8090/ && /app:app/ && !/SCREEN/ && !/awk/ {
   pid=$1; ppid=$2; found[pid]=1; parent[pid]=ppid
 } END { for (id in found) if (!(parent[id] in found)) { print id; count++ } if (count != 1) exit 2 }') || {
  echo "Files installed, but Gunicorn master ambiguous. No restart attempted."; exit 4;
}
kill -HUP "$master"
sleep 5
status=$(curl -sS --max-time 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/health || true)
if [[ "$status" != '200' ]]; then
  echo "WARNING: health=$status; rollback using .thumbnail_backups, check logs."
  exit 5
fi
echo 'SUCCESS: backend updated, 8090 returned health 200.'
echo 'In Chrome, replace existing Tampermonkey uploader code with Railway_Auto_Telegram.user.js (from the ZIP).'
echo 'Local edits backed up under /lecture-bridge/.thumbnail_backups/.'
