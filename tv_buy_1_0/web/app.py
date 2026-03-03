# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import time
import sqlite3
import io
import sys
from uuid import uuid4
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List, Iterable
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# =========================================================
# ✅ 安全处理 stdout/stderr：避免 uvicorn 启动时 stderr 被关闭导致 lost sys.stderr
# =========================================================
def _safe_rewrap_stream(stream, encoding: str = "utf-8"):
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
# 你项目里已有：run_reco（保留推荐逻辑 recommend_text）
# =========================================================
from tv_buy_1_0.run_reco import recommend_text

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
# ✅ 默认环境变量（只在“未设置时”写入）
# =========================================================
def _env_default(key: str, value: str) -> None:
    if (os.environ.get(key) or "").strip() == "":
        os.environ[key] = value


_env_default("TVBUY_PRODUCTS_YAML_DIR", str(TVBUY_ROOT / "data_raw" / "excel_import_all_v1"))
_env_default("TVBUY_YAML_CACHE_TTL", "300")
_env_default("TVBUY_ENABLE_XCG_LLM", "1")
_env_default("TVBUY_OPENAI_TIMEOUT", "2.5")
_env_default("TVBUY_USE_SQLITE_FALLBACK", "0")
_env_default("TVBUY_YAML_MAX_DEPTH", "8")
_env_default("TVBUY_XCG_NOTES_MAX_FILES", "80")
_env_default("TVBUY_LLM_CACHE_TTL", "600")
_env_default("TVBUY_LLM_CIRCUIT_FAILS", "3")
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
# Storage
# =========================================================
UPLOAD_DIR = TVBUY_ROOT / "data_raw" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

CONTRAST_OUT_DIR = TVBUY_ROOT / "summaries" / "contrast_records"
CONTRAST_OUT_DIR.mkdir(parents=True, exist_ok=True)

CONTRAST_ANALYSIS_DIR = TVBUY_ROOT / "summaries" / "contrast_analysis"
CONTRAST_ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# JSON helpers
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
# 解析：品牌 / 尺寸 / 预算 / 场景
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

# ✅ 只为“清空 brand”服务：不限品牌/不限制品牌/随便 等
_BRAND_CLEAR_KWS = [
    "不限品牌",
    "不限制品牌",
    "不限定品牌",
    "品牌不限",
    "品牌不限制",
    "品牌不限定",
    "随便品牌",
    "都行",
    "随便",
    "任意品牌",
    "不挑品牌",
    "不挑牌子",
    "不限制牌子",
    "不限牌子",
]

# =========================================================
# ✅ 场景归一化：自然语言 → ps5/movie/bright
# =========================================================
_SCENE_ALIASES: Dict[str, List[str]] = {
    "ps5": [
        # 原始
        "ps5", "ps", "playstation", "ps 5",
        # 游戏表述
        "打游戏", "玩游戏", "玩儿游戏", "游戏", "主机", "游戏机", "次世代", "电竞",
        # 典型诉求
        "120hz", "144hz", "高刷", "低延迟", "输入延迟", "allm", "hdmi2.1", "hdmi 2.1",
        # 类型词（尽量别太激进）
        "fps", "射击", "动作", "格斗", "竞速",
        # 其他主机（用户说“玩 switch / xbox”也等价走游戏取向）
        "switch", "ns", "xbox",
    ],
    "movie": [
        "movie",
        "看电影", "电影", "观影", "影院", "影院感", "电影感",
        "追剧", "看剧", "电视剧", "综艺",
        "netflix", "网飞", "disney", "disney+", "apple tv", "hbomax",
        "杜比视界", "dolby vision", "杜比", "hdr 电影", "hdr影片",
    ],
    "bright": [
        "bright",
        "强光", "很亮", "太亮", "明亮", "白天", "白天看",
        "客厅白天", "客厅很亮", "客厅强光", "阳光", "阳光直射", "采光好", "大窗", "落地窗",
        "开灯", "灯光", "反光", "眩光",
    ],
}


def _should_clear_brand(raw: str) -> bool:
    t = (raw or "").strip().lower()
    if not t:
        return False
    return any(k in t for k in _BRAND_CLEAR_KWS)


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


