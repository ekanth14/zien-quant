#!/usr/bin/env bash
# Restart only this checkout's local server; preserve accounts, data and .env.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
PYTHON_CMD="$(command -v python3.12 || true)"
if [ -z "$PYTHON_CMD" ]; then
    echo "Python 3.12 is required. Install it before restarting." >&2
    exit 1
fi
pids="$(lsof -t -nP -iTCP:8766 -sTCP:LISTEN 2>/dev/null | sort -u || true)"
for pid in $pids; do
    process_dir="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    if [ "$process_dir" != "$ROOT_DIR" ]; then
        echo "Port 8766 belongs to another application; leaving it running." >&2
        exit 1
    fi
done
for pid in $pids; do kill -TERM "$pid"; done
for attempt in {1..50}; do
    if ! lsof -t -nP -iTCP:8766 -sTCP:LISTEN >/dev/null 2>&1; then
        exec "$PYTHON_CMD" server.py --host 127.0.0.1 --port 8766
    fi
    sleep 0.2
done
echo "The existing server has not stopped yet; no second server was started." >&2
exit 1
