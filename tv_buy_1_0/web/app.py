# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import time
import sqlite3
import importlib
import io
import sys
from uuid import uuid4
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List, Iterable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# =========================================================
# ✅ 安全处理 stdout/stderr：避免 uvicorn 启动时 stderr 被关闭导致 lost sys.stderr
# =========================================================
def _safe_rewrap_stream(stream, encoding: str = "utf-8"):
    """
    有些环境下（尤其 Git Bash + Windows + uvicorn），sys.stderr 可能在启动过程中被关闭/替换。
    这里做“安全重包”：
    - stream 为 None / 已关闭 => 不处理
    - 没有 buffer / 已经是 utf-8 wrapper => 不处理
    - 否则用 TextIOWrapper 包一层，保证中文输出不炸
    """
    try:
        if stream is None:
            return stream
        if getattr(stream, "closed", False):
            return stream
        buf = getattr(stream, "buffer", None)
        if buf is None:
            return stream
        enc = getattr(stream, "encoding", "") or ""
        if isinstance(stream, io.TextIOBase) and enc.lower().startswith("utf-8"):
            return stream
        return io.TextIOWrapper(buf, encoding=encoding, errors="replace")
    except Exception:
        return stream


sys.stdout = _safe_rewrap_stream(sys.stdout, "utf-8")
sys.stderr = _safe_rewrap_stream(sys.stderr, "utf-8")

# =========================================================
# 你项目里已有：run_reco（保留 /api/chat 1.0 推荐逻辑）
# =========================================================
from tv_buy_1_0.run_reco import recommend_text, list_candidates, format_candidates

# tools 路由（保留）
from tv_buy_1_0.tools.tool_api import router as tools_router

# 你已有的报告路由（/api/report/contrast）
from tv_buy_1_0.g2_lab.api.router_report_contrast import router as g2_report_router

# 报告生成（用于 /api/g2/contrast_report 串联）
from tv_buy_1_0.g2_lab.report.contrast_report import generate_contrast_report
from tv_buy_1_0.g2_lab.report.postprocess import split_output

# =========================================================
# Root Paths (IMPORTANT)
# =========================================================
TVBUY_ROOT = Path(__file__).resolve().parents[1]  # => tv_buy_1_0/

# =========================================================
# ✅ 默认环境变量（只在“未设置时”写入，避免每次启动都手动 export）
# - 不要在代码里写 OPENAI_API_KEY（安全）
# =========================================================
def _env_default(key: str, value: str) -> None:
    if (os.environ.get(key) or "").strip() == "":
        os.environ[key] = value


# 1) YAML 目录默认值（你也可以之后用环境变量覆盖）
_env_default("TVBUY_PRODUCTS_YAML_DIR", str(TVBUY_ROOT / "data_raw" / "excel_import_all_v1"))

# 2) YAML 缓存 TTL 默认 300s（提速关键）
_env_default("TVBUY_YAML_CACHE_TTL", "300")

# 3) 默认启用“晓春哥 XCG” LLM（有 key 才会真正调用）
_env_default("TVBUY_ENABLE_XCG_LLM", "1")

# 4) OpenAI 超时（秒）—— ✅ 默认改成更“硬”的 2.5s
_env_default("TVBUY_OPENAI_TIMEOUT", "2.5")

# 5) sqlite fallback 默认关闭
_env_default("TVBUY_USE_SQLITE_FALLBACK", "0")

# 6) YAML 扫描深度限制（防止目录很深时拖慢）
_env_default("TVBUY_YAML_MAX_DEPTH", "8")

# 7) xcg_notes 最多扫描文件数（防止笔记太多时拖慢）
_env_default("TVBUY_XCG_NOTES_MAX_FILES", "80")

# 8) LLM 文本缓存 TTL（秒）—— 10 分钟
_env_default("TVBUY_LLM_CACHE_TTL", "600")

# 9) 熔断：连续失败多少次开启熔断
_env_default("TVBUY_LLM_CIRCUIT_FAILS", "3")

# 10) 熔断：开启后持续多久（秒）
_env_default("TVBUY_LLM_CIRCUIT_OPEN_SEC", "120")


# =========================================================
# 数据源配置
# =========================================================
YAML_PRODUCTS_DIR = Path(os.environ.get("TVBUY_PRODUCTS_YAML_DIR", "").strip())
USE_SQLITE_FALLBACK = (os.environ.get("TVBUY_USE_SQLITE_FALLBACK", "0").strip() == "1")
SQLITE_DB = Path(os.environ.get("TVBUY_SQLITE_DB", str(TVBUY_ROOT / "db" / "tv.sqlite")))
XCG_NOTES_DIR = Path(os.environ.get("TVBUY_XCG_NOTES_DIR", str(TVBUY_ROOT / "data_raw" / "xcg_notes")))

# =========================================================
# LLM 配置（可选）
# - 兼容 Git Bash export：OPENAI_MODEL / OPENAI_BASE_URL / OPENAI_API_KEY
# - 也兼容你自定义：TVBUY_OPENAI_MODEL
# =========================================================
OPENAI_MODEL = (
    os.environ.get("OPENAI_MODEL", "").strip()
    or os.environ.get("TVBUY_OPENAI_MODEL", "").strip()
    or "gpt-5.2"
)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "").strip() or None

OPENAI_TIMEOUT_SEC = float(os.environ.get("TVBUY_OPENAI_TIMEOUT", "2.5").strip() or "2.5")
ENABLE_XCG_LLM = (os.environ.get("TVBUY_ENABLE_XCG_LLM", "1").strip() == "1")

# =========================================================
# YAML 加载策略：缓存 + 目录签名（快） + 文件列表缓存
# =========================================================
_YAML_CACHE_TTL = float((os.environ.get("TVBUY_YAML_CACHE_TTL") or "300").strip() or "300")
_YAML_MAX_DEPTH = int((os.environ.get("TVBUY_YAML_MAX_DEPTH") or "8").strip() or "8")
_XCG_NOTES_MAX_FILES = int((os.environ.get("TVBUY_XCG_NOTES_MAX_FILES") or "80").strip() or "80")

_yaml_cache: Dict[str, Any] = {"ts": 0.0, "sig": None, "items": [], "loaded": 0, "paths": []}

# =========================================================
# YAML 解析库（优先 ruamel.yaml，否则 pyyaml）
# =========================================================
_YAML_IMPL = "none"
_yaml_ruamel = None
_yaml_pyyaml = None
try:
    from ruamel.yaml import YAML  # type: ignore

    _yaml_ruamel = YAML(typ="safe")
    _YAML_IMPL = "ruamel"
except Exception:
    try:
        import yaml as _yaml_pyyaml  # type: ignore

        _YAML_IMPL = "pyyaml"
    except Exception:
        _yaml_pyyaml = None
        _YAML_IMPL = "none"


def _yaml_safe_load(text: str) -> Any:
    if _YAML_IMPL == "ruamel" and _yaml_ruamel is not None:
        import io as _io
        return _yaml_ruamel.load(_io.StringIO(text))
    if _YAML_IMPL == "pyyaml" and _yaml_pyyaml is not None:
        return _yaml_pyyaml.safe_load(text)
    raise RuntimeError("No YAML parser installed. Please install ruamel.yaml or pyyaml.")


