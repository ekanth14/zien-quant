#!/usr/bin/env bash
# ==============================================================================
# Zien Quant - Automated Setup Script (Linux / macOS)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

echo "=== Zien Quant - Environment Setup ==="

# 1. Check Python version >= 3.12
PYTHON_CMD=""
if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_CMD="python3.12"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
else
    echo "Error: Python is not installed or not in PATH." >&2
    exit 1
fi

PY_VER=$($PYTHON_CMD -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$($PYTHON_CMD -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$($PYTHON_CMD -c 'import sys; print(sys.version_info.minor)')

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]; }; then
    echo "Error: Python 3.12 or higher is required. Found: $PY_VER" >&2
    exit 1
fi
echo "[OK] Python $PY_VER detected."

# 2. Create runtime directories
mkdir -p work/runtime work/backups
echo "[OK] Runtime directories created (work/runtime, work/backups)."

# 3. Copy .env.example -> .env if missing
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[OK] Created .env from .env.example."
else
    echo "[OK] Existing .env file found."
fi

# 4. Run database initialization & migrations
echo "Initializing database schemas..."
$PYTHON_CMD -c '
from marketscope.security import DatabaseManager
import os
db_path = os.environ.get("MS_DB_PATH", "work/runtime/marketscope.db")
db = DatabaseManager(db_path)
print("[OK] MarketScope database schema initialized.")
'

# 5. Seed stock master database if missing
STOCK_DB="${MS_STOCK_DB:-work/runtime/stock_master.db}"
if [ ! -f "$STOCK_DB" ]; then
    echo "Populating stock master database..."
    $PYTHON_CMD manage.py import-stock-master || true
    echo "[OK] Stock master database initialized."
else
    echo "[OK] Stock master database already exists."
fi

# 6. Check Ollama reachability & model status
$PYTHON_CMD manage.py check-ollama

# 7. Print next steps
echo ""
echo "=================================================================="
echo "  Setup Complete! Next steps:"
echo "  1. Initialize terminal owner account:"
echo "     $PYTHON_CMD manage.py init-owner"
echo "  2. Start the terminal:"
echo "     bash scripts/run.sh"
echo "=================================================================="
