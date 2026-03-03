# -*- coding: utf-8 -*-
"""
clawdbot_dialog_proxy.py
=========================================================
Clawdbot webhook 代理 + 新增“更新数据(2026)”触发爬虫 + 增量diff汇总
---------------------------------------------------------
你在飞书/对话中发送：
  更新数据
就会触发后台执行 2026 爬取，并在完成后给出：
  - 新增多少条
  - 新增了哪些品牌（brand_path）
  - 每个品牌新增了哪些型号（model/title）
你再发送：
  结果 / 继续 / 完成了吗
即可取回最新更新结果摘要（不会报红、不告警）。

依赖：
  pip install fastapi uvicorn requests pyyaml

环境变量（对话代理）：
  TV_SERVICE_BASE   默认 http://127.0.0.1:8000
  TV_DIALOG_PATH    默认 /api/dialog/3p2
  TV_TIMEOUT_SEC    默认 180
  TV_BG_TIMEOUT_SEC 默认 240
  MAX_BG_WORKERS    默认 8
  CACHE_TTL_SEC     默认 600
  WAITING_TEXT      默认 "正在生成推荐..."
  VERBOSE_LOG       默认 1（设为 0 关闭）

环境变量（更新数据/爬虫触发）：
  TVGRAB_API_TOKEN          默认 CHANGE_ME（务必改）
  TVGRAB_REPO_ROOT          默认 本文件所在目录（TV_Grab 仓库根目录）
  TVGRAB_BRANDS_YAML        默认 brands.yaml
  TVGRAB_OUT_ROOT           默认 output_all_brands_2026_spec   # 你仓库里已有这个目录
  TVGRAB_TARGET_YEAR        默认 2026（固定）
  TVGRAB_HEADLESS           默认 1
  TVGRAB_SKIP_IF_EXISTS     默认 1（增量）
  TVGRAB_SCRAPE_SCRIPT      默认 step3_scrape_2026_detail_specs.py
  TVGRAB_MAX_ITEMS          默认 -1（全量；但 skip_if_exists=1 时等价“只抓新增没存在的文件”）

启动：
  /c/software/Anaconda3/python.exe clawdbot_dialog_proxy.py --port 9000
"""

from __future__ import annotations

import argparse
import json
import os
import time
import threading
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List, Set
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path

import requests
import yaml
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

# 触发更新数据命令关键词（可按需扩展）
UPDATE_KEYWORDS = {
    "更新数据", "更新2026", "抓取2026", "爬取2026", "同步2026"
}

# 定期清理线程间隔（秒）
CLEAN_INTERVAL_SEC = 15


# =========================
# 新增：TV_Grab 爬虫触发配置（只跑 2026）
# =========================
TVGRAB_API_TOKEN = os.environ.get("TVGRAB_API_TOKEN", "CHANGE_ME")

TVGRAB_REPO_ROOT = Path(os.environ.get("TVGRAB_REPO_ROOT", str(Path(__file__).resolve().parent))).resolve()
TVGRAB_BRANDS_YAML = os.environ.get("TVGRAB_BRANDS_YAML", "brands.yaml")
TVGRAB_OUT_ROOT = os.environ.get("TVGRAB_OUT_ROOT", "output_all_brands_2026_spec")

TVGRAB_TARGET_YEAR = int(os.environ.get("TVGRAB_TARGET_YEAR", "2026"))  # 固定 2026
TVGRAB_HEADLESS = int(os.environ.get("TVGRAB_HEADLESS", "1"))
TVGRAB_SKIP_IF_EXISTS = int(os.environ.get("TVGRAB_SKIP_IF_EXISTS", "1"))
TVGRAB_MAX_ITEMS = int(os.environ.get("TVGRAB_MAX_ITEMS", "-1"))

# 默认用你提供的 2026 detail specs 脚本（支持 --brands_yaml --target_year --out_root --headless --max_items --skip_if_exists）
# 该脚本会把页面参数空值置为 '-' 并保存 YAML，适合“新增机型增量抓取”:contentReference[oaicite:1]{index=1}
TVGRAB_SCRAPE_SCRIPT = os.environ.get("TVGRAB_SCRAPE_SCRIPT", "step3_scrape_2026_detail_specs.py")