# =========================================================
# App
# =========================================================
app = FastAPI()
app.include_router(g2_report_router)
app.include_router(tools_router)

templates = Jinja2Templates(directory=str(TVBUY_ROOT / "web" / "templates"))

# =========================================================
# Storage (固定到 tv_buy_1_0 目录下)
# =========================================================
UPLOAD_DIR = TVBUY_ROOT / "data_raw" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

CONTRAST_OUT_DIR = TVBUY_ROOT / "summaries" / "contrast_records"
CONTRAST_OUT_DIR.mkdir(parents=True, exist_ok=True)

CONTRAST_ANALYSIS_DIR = TVBUY_ROOT / "summaries" / "contrast_analysis"
CONTRAST_ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

# =========================================================
# Body Helpers (兼容 Git Bash curl 中文 JSON)
# =========================================================
async def _read_json_body(request: Request) -> Dict[str, Any]:
    raw = await request.body()
    last_err: Optional[Exception] = None
    for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk", "cp936"):
        try:
            s = raw.decode(enc)
            return json.loads(s)
        except Exception as e:
            last_err = e
    raise ValueError(f"Bad JSON body (decode failed): {last_err}")


def _json_ok(reply: str, raw: Optional[Dict[str, Any]] = None) -> JSONResponse:
    return JSONResponse(content={"ok": True, "reply": reply, "raw": raw or {}})


def _json_err(msg: str, status_code: int = 400, raw: Optional[Dict[str, Any]] = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"ok": False, "error": msg, "raw": raw or {}})


# =========================================================
# Upload Helpers
# =========================================================
def _is_allowed_image(filename: str) -> bool:
    fn = (filename or "").lower()
    return fn.endswith(".png") or fn.endswith(".jpg") or fn.endswith(".jpeg") or fn.endswith(".webp")


def _find_uploaded_image(image_id: str) -> str:
    for ext in [".png", ".jpg", ".jpeg", ".webp"]:
        p = UPLOAD_DIR / f"{image_id}{ext}"
        if p.exists():
            return str(p)
    raise FileNotFoundError(f"找不到图片：{image_id}（UPLOAD_DIR={UPLOAD_DIR}）")


def _safe_prefix(device_id: Optional[str], fallback: str) -> str:
    if not device_id:
        return fallback
    safe = re.sub(r"[^0-9A-Za-z_\-]+", "_", device_id.strip())
    return safe or fallback


