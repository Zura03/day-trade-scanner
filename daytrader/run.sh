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
  exec .venv/bin/streamlit run dashboard.py --server.port "${PORT:-8501}" --server.headless true
fi
exec .venv/bin/python cli.py "$@"
