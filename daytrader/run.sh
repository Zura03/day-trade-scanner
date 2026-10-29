#!/usr/bin/env bash
# One-command launcher. Creates the virtualenv on first run.
#
#   ./run.sh                 dashboard (http://localhost:8501)
#   ./run.sh monitor         live BUY/SELL alerts
#   ./run.sh scan --all      any other cli.py command
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  PY=$(command -v python3.12 || command -v /opt/homebrew/bin/python3.12 || command -v python3)
  echo "Creating virtualenv with $PY ..."
  "$PY" -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi
[ -f .env ] || { cp .env.example .env; chmod 600 .env; echo "Created .env - add your Alpaca keys to it."; }

if [ $# -eq 0 ] || [ "$1" = "dashboard" ]; then
  PORT="${PORT:-8501}"
  # Reachable from your phone only when a password protects it.
  if grep -q '^DASHBOARD_PASSWORD=.\+' .env; then
    HOST="${DASHBOARD_HOST:-0.0.0.0}"
    LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
    [ -n "$LAN_IP" ] && echo "Phone on same Wi-Fi: http://$LAN_IP:$PORT"
    TS=$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)
    TS_NAME=$("$TS" status --json 2>/dev/null | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)
    [ -n "$TS_NAME" ] && echo "Phone anywhere (Tailscale): http://$TS_NAME:$PORT"
  else
    HOST="${DASHBOARD_HOST:-127.0.0.1}"
    echo "Local only. Set DASHBOARD_PASSWORD in .env to open it from your phone."
  fi
  exec .venv/bin/streamlit run dashboard.py --server.port "$PORT" --server.address "$HOST" --server.headless true
fi
exec .venv/bin/python cli.py "$@"
