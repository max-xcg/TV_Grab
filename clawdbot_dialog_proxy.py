# -*- coding: utf-8 -*-
"""
clawdbot_dialog_proxy.py
=========================================================
Clawdbot 侧：/api/dialog/3p2 调用适配器（避免“超时告警”）
---------------------------------------------------------
目标：
1) 正常情况：把 /api/dialog/3p2 的结果原样返回给用户
2) 超时/网络错误：不报红、不发告警，只回“正在生成推荐，请稍等…”
3) 后台继续请求并缓存结果；用户回复“继续/结果/完成了吗”可取回缓存

用法：
  1) 启动本文件作为一个小服务（FastAPI）：
     python3 clawdbot_dialog_proxy.py
     # 或指定端口
     python3 clawdbot_dialog_proxy.py --port 9000
  2) Clawdbot 的 webhook 指向：
     http://127.0.0.1:9000/webhook
  3) 你的 TV 服务在：
     http://127.0.0.1:8000/api/dialog/3p2

依赖：
  pip install fastapi uvicorn requests

环境变量（可选）：
  TV_SERVICE_BASE   默认 http://127.0.0.1:8000
  TV_DIALOG_PATH    默认 /api/dialog/3p2
  TV_TIMEOUT_SEC    默认 180
  TV_BG_TIMEOUT_SEC 默认 240
  MAX_BG_WORKERS    默认 8
  CACHE_TTL_SEC     默认 600
  WAITING_TEXT      默认 "正在生成推荐..."
  VERBOSE_LOG       默认 1（设为 0 关闭）
"""

from __future__ import annotations

import argparse
import json
import os
import time
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, Future

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


# =========================
# 配置区（环境变量优先，其次默认值；也可被命令行覆盖）
# =========================
TV_SERVICE_BASE = os.environ.get("TV_SERVICE_BASE", "http://127.0.0.1:8000")
TV_DIALOG_PATH = os.environ.get("TV_DIALOG_PATH", "/api/dialog/3p2")

TV_TIMEOUT_SEC = int(os.environ.get("TV_TIMEOUT_SEC", "180"))
TV_BG_TIMEOUT_SEC = int(os.environ.get("TV_BG_TIMEOUT_SEC", "240"))

MAX_BG_WORKERS = int(os.environ.get("MAX_BG_WORKERS", "8"))
CACHE_TTL_SEC = int(os.environ.get("CACHE_TTL_SEC", str(60 * 10)))

WAITING_TEXT = os.environ.get(
    "WAITING_TEXT",
    "正在生成推荐，请稍等…（你也可以稍后回复：继续 / 结果）"
)

VERBOSE_LOG = os.environ.get("VERBOSE_LOG", "1").strip().lower() not in {"0", "false", "no"}

# 关键词：用于“催结果”
POLL_KEYWORDS = {
    "继续", "结果", "完成了吗", "好了没", "出了吗",
    "done", "status", "continue"
}

# 定期清理线程间隔（秒）
CLEAN_INTERVAL_SEC = 15


# =========================
# 内存缓存与后台任务
# =========================
@dataclass
class PendingJob:
    key: str
    created_ts: float
    future: Future


@dataclass
class CachedResult:
    key: str
    created_ts: float
    payload: Dict[str, Any]


_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_BG_WORKERS)
_LOCK = threading.Lock()

_PENDING: Dict[str, PendingJob] = {}
_CACHE: Dict[str, CachedResult] = {}


def log_info(msg: str) -> None:
    if VERBOSE_LOG:
        print(msg, flush=True)


def _now() -> float:
    return time.time()


def _cleanup() -> None:
    """清理过期缓存 & 无用 pending（简单内存策略）"""
    now = _now()
    with _LOCK:
        dead_cache = [k for k, v in _CACHE.items() if now - v.created_ts > CACHE_TTL_SEC]
        for k in dead_cache:
            _CACHE.pop(k, None)

        dead_pending = []
        for k, job in _PENDING.items():
            if job.future.done():
                dead_pending.append(k)
            elif now - job.created_ts > (CACHE_TTL_SEC + 60):
                dead_pending.append(k)
        for k in dead_pending:
            _PENDING.pop(k, None)


