#!/usr/bin/env bash
# Only touches /lecture-bridge Gunicorn and catalog-control bot Screen.
set -euo pipefail
cd /lecture-bridge

echo '[1/5] Backup existing catalog code'
BACKUP="/tmp/catalog-worker-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"
for f in auto_catalog.py catalog_web.py catalog_bot_client.py catalog_control_bot.py VidCloud_Auto_Catalog.user.js; do
  [[ ! -f "$f" ]] || cp -p "$f" "$BACKUP/$f"
done
echo "Backup: $BACKUP"

echo '[2/5] Pull latest uploaded GitHub files'
git pull --ff-only
for f in auto_catalog.py catalog_web.py catalog_bot_client.py catalog_control_bot.py; do
  [[ -s "$f" ]] || { echo "MISSING FILE: $f"; exit 1; }
done
python3 -m py_compile auto_catalog.py catalog_web.py catalog_bot_client.py catalog_control_bot.py

echo '[3/5] Reload only existing lecture bridge Gunicorn on port 8090'
MASTER="$(pgrep -of '[g]unicorn.*0.0.0.0:8090 app:app' || true)"
if [[ -z "$MASTER" ]]; then
  echo 'No Gunicorn master found for 8090. Do not start a duplicate automatically.'
  exit 1
fi
kill -HUP "$MASTER"
sleep 4
curl -fsS http://127.0.0.1:8090/health
printf '\n'

echo '[4/5] Restart only catalog-control Telegram bot'
screen -S catalog-control -X quit 2>/dev/null || true
sleep 2
: > /tmp/catalog-control.log
screen -dmS catalog-control bash -lc 'cd /lecture-bridge && set -a && . ./.env && set +a && exec python3 -u catalog_control_bot.py > /tmp/catalog-control.log 2>&1'
sleep 3
if grep -q 'Catalog control bot started' /tmp/catalog-control.log; then
  echo 'Catalog Telegram listener started.'
else
  echo 'Telegram listener not confirmed. Check below log:'
fi
tail -n 15 /tmp/catalog-control.log

echo '[5/5] Done. Next: update existing Auto Catalog Tampermonkey script; enable worker in ONE tab.'
