#!/usr/bin/env bash
set -e

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

# 推荐：部署时用环境变量指定；不给也能跑
export PYTHONUNBUFFERED=1
export TZ="${TZ:-Asia/Shanghai}"

# 你后面做 SQLite 索引/检索会用到
export TV_DB_PATH="${TV_DB_PATH:-/app/tv_buy_1_0/db/tv.sqlite}"
export TV_DATA_DIR="${TV_DATA_DIR:-/app/tv_buy_1_0/data_raw}"

# 1) 优先用 PATH 里的 python / python3
if command -v python >/dev/null 2>&1; then
  PY=python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
# 2) 兜底：Windows Anaconda（仅你本机需要，容器里基本用不到）
elif [ -x "/c/software/Anaconda3/python.exe" ]; then
  PY="/c/software/Anaconda3/python.exe"
else
  echo "[ERROR] python not found. Please add python to PATH or edit start.sh with your python.exe path."
  exit 1
fi

exec "$PY" -m uvicorn tv_buy_1_0.web.app:app --host "$HOST" --port "$PORT"