def _cleanup_daemon(stop_event: threading.Event) -> None:
    """后台定时清理，避免内存越积越多"""
    while not stop_event.is_set():
        try:
            _cleanup()
        except Exception as e:
            log_info(f"[proxy] cleanup daemon error: {type(e).__name__}: {e}")
        stop_event.wait(CLEAN_INTERVAL_SEC)


def _job_key(user_id: str, session_id: str) -> str:
    return f"{user_id}::{session_id}"


def _call_tv_service(text: str, session_id: str, timeout_sec: float) -> Dict[str, Any]:
    url = TV_SERVICE_BASE.rstrip("/") + TV_DIALOG_PATH
    payload = {"text": text, "session_id": session_id}
    resp = requests.post(url, json=payload, timeout=timeout_sec)
    resp.raise_for_status()
    return resp.json()


def _bg_job_runner(key: str, text: str, session_id: str) -> None:
    try:
        out = _call_tv_service(text=text, session_id=session_id, timeout_sec=TV_BG_TIMEOUT_SEC)
        with _LOCK:
            _CACHE[key] = CachedResult(key=key, created_ts=_now(), payload=out)
        log_info(f"[proxy] bg job finished: key={key} (cached)")
    except Exception as e:
        log_info(f"[proxy] bg job failed: key={key} err={type(e).__name__}: {e}")


def _start_bg_job(key: str, text: str, session_id: str) -> None:
    """同 key 不重复启动后台任务"""
    with _LOCK:
        _cleanup()
        if key in _PENDING:
            return
        future = _EXECUTOR.submit(_bg_job_runner, key, text, session_id)
        _PENDING[key] = PendingJob(key=key, created_ts=_now(), future=future)
    log_info(f"[proxy] bg job started: key={key}")