def _ensure_text(x: Any) -> str:
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        for k in ("text", "content", "output_text", "message"):
            v = x.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return str(x)
    for attr in ("text", "content", "output_text", "message"):
        v = getattr(x, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return str(x)


# =========================================================
# Upload API
# =========================================================
@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    if not _is_allowed_image(file.filename or ""):
        return JSONResponse(status_code=400, content={"error": "只支持 png/jpg/jpeg/webp"})

    image_id = uuid4().hex
    suffix = Path(file.filename).suffix.lower()
    save_path = UPLOAD_DIR / f"{image_id}{suffix}"

    data = await file.read()
    save_path.write_bytes(data)

    return {"image_id": image_id, "path": str(save_path)}


# =========================================================
# G2 Contrast (OCR -> YAML)
# =========================================================
class ContrastOCRReq(BaseModel):
    native_image_id: str
    effective_image_id: str
    device_id: Optional[str] = None


@app.post("/api/g2/contrast_ocr")
def api_g2_contrast_ocr(req: ContrastOCRReq):
    try:
        native_path = _find_uploaded_image(req.native_image_id)
        effective_path = _find_uploaded_image(req.effective_image_id)

        from tv_buy_1_0.g2_lab.services.contrast_ocr_service import contrast_yaml_from_images

        yaml_text = contrast_yaml_from_images(native_path, effective_path)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = _safe_prefix(req.device_id, "contrast")
        out_path = CONTRAST_OUT_DIR / f"{prefix}_{ts}.yaml"
        out_path.write_text(yaml_text, encoding="utf-8")

        return {"yaml": yaml_text, "saved_to": str(out_path)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": "生成失败", "detail": str(e)})


# =========================================================
# G2 Contrast (OCR -> YAML -> Report)
# =========================================================
class ContrastReportReq(BaseModel):
    native_image_id: str
    effective_image_id: str
    device_id: Optional[str] = None


@app.post("/api/g2/contrast_report")
def api_g2_contrast_report(req: ContrastReportReq):
    try:
        native_path = _find_uploaded_image(req.native_image_id)
        effective_path = _find_uploaded_image(req.effective_image_id)

        from tv_buy_1_0.g2_lab.services.contrast_ocr_service import contrast_yaml_from_images

        yaml_text = contrast_yaml_from_images(native_path, effective_path)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = _safe_prefix(req.device_id, "contrast")

        yaml_path = CONTRAST_OUT_DIR / f"{prefix}_{ts}.yaml"
        yaml_path.write_text(yaml_text, encoding="utf-8")

        obj = None
        try:
            obj = _yaml_safe_load(yaml_text)
        except Exception:
            obj = None

        if isinstance(obj, dict) and "contrast_test_record" in obj and isinstance(obj["contrast_test_record"], dict):
            contrast_record = obj["contrast_test_record"]
        elif isinstance(obj, dict):
            contrast_record = obj
        else:
            raise ValueError("OCR 生成的 YAML 无法解析为 dict")

        meta, raw_output = generate_contrast_report(contrast_record)
        raw_output_text = _ensure_text(raw_output)

        analysis_text, editorial_yaml = split_output(raw_output_text)

        analysis_path = CONTRAST_ANALYSIS_DIR / f"{prefix}_{ts}.txt"
        analysis_path.write_text(analysis_text or "", encoding="utf-8")

        return {
            "yaml": yaml_text,
            "saved_to_yaml": str(yaml_path),
            "analysis": analysis_text,
            "saved_to_analysis": str(analysis_path),
            "editorial_verdict_yaml": editorial_yaml,
            "meta": meta,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": "生成失败", "detail": str(e)})


# =========================================================
# 简化版：只收集 品牌 / 尺寸 / 预算
# =========================================================
BRAND_ALIASES = {
    "TCL": ["tcl", "t.c.l", "只看tcl", "只要tcl", "我要tcl", "仅tcl", "我只看tcl"],
    "海信": ["海信", "hisense"],
    "小米": ["小米", "mi", "xiaomi", "redmi", "红米"],
    "雷鸟": ["雷鸟", "ffalcon", "f falcon", "f-falcon", "falcon", "雷鸟电视"],
    "Vidda": ["vidda", "vidda发现", "发现", "发现x", "发现x mini"],
    "创维": ["创维", "skyworth", "酷开", "coocaa"],
    "索尼": ["索尼", "sony"],
    "三星": ["三星", "samsung"],
    "LG": ["lg"],
}


def _parse_brand(raw: str) -> Optional[str]:
    t = (raw or "").strip().lower()
    for brand, kws in BRAND_ALIASES.items():
        if any(k in t for k in kws):
            return brand
    mbrand = re.search(r"(只看|只要|仅看|我只看|我要)\s*([a-zA-Z\u4e00-\u9fa5]{2,12})", raw or "")
    if mbrand:
        b = mbrand.group(2).strip()
        bl = b.lower()
        if bl == "tcl":
            return "TCL"
        if bl in ("ffalcon", "f-falcon", "falcon"):
            return "雷鸟"
        if bl in ("skyworth",):
            return "创维"
        return b
    return None


def _parse_size(raw: str) -> Optional[int]:
    t = (raw or "").strip().lower()
    m = re.search(r"(\d{2,3})\s*(寸|英寸|吋|inch|in|\")", t)
    if m:
        v = int(m.group(1))
        if 40 <= v <= 120:
            return v
    nums = re.findall(r"\b(\d{2,3})\b", t)
    for s in nums:
        v = int(s)
        if 40 <= v <= 120:
            return v
    return None


def _parse_budget(raw: str) -> Optional[int]:
    t = (raw or "").strip().lower().replace(",", "")
    m = re.search(r"预算\s*(\d{3,6})", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{3,6})\s*预算", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{3,6})\s*(以内|以下|不超过|之内)", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+(\.\d+)?)\s*万", t)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r"(\d{1,3})\s*k\b", t)
    if m:
        return int(m.group(1)) * 1000
    return None


def _parse_budget_from_free_numbers(raw: str, size: Optional[int]) -> Optional[int]:
    t = (raw or "").strip().lower().replace(",", "")
    nums = re.findall(r"\b(\d{3,6})\b", t)
    cands: List[int] = []
    for s in nums:
        try:
            v = int(s)
        except Exception:
            continue
        if v < 1000:
            continue
        if size is not None and v == int(size):
            continue
        cands.append(v)
    if not cands:
        return None
    return max(cands)


def parse_slots_simple(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    tl = raw.lower()
    if any(k in tl for k in ["重置", "清空", "重新开始", "reset"]):
        return {"_reset": True}

    brand = _parse_brand(raw)
    size = _parse_size(raw)
    budget = _parse_budget(raw)

    if budget is None:
        budget = _parse_budget_from_free_numbers(raw, size=size)

    return {"brand": brand, "size": size, "budget": budget}


def _next_missing_simple(state: Dict[str, Any]) -> Optional[str]:
    if state.get("brand") is None:
        return "brand"
    if state.get("size") is None:
        return "size"
    if state.get("budget") is None:
        return "budget"
    return None


QUESTION_SIMPLE = {
    "brand": "你想看哪个品牌？比如：TCL / 海信 / 小米 / 雷鸟 / Vidda / 创维（直接回“雷鸟”也行）",
    "size": "你想要多大尺寸？比如：65 / 75 / 85 / 98（直接回“98寸”也行）",
    "budget": "预算上限多少？比如：12000（或 1.2万 / 12k）",
}

# =========================================================
# YAML 产品加载（缓存 + 快速目录签名 + 文件列表缓存）
# =========================================================
def _safe_int(x: Any) -> Optional[int]:
    if x is None:
        return None
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return int(x)
    s = str(x).strip()
    if not s:
        return None
    s = s.replace(",", "")
    s = re.sub(r"[^\d.]", "", s)
    if not s:
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def _pick(d: Dict[str, Any], keys: Iterable[str]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, "", []):
            return d[k]
    return None


def _flatten_yaml_obj(obj: Any) -> List[Dict[str, Any]]:
    if obj is None:
        return []
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        for k in ("items", "rows", "products", "data", "models"):
            v = obj.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        return [obj]
    return []


def _normalize_launch(d: Dict[str, Any]) -> Optional[str]:
    v = _pick(d, ["first_release", "发布时间", "发布", "launch", "launch_date", "上市时间", "首发", "release"])
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return s.replace(".", "-").replace("/", "-")


def _normalize_brand(d: Dict[str, Any]) -> Optional[str]:
    v = _pick(d, ["brand", "品牌", "厂商", "manufacturer"])
    if v is None:
        return None
    s = str(v).strip()
    if s.lower() == "tcl":
        return "TCL"
    if s.lower() in ("ffalcon", "f-falcon", "falcon"):
        return "雷鸟"
    if s.lower() in ("skyworth",):
        return "创维"
    return s or None


def _normalize_model(d: Dict[str, Any]) -> Optional[str]:
    v = _pick(d, ["model", "型号", "机型", "name"])
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _normalize_positioning(d: Dict[str, Any]) -> str:
    v = _pick(d, ["positioning", "产品定位", "tier", "product_positioning", "segment"])
    if v is None:
        return "未知"
    s = str(v).strip()
    return s or "未知"


def _expand_excel_import_row(row: Dict[str, Any], source_name: str) -> List[Dict[str, Any]]:
    brand = _normalize_brand(row) or "TCL"
    base_model = _normalize_model(row)
    launch_date = _normalize_launch(row)
    positioning = _normalize_positioning(row)

    variants = row.get("variants")
    if isinstance(variants, list) and base_model:
        out: List[Dict[str, Any]] = []
        for v in variants:
            if not isinstance(v, dict):
                continue
            size_inch = _safe_int(v.get("size_inch"))
            if not size_inch:
                continue

            model = (
                f"{size_inch}{str(base_model).strip()}"
                if not re.match(r"^\d{2,3}", str(base_model).strip())
                else str(base_model).strip()
            )

            price_cny = _safe_int(v.get("price_cny"))

            out.append(
                {
                    "brand": brand,
                    "model": model,
                    "size_inch": int(size_inch),
                    "price_cny": price_cny,
                    "positioning": positioning,
                    "launch_date": launch_date,
                    "source": f"yaml:{source_name}",
                    "_from": "yaml",
                    "_variant": v,
                }
            )
        if out:
            return out

    return [
        {
            "brand": brand,
            "model": base_model,
            "size_inch": _safe_int(row.get("size_inch") or row.get("尺寸") or row.get("size")),
            "price_cny": _safe_int(row.get("price_cny") or row.get("价格") or row.get("price")),
            "positioning": positioning,
            "launch_date": launch_date,
            "source": f"yaml:{source_name}",
            "_from": "yaml",
        }
    ]


def _fast_dir_signature_and_paths(root: Path) -> Tuple[str, List[Path]]:
    if not root.exists():
        return "", []
    paths: List[Path] = []
    cnt = 0
    latest = 0.0

    stack: List[Tuple[Path, int]] = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        if depth > _YAML_MAX_DEPTH:
            continue
        try:
            with os.scandir(d) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append((Path(entry.path), depth + 1))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        name = entry.name.lower()
                        if not (name.endswith(".yaml") or name.endswith(".yml")):
                            continue
                        cnt += 1
                        st = entry.stat(follow_symlinks=False)
                        if st.st_mtime > latest:
                            latest = st.st_mtime
                        paths.append(Path(entry.path))
                    except Exception:
                        continue
        except Exception:
            continue

    sig = f"{cnt}:{int(latest)}"
    paths.sort(key=lambda p: str(p))
    return sig, paths


def _load_yaml_products() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    now = time.time()

    if now - float(_yaml_cache.get("ts", 0.0)) < _YAML_CACHE_TTL:
        return list(_yaml_cache.get("items") or []), {
            "yaml_dir": str(YAML_PRODUCTS_DIR),
            "yaml_loaded": int(_yaml_cache.get("loaded") or 0),
            "yaml_cache_ttl": _YAML_CACHE_TTL,
            "yaml_cache_hit": True,
            "yaml_lib": _YAML_IMPL,
        }

    sig, paths = _fast_dir_signature_and_paths(YAML_PRODUCTS_DIR)

    if sig and sig == _yaml_cache.get("sig"):
        _yaml_cache["ts"] = now
        if not _yaml_cache.get("paths"):
            _yaml_cache["paths"] = paths
        return list(_yaml_cache.get("items") or []), {
            "yaml_dir": str(YAML_PRODUCTS_DIR),
            "yaml_loaded": int(_yaml_cache.get("loaded") or 0),
            "yaml_cache_ttl": _YAML_CACHE_TTL,
            "yaml_cache_hit": True,
            "yaml_lib": _YAML_IMPL,
        }

    items: List[Dict[str, Any]] = []
    loaded_files = 0

    if _YAML_IMPL == "none":
        _yaml_cache.update({"ts": now, "sig": sig, "items": [], "loaded": 0, "paths": paths})
        return [], {
            "yaml_dir": str(YAML_PRODUCTS_DIR),
            "yaml_loaded": 0,
            "yaml_cache_ttl": _YAML_CACHE_TTL,
            "yaml_cache_hit": False,
            "yaml_lib": _YAML_IMPL,
            "warn": "No YAML parser installed (ruamel.yaml / pyyaml).",
        }

    for p in paths:
        try:
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                text = p.read_text(encoding="utf-8-sig")
        except Exception:
            continue

        try:
            obj = _yaml_safe_load(text)
        except Exception:
            continue

        rows = _flatten_yaml_obj(obj)
        if not rows:
            continue

        loaded_files += 1
        for row in rows:
            for it in _expand_excel_import_row(row, source_name=p.name):
                if not it.get("model") and not it.get("brand"):
                    continue
                items.append(it)

    _yaml_cache.update({"ts": now, "sig": sig, "items": items, "loaded": loaded_files, "paths": paths})
    return list(items), {
        "yaml_dir": str(YAML_PRODUCTS_DIR),
        "yaml_loaded": loaded_files,
        "yaml_cache_ttl": _YAML_CACHE_TTL,
        "yaml_cache_hit": False,
        "yaml_lib": _YAML_IMPL,
    }


# =========================================================
# sqlite fallback（默认关闭）
# =========================================================
def _sqlite_find_table_and_cols(conn: sqlite3.Connection) -> Tuple[Optional[str], Dict[str, str]]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall() if r and r[0]]

    prefer = ["tv", "tvs", "tv_models", "models", "products"]
    tables_sorted = sorted(tables, key=lambda x: (0 if x in prefer else 1, x))

    def cols_of(t: str) -> List[str]:
        try:
            cur.execute(f"PRAGMA table_info({t})")
            return [r[1] for r in cur.fetchall() if r and len(r) > 1]
        except Exception:
            return []

    brand_keys = ["brand", "品牌"]
    model_keys = ["model", "型号", "name", "机型"]
    size_keys = ["size_inch", "size", "尺寸", "inch"]
    price_keys = ["price_cny", "price", "价格", "street_rmb"]
    launch_keys = ["launch_date", "release_date", "首发", "发布时间", "publish_date"]

    for t in tables_sorted:
        cols = cols_of(t)
        if not cols:
            continue

        def pick(keys: List[str]) -> Optional[str]:
            for k in keys:
                if k in cols:
                    return k
            return None

        bm = pick(brand_keys)
        mm = pick(model_keys)
        sm = pick(size_keys)
        pm = pick(price_keys)

        if bm and mm and sm and pm:
            lm = pick(launch_keys)
            return t, {"brand": bm, "model": mm, "size": sm, "price": pm, "launch": lm or ""}

    return None, {}


def _sqlite_query_products(brand: Optional[str], size: Optional[int], budget: Optional[int]) -> Tuple[List[Dict[str, Any]], int]:
    if not USE_SQLITE_FALLBACK:
        return [], 0
    if not SQLITE_DB.exists():
        return [], 0

    conn = sqlite3.connect(str(SQLITE_DB))
    conn.row_factory = sqlite3.Row
    try:
        tname, cmap = _sqlite_find_table_and_cols(conn)
        if not tname:
            return [], 0

        where = []
        args: List[Any] = []

        if brand:
            where.append(f"{cmap['brand']} = ?")
            args.append(brand)
        if size:
            where.append(f"{cmap['size']} = ?")
            args.append(int(size))
        if budget is not None:
            where.append(f"{cmap['price']} <= ?")
            args.append(int(budget))

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        order_sql = f" ORDER BY {cmap['price']} DESC"
        sql = f"SELECT * FROM {tname}{where_sql}{order_sql} LIMIT 500"

        cur = conn.cursor()
        cur.execute(sql, args)
        rows = cur.fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            b = d.get(cmap["brand"])
            m = d.get(cmap["model"])
            s = d.get(cmap["size"])
            p = d.get(cmap["price"])
            launch = d.get(cmap["launch"]) if cmap.get("launch") else None

            b_norm = b
            if isinstance(b_norm, str) and b_norm.lower() == "tcl":
                b_norm = "TCL"
            if isinstance(b_norm, str) and b_norm.lower() in ("ffalcon", "f-falcon", "falcon"):
                b_norm = "雷鸟"
            if isinstance(b_norm, str) and b_norm.lower() in ("skyworth",):
                b_norm = "创维"

            out.append(
                {
                    "brand": b_norm,
                    "model": m,
                    "size_inch": _safe_int(s),
                    "price_cny": _safe_int(p),
                    "positioning": "未知",
                    "launch_date": str(launch) if launch not in (None, "") else None,
                    "source": f"sqlite:{SQLITE_DB.name}",
                    "_from": "sqlite",
                }
            )

        return out, len(out)
    finally:
        conn.close()


# =========================================================
# 合并、去重、排序
# =========================================================
def _norm_key(brand: Optional[str], model: Optional[str], size: Optional[int]) -> str:
    b = (brand or "").strip().lower()
    m = (model or "").strip().lower().replace(" ", "")
    s = str(size or "")
    return f"{b}::{m}::{s}"


def _merge_products(yaml_items: List[Dict[str, Any]], sqlite_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mp: Dict[str, Dict[str, Any]] = {}
    for it in sqlite_items:
        k = _norm_key(it.get("brand"), it.get("model"), it.get("size_inch"))
        if k and k not in mp:
            mp[k] = it
    for it in yaml_items:
        k = _norm_key(it.get("brand"), it.get("model"), it.get("size_inch"))
        if not k:
            continue
        mp[k] = it  # YAML 覆盖 sqlite
    return list(mp.values())


def _filter_products(items: List[Dict[str, Any]], brand: Optional[str], size: Optional[int], budget: Optional[int]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items:
        b = it.get("brand")
        s = it.get("size_inch")
        p = it.get("price_cny")

        if brand and (b != brand):
            continue
        if size and (s != size):
            continue
        if budget is not None and isinstance(p, (int, float)) and int(p) > int(budget):
            continue
        out.append(it)
    return out


def _sort_by_price_desc(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key(it: Dict[str, Any]) -> Tuple[int, int]:
        p = it.get("price_cny")
        if isinstance(p, (int, float)):
            return (0, -int(p))
        return (1, 0)

    return sorted(items, key=key)


# =========================================================
# 晓春哥笔记：加载 & 按型号命中（可选）
# =========================================================
def _load_xcg_notes_for_models(models: List[str], max_chars: int = 2200) -> str:
    if not XCG_NOTES_DIR.exists() or not XCG_NOTES_DIR.is_dir():
        return ""

    keys = []
    for m in models:
        if not m:
            continue
        s = str(m).lower()
        s = re.sub(r"[^a-z0-9]+", "", s)
        if s:
            keys.append(s)

    picked: List[str] = []
    files = sorted(XCG_NOTES_DIR.glob("*.md"), key=lambda p: str(p))[:_XCG_NOTES_MAX_FILES]

    for p in files:
        name_key = re.sub(r"[^a-z0-9]+", "", p.name.lower())
        hit = any(k in name_key for k in keys) if keys else False
        if hit:
            try:
                txt = p.read_text(encoding="utf-8")
            except Exception:
                try:
                    txt = p.read_text(encoding="utf-8-sig")
                except Exception:
                    continue
            picked.append(f"【笔记：{p.name}】\n{txt.strip()}\n")
            if sum(len(x) for x in picked) > max_chars:
                break

    if not picked and keys:
        for p in files:
            try:
                try:
                    txt_head = p.read_text(encoding="utf-8")[:6000]
                except Exception:
                    txt_head = p.read_text(encoding="utf-8-sig")[:6000]
            except Exception:
                continue
            txt_key = re.sub(r"[^a-z0-9]+", "", txt_head.lower())
            hit = any(k in txt_key for k in keys)
            if hit:
                try:
                    txt_full = p.read_text(encoding="utf-8")
                except Exception:
                    try:
                        txt_full = p.read_text(encoding="utf-8-sig")
                    except Exception:
                        continue
                picked.append(f"【笔记：{p.name}】\n{txt_full.strip()}\n")
                if sum(len(x) for x in picked) > max_chars:
                    break

    out = "\n\n".join(picked).strip()
    if len(out) > max_chars:
        out = out[:max_chars].rstrip() + "\n…(截断)"
    return out


# =========================================================
# ✅✅✅ LLM：硬超时 + 缓存 + 熔断（替换你原来的 _call_openai_for_xcg）
# =========================================================
_LLM_CACHE_TTL = float((os.environ.get("TVBUY_LLM_CACHE_TTL") or "600").strip() or "600")
_LLM_CIRCUIT_FAILS = int((os.environ.get("TVBUY_LLM_CIRCUIT_FAILS") or "3").strip() or "3")
_LLM_CIRCUIT_OPEN_SEC = int((os.environ.get("TVBUY_LLM_CIRCUIT_OPEN_SEC") or "120").strip() or "120")

_llm_cache: Dict[str, Dict[str, Any]] = {}  # key -> {"ts": float, "text": str}
_llm_fail_count: int = 0
_llm_circuit_open_until: float = 0.0

# 线程池：让“硬超时”生效（future.result(timeout=...)）
_llm_executor = ThreadPoolExecutor(max_workers=4)


def _llm_cache_key(prompt_text: str) -> str:
    # 简单稳定：去空白 + 截断；不会泄露 key
    s = re.sub(r"\s+", " ", (prompt_text or "")).strip()
    if len(s) > 2000:
        s = s[:2000]
    return s


def _llm_cache_get(key: str) -> Optional[str]:
    now = time.time()
    it = _llm_cache.get(key)
    if not it:
        return None
    ts = float(it.get("ts") or 0.0)
    if now - ts > _LLM_CACHE_TTL:
        _llm_cache.pop(key, None)
        return None
    text = it.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return None


def _llm_cache_set(key: str, text: str) -> None:
    if not (isinstance(text, str) and text.strip()):
        return
    _llm_cache[key] = {"ts": time.time(), "text": text.strip()}
    # 简单控量：最多 300 条
    if len(_llm_cache) > 300:
        # 删最旧的 60 条
        olds = sorted(_llm_cache.items(), key=lambda kv: float(kv[1].get("ts") or 0.0))[:60]
        for k, _ in olds:
            _llm_cache.pop(k, None)


def _circuit_is_open() -> bool:
    return time.time() < float(_llm_circuit_open_until or 0.0)


def _circuit_record_success() -> None:
    global _llm_fail_count
    _llm_fail_count = 0


def _circuit_record_failure() -> None:
    global _llm_fail_count, _llm_circuit_open_until
    _llm_fail_count += 1
    if _llm_fail_count >= _LLM_CIRCUIT_FAILS:
        _llm_circuit_open_until = time.time() + float(_LLM_CIRCUIT_OPEN_SEC)


def _openai_call_blocking(prompt_text: str) -> Optional[str]:
    """
    真实 OpenAI 调用（阻塞）。注意：这里不要依赖 SDK 的 timeout 参数来“保证”超时，
    我们外层用 future.result(timeout=...) 做硬超时。
    """
    if not (OPENAI_API_KEY and ENABLE_XCG_LLM):
        return None
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return None

    try:
        client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        resp = client.responses.create(
            model=OPENAI_MODEL,
            instructions="你是中文电视导购专家，输出要简洁、可执行、不要编造。若缺关键数据，请明确提示“需确认/等实测”。",
            input=prompt_text,
            temperature=0.3,
        )
        text = getattr(resp, "output_text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
        return None
    except Exception:
        return None


def _call_openai_for_xcg_hard_timeout(prompt_text: str, timeout_sec: float) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    返回：(text_or_none, llm_meta)
    llm_meta: {used, cache_hit, circuit_open, timeout, elapsed_ms}
    """
    t0 = time.time()
    meta = {"used": False, "cache_hit": False, "circuit_open": False, "timeout": False, "elapsed_ms": 0}

    if not (OPENAI_API_KEY and ENABLE_XCG_LLM):
        meta["elapsed_ms"] = int((time.time() - t0) * 1000)
        return None, meta

    if _circuit_is_open():
        meta["circuit_open"] = True
        meta["elapsed_ms"] = int((time.time() - t0) * 1000)
        return None, meta

    ck = _llm_cache_key(prompt_text)
    cached = _llm_cache_get(ck)
    if cached:
        meta["used"] = True
        meta["cache_hit"] = True
        meta["elapsed_ms"] = int((time.time() - t0) * 1000)
        return cached, meta

    meta["used"] = True
    fut = _llm_executor.submit(_openai_call_blocking, prompt_text)
    try:
        text = fut.result(timeout=max(0.5, float(timeout_sec)))
        if text:
            _llm_cache_set(ck, text)
            _circuit_record_success()
            meta["elapsed_ms"] = int((time.time() - t0) * 1000)
            return text, meta
        _circuit_record_failure()
        meta["elapsed_ms"] = int((time.time() - t0) * 1000)
        return None, meta
    except FuturesTimeoutError:
        meta["timeout"] = True
        _circuit_record_failure()
        meta["elapsed_ms"] = int((time.time() - t0) * 1000)
        return None, meta
    except Exception:
        _circuit_record_failure()
        meta["elapsed_ms"] = int((time.time() - t0) * 1000)
        return None, meta


# =========================================================
# LLM：用 prompt.py 生成“晓春哥 XCG 推荐”
# =========================================================
def _load_prompt_module() -> Optional[Any]:
    try:
        return importlib.import_module("tv_buy_1_0.llm.prompt")
    except Exception:
        return None


def _build_user_prompt(filters: Dict[str, Any], top_items: List[Dict[str, Any]]) -> str:
    pm = _load_prompt_module()
    if pm is not None:
        fn = getattr(pm, "render", None)
        if callable(fn):
            try:
                return str(fn(filters, top_items))
            except Exception:
                pass

        for name in ("PROMPT_TEMPLATE", "USER_PROMPT", "PROMPT", "TEMPLATE"):
            tpl = getattr(pm, name, None)
            if isinstance(tpl, str) and tpl.strip():
                try:
                    return tpl.format(filters=filters, items=top_items)
                except Exception:
                    return tpl

    models = [str(x.get("model") or "") for x in top_items[:10]]
    notes = _load_xcg_notes_for_models(models)

    lines = [
        "你是电视导购专家（口吻：晓春哥 XCG）。",
        "只基于用户的【品牌/尺寸/预算】和候选机型清单，按价格段给出购买建议。",
        "要求：输出 3 条：旗舰堆料/均衡选择/性价比；每条必须引用清单里的具体型号与价格；不要编造参数。",
        "",
        f"用户筛选条件：{json.dumps(filters, ensure_ascii=False)}",
        "候选清单（按价格从高到低）：",
    ]
    for it in top_items[:10]:
        lines.append(
            f"- {it.get('brand')} {it.get('model')} {it.get('size_inch')}寸 ￥{it.get('price_cny')} 定位={it.get('positioning')}"
        )

    if notes:
        lines += ["", "———", "附：晓春哥笔记素材（仅供参考，若与清单冲突以清单为准）：", notes]

    return "\n".join(lines)


def _xcg_reco_text(filters: Dict[str, Any], sorted_items: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """
    返回：(xcg_text, llm_meta)
    llm_meta 会被塞进 /api/dialog/3p2 的 meta 里，方便你看：是否 cache/是否 timeout/是否熔断。
    """
    user_prompt = _build_user_prompt(filters, sorted_items[:10])

    llm_text, llm_meta = _call_openai_for_xcg_hard_timeout(user_prompt, timeout_sec=OPENAI_TIMEOUT_SEC)
    if llm_text:
        return llm_text, llm_meta

    if not sorted_items:
        return "晓春哥 XCG 推荐：当前条件下没有匹配候选，建议放宽预算/尺寸/品牌。", llm_meta

    priced = [x for x in sorted_items if isinstance(x.get("price_cny"), (int, float))]
    if not priced:
        return "晓春哥 XCG 推荐：候选价格缺失较多，建议先补齐价格字段再做“按预算”推荐。", llm_meta

    best_hi = priced[0]
    best_lo = priced[-1]
    mid = priced[len(priced) // 2]

    def fmt_it(it: Dict[str, Any]) -> str:
        p = it.get("price_cny")
        ptxt = f"￥{int(p)}" if isinstance(p, (int, float)) else "￥?"
        return f"{it.get('brand')} {it.get('model')} {it.get('size_inch')}寸（{ptxt}｜{it.get('positioning','未知')}）"

    fallback = (
        "晓春哥 XCG 推荐（只按品牌/尺寸/预算，按价格段给买法）：\n"
        f"- 旗舰堆料：优先看 {fmt_it(best_hi)}（预算充足就直接冲更高定位）\n"
        f"- 均衡选择：{fmt_it(mid)}（大多数人买这个最稳）\n"
        f"- 性价比：{fmt_it(best_lo)}（同尺寸里更省钱，适合“能大就行”）"
    )
    return fallback, llm_meta


# =========================================================
# /api/chat（保留原来 1.0，但增强：支持 session_id & 可输出 XCG 推荐段落）
# =========================================================
def next_question(state: Dict[str, Any]) -> Optional[str]:
    if state.get("size") is None:
        return "你想要多大尺寸？比如：65 / 75 / 85（直接回“75寸”也行）"
    if state.get("scene") is None:
        return "主要用途是什么？回一个就行：ps5 / movie / bright（白天客厅很亮）"
    return None


class ChatReq(BaseModel):
    text: str
    state: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None


class ChatResp(BaseModel):
    state: Dict[str, Any]
    reply: str


# =========================================================
# ✅ Dialog 3p2 / webhook：简化版（品牌/尺寸/预算 → 按价格降序）
# =========================================================
_SESS: Dict[str, Dict[str, Any]] = {}
_SESS_TTL_SEC = 60 * 60 * 24  # 24h


def _now_ts() -> int:
    return int(time.time())


def _gc_sessions() -> None:
    if len(_SESS) < 2000:
        return
    ts = _now_ts()
    dead = []
    for sid, pack in _SESS.items():
        if ts - int(pack.get("_ts", ts)) > _SESS_TTL_SEC:
            dead.append(sid)
    for sid in dead:
        _SESS.pop(sid, None)


def _get_session(session_id: Optional[str]) -> Tuple[str, Dict[str, Any]]:
    _gc_sessions()
    sid = session_id or uuid4().hex
    pack = _SESS.get(sid)
    if not pack:
        pack = {
            "state_chat": {"size": None, "scene": None, "budget": None, "brand": None},
            "state_3p2": {"brand": None, "size": None, "budget": None},
            "_ts": _now_ts(),
            "last_reply_full": None,
            "last_reply_short": None,
            "last_structured": None,
            "last_state": None,
        }
        _SESS[sid] = pack
    pack["_ts"] = _now_ts()
    return sid, pack


def _normalize_state_simple(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(state, dict):
        return {"brand": None, "size": None, "budget": None}
    return {"brand": state.get("brand"), "size": state.get("size"), "budget": state.get("budget")}


def _merge_state_simple(base: Dict[str, Any], slots: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k in ("brand", "size", "budget"):
        if slots.get(k) is not None:
            out[k] = slots.get(k)
    return out


def _is_expand_cmd(text: str) -> bool:
    t = re.sub(r"\s+", "", (text or "")).strip().lower()
    t = re.sub(r"[!！。.,，?？]+$", "", t)
    return t in ["更多", "展开", "详细", "详情", "全文", "more", "detail", "查看完整列表", "完整列表"]


def _build_price_list_reply(state: Dict[str, Any], topn: int = 10) -> Tuple[str, str, Dict[str, Any]]:
    t0 = time.time()

    brand = state.get("brand")
    size = _safe_int(state.get("size"))
    budget = _safe_int(state.get("budget"))

    t1 = time.time()
    yaml_items, yaml_meta = _load_yaml_products()
    yaml_filtered = _filter_products(yaml_items, brand=brand, size=size, budget=budget)
    t2 = time.time()

    sqlite_items, sqlite_cnt = _sqlite_query_products(brand=brand, size=size, budget=budget)
    t3 = time.time()

    merged = _merge_products(yaml_filtered, sqlite_items)
    merged_sorted = _sort_by_price_desc(merged)
    t4 = time.time()

    filters = {"brand": brand, "size": size, "budget": budget}
    xcg_text, llm_meta = _xcg_reco_text(filters, merged_sorted)
    t5 = time.time()

    meta = {
        "yaml_dir": str(YAML_PRODUCTS_DIR),
        "yaml_loaded": int(yaml_meta.get("yaml_loaded") or 0),
        "yaml_matched": len(yaml_filtered),
        "sqlite_enabled": bool(USE_SQLITE_FALLBACK),
        "sqlite_matched": int(sqlite_cnt),
        "merged_total": len(merged_sorted),
        "openai_enabled": bool(bool(OPENAI_API_KEY) and ENABLE_XCG_LLM),
        "openai_model": OPENAI_MODEL,
        "openai_base_url": OPENAI_BASE_URL or "",
        "openai_timeout_sec": OPENAI_TIMEOUT_SEC,
        "yaml_cache_ttl": _YAML_CACHE_TTL,
        "yaml_lib": yaml_meta.get("yaml_lib") or _YAML_IMPL,
        "yaml_sig": _yaml_cache.get("sig"),
        "llm_meta": {
            **llm_meta,
            "cache_items": len(_llm_cache),
            "fail_count": int(_llm_fail_count),
            "circuit_open_until": float(_llm_circuit_open_until or 0.0),
            "cache_ttl": _LLM_CACHE_TTL,
            "circuit_fails": _LLM_CIRCUIT_FAILS,
            "circuit_open_sec": _LLM_CIRCUIT_OPEN_SEC,
        },
        "timing_ms": {
            "total": int((t5 - t0) * 1000),
            "yaml_load+filter": int((t2 - t1) * 1000),
            "sqlite_query": int((t3 - t2) * 1000),
            "merge+sort": int((t4 - t3) * 1000),
            "xcg_text": int((t5 - t4) * 1000),
        },
    }

    title = (
        f"📌 当前筛选候选：{len(merged_sorted)} 台（品牌={brand}；尺寸={size}寸；预算≤{budget}）\n"
        f"（按价格从高到低；优先 YAML；sqlite fallback={'开启' if USE_SQLITE_FALLBACK else '关闭'}）\n"
    )

    def fmt_line(i: int, it: Dict[str, Any]) -> str:
        b = it.get("brand") or ""
        m = it.get("model") or ""
        s = it.get("size_inch") or ""
        p = it.get("price_cny")
        ptxt = f"￥{int(p)}" if isinstance(p, (int, float)) else "￥?"
        tier = it.get("positioning") or "未知"
        return f"{i}. {b} {m} {s}寸 | {ptxt} | 定位={tier}".strip()

    top_list = merged_sorted[:topn]
    lines = [title, "", "———", "晓春哥 XCG 推荐：", xcg_text, "", "———", f"Top{min(topn, len(top_list))}："]
    for idx, it in enumerate(top_list, 1):
        lines.append(fmt_line(idx, it))

    lines.append("（回复：更多 / 查看完整列表 / 完整列表）")
    reply_short = "\n".join([x for x in lines if x is not None]).strip()

    full = reply_short + "\n\n调试信息：\n" + json.dumps(meta, ensure_ascii=False, indent=2)
    structured = {
        "filters": filters,
        "meta": meta,
        "top10": [
            {
                "brand": x.get("brand"),
                "model": x.get("model"),
                "size_inch": x.get("size_inch"),
                "price_cny": x.get("price_cny"),
                "positioning": x.get("positioning"),
                "launch_date": x.get("launch_date"),
                "source": x.get("source"),
                "_from": x.get("_from"),
            }
            for x in top_list
        ],
    }
    return reply_short, full, structured


# =========================================================
# /api/chat（保留你的原逻辑）
# =========================================================
@app.post("/api/chat", response_model=ChatResp)
def chat(req: ChatReq):
    if req.session_id:
        _, pack = _get_session(req.session_id)
        base = dict(pack.get("state_chat") or {"size": None, "scene": None, "budget": None, "brand": None})
    else:
        pack = {}
        base = {"size": None, "scene": None, "budget": None, "brand": None}

    if req.state:
        base = dict(req.state)

    t = (req.text or "").strip()
    tl = t.lower()

    slots = {
        "brand": _parse_brand(t),
        "size": _parse_size(t),
        "budget": _parse_budget(t),
        "scene": ("ps5" if "ps5" in tl else ("movie" if "movie" in tl else ("bright" if "bright" in tl else None))),
        "_reset": any(k in tl for k in ["重置", "清空", "reset"]),
    }

    if slots.get("budget") is None:
        slots["budget"] = _parse_budget_from_free_numbers(t, size=slots.get("size"))

    if slots.get("_reset"):
        base = {"size": None, "scene": None, "budget": None, "brand": None}
        if req.session_id:
            pack["state_chat"] = base
        return ChatResp(state=base, reply="✅ 已重置。你想买多大尺寸的电视？比如：65 / 75 / 85")

    for k in ["size", "scene", "budget", "brand"]:
        v = slots.get(k)
        if v is not None:
            base[k] = v

    if req.session_id:
        pack["state_chat"] = dict(base)

    collected = []
    if base.get("brand"):
        collected.append(f"品牌={base['brand']}")
    if base.get("budget") is not None:
        collected.append(f"预算≤{base['budget']}")
    if base.get("size") is not None:
        collected.append(f"尺寸≈{base['size']}寸")
    if base.get("scene") is not None:
        collected.append(f"场景={base['scene']}")
    header = f"（当前已收集：{'; '.join(collected) if collected else '暂无'}）\n\n"

    reply_parts: List[str] = []

    if base.get("size") is not None:
        total, cands = list_candidates(
            size=int(base["size"]),
            brand=base.get("brand"),
            budget=base.get("budget"),
            limit=10,
        )
        reply_parts.append(
            format_candidates(
                size=int(base["size"]),
                total=total,
                cands=cands,
                brand=base.get("brand"),
                budget=base.get("budget"),
            )
        )

        if base.get("scene") is not None:
            reply_parts.append("")
            reply_parts.append(
                recommend_text(
                    size=int(base["size"]),
                    scene=str(base["scene"]),
                    brand=base.get("brand"),
                    budget=base.get("budget"),
                )
            )

        if base.get("brand") and base.get("budget") is not None:
            try:
                state_simple = {"brand": base.get("brand"), "size": base.get("size"), "budget": base.get("budget")}
                reply_short, _, _ = _build_price_list_reply(state_simple, topn=10)
                reply_parts.append("")
                reply_parts.append("====（价格段推荐 / 晓春哥 XCG）====")
                reply_parts.append(reply_short)
            except Exception as e:
                reply_parts.append("")
                reply_parts.append(f"⚠️ 晓春哥 XCG 推荐生成失败（已忽略，不影响主推荐）：{e}")

        if total == 0:
            reply_parts.append("\n💡 建议：提高预算 / 换尺寸 / 先不限定品牌试试。")

    q = next_question(base)
    if q:
        reply = header + "\n\n".join(reply_parts) + ("\n\n" if reply_parts else "") + q
        return ChatResp(state=base, reply=reply)

    reply = header + "\n\n".join(reply_parts)
    return ChatResp(state=base, reply=reply)


# =========================================================
# ✅ /api/dialog/3p2 + /health + /webhook + /（保持原结构）
# =========================================================
class DialogReq(BaseModel):
    text: str
    session_id: Optional[str] = None
    state: Optional[Dict[str, Any]] = None


class DialogResp(BaseModel):
    ok: bool
    session_id: str
    reply: str
    state: Dict[str, Any]
    done: bool
    reply_short: Optional[str] = None
    reply_full: Optional[str] = None
    structured: Optional[Dict[str, Any]] = None


@app.get("/health")
def health():
    try:
        _load_yaml_products()
    except Exception:
        pass
    return {
        "ok": True,
        "ts": _now_ts(),
        "yaml_dir": str(YAML_PRODUCTS_DIR),
        "yaml_sig": _yaml_cache.get("sig"),
        "yaml_loaded": int(_yaml_cache.get("loaded") or 0),
        "yaml_lib": _YAML_IMPL,
        "sqlite_db": str(SQLITE_DB),
        "sqlite_enabled": bool(USE_SQLITE_FALLBACK),
        "openai_enabled": bool(bool(OPENAI_API_KEY) and ENABLE_XCG_LLM),
        "openai_model": OPENAI_MODEL,
        "openai_base_url": OPENAI_BASE_URL or "",
        "xcg_notes_dir": str(XCG_NOTES_DIR),
        "yaml_cache_ttl": _YAML_CACHE_TTL,
        "enable_xcg_llm": ENABLE_XCG_LLM,
        "openai_timeout_sec": OPENAI_TIMEOUT_SEC,
        "yaml_max_depth": _YAML_MAX_DEPTH,
        # ✅ 新增：LLM 缓存/熔断状态（方便你一眼看清到底是不是“秒出/缓存/熔断/超时”）
        "llm_cache_ttl": _LLM_CACHE_TTL,
        "llm_cache_items": len(_llm_cache),
        "llm_fail_count": int(_llm_fail_count),
        "llm_circuit_open_until": float(_llm_circuit_open_until or 0.0),
        "llm_circuit_is_open": _circuit_is_open(),
        "llm_circuit_fails": _LLM_CIRCUIT_FAILS,
        "llm_circuit_open_sec": _LLM_CIRCUIT_OPEN_SEC,
    }


@app.post("/api/dialog/3p2", response_model=DialogResp)
async def api_dialog_3p2(request: Request):
    try:
        data = await _read_json_body(request)
        req = DialogReq(**data)
    except Exception as e:
        return DialogResp(
            ok=False,
            session_id="",
            reply=f"❌ 解析请求失败：{e}",
            state={"brand": None, "size": None, "budget": None},
            done=False,
        )

    sid, pack = _get_session(req.session_id)

    if req.state is not None:
        pack["state_3p2"] = _normalize_state_simple(req.state)

    state = _normalize_state_simple(pack.get("state_3p2"))
    text = (req.text or "").strip()

    if _is_expand_cmd(text):
        last_full = pack.get("last_reply_full")
        last_short = pack.get("last_reply_short")
        last_struct = pack.get("last_structured")
        last_state = _normalize_state_simple(pack.get("last_state") or pack.get("state_3p2"))
        if last_full:
            return DialogResp(
                ok=True,
                session_id=sid,
                reply=last_full,
                reply_short=last_short,
                reply_full=last_full,
                structured=last_struct,
                state=last_state,
                done=True,
            )
        return DialogResp(
            ok=True,
            session_id=sid,
            reply="我还没有上一条结果可展开。你可以先发：例如“雷鸟 85 1.2万”。",
            state=last_state,
            done=False,
        )

    slots = parse_slots_simple(text)

    if slots.get("_reset"):
        pack["state_3p2"] = {"brand": None, "size": None, "budget": None}
        pack["last_reply_full"] = None
        pack["last_reply_short"] = None
        pack["last_structured"] = None
        pack["last_state"] = None
        return DialogResp(ok=True, session_id=sid, reply="✅ 已重置。你想看哪个品牌？比如：雷鸟 / tcl", state=pack["state_3p2"], done=False)

    state = _merge_state_simple(state, slots)
    pack["state_3p2"] = state

    missing = _next_missing_simple(state)
    if missing:
        return DialogResp(ok=True, session_id=sid, reply=QUESTION_SIMPLE[missing], state=state, done=False)

    try:
        reply_short, reply_full, structured = _build_price_list_reply(state, topn=10)
    except Exception as e:
        return DialogResp(ok=False, session_id=sid, reply=f"❌ 生成结果失败：{e}", state=state, done=False)

    pack["last_reply_full"] = reply_full
    pack["last_reply_short"] = reply_short
    pack["last_structured"] = structured
    pack["last_state"] = dict(state)

    # 维持你原本的行为：done=true 后清空 3p2 的 state
    pack["state_3p2"] = {"brand": None, "size": None, "budget": None}

    return DialogResp(
        ok=True,
        session_id=sid,
        reply=reply_short,
        reply_short=reply_short,
        reply_full=reply_full,
        structured=structured,
        state=state,
        done=True,
    )


@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await _read_json_body(request)
    except Exception as e:
        return _json_err(f"bad json: {e}", status_code=400)

    user_id = str(data.get("user_id") or "")
    session_id = str(data.get("session_id") or "") or uuid4().hex
    text = str(data.get("text") or "").strip()
    if not text:
        return _json_err("missing text", status_code=400)

    req = DialogReq(text=text, session_id=session_id, state=data.get("state"))
    sid, pack = _get_session(req.session_id)

    if req.state is not None:
        pack["state_3p2"] = _normalize_state_simple(req.state)

    state = _normalize_state_simple(pack.get("state_3p2"))

    if _is_expand_cmd(text):
        last_full = pack.get("last_reply_full")
        if last_full:
            raw = {
                "ok": True,
                "session_id": sid,
                "reply": last_full,
                "state": _normalize_state_simple(pack.get("last_state") or pack.get("state_3p2")),
                "done": True,
                "reply_short": pack.get("last_reply_short"),
                "reply_full": last_full,
                "structured": pack.get("last_structured"),
                "user_id": user_id,
            }
            return _json_ok(last_full, raw=raw)
        return _json_ok("我还没有上一条结果可展开。你可以先发：例如“雷鸟 85 1.2万”。", raw={"ok": True, "session_id": sid})

    slots = parse_slots_simple(text)

    if slots.get("_reset"):
        pack["state_3p2"] = {"brand": None, "size": None, "budget": None}
        pack["last_reply_full"] = None
        pack["last_reply_short"] = None
        pack["last_structured"] = None
        pack["last_state"] = None
        raw = {"ok": True, "session_id": sid, "done": False, "state": pack["state_3p2"]}
        return _json_ok("✅ 已重置。你想看哪个品牌？比如：雷鸟 / tcl", raw=raw)

    state = _merge_state_simple(state, slots)
    pack["state_3p2"] = state

    missing = _next_missing_simple(state)
    if missing:
        q = QUESTION_SIMPLE[missing]
        raw = {"ok": True, "session_id": sid, "done": False, "state": state}
        return _json_ok(q, raw=raw)

    try:
        reply_short, reply_full, structured = _build_price_list_reply(state, topn=10)
    except Exception as e:
        return _json_err(f"generate failed: {e}", status_code=500)

    pack["last_reply_full"] = reply_full
    pack["last_reply_short"] = reply_short
    pack["last_structured"] = structured
    pack["last_state"] = dict(state)

    pack["state_3p2"] = {"brand": None, "size": None, "budget": None}

    raw = {
        "ok": True,
        "session_id": sid,
        "reply": reply_short,
        "state": state,
        "done": True,
        "reply_short": reply_short,
        "reply_full": reply_full,
        "structured": structured,
        "user_id": user_id,
    }
    return _json_ok(reply_short, raw=raw)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})