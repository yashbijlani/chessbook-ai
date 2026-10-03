#!/usr/bin/env bash
# Run the local API and the Vite dev server together.
# Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${CHESSBOOK_PORT:-8000}"
UI_PORT="${CHESSBOOK_UI_PORT:-5173}"
API="${CHESSBOOK_API:-http://127.0.0.1:$PORT}"

PY=backend/.venv/bin/python
[ -x "$PY" ] || { echo "backend/.venv missing - see README (Quick start)"; exit 1; }

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "backend  http://127.0.0.1:$PORT"
PYTHONPATH="$PWD/backend" "$PY" -m uvicorn chessbook.api.app:app \
    --host 127.0.0.1 --port "$PORT" --reload &

echo "frontend http://127.0.0.1:$UI_PORT"
CHESSBOOK_API="$API" npm run dev -- --port "$UI_PORT" --host 127.0.0.1 &

wait