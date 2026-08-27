#!/usr/bin/env bash
# MySQL Console - One-click Initialize (Reset to Factory)
# Deletes all configs, system DB & backups. Keeps program files usable.
cd "$(dirname "$0")" || exit 1
echo "============================================"
echo "  MySQL Console - One-click Initialize (Reset)"
echo "  This will DELETE all configs, system DB & backups."
echo "============================================"
echo

# Detect Python 3.10+
PY=""
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)'; then
  PY="python3"
elif command -v python >/dev/null 2>&1 && python -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)'; then
  PY="python"
fi
if [ -z "$PY" ]; then
  echo "[ERROR] Python 3.10+ not found. Please install Python first."
  read -r -p "Press Enter to exit..."
  exit 1
fi
echo "Using interpreter: $PY"

echo "Detecting current environment..."
"$PY" cli_init.py --check
echo
echo "============================================"
echo "  WARNING: The above data will be PERMANENTLY destroyed."
echo "============================================"
read -r -p "Type 'y' to confirm initialize, or any key to cancel: " CONFIRM
if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
  echo
  echo "Cancelled. No changes made."
  exit 1
fi

echo
"$PY" cli_init.py --do --force

echo
echo "Done. To re-run: start.sh   (opens fresh setup wizard at http://127.0.0.1:8090)"