# 更新任务锁
_TVGRAB_LOCK = threading.Lock()
_TVGRAB_FUTURE: Optional[Future] = None

# 运行状态缓存（内存即可）
_TVGRAB_STATE: Dict[str, Any] = {
    "running": False,
    "last_start": None,
    "last_end": None,
    "last_exit_code": None,
    "last_summary": None,
    "last_error": None,
}


# =========================
# 内存缓存与后台任务（对话代理）
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
        log_info(f"[proxy] bg dialog job finished: key={key} (cached)")
    except Exception as e:
        log_info(f"[proxy] bg dialog job failed: key={key} err={type(e).__name__}: {e}")


def _start_bg_dialog_job(key: str, text: str, session_id: str) -> None:
    """同 key 不重复启动后台任务（对话请求）"""
    with _LOCK:
        _cleanup()
        if key in _PENDING:
            return
        future = _EXECUTOR.submit(_bg_job_runner, key, text, session_id)
        _PENDING[key] = PendingJob(key=key, created_ts=_now(), future=future)
    log_info(f"[proxy] bg dialog job started: key={key}")


def _try_get_cached_or_pending(key: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    with _LOCK:
        _cleanup()
        if key in _CACHE:
            return _CACHE[key].payload, False
        if key in _PENDING:
            return None, True
        return None, False


def _is_poll(text: str) -> bool:
    """包含关键词就算催结果（例如“继续一下”“出结果了吗”）"""
    t = (text or "").strip().lower()
    if not t:
        return False
    for kw in POLL_KEYWORDS:
        if kw.lower() in t:
            return True
    return False


def _is_update_cmd(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    # 允许“更新数据”四个字包含在句子里
    for kw in UPDATE_KEYWORDS:
        if kw in t:
            return True
    # 兜底：完全等于“更新数据”
    return t == "更新数据"


# =========================
# 新增：TV_Grab 扫描输出目录、做diff、生成摘要
# =========================
def _safe_load_yaml(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return None


def _extract_brand_model_from_yaml(obj: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    兼容多种结构：
    - step3_batch_brand_paths_2026.py：顶层 brand/model 字段 :contentReference[oaicite:2]{index=2}
    - 其他结构：meta/product/spec 等
    """
    if not isinstance(obj, dict):
        return None, None

    brand = obj.get("brand")
    model = obj.get("model")

    # 常见嵌套结构
    if (not brand or not model) and isinstance(obj.get("product"), dict):
        brand = brand or obj["product"].get("brand")
        model = model or obj["product"].get("model")

    if (not brand or not model) and isinstance(obj.get("spec"), dict):
        # spec 里未必有 brand/model，但尝试
        brand = brand or obj["spec"].get("brand")
        model = model or obj["spec"].get("model")

    # 兜底：用 title/slug 作为 model
    if not model:
        model = obj.get("title") or obj.get("name")

    if brand:
        brand = str(brand).strip()
    if model:
        model = str(model).strip()

    return brand or None, model or None


def _scan_inventory(out_root: Path) -> Dict[str, Set[str]]:
    """
    返回：{ brand_path(or brand) : set(models) }
    只统计“产品规格类 YAML”，尽量跳过 summary/brands/config。
    """
    inv: Dict[str, Set[str]] = {}

    if not out_root.exists():
        return inv

    # 扫描所有 yaml/yml
    for p in out_root.rglob("*.y*ml"):
        name = p.name.lower()

        # 跳过明显不是产品规格的文件
        if "brands" in name or "summary" in name or "counts" in name:
            continue
        if name.startswith("cards_") or name.endswith("_counts.yaml"):
            continue

        obj = _safe_load_yaml(p)
        if not obj:
            continue

        brand, model = _extract_brand_model_from_yaml(obj)
        if not model:
            # 再兜底：从文件名提取（pid_spec.yaml 这种）
            model = p.stem

        if not brand:
            # 再兜底：用目录名当 brand_path（很多输出是按 brand_path 分目录）
            # 比如 out_root/Hisense/xxx.yaml
            try:
                rel = p.relative_to(out_root).parts
                if len(rel) >= 2:
                    brand = rel[0]
            except Exception:
                brand = "unknown"

        brand = str(brand)
        model = str(model)

        inv.setdefault(brand, set()).add(model)

    return inv


def _diff_inventory(before: Dict[str, Set[str]], after: Dict[str, Set[str]]) -> Dict[str, Any]:
    before_brands = set(before.keys())
    after_brands = set(after.keys())

    added_brands = sorted(list(after_brands - before_brands))

    added_items_by_brand: Dict[str, List[str]] = {}
    total_added = 0

    for b in sorted(after_brands):
        bef = before.get(b, set())
        aft = after.get(b, set())
        added = sorted(list(aft - bef))
        if added:
            added_items_by_brand[b] = added
            total_added += len(added)

    return {
        "total_added": total_added,
        "added_brands": added_brands,
        "added_items_by_brand": added_items_by_brand,
        "before_brand_count": len(before_brands),
        "after_brand_count": len(after_brands),
    }


def _format_update_summary(diff: Dict[str, Any], out_root: Path, exit_code: int) -> str:
    total_added = diff.get("total_added", 0)
    added_brands = diff.get("added_brands", [])
    added_items_by_brand = diff.get("added_items_by_brand", {})

    lines: List[str] = []
    lines.append(f"✅ 2026 更新任务完成（exit_code={exit_code}）")
    lines.append(f"输出目录：{out_root}")
    lines.append(f"新增条目：{total_added} 条")
    if added_brands:
        lines.append(f"新增品牌：{', '.join(added_brands)}")
    else:
        lines.append("新增品牌：无")

    # 每个品牌列出新增型号（控制长度，避免太长）
    if added_items_by_brand:
        lines.append("\n—— 新增型号（按品牌）——")
        for b, models in added_items_by_brand.items():
            show = models[:20]
            more = len(models) - len(show)
            if more > 0:
                lines.append(f"- {b}: {', '.join(show)} ...（另有 {more} 条）")
            else:
                lines.append(f"- {b}: {', '.join(show)}")
    else:
        lines.append("新增型号：无（可能今天没有新发布机型，或你本地已存在对应 YAML，被 skip 掉了）")

    return "\n".join(lines)


def _tvgrab_check_token(token: str) -> bool:
    # token 未设置时仍可跑，但建议必须设置（否则不安全）
    return (TVGRAB_API_TOKEN != "CHANGE_ME") and (token == TVGRAB_API_TOKEN)


def _tvgrab_run_update(out_root: Path) -> Tuple[int, str]:
    """
    执行 2026 更新：运行 step3_scrape_2026_detail_specs.py（默认）
    返回：exit_code, log_tail
    """
    script = TVGRAB_REPO_ROOT / TVGRAB_SCRAPE_SCRIPT
    brands_yaml = TVGRAB_REPO_ROOT / TVGRAB_BRANDS_YAML

    if not script.exists():
        raise RuntimeError(f"scrape script not found: {script}")
    if not brands_yaml.exists():
        raise RuntimeError(f"brands.yaml not found: {brands_yaml}")

    cmd = [
        str(os.environ.get("PYTHON_BIN") or "python"),
        str(script),
        "--brands_yaml", str(brands_yaml),
        "--target_year", str(TVGRAB_TARGET_YEAR),
        "--out_root", str(out_root),
        "--headless", str(TVGRAB_HEADLESS),
        "--max_items", str(TVGRAB_MAX_ITEMS),
        "--skip_if_exists", str(TVGRAB_SKIP_IF_EXISTS),
    ]

    # 用当前进程的 python 更可靠（Conda）
    cmd[0] = os.sys.executable

    log_info(f"[tvgrab] RUN CMD: {' '.join(cmd)}")

    p = subprocess.Popen(
        cmd,
        cwd=str(TVGRAB_REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert p.stdout is not None
    tail: List[str] = []
    for line in p.stdout:
        # 只保留尾部 200 行用于返回（完整日志你也可以自己重定向到文件）
        tail.append(line.rstrip("\n"))
        if len(tail) > 200:
            tail = tail[-200:]
    p.wait()
    return int(p.returncode), "\n".join(tail[-120:])


def _tvgrab_bg_update_runner(job_key: str) -> None:
    """
    后台执行更新，并把“摘要结果”放进 _CACHE（复用你现有的取回机制）
    """
    global _TVGRAB_STATE
    start_ts = time.strftime("%Y-%m-%d %H:%M:%S")

    out_root = (TVGRAB_REPO_ROOT / TVGRAB_OUT_ROOT).resolve()

    with _TVGRAB_LOCK:
        _TVGRAB_STATE["running"] = True
        _TVGRAB_STATE["last_start"] = start_ts
        _TVGRAB_STATE["last_end"] = None
        _TVGRAB_STATE["last_exit_code"] = None
        _TVGRAB_STATE["last_summary"] = None
        _TVGRAB_STATE["last_error"] = None

    try:
        before = _scan_inventory(out_root)
        code, tail = _tvgrab_run_update(out_root)
        after = _scan_inventory(out_root)
        diff = _diff_inventory(before, after)

        summary_text = _format_update_summary(diff, out_root, code)

        payload = {
            "reply": summary_text,
            "update": {
                "exit_code": code,
                "out_root": str(out_root),
                "diff": diff,
                "log_tail": tail,
            }
        }

        with _LOCK:
            _CACHE[job_key] = CachedResult(key=job_key, created_ts=_now(), payload=payload)

        end_ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with _TVGRAB_LOCK:
            _TVGRAB_STATE["running"] = False
            _TVGRAB_STATE["last_end"] = end_ts
            _TVGRAB_STATE["last_exit_code"] = code
            _TVGRAB_STATE["last_summary"] = summary_text

        log_info(f"[tvgrab] update done: exit={code} added={diff.get('total_added')}")

    except Exception as e:
        end_ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with _TVGRAB_LOCK:
            _TVGRAB_STATE["running"] = False
            _TVGRAB_STATE["last_end"] = end_ts
            _TVGRAB_STATE["last_error"] = f"{type(e).__name__}: {e}"

        with _LOCK:
            _CACHE[job_key] = CachedResult(
                key=job_key,
                created_ts=_now(),
                payload={"reply": f"❌ 更新失败：{type(e).__name__}: {e}"}
            )
        log_info(f"[tvgrab] update failed: {type(e).__name__}: {e}")

    finally:
        with _TVGRAB_LOCK:
            global _TVGRAB_FUTURE
            _TVGRAB_FUTURE = None


def _start_tvgrab_update(job_key: str) -> bool:
    """
    启动更新任务：同一时间只允许一个更新任务在跑
    """
    global _TVGRAB_FUTURE
    with _TVGRAB_LOCK:
        if _TVGRAB_FUTURE is not None and not _TVGRAB_FUTURE.done():
            return False
        # 用对话的 executor 也行，但单独开一个更干净
        _TVGRAB_FUTURE = _EXECUTOR.submit(_tvgrab_bg_update_runner, job_key)
        return True


# =========================
# 业务：处理用户消息
# =========================
def handle_user_text(user_id: str, session_id: str, text: str, raw_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    t = (text or "").strip()
    key = _job_key(user_id, session_id)

    # 特殊：更新数据 job key（与对话 key 隔离，避免覆盖推荐缓存）
    update_key = f"tvgrab_update::{key}"

    # 0) 若用户发“更新数据”：触发 2026 更新（后台跑）
    if _is_update_cmd(t):
        # token 可选：若你担心内网被随便触发，可要求携带 token
        token = ""
        if isinstance(raw_data, dict):
            token = str(raw_data.get("token") or "")

        # 如果你已经把 TVGRAB_API_TOKEN 改掉了（不是 CHANGE_ME），则必须校验 token
        if TVGRAB_API_TOKEN != "CHANGE_ME" and not _tvgrab_check_token(token):
            return {"reply": "需要 token 才能更新。请在请求体里带上 token（或把 TVGRAB_API_TOKEN 设为 CHANGE_ME 以关闭校验）。"}

        ok = _start_tvgrab_update(update_key)
        if not ok:
            return {"reply": "更新任务正在运行中。你可以稍后回复：结果 / 继续 来查看进度或取回结果。"}

        return {"reply": "✅ 已开始更新 2026 数据（后台运行）。完成后你回复：结果 / 继续，我会把新增品牌/型号/条数发你。"}

    # 1) 催结果：优先取更新结果，再取对话结果
    if _is_poll(t):
        # 优先查更新任务缓存
        cached_u, pending_u = _try_get_cached_or_pending(update_key)
        if cached_u is not None:
            reply = cached_u.get("reply") or json.dumps(cached_u, ensure_ascii=False)
            return {"reply": reply, "raw": cached_u}

        # 若更新任务正在跑
        with _TVGRAB_LOCK:
            running = bool(_TVGRAB_STATE.get("running"))
        if running:
            return {"reply": "更新任务还在运行中…你稍后再回复：结果 / 继续（我会返回新增摘要）。"}

        # 再查对话缓存
        cached, pending = _try_get_cached_or_pending(key)
        if cached is not None:
            reply = cached.get("reply") or cached.get("reply_short") or json.dumps(cached, ensure_ascii=False)
            return {"reply": reply, "raw": cached}
        if pending:
            return {"reply": WAITING_TEXT}

        return {"reply": "还没有可取回的结果。你可以发：更新数据，或发一句完整需求例如：75 13k ps5 只要tcl"}

    # 2) 正常对话请求：先直连 TV 服务
    try:
        out = _call_tv_service(text=t, session_id=session_id, timeout_sec=TV_TIMEOUT_SEC)
        reply = out.get("reply") or out.get("reply_short") or json.dumps(out, ensure_ascii=False)
        with _LOCK:
            _CACHE[key] = CachedResult(key=key, created_ts=_now(), payload=out)
        return {"reply": reply, "raw": out}

    except (requests.Timeout, requests.ConnectionError) as e:
        # ✅ 不报红、不告警；回等待文案 + 后台继续跑
        log_info(f"[proxy] non-fatal network/timeout: key={key} err={type(e).__name__}: {e}")
        _start_bg_dialog_job(key=key, text=t, session_id=session_id)
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
    with _TVGRAB_LOCK:
        st = dict(_TVGRAB_STATE)
    return {
        "ok": True,
        "ts": int(_now()),
        "tv_base": TV_SERVICE_BASE,
        "tv_path": TV_DIALOG_PATH,
        "timeout": TV_TIMEOUT_SEC,
        "bg_timeout": TV_BG_TIMEOUT_SEC,
        "tvgrab": {
            "repo_root": str(TVGRAB_REPO_ROOT),
            "out_root": str((TVGRAB_REPO_ROOT / TVGRAB_OUT_ROOT).resolve()),
            "brands_yaml": TVGRAB_BRANDS_YAML,
            "script": TVGRAB_SCRAPE_SCRIPT,
            "target_year": TVGRAB_TARGET_YEAR,
            "headless": TVGRAB_HEADLESS,
            "skip_if_exists": TVGRAB_SKIP_IF_EXISTS,
            "max_items": TVGRAB_MAX_ITEMS,
            "state": st,
        }
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
    可选带 token（用于更新数据接口校验）：
      - token
    """
    try:
        data = await request.json()
    except Exception:
        raw = await request.body()
        return JSONResponse(status_code=400, content={"ok": False, "error": f"bad json: {raw[:200]!r}"})

    user_id = str(data.get("user_id") or data.get("uid") or "unknown_user")
    session_id = str(data.get("session_id") or data.get("sid") or "default")
    text = str(data.get("text") or data.get("message") or "")

    out = handle_user_text(user_id=user_id, session_id=session_id, text=text, raw_data=data)

    return JSONResponse(content={
        "ok": True,
        "reply": out["reply"],
        "raw": out.get("raw"),
    })


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Clawdbot dialog proxy (local) + TV_Grab 2026 update")
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