def _parse_scene(raw: str) -> Optional[str]:
    """
    归一化用户用途输入：
    - 游戏/主机/高刷/低延迟 等 => ps5
    - 电影/观影/追剧 等 => movie
    - 强光/白天客厅/反光 等 => bright

    同一句里如果同时命中多个场景：按命中次数优先；平手则 ps5 > movie > bright
    """
    t = (raw or "").strip()
    if not t:
        return None

    tl = t.lower()

    # 快速：用户直接输入标准标签
    if tl in ("ps5", "movie", "bright"):
        return tl

    # 计分：避免 “ps” 这种太短的 token 误匹配（用词边界）
    scores: Dict[str, int] = {"ps5": 0, "movie": 0, "bright": 0}

    def hit_kw(kw: str) -> bool:
        k = (kw or "").strip()
        if not k:
            return False
        kl = k.lower()

        # 英文/数字短词：用边界降低误判
        if re.fullmatch(r"[a-z0-9\+\.\-\s]{1,8}", kl):
            pat = r"(?<![a-z0-9])" + re.escape(kl.replace(" ", "")) + r"(?![a-z0-9])"
            tl2 = re.sub(r"\s+", "", tl)
            return re.search(pat, tl2) is not None

        # 中文/长词：直接包含
        return kl in tl

    for scene, kws in _SCENE_ALIASES.items():
        for kw in kws:
            if hit_kw(kw):
                scores[scene] += 1

    if max(scores.values()) <= 0:
        return None

    # 平手优先级：ps5 > movie > bright
    best = sorted(
        scores.items(),
        key=lambda x: (-x[1], 0 if x[0] == "ps5" else (1 if x[0] == "movie" else 2)),
    )[0][0]
    return best


# =========================================================
# ✅ 价格区间（后端写死：只用于解析/提示；按钮由 index.html 渲染）
# =========================================================
PRICE_BUCKETS: Dict[int, List[Dict[str, Any]]] = {
    43: [
        {"range": "0-1200", "percent": 22},
        {"range": "1200-1800", "percent": 34},
        {"range": "1800-2500", "percent": 24},
        {"range": "2500-3500", "percent": 14},
        {"range": "3500+", "percent": 6},
    ],
    50: [
        {"range": "0-2200", "percent": 20},
        {"range": "2200-3000", "percent": 32},
        {"range": "3000-4000", "percent": 26},
        {"range": "4000-5500", "percent": 15},
        {"range": "5500+", "percent": 7},
    ],
    55: [
        {"range": "0-2800", "percent": 28},
        {"range": "2800-3800", "percent": 30},
        {"range": "3800-5000", "percent": 24},
        {"range": "5000-7000", "percent": 12},
        {"range": "7000+", "percent": 6},
    ],
    65: [
        {"range": "0-3500", "percent": 18},
        {"range": "3500-5000", "percent": 30},
        {"range": "5000-7000", "percent": 26},
        {"range": "7000-10000", "percent": 16},
        {"range": "10000+", "percent": 10},
    ],
    75: [
        {"range": "0-6000", "percent": 20},
        {"range": "6000-8500", "percent": 30},
        {"range": "8500-12000", "percent": 24},
        {"range": "12000-18000", "percent": 16},
        {"range": "18000+", "percent": 10},
    ],
    85: [
        {"range": "0-7000", "percent": 6},
        {"range": "7000-10000", "percent": 35},
        {"range": "10000-14000", "percent": 30},
        {"range": "14000-20000", "percent": 21},
        {"range": "20000+", "percent": 8},
    ],
    98: [
        {"range": "0-7000", "percent": 6},
        {"range": "7000-18000", "percent": 38},
        {"range": "18000-26000", "percent": 30},
        {"range": "26000-40000", "percent": 18},
        {"range": "40000+", "percent": 8},
    ],
    100: [
        {"range": "0-8000", "percent": 16},
        {"range": "8000-13000", "percent": 28},
        {"range": "13000-18000", "percent": 26},
        {"range": "18000-20000", "percent": 18},
        {"range": "20000+", "percent": 12},
    ],
    115: [
        {"range": "50000-62000", "percent": 22},
        {"range": "62000-72000", "percent": 30},
        {"range": "72000-82000", "percent": 24},
        {"range": "82000-95000", "percent": 16},
        {"range": "95000+", "percent": 8},
    ],
    116: [
        {"range": "50000-62000", "percent": 22},
        {"range": "62000-72000", "percent": 30},
        {"range": "72000-82000", "percent": 24},
        {"range": "82000-95000", "percent": 16},
        {"range": "95000+", "percent": 8},
    ],
}


