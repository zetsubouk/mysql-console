#!/usr/bin/env bash
# MySQL Console 一键安装(Linux / macOS)
# 用法:
#   ./install.sh                   安装依赖到项目内 .venv
#   ./install.sh --service         安装并注册 systemd 服务(仅 Linux, 需 root/sudo)
#   ./install.sh --remove-service  注销并删除 systemd 服务
#   ./install.sh --print-service   仅打印渲染后的 unit 文件内容(供手动安装/审查)
set -e

# 定位部署根:自脚本所在目录逐级向上查找 src/server.py。
# 兼容:发布包根(install.sh 在根)、scripts/(开发仓库)、platforms/<os>/scripts/(单仓库双目录)。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT=""
DIR="$SCRIPT_DIR"
while [ -n "$DIR" ] && [ "$DIR" != "/" ]; do
  if [ -f "$DIR/src/server.py" ]; then
    ROOT="$DIR"
    break
  fi
  DIR="$(dirname "$DIR")"
done
if [ -z "$ROOT" ]; then
  echo "[ERROR] Can't locate src/server.py. Run from project root or scripts/."
  exit 1
fi
cd "$ROOT"

PORT="${MC_PORT:-8090}"
SERVICE_NAME="mysql-console"
UNIT_SRC="scripts/mysql-console.service"
[ -f "$UNIT_SRC" ] || UNIT_SRC="mysql-console.service"
[ -f "$UNIT_SRC" ] || { echo "[ERROR] unit template not found: mysql-console.service"; exit 1; }

log() { echo "[install] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

venv_python() {
  # 兼容标准布局与 Debian/Ubuntu python3-venv 布局
  for p in ".venv/bin/python" ".venv/bin/python3" ".venv/Scripts/python.exe"; do
    [ -x "$p" ] && { echo "$p"; return; }
  done
  echo ""
}

install_deps() {
  log "Detect Python interpreter (3.10+) ..."
  local PY=""
  # 允许显式指定: MC_PYTHON=/path/to/python3 ./install.sh
  if [ -n "$MC_PYTHON" ] && [ -x "$MC_PYTHON" ]; then PY="$MC_PYTHON"; fi
  if [ -z "$PY" ]; then
    local c
    for c in python3 python /usr/bin/python3 /usr/local/bin/python3; do
      command -v "$c" >/dev/null 2>&1 || [ -x "$c" ] || continue
      # 跳过 Windows 商店占位符(执行会打印提示而非运行解释器)
      "$c" -c 'pass' </dev/null >/dev/null 2>&1 || continue
      PY="$c"
      break
    done
  fi
  [ -n "$PY" ] || die "Python 3.10+ not found. Please install python3 first (or set MC_PYTHON=/path/to/python)."
  "$PY" -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)' \
    || die "Python >= 3.10 required, current: $("$PY" -V 2>&1)"
  log "Found Python $("$PY" -V 2>&1)"

  log "Create project venv (.venv) ..."
  if [ -n "$(venv_python)" ]; then
    log ".venv already exists, reuse it."
  else
    "$PY" -m venv .venv || die "Failed to create .venv (Debian/Ubuntu may need: apt install python3-venv)"
  fi

  local VPY; VPY="$(venv_python)"
  log "Install dependencies into .venv ..."
  "$VPY" -m pip install --upgrade pip --quiet || true
  "$VPY" -m pip install -r requirements.txt

  log "Install OK. Run './start.sh' then open http://127.0.0.1:${PORT}"
}

render_unit() {
  # 用当前绝对路径渲染 systemd unit 到 stdout
  sed -e "s|__BASE_DIR__|$(pwd)|g" \
      -e "s|__USER__|${SUDO_USER:-$USER}|g" \
      "$UNIT_SRC"
}

case "${1:-}" in
  --print-service)
    render_unit
    ;;
  --service)
    install_deps
    [ "$(uname -s)" = "Linux" ] || die "--service only supports Linux (systemd)."
    systemctl --version >/dev/null 2>&1 || die "systemd not detected."
    [ "$(id -u)" = "0" ] || die "please run with sudo: sudo ./install.sh --service"
    [ -n "$(venv_python)" ] || die ".venv missing, run ./install.sh first."

    render_unit > "/etc/systemd/system/${SERVICE_NAME}.service"
    chmod 644 "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    systemctl enable --now "${SERVICE_NAME}.service"
    sleep 1
    systemctl --no-pager status "${SERVICE_NAME}.service" | head -12
    log "Service '${SERVICE_NAME}' enabled and started."
    log "Logs: journalctl -u ${SERVICE_NAME} -f"
    ;;
  --remove-service)
    [ "$(id -u)" = "0" ] || die "please run with sudo: sudo ./install.sh --remove-service"
    systemctl disable --now "${SERVICE_NAME}.service" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    log "Service '${SERVICE_NAME}' removed."
    ;;
  "")
    install_deps
    echo "Tip: Linux + systemd users can also run: sudo ./install.sh --service"
    ;;
  *)
    echo "Usage: $0 [--service|--remove-service|--print-service]"
    exit 2
    ;;
esac