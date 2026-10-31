#!/usr/bin/env bash
# ==============================================================================
# Zien Quant - Server Launcher (Linux / macOS)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

PYTHON_CMD="python3.12"
if ! command -v python3.12 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
fi
if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

# Load environment variables from .env if present
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env 2>/dev/null || true
    set +a
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"

echo "=================================================================="
echo " Starting Zien Quant - Order Block Terminal"
echo " Listening on: http://${HOST}:${PORT}/"
echo " Press Ctrl+C to stop."
echo "=================================================================="

exec $PYTHON_CMD server.py --host "$HOST" --port "$PORT"