def _parse_price_bucket_range(v: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """
    输入： "7000-10000" / "3500+" / "skip"
    输出： (min_price, max_price, bucket_str)
      - a-b => (a, b, "a-b")
      - x+  => (x, None, "x+")
      - skip => (None, None, "skip")
      - 不匹配 => (None, None, None)
    """
    s = (v or "").strip()
    if not s:
        return None, None, None
    if s == "skip":
        return None, None, "skip"
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", s)
    if m:
        lo = int(m.group(1))
        hi = int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi, s
    m2 = re.match(r"^\s*(\d+)\s*\+\s*$", s)
    if m2:
        lo = int(m2.group(1))
        return lo, None, s
    return None, None, None


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
# sqlite fallback（可选）
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


def _sqlite_query_products_exact(
    brand: Optional[str],
    size: Optional[int],
    budget_min: Optional[int],
    budget_max: Optional[int],
) -> List[Dict[str, Any]]:
    if not USE_SQLITE_FALLBACK:
        return []
    if not SQLITE_DB.exists():
        return []

    conn = sqlite3.connect(str(SQLITE_DB))
    conn.row_factory = sqlite3.Row
    try:
        tname, cmap = _sqlite_find_table_and_cols(conn)
        if not tname:
            return []

        where = []
        args: List[Any] = []

        if brand:
            where.append(f"{cmap['brand']} = ?")
            args.append(brand)
        if size:
            where.append(f"{cmap['size']} = ?")
            args.append(int(size))

        if budget_min is not None:
            where.append(f"{cmap['price']} >= ?")
            args.append(int(budget_min))
        if budget_max is not None:
            where.append(f"{cmap['price']} <= ?")
            args.append(int(budget_max))

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
        return out
    finally:
        conn.close()


# =========================================================
# 合并、去重、排序（YAML 优先）
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


def _filter_products_exact(
    items: List[Dict[str, Any]],
    brand: Optional[str],
    size: Optional[int],
    budget_min: Optional[int],
    budget_max: Optional[int],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items:
        b = it.get("brand")
        s = it.get("size_inch")
        p = it.get("price_cny")

        if brand and (b != brand):
            continue
        if size is not None:
            if not isinstance(s, (int, float)):
                continue
            if int(s) != int(size):
                continue

        if isinstance(p, (int, float)):
            ip = int(p)
            if budget_min is not None and ip < int(budget_min):
                continue
            if budget_max is not None and ip > int(budget_max):
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


def list_candidates_exact(
    size: int,
    brand: Optional[str],
    budget_min: Optional[int],
    budget_max: Optional[int],
    limit: int = 10,
) -> Tuple[int, List[Dict[str, Any]]]:
    yaml_items, _meta = _load_yaml_products()
    sqlite_items = _sqlite_query_products_exact(brand=brand, size=size, budget_min=budget_min, budget_max=budget_max)
    merged = _merge_products(yaml_items=yaml_items, sqlite_items=sqlite_items)
    filt = _filter_products_exact(merged, brand=brand, size=size, budget_min=budget_min, budget_max=budget_max)
    sorted_items = _sort_by_price_desc(filt)
    return len(sorted_items), sorted_items[:limit]


def format_candidates_exact(
    size: int,
    total: int,
    cands: List[Dict[str, Any]],
    brand: Optional[str],
    budget_min: Optional[int],
    budget_max: Optional[int],
    budget_bucket: Optional[str],
) -> str:
    cond = []
    if brand:
        cond.append(f"品牌={brand}")

    if budget_bucket == "skip":
        cond.append("不限预算")
    elif budget_min is not None and budget_max is not None:
        cond.append(f"预算区间={budget_min}-{budget_max}")
    elif budget_min is not None and budget_max is None:
        cond.append(f"预算≥{budget_min}")
    elif budget_max is not None:
        cond.append(f"预算≤{budget_max}")

    cond.append(f"尺寸={size}寸")
    head = f"📌 当前筛选候选：{total} 台（" + "，".join(cond) + "）"

    if total == 0:
        return head + "\n⚠️ 当前条件下没有候选。你可以：放宽品牌/提高预算/换尺寸。"

    lines = [head, "（展示前10）"]
    for i, tv in enumerate(cands, 1):
        lines.append(
            f"{i}. {tv.get('brand')} {tv.get('model')} {tv.get('size_inch')}寸 | 首发 {tv.get('launch_date')} | ￥{tv.get('price_cny')}"
        )
    return "\n".join(lines)


# =========================================================
# /api/chat：问答顺序
# =========================================================
def next_question(state: Dict[str, Any]) -> Optional[str]:
    if state.get("size") is None:
        return "请先选择【尺寸】（点页面按钮）。只要告诉我尺寸，我再继续问价格区间/用途/品牌。"
    if state.get("budget_bucket") is None:
        return "请选择【价格区间】（点页面按钮）。也可以点「不限预算」。"
    if state.get("scene") is None:
        return "主要用途是什么？你可以直接说：打游戏/玩PS5/看电影/追剧/白天客厅强光（也支持输入 ps5 / movie / bright）"
    return None


class ChatReq(BaseModel):
    text: str
    state: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None


class ChatResp(BaseModel):
    state: Dict[str, Any]
    reply: str
    ui: Optional[Dict[str, Any]] = None


# =========================================================
# ✅ /api/chat（按钮由 index.html 渲染；这里永远 ui=None）
# =========================================================
@app.post("/api/chat", response_model=ChatResp)
def chat(req: ChatReq):
    base = {
        "size": None,
        "scene": None,
        "budget": None,         # 兼容字段：上限预算（若有）
        "brand": None,
        "budget_bucket": None,
        "budget_min": None,
        "budget_max": None,
    }

    if req.state:
        st = dict(req.state)
        for k in base.keys():
            if k in st:
                base[k] = st.get(k)

    t = (req.text or "").strip()
    tl = t.lower()

    # boot: 前端传 text="" 进来 -> 只回一句提示（按钮前端自己画）
    if t == "":
        q = next_question(base) or "你可以继续输入需求。"
        return ChatResp(state=base, reply=q, ui=None)

    # reset
    if any(k in tl for k in ["重置", "清空", "重新开始", "reset"]):
        base = {
            "size": None,
            "scene": None,
            "budget": None,
            "brand": None,
            "budget_bucket": None,
            "budget_min": None,
            "budget_max": None,
        }
        return ChatResp(state=base, reply="✅ 已重置。请先选择【尺寸】（点页面按钮）。", ui=None)

    # ✅✅✅ 关键：只要用户表达“不限品牌”，立刻清空 brand（不动其它任何字段）
    if _should_clear_brand(t):
        base["brand"] = None

    # =====================================================
    # 1) 优先处理“尺寸切换”
    # =====================================================
    new_size = _parse_size(t)
    if new_size is not None:
        new_size = int(new_size)
        old_size = base.get("size")
        if old_size != new_size:
            base["size"] = new_size
            base["scene"] = None
            base["budget"] = None
            base["brand"] = None
            base["budget_bucket"] = None
            base["budget_min"] = None
            base["budget_max"] = None

            total, cands = list_candidates_exact(
                size=int(base["size"]),
                brand=None,
                budget_min=None,
                budget_max=None,
                limit=10,
            )
            reply = (
                f"✅ 已切换尺寸：{old_size} → {new_size} 寸\n\n"
                + format_candidates_exact(
                    size=int(base["size"]),
                    total=total,
                    cands=cands,
                    brand=None,
                    budget_min=None,
                    budget_max=None,
                    budget_bucket=None,
                )
                + "\n\n请选择【价格区间】（点页面按钮）。"
            )
            return ChatResp(state=base, reply=reply, ui=None)

    # 没尺寸：必须先选尺寸
    if base.get("size") is None:
        return ChatResp(
            state=base,
            reply="请先选择【尺寸】（点页面按钮）。只要告诉我尺寸，我再继续问价格区间/用途/品牌。",
            ui=None,
        )

    # =====================================================
    # 2) 价格区间
    # =====================================================
    if base.get("budget_bucket") is None:
        lo, hi, bucket = _parse_price_bucket_range(t)
        if bucket is not None:
            base["budget_bucket"] = bucket
            base["budget_min"] = lo
            base["budget_max"] = hi
            base["budget"] = hi  # 兼容：recommend_text 用上限

            total, cands = list_candidates_exact(
                size=int(base["size"]),
                brand=base.get("brand"),
                budget_min=base.get("budget_min"),
                budget_max=base.get("budget_max"),
                limit=10,
            )
            reply = (
                f"✅ 已选择价格区间：{bucket}\n\n"
                + format_candidates_exact(
                    size=int(base["size"]),
                    total=total,
                    cands=cands,
                    brand=base.get("brand"),
                    budget_min=base.get("budget_min"),
                    budget_max=base.get("budget_max"),
                    budget_bucket=base.get("budget_bucket"),
                )
                + "\n\n主要用途是什么？你可以直接说：打游戏/玩PS5/看电影/追剧/白天客厅强光（也支持 ps5 / movie / bright）"
            )
            return ChatResp(state=base, reply=reply, ui=None)

        return ChatResp(
            state=base,
            reply="请选择【价格区间】（点页面按钮）。也可以点「不限预算」。",
            ui=None,
        )

    # =====================================================
    # 3) 有尺寸 + 价格区间后：解析 scene / 预算 / brand（brand 可随时点）
    # =====================================================
    slots = {
        "brand": _parse_brand(t),
        "budget": _parse_budget(t),
        "scene": _parse_scene(t),
    }
    if slots.get("budget") is None:
        slots["budget"] = _parse_budget_from_free_numbers(t, size=base.get("size"))

    # 手动预算：定义为“上限≤X”；清空下限（更符合用户直觉）
    if slots.get("budget") is not None:
        base["budget"] = int(slots["budget"])
        base["budget_bucket"] = "manual"
        base["budget_min"] = None
        base["budget_max"] = int(slots["budget"])

    # ✅✅✅ 若本次是“不限品牌”，上面已经清空了；否则本次若识别到具体品牌才写入
    if slots.get("brand") is not None and (not _should_clear_brand(t)):
        base["brand"] = slots["brand"]

    if slots.get("scene") is not None:
        base["scene"] = slots["scene"]

    collected = []
    if base.get("brand"):
        collected.append(f"品牌={base['brand']}")

    if base.get("budget_bucket") == "skip":
        collected.append("不限预算")
    elif base.get("budget_min") is not None and base.get("budget_max") is not None:
        collected.append(f"预算区间={base['budget_min']}-{base['budget_max']}")
    elif base.get("budget_min") is not None and base.get("budget_max") is None:
        collected.append(f"预算≥{base['budget_min']}")
    elif base.get("budget_max") is not None:
        collected.append(f"预算≤{base['budget_max']}")

    if base.get("size") is not None:
        collected.append(f"尺寸={base['size']}寸")
    if base.get("scene") is not None:
        collected.append(f"场景={base['scene']}")
    header = f"（当前已收集：{'; '.join(collected) if collected else '暂无'}）\n\n"

    total, cands = list_candidates_exact(
        size=int(base["size"]),
        brand=base.get("brand"),
        budget_min=base.get("budget_min"),
        budget_max=base.get("budget_max"),
        limit=10,
    )

    reply_parts: List[str] = []
    reply_parts.append(
        format_candidates_exact(
            size=int(base["size"]),
            total=total,
            cands=cands,
            brand=base.get("brand"),
            budget_min=base.get("budget_min"),
            budget_max=base.get("budget_max"),
            budget_bucket=base.get("budget_bucket"),
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

    if total == 0:
        reply_parts.append("\n💡 建议：提高预算 / 换尺寸 / 先不限定品牌试试。")

    q = next_question(base)
    if q:
        reply = header + "\n\n".join(reply_parts) + ("\n\n" if reply_parts else "") + q
        return ChatResp(state=base, reply=reply, ui=None)

    reply = header + "\n\n".join(reply_parts)
    return ChatResp(state=base, reply=reply, ui=None)


# =========================================================
# /health
# =========================================================
@app.get("/health")
def health():
    try:
        _load_yaml_products()
    except Exception:
        pass
    return {
        "ok": True,
        "ts": int(time.time()),
        "yaml_dir": str(YAML_PRODUCTS_DIR),
        "yaml_sig": _yaml_cache.get("sig"),
        "yaml_loaded": int(_yaml_cache.get("loaded") or 0),
        "yaml_lib": _YAML_IMPL,
        "sqlite_db": str(SQLITE_DB),
        "sqlite_enabled": bool(USE_SQLITE_FALLBACK),
        "openai_enabled": bool(bool(OPENAI_API_KEY) and ENABLE_XCG_LLM),
        "openai_model": OPENAI_MODEL,
        "openai_base_url": OPENAI_BASE_URL or "",
        "yaml_cache_ttl": _YAML_CACHE_TTL,
        "enable_xcg_llm": ENABLE_XCG_LLM,
        "openai_timeout_sec": OPENAI_TIMEOUT_SEC,
        "yaml_max_depth": _YAML_MAX_DEPTH,
    }


# =========================================================
# pages
# =========================================================
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/output", response_class=HTMLResponse)
def output_page(request: Request):
    return templates.TemplateResponse("output.html", {"request": request})