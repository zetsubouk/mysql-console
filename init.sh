#!/usr/bin/env bash
# MySQL Console - One-click Initialize (Reset to Factory)
# Deletes all configs, system DB & backups. Keeps program files usable.
set -e

# 定位部署根:本脚本可在 scripts/(开发仓库) 或 发布包根 下运行。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/src/server.py" ]; then
  ROOT="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/../src/server.py" ]; then
  ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  echo "[错误] 未找到 src/server.py,请先解压并运行 install.sh。"
  exit 1
fi
cd "$ROOT"

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
  read -r -p "Press Enter to exit..." _
  exit 1
fi
echo "Using interpreter: $PY"

echo "Detecting current environment..."
"$PY" src/cli_init.py --check
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
"$PY" src/cli_init.py --do --force

echo
echo "Done. To re-run: start.sh   (opens fresh setup wizard at http://127.0.0.1:8090)"