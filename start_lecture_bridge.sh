#!/usr/bin/env bash

# Show current Cloudflare Tunnel URL
show_tunnel_url() {
    URL=$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com'         /root/lecture-bridge/tunnel.log 2>/dev/null | tail -1 || true)

    if [ -n "$URL" ]; then
        echo
        echo "========== LECTURE BRIDGE PUBLIC URL =========="
        echo "URL    : $URL"
        echo "Health : $URL/health"
        echo "================================================"
    else
        echo "Cloudflare URL not found yet."
    fi
}

if [ "${1:-}" = "--url" ]; then
    show_tunnel_url
    exit 0
fi
# Lecture Bridge: screen-based API, Cloudflare quick tunnel, catalog bot, URL synchronizer.
set -Eeuo pipefail
BASE="${LECTURE_BRIDGE_DIR:-/root/lecture-bridge}"
cd "$BASE"
ENV_FILE="$BASE/.env"
TUNNEL_LOG="$BASE/tunnel.log"
API_LOG="$BASE/api-error.log"
BOT_LOG="$BASE/catalog-bot.log"
SYNC_LOG="$BASE/tunnel-sync.log"
PORT="${PORT:-8090}"

if [[ "${1:-}" == "--watch" ]]; then
  # Watcher is started only by this script in its own Screen session.
  while :; do
    if ! screen -ls 2>/dev/null | grep -Eq '[.]lecture-tunnel[[:space:]]'; then
      echo "[$(date -Is)] Tunnel screen exited; starting a fresh tunnel."
      : > "$TUNNEL_LOG"
      screen -dmS lecture-tunnel bash -c 'exec cloudflared tunnel --url http://127.0.0.1:8090 >> /root/lecture-bridge/tunnel.log 2>&1'
    fi
    URL=$(grep -oE 'https://[[:alnum:]-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | tail -n 1 || true)
    if [[ -n "$URL" && -f "$ENV_FILE" ]]; then
      CURRENT=$(grep -m1 '^RELAY_PUBLIC_BASE_URL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)
      if [[ "$URL" != "$CURRENT" ]]; then
        printf '[%s] New Cloudflare URL: %s\n' "$(date -Is)" "$URL"
        python3 - "$ENV_FILE" "$URL" <<'PY'
import os,sys,tempfile
p,url=sys.argv[1:]
with open(p) as f: lines=f.readlines()
lines=[line for line in lines if not line.startswith('RELAY_PUBLIC_BASE_URL=')]
lines.append('RELAY_PUBLIC_BASE_URL='+url+'\n')
lines=[line for line in lines if not line.startswith('CATALOG_BASE_URL=')]
lines.append('CATALOG_BASE_URL=http://127.0.0.1:8090\n')
fd,tmp=tempfile.mkstemp(prefix='.env.',dir=os.path.dirname(p))
try:
    os.fchmod(fd,0o600)
    with os.fdopen(fd,'w') as f: f.writelines(lines)
    os.replace(tmp,p)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
PY
        # A process does not automatically reload its environment after .env changes.
        if screen -ls 2>/dev/null | grep -Eq '[.]lecture-api[[:space:]]'; then
          screen -S lecture-api -X quit || true
          sleep 2
          screen -dmS lecture-api bash -c 'cd /root/lecture-bridge; source venv/bin/activate; set -a; source .env; set +a; exec gunicorn --workers 1 --threads 8 --timeout 600 --bind 127.0.0.1:8090 --access-logfile api-access.log --error-logfile api-error.log app:app'
          echo 'API restarted to load new URL.'
        else
          echo 'API was not managed by lecture-api Screen; restart it manually to load new URL.'
        fi
        if screen -ls 2>/dev/null | grep -Eq '[.]catalog-bot[[:space:]]'; then
          screen -S catalog-bot -X quit || true
          sleep 2
          screen -dmS catalog-bot bash -c 'cd /root/lecture-bridge; source venv/bin/activate; set -a; source .env; set +a; exec python -u catalog_control_bot.py >> catalog-bot.log 2>&1'
          echo 'Catalog bot restarted to load new URL.'
        fi
      fi
    fi
    sleep 15
  done
fi

if [[ ! -f "$ENV_FILE" ]]; then echo 'ERROR: .env missing'; exit 1; fi
if [[ ! -x "$BASE/venv/bin/python" ]]; then echo 'ERROR: venv missing in project folder'; exit 1; fi
if ! command -v cloudflared >/dev/null || ! command -v screen >/dev/null; then echo 'ERROR: install cloudflared and screen first'; exit 1; fi
chmod 600 "$ENV_FILE"
# Do not expose upload endpoints with intentionally disabled authentication.
if grep -q '^RELAY_REQUIRE_UPLOAD_KEY=0\([[:space:]]*\|$\)' "$ENV_FILE"; then
  echo 'WARNING: RELAY_REQUIRE_UPLOAD_KEY=0. Secure upload endpoints before public use.'
fi
# Only start an API when /health is not already responding.
if curl -fsS --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "API already healthy on localhost:$PORT"
else
  if screen -ls 2>/dev/null | grep -Eq '[.]lecture-api[[:space:]]'; then
    echo 'API screen exists but health is down; inspect api-error.log.'
  else
    screen -dmS lecture-api bash -c 'cd /root/lecture-bridge; source venv/bin/activate; set -a; source .env; set +a; exec gunicorn --workers 1 --threads 8 --timeout 600 --bind 127.0.0.1:8090 --access-logfile api-access.log --error-logfile api-error.log app:app'
    echo 'API screen started.'
  fi
fi

if ! screen -ls 2>/dev/null | grep -Eq '[.]lecture-tunnel[[:space:]]'; then
  : > "$TUNNEL_LOG"
  screen -dmS lecture-tunnel bash -c 'exec cloudflared tunnel --url http://127.0.0.1:8090 >> /root/lecture-bridge/tunnel.log 2>&1'
  echo 'Cloudflare tunnel started.'
else
  echo 'Cloudflare tunnel screen already running.'
fi

# Single persistent watcher keeps the URL synced even after cloudflared reconnects.
if ! screen -ls 2>/dev/null | grep -Eq '[.]tunnel-sync[[:space:]]'; then
  screen -dmS tunnel-sync bash -c 'exec /bin/bash /root/lecture-bridge/start_lecture_bridge.sh --watch >> /root/lecture-bridge/tunnel-sync.log 2>&1'
  echo 'Tunnel URL sync watcher started.'
fi

if ! screen -ls 2>/dev/null | grep -Eq '[.]catalog-bot[[:space:]]'; then
  screen -dmS catalog-bot bash -c 'cd /root/lecture-bridge; source venv/bin/activate; set -a; source .env; set +a; exec python -u catalog_control_bot.py >> catalog-bot.log 2>&1'
  echo 'Catalog bot screen started.'
fi

URL=''
for i in $(seq 1 25); do
  URL=$(grep -oE 'https://[[:alnum:]-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | tail -1 || true)
  [[ -n "$URL" ]] && break
  sleep 1
done
if [[ -n "$URL" ]]; then
  echo "Tunnel: $URL"
  echo "Health: $URL/health"
else
  echo 'Tunnel URL not found yet; inspect tunnel.log.'
fi
sleep 2
printf '\n== Screen sessions ==\n'
screen -ls || true
printf '\n== Local health ==\n'
curl -fsS --max-time 6 "http://127.0.0.1:$PORT/health" || echo 'Health failed; see api-error.log'
echo
printf '\nURL sync log: %s\nBot log: %s\n' "$SYNC_LOG" "$BOT_LOG"

# AUTO_PRINT_TUNNEL_URL
show_tunnel_url
