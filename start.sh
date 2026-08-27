#!/usr/bin/env bash
# MySQL Console 启动脚本(Linux / macOS)
# 用法: ./start.sh [端口]
set -e
cd "$(dirname "$0")"
PORT="${1:-8090}"

echo "============================================"
echo "  MySQL Console - 数据库可视化管理平台"
echo "  启动后浏览器访问: http://127.0.0.1:${PORT}"
echo "============================================"

# 优先使用项目内虚拟环境
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY=""
  for c in python3 python /usr/bin/python3 /usr/local/bin/python3; do
    command -v "$c" >/dev/null 2>&1 || [ -x "$c" ] || continue
    # 跳过 Windows 商店占位符等假解释器(执行会失败或打印提示)
    "$c" -c 'pass' </dev/null >/dev/null 2>&1 || continue
    PY="$c"
    break
  done
fi
if [ -z "$PY" ]; then
  echo "[错误] 未找到 python3,请先安装 Python 3.10+ 或运行 install.sh 部署。"
  exit 1
fi
echo "使用解释器: $PY"

ver_ok=$("$PY" -c 'import sys;print(1 if sys.version_info>=(3,10) else 0)')
if [ "$ver_ok" != "1" ]; then
  echo "[错误] Python 版本需 3.10+,当前: $("$PY" -V 2>&1)"
  exit 1
fi

"$PY" -c "import pymysql, cryptography" 2>/dev/null || {
  echo "缺少依赖,自动安装 requirements.txt ..."
  "$PY" -m pip install -r requirements.txt
}

exec "$PY" server.py
