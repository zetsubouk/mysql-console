#!/usr/bin/env bash
# MySQL Console 停止脚本(Linux / macOS)
cd "$(dirname "$0")" || exit 1
PORT=8090

old=$(lsof -ti tcp:${PORT} 2>/dev/null || true)
if [ -z "$old" ]; then
  echo "未发现运行中的服务(端口 ${PORT} 无监听)。"
  exit 0
fi
echo "终止实例: $old"
kill $old 2>/dev/null
sleep 1
rest=$(lsof -ti tcp:${PORT} 2>/dev/null || true)
if [ -n "$rest" ]; then
  kill -9 $rest 2>/dev/null
  sleep 1
fi
final=$(lsof -ti tcp:${PORT} 2>/dev/null || true)
if [ -n "$final" ]; then
  echo "警告: 端口 ${PORT} 仍有监听,请手动检查。"
  exit 1
fi
echo "服务已停止。"