def _try_get_cached_or_pending(key: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    with _LOCK:
        _cleanup()
        if key in _CACHE:
            return _CACHE[key].payload, False
        if key in _PENDING:
            return None, True
        return None, False


def _is_poll(text: str) -> bool:
    """更稳：包含关键词就算催结果（例如“继续一下”“出结果了吗”）"""
    t = (text or "").strip().lower()
    if not t:
        return False
    for kw in POLL_KEYWORDS:
        if kw.lower() in t:
            return True
    return False


# =========================
# 业务：处理用户消息
# =========================
def handle_user_text(user_id: str, session_id: str, text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    key = _job_key(user_id, session_id)

    # 1) 催结果：优先取缓存
    if _is_poll(t):
        cached, pending = _try_get_cached_or_pending(key)
        if cached is not None:
            reply = cached.get("reply") or cached.get("reply_short") or json.dumps(cached, ensure_ascii=False)
            return {"reply": reply, "raw": cached}
        if pending:
            return {"reply": WAITING_TEXT}
        return {"reply": "还没有可取回的结果。你可以直接发一句完整需求，例如：75 13k ps5 只要tcl"}

    # 2) 正常请求：先直连
    try:
        out = _call_tv_service(text=t, session_id=session_id, timeout_sec=TV_TIMEOUT_SEC)
        reply = out.get("reply") or out.get("reply_short") or json.dumps(out, ensure_ascii=False)
        with _LOCK:
            _CACHE[key] = CachedResult(key=key, created_ts=_now(), payload=out)
        return {"reply": reply, "raw": out}

    except (requests.Timeout, requests.ConnectionError) as e:
        # ✅ 不报红、不告警；回等待文案 + 后台继续跑
        log_info(f"[proxy] non-fatal network/timeout: key={key} err={type(e).__name__}: {e}")
        _start_bg_job(key=key, text=t, session_id=session_id)
        return {"reply": WAITING_TEXT}

    except Exception as e:
        log_info(f"[proxy] non-fatal error: key={key} err={type(e).__name__}: {e}")
        return {"reply": "服务当前繁忙或返回异常，请稍后再试（或换个 session_id 重新发一次）。"}


# =========================
# FastAPI：webhook 服务
# =========================
app = FastAPI()
_STOP_CLEAN = threading.Event()
_CLEAN_THREAD: Optional[threading.Thread] = None


@app.on_event("startup")
def _on_startup():
    global _CLEAN_THREAD
    if _CLEAN_THREAD is None:
        _CLEAN_THREAD = threading.Thread(target=_cleanup_daemon, args=(_STOP_CLEAN,), daemon=True)
        _CLEAN_THREAD.start()
        log_info("[proxy] cleanup daemon started")


@app.on_event("shutdown")
def _on_shutdown():
    _STOP_CLEAN.set()
    log_info("[proxy] shutdown requested")


@app.get("/health")
def health():
    return {
        "ok": True,
        "ts": int(_now()),
        "tv_base": TV_SERVICE_BASE,
        "tv_path": TV_DIALOG_PATH,
        "timeout": TV_TIMEOUT_SEC,
        "bg_timeout": TV_BG_TIMEOUT_SEC,
    }


@app.get("/debug/state")
def debug_state():
    """可选：看一下队列/缓存数量，排查问题用"""
    with _LOCK:
        _cleanup()
        return {
            "ok": True,
            "pending": len(_PENDING),
            "cache": len(_CACHE),
            "cache_ttl_sec": CACHE_TTL_SEC,
        }


@app.post("/webhook")
async def webhook(request: Request):
    """
    兼容字段：
      - user_id / uid
      - session_id / sid
      - text / message
    """
    try:
        data = await request.json()
    except Exception:
        raw = await request.body()
        return JSONResponse(status_code=400, content={"ok": False, "error": f"bad json: {raw[:200]!r}"})

    user_id = str(data.get("user_id") or data.get("uid") or "unknown_user")
    session_id = str(data.get("session_id") or data.get("sid") or "default")
    text = str(data.get("text") or data.get("message") or "")

    out = handle_user_text(user_id=user_id, session_id=session_id, text=text)

    return JSONResponse(content={
        "ok": True,
        "reply": out["reply"],
        "raw": out.get("raw"),
    })


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Clawdbot dialog proxy (local)")
    p.add_argument("--host", default=os.environ.get("PROXY_HOST", "127.0.0.1"), help="listen host")
    p.add_argument("--port", type=int, default=int(os.environ.get("PROXY_PORT", "9000")), help="listen port")

    p.add_argument("--tv-base", default=os.environ.get("TV_SERVICE_BASE", TV_SERVICE_BASE), help="tv service base url")
    p.add_argument("--tv-path", default=os.environ.get("TV_DIALOG_PATH", TV_DIALOG_PATH), help="tv dialog path")
    p.add_argument("--timeout", type=int, default=int(os.environ.get("TV_TIMEOUT_SEC", str(TV_TIMEOUT_SEC))), help="front timeout")
    p.add_argument("--bg-timeout", type=int, default=int(os.environ.get("TV_BG_TIMEOUT_SEC", str(TV_BG_TIMEOUT_SEC))), help="bg timeout")
    p.add_argument("--cache-ttl", type=int, default=int(os.environ.get("CACHE_TTL_SEC", str(CACHE_TTL_SEC))), help="cache ttl seconds")
    return p.parse_args()


if __name__ == "__main__":
    import uvicorn

    args = parse_args()

    # 命令行覆盖
    TV_SERVICE_BASE = str(args.tv_base)
    TV_DIALOG_PATH = str(args.tv_path)
    TV_TIMEOUT_SEC = int(args.timeout)
    TV_BG_TIMEOUT_SEC = int(args.bg_timeout)
    CACHE_TTL_SEC = int(args.cache_ttl)

    uvicorn.run(app, host=str(args.host), port=int(args.port), log_level="info")
