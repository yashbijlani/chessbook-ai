#!/usr/bin/env bash
# API only.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${CHESSBOOK_PORT:-8000}"
PYTHONPATH="$PWD/backend" exec backend/.venv/bin/python -m uvicorn \
    chessbook.api.app:app --host 127.0.0.1 --port "$PORT" --reload "$@"