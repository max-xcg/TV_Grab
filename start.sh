#!/usr/bin/env bash
set -e

HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8000}

# 1) 优先用 PATH 里的 python / python3
if command -v python >/dev/null 2>&1; then
  PY=python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
# 2) 兜底：你的 Windows Anaconda 路径（按你当前机器实际情况）
elif [ -x "/c/software/Anaconda3/python.exe" ]; then
  PY="/c/software/Anaconda3/python.exe"
else
  echo "[ERROR] python not found. Please add python to PATH or edit start.sh with your python.exe path."
  exit 1
fi

exec "$PY" -m uvicorn tv_buy_1_0.web.app:app --host "$HOST" --port "$PORT"
