#!/usr/bin/env bash
# Frontend only (expects the API on CHESSBOOK_API, default :8000).
set -euo pipefail
cd "$(dirname "$0")/.."
CHESSBOOK_API="${CHESSBOOK_API:-http://127.0.0.1:8000}" exec npm run dev