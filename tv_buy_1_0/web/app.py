# -*- coding: utf-8 -*-
"""
tv_buy_1_0/web/app.py  （最终版｜可一键复制粘贴替换）

✅ 已落实（保持你上一版完整能力）：
1) 选尺寸/选价格区间/选品牌 这些“筛选动作”——只做本地 YAML/DB 检索，不调用任何 API（不走智谱）
2) 用户“自然语言输入”（输入框）——默认走「混合导购模式（RAG）」：本地候选TopN + 智谱生成导购式回答（可开关）
3) 本地识别不到型号/对比对象时：如果是“提问/分析/对比”意图，可走智谱保底
4) ✅ 删除“对比度截图分析”相关功能：不在本文件提供

✅ 新增关键修复（用途确定后也能触发智谱）：
- 即使本次消息是 source="ui_button"，只要“本轮刚刚设置了 scene（ps5/movie/bright）”，也允许触发一次 RAG 导购
- 仍然保证：尺寸/价格/品牌 的按钮点击不触发智谱

✅ 新增关键修复（你截图提出的）：
- 智谱回答如果给了“关键追问”，但用户可能“没问题/不想回答”
  => 后端自动追加「不回答也行：按当前条件给你默认最终推荐（可直接下单）」段落

✅ 额外保留/加强：
- 支持“序号对比”（1 vs 2 / 1和2）与“机型对比”（A 和 B / A vs B）：
  - user_text 且开启智谱 => 走智谱生成对比结论（基于候选，不乱编）
  - 否则 => 返回本地字段对照兜底
"""

from __future__ import annotations

import json
import os
import re
import time
import io
import sys
from uuid import uuid4
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List, Iterable, Deque
from collections import deque

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
from tv_buy_1_0.run_reco import recommend_text  # noqa: E402
from tv_buy_1_0.tools.tool_api import router as tools_router  # noqa: E402

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
_env_default("TVBUY_USE_SQLITE_FALLBACK", "0")
_env_default("TVBUY_YAML_MAX_DEPTH", "8")
_env_default("TVBUY_XCG_NOTES_MAX_FILES", "80")

# ✅ 智谱（Zhipu）配置
_env_default("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
_env_default("ZHIPU_MODEL", "glm-4-plus")
_env_default("TVBUY_ZHIPU_TIMEOUT", "20")

# ✅ 重要：默认开启“输入框导购模式”
_env_default("TVBUY_ENABLE_ZHIPU_QA", "1")

# ✅ 会话记忆（服务端内存）
_env_default("TVBUY_SESSION_TTL_SEC", "1800")  # 30min
_env_default("TVBUY_SESSION_MAX_TURNS", "18")  # 最近18轮（user+assistant算两条）

# =========================================================
# 数据源配置
# =========================================================
YAML_PRODUCTS_DIR = Path(os.environ.get("TVBUY_PRODUCTS_YAML_DIR", "").strip())
USE_SQLITE_FALLBACK = (os.environ.get("TVBUY_USE_SQLITE_FALLBACK", "0").strip() == "1")
SQLITE_DB = Path(os.environ.get("TVBUY_SQLITE_DB", str(TVBUY_ROOT / "db" / "tv.sqlite")))

# =========================================================
# ✅ 智谱 LLM
# =========================================================
ZHIPU_API_KEY = (os.environ.get("ZHIPU_API_KEY", "") or "").strip()
ZHIPU_BASE_URL = (os.environ.get("ZHIPU_BASE_URL", "") or "https://open.bigmodel.cn/api/paas/v4").strip()
ZHIPU_MODEL = (os.environ.get("ZHIPU_MODEL", "") or "glm-4-plus").strip()
ZHIPU_TIMEOUT_SEC = float((os.environ.get("TVBUY_ZHIPU_TIMEOUT", "20") or "20").strip() or "20")
ENABLE_ZHIPU_QA = (os.environ.get("TVBUY_ENABLE_ZHIPU_QA", "0").strip() == "1")


def _http_post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
    import urllib.request
    import urllib.error

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_sec)) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            return json.loads(text)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise RuntimeError(f"HTTPError {e.code}: {body or str(e)}") from e
    except Exception as e:
        raise RuntimeError(f"HTTP request failed: {e}") from e


def zhipu_chat(messages: List[Dict[str, str]], model: Optional[str] = None, temperature: float = 0.35) -> str:
    """智谱聊天（带 timeout + 兼容 choices/message/content）"""
    if not ZHIPU_API_KEY:
        raise RuntimeError("ZHIPU_API_KEY 未设置（无法进行智能对话）")

    base = (ZHIPU_BASE_URL or "").rstrip("/")
    url = base + "/chat/completions"

    headers = {"Authorization": f"Bearer {ZHIPU_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": model or ZHIPU_MODEL, "messages": messages, "temperature": float(temperature)}
    data = _http_post_json(url, headers=headers, payload=payload, timeout_sec=ZHIPU_TIMEOUT_SEC)

    try:
        return (data.get("choices") or [])[0]["message"]["content"]
    except Exception:
        return json.dumps(data, ensure_ascii=False)


def zhipu_chat_with_retry(messages: List[Dict[str, str]], temperature: float = 0.25, retries: int = 2) -> str:
    """✅ timeout 自动重试"""
    last_err: Optional[Exception] = None
    for i in range(max(1, retries + 1)):
        try:
            return zhipu_chat(messages, temperature=temperature).strip()
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if "timed out" in msg or "timeout" in msg or "read operation timed out" in msg:
                if i < retries:
                    time.sleep(0.6 + 0.4 * i)
                    continue
            raise
    raise RuntimeError(str(last_err) if last_err else "智谱请求失败")


# =========================================================
# ✅ 日期解析：统一展示首发 YYYY-MM（修复 2025-12-01 等格式）
# =========================================================
def _parse_ymd_any(v: Any) -> Optional[Tuple[int, int, int]]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("none", "null", "nan", "-", "未知"):
        return None

    s = re.split(r"[ T]", s, maxsplit=1)[0]
    s = s.replace("/", "-").replace(".", "-").replace("年", "-").replace("月", "").replace("日", "")

    m = re.match(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$", s)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        dd = int(m.group(3)) if m.group(3) else 1
        if 1 <= mo <= 12:
            dd = dd if 1 <= dd <= 31 else 1
            return y, mo, dd
        return None

    m = re.match(r"^(\d{4})(\d{2})(\d{2})?$", s)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        dd = int(m.group(3)) if m.group(3) else 1
        if 1 <= mo <= 12:
            dd = dd if 1 <= dd <= 31 else 1
            return y, mo, dd
        return None

    m = re.match(r"^(\d{4})$", s)
    if m:
        return int(m.group(1)), 1, 1

    m = re.search(r"(\d{4})\D+(\d{1,2})", s)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            return y, mo, 1

    m = re.match(r"^(\d{2})-(\d{1,2})$", s)
    if m:
        yy = int(m.group(1))
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            return 2000 + yy, mo, 1

    return None


def fmt_launch_yyyy_mm(v: Any) -> str:
    ymd = _parse_ymd_any(v)
    if not ymd:
        return "-"
    y, m, _ = ymd
    return f"{y:04d}-{m:02d}"


def _normalize_launch_to_store(v: Any) -> Optional[str]:
    ymd = _parse_ymd_any(v)
    if not ymd:
        return None
    y, m, _ = ymd
    return f"{y:04d}-{m:02d}"


# =========================================================
# YAML 加载策略：缓存 + 目录签名（快） + 文件列表缓存
# =========================================================
_YAML_CACHE_TTL = float((os.environ.get("TVBUY_YAML_CACHE_TTL") or "300").strip() or "300")
_YAML_MAX_DEPTH = int((os.environ.get("TVBUY_YAML_MAX_DEPTH") or "8").strip() or "8")

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
app.include_router(tools_router)
templates = Jinja2Templates(directory=str(TVBUY_ROOT / "web" / "templates"))

# =========================================================
# Storage
# =========================================================
UPLOAD_DIR = TVBUY_ROOT / "data_raw" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# =========================================================
# ✅ Session Memory (in-memory)
# =========================================================
_SESSION_TTL = int((os.environ.get("TVBUY_SESSION_TTL_SEC") or "1800").strip() or "1800")
_SESSION_MAX_TURNS = int((os.environ.get("TVBUY_SESSION_MAX_TURNS") or "18").strip() or "18")

_sessions: Dict[str, Dict[str, Any]] = {}
# 每个 session: {"ts": last_seen_ts, "turns": deque([{"role":"user/assistant","content":"..."}])}


def _now() -> float:
    return time.time()


def _session_get(session_id: str) -> Deque[Dict[str, str]]:
    sid = (session_id or "").strip()
    if not sid:
        sid = "anonymous"

    tnow = _now()
    for k in list(_sessions.keys()):
        if tnow - float(_sessions[k].get("ts", 0.0)) > _SESSION_TTL:
            _sessions.pop(k, None)

    if sid not in _sessions:
        _sessions[sid] = {"ts": tnow, "turns": deque(maxlen=_SESSION_MAX_TURNS * 2)}
    _sessions[sid]["ts"] = tnow
    return _sessions[sid]["turns"]


def _session_add(session_id: str, role: str, content: str) -> None:
    turns = _session_get(session_id)
    c = (content or "").strip()
    if not c:
        return
    turns.append({"role": role, "content": c})


# =========================================================
# Upload Helpers
# =========================================================
def _is_allowed_image(filename: str) -> bool:
    fn = (filename or "").lower()
    return fn.endswith(".png") or fn.endswith(".jpg") or fn.endswith(".jpeg") or fn.endswith(".webp")


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
    "卡萨帝": ["卡萨帝", "casarte"],
    "长虹": ["长虹", "changhong"],
    "华为": ["华为", "huawei"],
    "荣耀": ["荣耀", "honor"],
    "松下": ["松下", "panasonic"],
    "飞利浦": ["飞利浦", "philips"],
    "东芝": ["东芝", "toshiba"],
}

_BRAND_CLEAR_KWS = [
    "不限品牌", "不限制品牌", "不限定品牌", "品牌不限", "品牌不限制", "品牌不限定",
    "随便品牌", "都行", "随便", "任意品牌", "不挑品牌", "不挑牌子", "不限制牌子", "不限牌子",
]

_SIZE_CLEAR_KWS = [
    "不限尺寸", "不限制尺寸", "不限定尺寸", "尺寸不限", "尺寸不限制", "尺寸不限定",
    "不挑尺寸", "随便尺寸",
]

_SCENE_ALIASES: Dict[str, List[str]] = {
    "ps5": ["ps5", "ps", "playstation", "ps 5", "打游戏", "玩游戏", "游戏", "主机", "次世代", "电竞",
            "120hz", "144hz", "高刷", "低延迟", "输入延迟", "allm", "hdmi2.1", "hdmi 2.1", "fps", "switch", "xbox"],
    "movie": ["movie", "看电影", "电影", "观影", "影院", "追剧", "看剧", "netflix", "网飞", "disney", "杜比视界", "dolby vision", "hdr"],
    "bright": ["bright", "强光", "明亮", "白天", "客厅很亮", "阳光直射", "落地窗", "反光", "眩光"],
}

_TV_DOMAIN_KWS = [
    "电视", "tv", "tcl", "海信", "索尼", "三星", "lg", "vidda", "雷鸟", "创维", "卡萨帝", "长虹",
    "画质", "亮度", "分区", "对比度", "背光", "mini led", "oled", "qd", "量子点",
    "hdmi", "hdmi2.1", "allm", "vr", "vrr", "120hz", "144hz", "输入延迟", "ps5", "xbox",
    "杜比视界", "hdr", "怎么选", "推荐", "对比", "比较", "pk", "预算", "尺寸", "英寸", "吋",
]

_QA_HINT_KWS = [
    "为什么", "怎么", "如何", "区别", "怎么样", "好不好", "值不值得", "值得吗", "能买吗", "推荐吗", "靠谱不", "行不行",
    "差别", "差异", "优缺点", "优点", "缺点", "解释", "分析",
    "怎么选", "选哪个", "哪个好", "建议", "方案",
    "性能", "配置", "参数", "面板", "背光", "控光", "分区", "亮度", "对比度", "色域", "灰阶",
    "hdr", "杜比", "vrr", "allm", "hdmi2.1", "120hz", "144hz",
    "观看距离", "离多远", "客厅", "卧室", "墙挂", "底座", "安装",
]

_COMPARE_HINT_WORDS = ["对比", "比較", "比较", "pk", "区别", "差别", "差异", "哪个好", "怎么选", "推荐哪个", "选哪个", "优缺点", "参数对照", "对照", "vs", "v"]

_RECO_INTENT_KWS = [
    "推荐", "给我推荐", "帮我推荐", "帮我选", "帮我挑", "给我选", "选一台", "选个", "买哪款", "买哪个",
    "想买", "求推荐", "选购", "给个建议",
]


def _is_tv_domain_question(text: str) -> bool:
    tl = (text or "").strip().lower()
    if not tl:
        return True
    return any(k in tl for k in _TV_DOMAIN_KWS)


def _is_qa_question(text: str) -> bool:
    tl = (text or "").strip().lower()
    if not tl:
        return False
    if "?" in tl or "？" in tl:
        return True
    return any(k in tl for k in _QA_HINT_KWS)


def _is_compare_intent(text: str) -> bool:
    tl = (text or "").strip().lower()
    if not tl:
        return False
    return any(w in tl for w in _COMPARE_HINT_WORDS)


def _is_reco_intent(text: str) -> bool:
    tl = (text or "").strip().lower()
    if not tl:
        return False
    return any(k in tl for k in _RECO_INTENT_KWS)


def _should_clear_brand(raw: str) -> bool:
    t = (raw or "").strip().lower()
    return bool(t) and any(k in t for k in _BRAND_CLEAR_KWS)


def _should_clear_size(raw: str) -> bool:
    t = (raw or "").strip().lower()
    return bool(t) and any(k in t for k in _SIZE_CLEAR_KWS)


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
        if bl in ("sony",):
            return "索尼"
        if bl in ("hisense",):
            return "海信"
        return b
    return None


# =========================================================
# ✅ 识别“尺寸前缀型号”（避免把 75E7Q 当成输入尺寸）
# =========================================================
def _looks_like_model_with_size_prefix(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False

    s2 = re.sub(r"(怎么样|如何|好不好|值得买吗|能买吗|推荐吗|咋样|对比|比较|哪个好)\s*$", "", s, flags=re.I).strip()
    compact = re.sub(r"\s+", "", s2)

    m = re.search(r"(?<!\d)(\d{2,3})([A-Za-z]{1,4}[A-Za-z0-9\-\+]{0,16})", compact)
    if not m:
        return False
    try:
        size = int(m.group(1))
    except Exception:
        return False
    if size < 40 or size > 120:
        return False

    tail = m.group(2)
    if not re.search(r"[A-Za-z]", tail):
        return False

    if re.search(r"(寸|英寸|吋|inch|in|\")", text.lower()):
        return False

    return True


def _parse_size(raw: str) -> Optional[int]:
    t = (raw or "").strip().lower()
    if not t:
        return None

    if _looks_like_model_with_size_prefix(raw):
        return None

    m = re.search(r"(?<!\d)(\d{2,3})\s*(寸|英寸|吋|inch|in|\")", t)
    if m:
        v = int(m.group(1))
        if 40 <= v <= 120:
            return v

    m = re.search(r"(?<!\d)(\d{2,3})(?!\d)\s*(?:的)?\s*(?:电视|tv)\b", t)
    if m:
        v = int(m.group(1))
        if 40 <= v <= 120:
            return v

    m = re.search(r"(?<!\d)(\d{2,3})(?!\d)(?=\s*(?:的|电视|电视机|tv))", t)
    if m:
        v = int(m.group(1))
        if 40 <= v <= 120:
            return v

    m = re.search(r"(?<!\d)(\d{2,3})(?!\d)", t)
    if m:
        v = int(m.group(1))
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
    return max(cands) if cands else None


def _parse_scene(raw: str) -> Optional[str]:
    t = (raw or "").strip()
    if not t:
        return None
    tl = t.lower()
    if tl in ("ps5", "movie", "bright"):
        return tl

    scores: Dict[str, int] = {"ps5": 0, "movie": 0, "bright": 0}

    def hit_kw(kw: str) -> bool:
        k = (kw or "").strip()
        if not k:
            return False
        kl = k.lower()
        if re.fullmatch(r"[a-z0-9\+\.\-\s]{1,10}", kl):
            pat = r"(?<![a-z0-9])" + re.escape(kl.replace(" ", "")) + r"(?![a-z0-9])"
            tl2 = re.sub(r"\s+", "", tl)
            return re.search(pat, tl2) is not None
        return kl in tl

    for scene, kws in _SCENE_ALIASES.items():
        for kw in kws:
            if hit_kw(kw):
                scores[scene] += 1

    if max(scores.values()) <= 0:
        return None

    best = sorted(scores.items(), key=lambda x: (-x[1], 0 if x[0] == "ps5" else (1 if x[0] == "movie" else 2)))[0][0]
    return best


# =========================================================
# ✅ 尺寸冲突确认（pending_size）
# =========================================================
_SIZE_CONFIRM_YES = ["是", "对", "要", "改", "换", "切换", "确认", "可以", "行", "好的", "ok", "yes", "y"]
_SIZE_CONFIRM_NO = ["不", "不是", "不要", "别", "继续", "保持", "算了", "取消", "no", "n"]


def _is_size_confirm_reply(text: str, to_size: int) -> bool:
    tl = (text or "").strip().lower()
    if not tl:
        return False
    if re.fullmatch(rf"{to_size}\s*(寸|英寸|吋|inch|in|\")?", tl):
        return True
    if re.search(rf"(改成|换成|切换到|改到|换到)\s*{to_size}", tl):
        return True
    return any(w == tl or w in tl for w in _SIZE_CONFIRM_YES)


def _is_size_cancel_reply(text: str, from_size: int) -> bool:
    tl = (text or "").strip().lower()
    if not tl:
        return False
    if re.fullmatch(rf"{from_size}\s*(寸|英寸|吋|inch|in|\")?", tl):
        return True
    if re.search(rf"(继续|保持)\s*{from_size}", tl):
        return True
    return any(w == tl or w in tl for w in _SIZE_CONFIRM_NO)


def _explicit_size_switch(text: str, to_size: int) -> bool:
    tl = (text or "").strip().lower()
    if not tl:
        return False
    if re.search(rf"(改|换|切换|改成|换成|调整|改到|换到)\s*{to_size}\s*(寸|英寸|吋|inch|in|\")?", tl):
        return True
    if re.search(rf"(我要|就要|给我|想要)\s*{to_size}\s*(寸|英寸|吋|inch|in|\")?", tl):
        return True
    return False


def _text_has_other_constraints(text: str) -> bool:
    tl = (text or "").strip().lower()
    if not tl:
        return False
    if _parse_brand(text) is not None:
        return True
    if _parse_scene(text) is not None:
        return True
    if _parse_budget(text) is not None:
        return True
    if "以内" in tl or "以下" in tl or "不超过" in tl or "预算" in tl or "价格" in tl or "价位" in tl:
        return True
    if _is_reco_intent(text):
        return True
    if _is_compare_intent(text) or _is_qa_question(text):
        return True
    return False


# =========================================================
# ✅ 对比意图识别增强（序号/机型名）
# =========================================================
def _normalize_compare_text(t: str) -> str:
    s = (t or "").strip()
    s = s.replace("，", ",").replace("、", ",").replace("；", ";").replace("：", ":")
    s = s.replace("／", "/").replace("｜", "|").replace("—", "-").replace("～", "~")
    return s


def _parse_two_indices_any(t: str) -> Optional[Tuple[int, int, str]]:
    s = _normalize_compare_text(t)
    if not s:
        return None
    tl = s.lower()

    m = re.search(r"第\s*(\d{1,2})\s*(?:个|台|款|条|项)?\s*(?:和|与|跟|对比|比较|vs|v)\s*第?\s*(\d{1,2})", tl, re.I)
    if m:
        return int(m.group(1)), int(m.group(2)), "strong"
    m = re.search(r"(\d{1,2})\s*(?:和|与|跟|对比|比较)\s*(\d{1,2})", tl, re.I)
    if m:
        return int(m.group(1)), int(m.group(2)), "strong"
    m = re.search(r"(\d{1,2})\s*(?:vs|v)\s*(\d{1,2})", tl, re.I)
    if m:
        return int(m.group(1)), int(m.group(2)), "strong"
    m = re.fullmatch(r"\s*(\d{1,2})\s*(?:[,\s/|;]+)\s*(\d{1,2})\s*", tl)
    if m:
        return int(m.group(1)), int(m.group(2)), "weak"
    return None


def _valid_index_pair(a: int, b: int, total: int) -> bool:
    return total > 0 and a != b and 1 <= a <= total and 1 <= b <= total


def _normalize_pair_order(a: int, b: int) -> Tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _is_compare_confirm_reply(t: str) -> bool:
    tl = (t or "").strip().lower()
    yes = ["对比", "比较", "是", "好", "好的", "行", "可以", "确认", "开始", "继续", "对", "嗯", "y", "yes", "ok"]
    return bool(tl) and any(w == tl or w in tl for w in yes)


def _is_compare_cancel_reply(t: str) -> bool:
    tl = (t or "").strip().lower()
    no = ["不是", "不要", "取消", "算了", "不", "no", "n"]
    return bool(tl) and any(w == tl or w in tl for w in no)


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip().lower())


def _parse_compare_models(t: str) -> Optional[Tuple[str, str]]:
    s = (t or "").strip()
    if not s:
        return None
    quoted = re.findall(r"[\"“”'‘’](.*?)[\"“”'‘’]", s)
    quoted = [x.strip() for x in quoted if x.strip()]
    if len(quoted) >= 2:
        return quoted[0], quoted[1]
    m = re.search(r"(.+?)\s*(?:和|与|vs|v)\s*(.+)", s, flags=re.I)
    if not m:
        return None
    a = m.group(1).strip()
    b = m.group(2).strip()
    b = re.sub(r"(对比|比較|比较|pk|哪个好|怎么选|区别|差别|差异)\s*$", "", b, flags=re.I).strip()
    a = re.sub(r"(对比|比較|比较|pk)\s*$", "", a, flags=re.I).strip()
    if not a or not b or a == b:
        return None
    if not (re.search(r"\d", a) or re.search(r"\d", b)):
        return None
    return a, b


def _find_best_candidate_by_text(cands: List[Dict[str, Any]], q: str) -> Optional[Dict[str, Any]]:
    qq = _norm_text(q)
    if not qq:
        return None
    for it in cands:
        m = _norm_text(str(it.get("model") or ""))
        b = _norm_text(str(it.get("brand") or ""))
        combo = b + m
        if qq == m or qq == combo:
            return it
    best = None
    best_score = -1
    for it in cands:
        m0 = str(it.get("model") or "")
        b0 = str(it.get("brand") or "")
        mm = _norm_text(m0)
        bb = _norm_text(b0)
        combo = bb + mm

        score = 0
        if qq in combo:
            score += 10
        if qq in mm:
            score += 8
        if mm in qq:
            score += 6
        if combo in qq:
            score += 7

        qnums = re.findall(r"\d{2,3}", q)
        mnums = re.findall(r"\d{2,3}", m0)
        if qnums and mnums and qnums[0] == mnums[0]:
            score += 2

        if score > best_score:
            best_score = score
            best = it
    return best if best_score > 0 else None


# =========================================================
# ✅ 价格区间解析（兼容中文破折号/全角）
# =========================================================
def _parse_price_bucket_range(v: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    s = (v or "").strip()
    if not s:
        return None, None, None
    if s == "skip":
        return None, None, "skip"

    s2 = s.replace("—", "-").replace("–", "-").replace("－", "-").replace("~", "-").replace("～", "-")
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", s2)
    if m:
        lo = int(m.group(1))
        hi = int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi, f"{lo}-{hi}"

    m2 = re.match(r"^\s*(\d+)\s*\+\s*$", s2)
    if m2:
        lo = int(m2.group(1))
        return lo, None, f"{lo}+"

    return None, None, None


_BUDGET_CLEAR_KWS = [
    "不限预算", "预算不限", "不限制预算", "不限定预算",
    "没有预算", "没预算", "不设预算", "不设上限",
    "随便预算", "预算随便", "无预算要求", "预算无要求",
    "不考虑预算", "不看预算", "预算不重要",
]


def _should_clear_budget(raw: str) -> bool:
    t = (raw or "").strip().lower()
    if not t:
        return False
    return any(k in t for k in _BUDGET_CLEAR_KWS)


def _is_budget_range_string(s: str) -> bool:
    s2 = (s or "").strip().replace("—", "-").replace("–", "-").replace("－", "-").replace("~", "-").replace("～", "-")
    return re.fullmatch(r"\d+\s*-\s*\d+", s2) is not None


# =========================================================
# ✅ 判定“纯尺寸切换输入”（只有它才清空其它槽位）
# =========================================================
def _is_size_only_input(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    tl = t.lower()

    if _looks_like_model_with_size_prefix(t):
        return False

    if _parse_brand(t) is not None:
        return False
    if _parse_scene(t) is not None:
        return False
    if _should_clear_budget(t) or _parse_budget(t) is not None or _is_budget_range_string(t):
        return False
    if _is_reco_intent(t) or _is_qa_question(t) or _is_compare_intent(t):
        return False
    if any(k in tl for k in ("预算", "价位", "价格", "以内", "以下", "不超过", "之内", "万", "k")):
        return False

    if re.fullmatch(r"\d{2,3}", tl):
        return True
    if re.fullmatch(r"\d{2,3}\s*(寸|英寸|吋|inch|in|\")", tl):
        return True

    return False


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
    return _normalize_launch_to_store(v)


def _normalize_brand(d: Dict[str, Any]) -> Optional[str]:
    v = _pick(d, ["brand", "品牌", "厂商", "manufacturer"])
    if v is None:
        return None
    s = str(v).strip()
    sl = s.lower()
    if sl == "tcl":
        return "TCL"
    if sl in ("sony",):
        return "索尼"
    if sl in ("hisense",):
        return "海信"
    if sl in ("ffalcon", "f-falcon", "falcon"):
        return "雷鸟"
    if sl in ("skyworth",):
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
    brand = _normalize_brand(row) or "未知"
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
                it["launch_date"] = _normalize_launch_to_store(it.get("launch_date"))
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
# sqlite fallback（可选）— 你当前默认关闭
# =========================================================
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
    # 你之前版本如需启用 sqlite，这里接回你的实现
    return []


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
        mp[k] = it
    return list(mp.values())


def _filter_products(
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

        ip = _safe_int(p)
        if ip is not None:
            if budget_min is not None and ip < int(budget_min):
                continue
            if budget_max is not None and ip > int(budget_max):
                continue
        out.append(it)
    return out


def _sort_by_price_desc(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key(it: Dict[str, Any]) -> Tuple[int, int]:
        ip = _safe_int(it.get("price_cny"))
        if ip is not None:
            return (0, -int(ip))
        return (1, 0)

    return sorted(items, key=key)


def list_candidates(
    size: Optional[int],
    brand: Optional[str],
    budget_min: Optional[int],
    budget_max: Optional[int],
    limit: int = 10,
) -> Tuple[int, List[Dict[str, Any]], Dict[str, Any]]:
    yaml_items, meta = _load_yaml_products()
    sqlite_items = _sqlite_query_products_exact(brand=brand, size=size, budget_min=budget_min, budget_max=budget_max)
    merged = _merge_products(yaml_items=yaml_items, sqlite_items=sqlite_items)
    filt = _filter_products(merged, brand=brand, size=size, budget_min=budget_min, budget_max=budget_max)
    sorted_items = _sort_by_price_desc(filt)
    return len(sorted_items), sorted_items[:limit], meta


def _format_candidates(
    size: Optional[int],
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

    if size is None:
        cond.append("尺寸=不限")
    else:
        cond.append(f"尺寸={size}寸")

    head = f"📌 当前筛选候选：{total} 台（" + "，".join(cond) + "）"
    if total == 0:
        return head + "\n⚠️ 当前条件下没有候选。你可以：放宽品牌/提高预算/换尺寸/或输入“不限尺寸”。"

    lines = [head, "（展示前10）"]
    for i, tv in enumerate(cands, 1):
        price = tv.get("price_cny")
        price_str = f"￥{price}" if price is not None else "￥未知"
        launch_mm = fmt_launch_yyyy_mm(tv.get("launch_date"))
        lines.append(f"{i}. {tv.get('brand')} {tv.get('model')} {tv.get('size_inch')}寸 | 首发 {launch_mm} | {price_str}")
    return "\n".join(lines)


# =========================================================
# ✅ RAG 导购：把本地候选 + 用户原话交给智谱
# =========================================================
def _compact_tv_for_llm(tv: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "brand": tv.get("brand"),
        "model": tv.get("model"),
        "size_inch": tv.get("size_inch"),
        "price_cny": tv.get("price_cny"),
        "launch_date": tv.get("launch_date"),
        "positioning": tv.get("positioning"),
        "source": tv.get("source"),
    }


def _build_llm_sales_messages(
    state: Dict[str, Any],
    user_text: str,
    topn: List[Dict[str, Any]],
    history: List[Dict[str, str]],
    local_reco: str = "",
) -> List[Dict[str, str]]:
    # ✅ 改造：先给“最终推荐方案”，追问放最后且明确“不回答也行”
    system = (
        "你是“晓春哥 XCG”的智能导购（电视选购顾问）。目标：像真人导购一样对话，不要死板推流程。\n"
        "严格要求：\n"
        "1) 我会给你本地候选清单（JSON）。推荐/对比必须优先基于本地候选；不要编造不存在的型号和参数。\n"
        "2) 如果候选不足或用户问的型号本地没收录：要明确说明“本地库未收录/参数需核对”，再给通用选购建议。\n"
        "3) 输出结构必须是：\n"
        "   - 一句话结论（直接告诉用户怎么选）\n"
        "   - 最终推荐方案（2-4 台：分别说明适合人群/理由/注意点），让用户“不用再回答也能直接买”\n"
        "   - 可选的关键追问（最多 2-3 个）：并明确说明“如果你暂时没问题/不想回答，也没关系，我就按默认方案走”\n"
        "4) 不要强迫用户按按钮流程走；可以顺带提示“你也可以点按钮更快筛选”，但回答必须先解决用户问题。\n"
        "5) 若用户给了预算上限（≤X）或区间（A-B），优先按预算过滤；如果没给预算，不要追问太多。\n"
        "6) 若用户明确品牌（或 brand_lock），不要推荐其他品牌（除非用户要求扩展）。\n"
        "7) 我会给你 local_reco（本地规则推荐话术）。它是系统底稿：你要在其基础上优化为更像导购的表达，补充关键对比点，但仍不得编造具体参数。\n"
    )

    payload = {
        "state": {
            "size": state.get("size"),
            "scene": state.get("scene"),
            "brand": state.get("brand"),
            "brand_lock": state.get("brand_lock"),
            "brand_unlimited": state.get("brand_unlimited"),
            "budget_bucket": state.get("budget_bucket"),
            "budget_min": state.get("budget_min"),
            "budget_max": state.get("budget_max"),
        },
        "user_text": user_text,
        "top_candidates": [_compact_tv_for_llm(x) for x in (topn or [])],
        "local_reco": (local_reco or "").strip(),
    }

    msgs: List[Dict[str, str]] = [{"role": "system", "content": system}]
    if history:
        msgs.append({"role": "user", "content": "最近对话历史(供上下文追踪)：\n" + json.dumps(history[-10:], ensure_ascii=False)})
    msgs.append({"role": "user", "content": "导购上下文(JSON)：\n" + json.dumps(payload, ensure_ascii=False)})
    msgs.append({"role": "user", "content": "请直接像导购一样回答用户，不要输出代码/JSON。"})
    return msgs


def _smart_fallback_text(state: Dict[str, Any], user_text: str) -> str:
    tips = []
    if state.get("size") is None and not state.get("size_unlimited"):
        tips.append("尺寸（如 65/75/85/98）")
    if state.get("budget_bucket") is None:
        tips.append("预算（如 4000以下 / 3500-5000 / 不限预算）")
    if state.get("scene") is None:
        tips.append("用途（ps5/电影/明亮客厅强光）")

    extra = ""
    if tips:
        extra = "为了更准，你再补充一下：" + "、".join(tips) + "。"

    return (
        "我明白你是在让 XCG 像导购一样直接给推荐/结论。\n"
        "但当前智能导购未启用或智谱不可用，所以我先按通用选购逻辑给你建议：\n"
        "1) 先定用途（游戏/电影/白天强光）决定优先指标。\n"
        "2) 同价位优先看：控光（分区）/峰值亮度/反射控制/HDMI2.1&ALLM（游戏）。\n"
        "3) 机型差异（面板/分区/亮度/接口/算法）建议以官方规格与权威评测核对。\n"
        + (("\n" + extra) if extra else "")
    )


# =========================================================
# ✅ 后端兜底：当智谱给了追问，也要追加“没问题就给最终推荐”
# =========================================================
def _looks_like_only_questions(ans: str) -> bool:
    t = (ans or "").strip().lower()
    if not t:
        return True
    if "关键追问" in t or "你再补充" in t or "想确认" in t or "再确认" in t:
        return True
    return False


def _append_default_final_reco_if_needed(
    ans: str,
    state: Dict[str, Any],
    top_candidates: List[Dict[str, Any]],
    local_reco: str,
) -> str:
    """
    如果答案里出现“关键追问”，但没有给出能直接下单的最终推荐，则强制追加一段默认最终推荐。
    """
    a = (ans or "").strip()
    if not a:
        a = ""

    lower = a.lower()
    if ("最终推荐" in a) or ("最终方案" in a) or ("直接下单" in a) or ("你可以直接买" in a) or ("直接买" in a):
        return a

    if not _looks_like_only_questions(a):
        return a

    lines: List[str] = []
    lines.append("")
    lines.append("——")
    lines.append("✅ **如果你暂时没问题/不想回答也没关系**：我就按当前信息，直接给你一个“默认最终推荐”（你可以照这个去下单）。")

    if (local_reco or "").strip():
        lines.append("")
        lines.append("**默认最终推荐（按当前条件）**：")
        lines.append(local_reco.strip())
    else:
        pick = []
        for it in (top_candidates or [])[:4]:
            b = it.get("brand")
            m = it.get("model")
            s = it.get("size_inch")
            p = it.get("price_cny")
            if not (b and m and s):
                continue
            price = f"￥{p}" if p is not None else "￥未知"
            pick.append(f"- {b} {m} {s}寸（{price}）")
        if pick:
            lines.append("")
            lines.append("**默认最终推荐（本地库候选Top）**：")
            lines.extend(pick)
        else:
            lines.append("")
            lines.append("当前条件下本地库候选不够完善，我建议你告诉我：预算上限 & 尺寸，我就能给你更确定的下单清单。")

    lines.append("")
    lines.append("你可以直接回：**“没问题，按默认推荐”**（我就把 1-2 台作为最终主推并给你购买注意点）；")
    lines.append("或者回一句：**预算上限/是否强光/更在意画质还是延迟**（我会把方案再收敛一档）。")

    return (a + "\n" + "\n".join(lines)).strip()


# =========================================================
# /api/chat
# =========================================================
def next_question(state: Dict[str, Any]) -> Optional[str]:
    """
    ✅ 新流程：尺寸 → 价格区间 → 品牌（可选/可跳过：不限品牌）→ 主要用途
    - brand_unlimited=True 代表用户已明确“不限品牌”，品牌步骤视为完成
    """
    if state.get("size") is None and not state.get("size_unlimited", False):
        return "请先选择【尺寸】（点页面按钮）。如果你想不限定尺寸，可以直接输入：不限尺寸。"

    if state.get("budget_bucket") is None:
        return "请选择【价格区间】（点页面按钮）。也可以直接输入「不限预算/没有预算」。"

    brand_unlimited = bool(state.get("brand_unlimited", False))
    if (state.get("brand") is None) and (not state.get("brand_lock")) and (not brand_unlimited):
        return "品牌偏好是什么？你可以直接说：TCL/海信/索尼/小米/雷鸟/创维…（或输入：不限品牌）"

    if state.get("scene") is None:
        return "主要用途是什么？你可以直接说：打游戏/玩PS5/看电影/追剧/白天客厅强光（也支持输入 ps5 / movie / bright）"

    return None


class ChatReq(BaseModel):
    text: str
    state: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    source: Optional[str] = None  # ✅ ui_button / user_text


class ChatResp(BaseModel):
    state: Dict[str, Any]
    reply: str
    ui: Optional[Dict[str, Any]] = None


_RESET_KWS = ("重置", "清空", "重新开始", "reset")


def _should_run_rag(source: str, text: str) -> bool:
    """
    ✅ 关键：默认只有用户输入框（user_text）才走 RAG 导购。
    - ui_button：不走（但“用途刚确定”例外在 chat() 里处理）
    - user_text：默认走（但 reset/确认类除外）
    """
    s = (source or "").strip().lower()
    t = (text or "").strip()
    if not t:
        return False
    if any(k in t.lower() for k in _RESET_KWS):
        return False
    if _is_compare_confirm_reply(t) or _is_compare_cancel_reply(t):
        return False
    return s == "user_text"


@app.post("/api/chat", response_model=ChatResp)
def chat(req: ChatReq):
    def _ret(reply: str, ui: Optional[Dict[str, Any]] = None) -> ChatResp:
        sid2 = (req.session_id or "anonymous").strip()
        _session_add(sid2, "assistant", reply)
        return ChatResp(state=base, reply=reply, ui=ui)

    sid = (req.session_id or "anonymous").strip()
    history_deque = _session_get(sid)

    base = {
        "size": None,
        "size_unlimited": False,
        "scene": None,
        "budget": None,
        "brand": None,
        "brand_lock": None,
        "brand_unlimited": False,
        "budget_bucket": None,
        "budget_min": None,
        "budget_max": None,
        "pending_compare": None,
        "pending_size": None,
    }

    if req.state:
        st = dict(req.state)
        for k in list(base.keys()):
            if k in st:
                base[k] = st.get(k)

    t = (req.text or "").strip()
    tl = t.lower()
    source = (req.source or "").strip().lower() or "user_text"

    if t != "":
        _session_add(sid, "user", t)

    if t == "":
        q = next_question(base) or "你可以继续输入需求。"
        return _ret(q, None)

    if any(k in tl for k in _RESET_KWS):
        base.update(
            {
                "size": None,
                "size_unlimited": False,
                "scene": None,
                "budget": None,
                "brand": None,
                "brand_lock": None,
                "brand_unlimited": False,
                "budget_bucket": None,
                "budget_min": None,
                "budget_max": None,
                "pending_compare": None,
                "pending_size": None,
            }
        )
        return _ret("✅ 已重置。请先选择【尺寸】（点页面按钮），或输入：不限尺寸。", None)

    # -----------------------------------------------------
    # pending_size（保持你原逻辑）
    # -----------------------------------------------------
    pending_size = base.get("pending_size")
    if isinstance(pending_size, dict) and pending_size.get("from") and pending_size.get("to"):
        from_size = int(pending_size["from"])
        to_size = int(pending_size["to"])

        if _is_size_confirm_reply(t, to_size=to_size):
            base["size"] = to_size
            base["size_unlimited"] = False
            base["pending_size"] = None
            if isinstance(pending_size.get("apply"), dict):
                ap = pending_size["apply"]
                if ap.get("budget_bucket") is not None:
                    base["budget_bucket"] = ap.get("budget_bucket")
                    base["budget_min"] = ap.get("budget_min")
                    base["budget_max"] = ap.get("budget_max")
                    base["budget"] = ap.get("budget")
                if ap.get("brand") is not None and not base.get("brand_lock"):
                    base["brand"] = ap.get("brand")
                    base["brand_unlimited"] = False
                if ap.get("brand_unlimited"):
                    base["brand"] = None
                    base["brand_lock"] = None
                    base["brand_unlimited"] = True
                if ap.get("scene") is not None:
                    base["scene"] = ap.get("scene")

            t = f"已确认切换到{to_size}寸"
            tl = t.lower()

        elif _is_size_cancel_reply(t, from_size=from_size):
            base["pending_size"] = None
            base["size"] = from_size
            base["size_unlimited"] = False
            return _ret(
                f"✅ 好的，尺寸保持 {from_size} 寸。\n你刚才那句里还提到预算/价格等条件的话，可以再说一遍（例如：4000以下 / 6000-8500 / 不限预算）。",
                None,
            )
        else:
            return _ret(
                (
                    f"我确认一下：你刚才已选 **{from_size} 寸**，但你这句话里又提到 **{to_size} 寸**。\n"
                    f"你是要把尺寸改成 **{to_size} 寸** 吗？\n\n"
                    f"- 回复：**是 / 改成{to_size} / {to_size}**  → 我就切到 {to_size} 寸并按你刚才条件检索\n"
                    f"- 回复：**不 / 继续{from_size} / {from_size}** → 我就保持 {from_size} 寸"
                ),
                None,
            )

    # -----------------------------------------------------
    # clear
    # -----------------------------------------------------
    if _should_clear_brand(t):
        base["brand"] = None
        base["brand_lock"] = None
        base["brand_unlimited"] = True

    if _should_clear_size(t):
        base["size"] = None
        base["size_unlimited"] = True
        base["pending_compare"] = None
        base["pending_size"] = None

    if _should_clear_budget(t):
        base["budget_bucket"] = "skip"
        base["budget_min"] = None
        base["budget_max"] = None
        base["budget"] = None

    # -----------------------------------------------------
    # pending_compare（弱确认）
    # -----------------------------------------------------
    pending = base.get("pending_compare")
    if isinstance(pending, dict) and pending.get("a") and pending.get("b"):
        if _is_compare_confirm_reply(t):
            pass
        elif _is_compare_cancel_reply(t):
            base["pending_compare"] = None
            return _ret("✅ 已取消对比。你可以继续输入需求或直接问“这两台差别是什么”。", None)
        else:
            return _ret("我正在等你确认是否要对比上次选择的两台：回复“对比/确认”开始，或回复“取消”。", None)

    # -----------------------------------------------------
    # 解析 brand/scene/budget/size/bucket
    # -----------------------------------------------------
    parsed_brand = _parse_brand(t)
    parsed_scene = _parse_scene(t)

    parsed_budget = _parse_budget(t)
    if parsed_budget is None:
        parsed_budget = _parse_budget_from_free_numbers(t, size=base.get("size"))

    parsed_size = _parse_size(t)
    lo, hi, bucket = _parse_price_bucket_range(t)

    # 尺寸冲突确认（已选A，句子出现B且还有其它条件 → 反问确认）
    if parsed_size is not None and base.get("size") is not None:
        old_size = int(base["size"])
        new_size = int(parsed_size)
        if (
            new_size != old_size
            and (not _is_size_only_input(t))
            and _text_has_other_constraints(t)
            and (not _explicit_size_switch(t, to_size=new_size))
        ):
            apply_pack: Dict[str, Any] = {}
            if bucket is not None:
                apply_pack.update({"budget_bucket": bucket, "budget_min": lo, "budget_max": hi, "budget": hi})
            elif base.get("budget_bucket") != "skip" and parsed_budget is not None:
                apply_pack.update({"budget_bucket": "manual", "budget_min": None, "budget_max": int(parsed_budget), "budget": int(parsed_budget)})

            if _should_clear_brand(t):
                apply_pack.update({"brand_unlimited": True})
            elif parsed_brand is not None and (not base.get("brand_lock")):
                apply_pack.update({"brand": parsed_brand})

            if parsed_scene is not None:
                apply_pack.update({"scene": parsed_scene})

            base["pending_size"] = {"from": old_size, "to": new_size, "raw": t, "apply": apply_pack}

            return _ret(
                (
                    f"我确认一下：你刚才已选 **{old_size} 寸**，但你这句话里又提到 **{new_size} 寸**（并且还带了预算/条件）。\n"
                    f"你是要把尺寸改成 **{new_size} 寸** 吗？\n\n"
                    f"- 回复：**是 / 改成{new_size} / {new_size}**  → 我就切到 {new_size} 寸并按你刚才条件检索\n"
                    f"- 回复：**不 / 继续{old_size} / {old_size}** → 我就保持 {old_size} 寸"
                ),
                None,
            )

    # 写入 size
    if parsed_size is not None:
        base["size"] = int(parsed_size)
        base["size_unlimited"] = False

    if parsed_brand is not None and (not _should_clear_brand(t)) and (not base.get("brand_lock")):
        base["brand"] = parsed_brand
        base["brand_unlimited"] = False

    # ✅ scene_just_set（用途刚刚设置）
    old_scene = base.get("scene")
    if parsed_scene is not None:
        old_scene = base.get("scene")
        base["scene"] = parsed_scene
    scene_just_set = (old_scene is None) and (base.get("scene") is not None)

    if base.get("budget_bucket") != "skip" and parsed_budget is not None:
        base["budget"] = int(parsed_budget)
        base["budget_bucket"] = "manual"
        base["budget_min"] = None
        base["budget_max"] = int(parsed_budget)

    if bucket is not None:
        base["budget_bucket"] = bucket
        base["budget_min"] = lo
        base["budget_max"] = hi
        base["budget"] = hi

    # -----------------------------------------------------
    # 如果还没尺寸：user_text 可走智谱追问兜底
    # -----------------------------------------------------
    if base.get("size") is None and not base.get("size_unlimited", False):
        if _should_run_rag(source, t):
            if ENABLE_ZHIPU_QA and ZHIPU_API_KEY and _is_tv_domain_question(t):
                try:
                    total_all, cands_all, _meta_all = list_candidates(
                        size=None, brand=base.get("brand"), budget_min=base.get("budget_min"), budget_max=base.get("budget_max"), limit=10
                    )
                    msgs = _build_llm_sales_messages(base, t, topn=cands_all[:8], history=list(history_deque), local_reco="")
                    ans = zhipu_chat_with_retry(msgs, temperature=0.25, retries=2)
                    ans = _append_default_final_reco_if_needed(ans, base, cands_all[:8], local_reco="")
                    return _ret(ans or "（导购生成失败：空输出）", None)
                except Exception:
                    return _ret(_smart_fallback_text(base, t), None)
            return _ret(_smart_fallback_text(base, t), None)

        return _ret("请先选择【尺寸】（点页面按钮），或输入：不限尺寸。", None)

    # -----------------------------------------------------
    # 拉候选（用于RAG/对比/展示）
    # -----------------------------------------------------
    total_all, cands_all, meta_all = list_candidates(
        size=base.get("size"),
        brand=base.get("brand"),
        budget_min=base.get("budget_min"),
        budget_max=base.get("budget_max"),
        limit=200,
    )

    # -----------------------------------------------------
    # RAG 触发策略：
    # - user_text：默认触发
    # - ui_button：默认不触发
    #   但：用途刚确定(scene_just_set) 且 size+budget_bucket 已完成 => 放行一次
    # -----------------------------------------------------
    allow_rag = _should_run_rag(source, t)
    if (not allow_rag) and scene_just_set:
        if base.get("size") is not None and base.get("budget_bucket") is not None:
            allow_rag = True

    # -----------------------------------------------------
    # ✅ 机型名对比（用户输入两个型号）优先处理（更像导购）
    # -----------------------------------------------------
    pair = _parse_compare_models(t)
    if pair is not None and _is_tv_domain_question(t):
        qa, qb = pair
        a = _find_best_candidate_by_text(cands_all, qa) if cands_all else None
        b = _find_best_candidate_by_text(cands_all, qb) if cands_all else None

        if ENABLE_ZHIPU_QA and ZHIPU_API_KEY and source == "user_text":
            try:
                topn: List[Dict[str, Any]] = []
                if a:
                    topn.append(a)
                if b and b is not a:
                    topn.append(b)

                local_reco = ""
                if base.get("scene") is not None and base.get("size") is not None:
                    try:
                        local_reco = recommend_text(
                            size=int(base["size"]),
                            scene=str(base["scene"]),
                            brand=base.get("brand"),
                            budget=base.get("budget"),
                        )
                    except Exception:
                        local_reco = ""

                msgs = _build_llm_sales_messages(base, t, topn=topn, history=list(history_deque), local_reco=local_reco)
                ans = zhipu_chat_with_retry(msgs, temperature=0.22, retries=2)
                ans = _append_default_final_reco_if_needed(ans, base, topn, local_reco=local_reco)
                return _ret(ans or "（对比生成失败：空输出）", None)
            except Exception:
                return _ret(_smart_fallback_text(base, t), None)

        # 本地兜底字段对照
        lines = ["（当前未启用智能对比：先给你本地字段对比兜底）\n"]
        if a:
            lines.append(
                f"A: {a.get('brand')} {a.get('model')} {a.get('size_inch')}寸  价格={a.get('price_cny')}  首发={fmt_launch_yyyy_mm(a.get('launch_date'))}  定位={a.get('positioning')}"
            )
        else:
            lines.append(f"A: {qa}（本地库未匹配到具体机型）")
        if b:
            lines.append(
                f"B: {b.get('brand')} {b.get('model')} {b.get('size_inch')}寸  价格={b.get('price_cny')}  首发={fmt_launch_yyyy_mm(b.get('launch_date'))}  定位={b.get('positioning')}"
            )
        else:
            lines.append(f"B: {qb}（本地库未匹配到具体机型）")
        lines.append("\n你也可以把预算/用途说一下（ps5/电影/强光），我会按你的场景给结论。")
        return _ret("\n".join(lines), None)

    # -----------------------------------------------------
    # ✅ 序号对比（1 vs 2 / 1和2）
    # -----------------------------------------------------
    cmp_idx = _parse_two_indices_any(t)
    if cmp_idx is not None and total_all > 0:
        a_i, b_i, mode = cmp_idx
        if not _valid_index_pair(a_i, b_i, total_all):
            return _ret("你输入的序号不在范围内。你也可以直接说“这两台差别是什么/哪个好”。", None)

        a_i, b_i = _normalize_pair_order(a_i, b_i)

        if mode == "weak":
            base["pending_compare"] = {"a": a_i, "b": b_i}
            t1 = cands_all[a_i - 1]
            t2 = cands_all[b_i - 1]
            return _ret(
                (
                    f"确认一下：你要对比的是这两台吗？\n"
                    f"A) {t1.get('brand')} {t1.get('model')} {t1.get('size_inch')}寸 | ￥{t1.get('price_cny')} | 首发 {fmt_launch_yyyy_mm(t1.get('launch_date'))}\n"
                    f"B) {t2.get('brand')} {t2.get('model')} {t2.get('size_inch')}寸 | ￥{t2.get('price_cny')} | 首发 {fmt_launch_yyyy_mm(t2.get('launch_date'))}\n\n"
                    f"回复“对比/确认”我就开始；或回复“取消”。"
                ),
                None,
            )

        a = cands_all[a_i - 1]
        b = cands_all[b_i - 1]

        if ENABLE_ZHIPU_QA and ZHIPU_API_KEY and source == "user_text":
            try:
                local_reco = ""
                if base.get("scene") is not None and base.get("size") is not None:
                    try:
                        local_reco = recommend_text(
                            size=int(base["size"]),
                            scene=str(base["scene"]),
                            brand=base.get("brand"),
                            budget=base.get("budget"),
                        )
                    except Exception:
                        local_reco = ""
                msgs = _build_llm_sales_messages(
                    base,
                    f"请对比：{a.get('brand')} {a.get('model')} vs {b.get('brand')} {b.get('model')}。{t}",
                    topn=[a, b],
                    history=list(history_deque),
                    local_reco=local_reco,
                )
                ans = zhipu_chat_with_retry(msgs, temperature=0.22, retries=2)
                ans = _append_default_final_reco_if_needed(ans, base, [a, b], local_reco=local_reco)
                return _ret(ans or "（对比生成失败：空输出）", None)
            except Exception:
                return _ret(_smart_fallback_text(base, t), None)

        # 本地字段对照兜底
        lines = []
        lines.append("（当前未启用智能对比：先给你本地字段对比）\n")
        lines.append(f"A: {a.get('brand')} {a.get('model')} {a.get('size_inch')}寸  价格={a.get('price_cny')}  首发={fmt_launch_yyyy_mm(a.get('launch_date'))}  定位={a.get('positioning')}")
        lines.append(f"B: {b.get('brand')} {b.get('model')} {b.get('size_inch')}寸  价格={b.get('price_cny')}  首发={fmt_launch_yyyy_mm(b.get('launch_date'))}  定位={b.get('positioning')}")
        return _ret("\n".join(lines), None)

    # pending_compare 确认（弱确认模式）
    pending = base.get("pending_compare")
    if isinstance(pending, dict) and pending.get("a") and pending.get("b") and _is_compare_confirm_reply(t):
        a_i = int(pending["a"])
        b_i = int(pending["b"])
        base["pending_compare"] = None
        if not _valid_index_pair(a_i, b_i, total_all):
            return _ret("当前候选不足以完成对比，我先把候选再列一次。", None)
        a_i, b_i = _normalize_pair_order(a_i, b_i)
        a = cands_all[a_i - 1]
        b = cands_all[b_i - 1]

        if ENABLE_ZHIPU_QA and ZHIPU_API_KEY and source == "user_text":
            try:
                local_reco = ""
                if base.get("scene") is not None and base.get("size") is not None:
                    try:
                        local_reco = recommend_text(
                            size=int(base["size"]),
                            scene=str(base["scene"]),
                            brand=base.get("brand"),
                            budget=base.get("budget"),
                        )
                    except Exception:
                        local_reco = ""
                msgs = _build_llm_sales_messages(base, f"用户确认对比：{a_i} vs {b_i}", topn=[a, b], history=list(history_deque), local_reco=local_reco)
                ans = zhipu_chat_with_retry(msgs, temperature=0.22, retries=2)
                ans = _append_default_final_reco_if_needed(ans, base, [a, b], local_reco=local_reco)
                return _ret(ans or "（对比生成失败：空输出）", None)
            except Exception:
                return _ret(_smart_fallback_text(base, t), None)

        lines = []
        lines.append("（当前未启用智能对比：先给你本地字段对比）\n")
        lines.append(f"A: {a.get('brand')} {a.get('model')} {a.get('size_inch')}寸  价格={a.get('price_cny')}")
        lines.append(f"B: {b.get('brand')} {b.get('model')} {b.get('size_inch')}寸  价格={b.get('price_cny')}")
        return _ret("\n".join(lines), None)

    # -----------------------------------------------------
    # ✅ RAG 导购（含“追问兜底追加最终推荐”）
    # -----------------------------------------------------
    if allow_rag and _is_tv_domain_question(t):
        if ENABLE_ZHIPU_QA and ZHIPU_API_KEY:
            try:
                topn = (cands_all or [])[:12]
                local_reco = ""
                if base.get("scene") is not None and base.get("size") is not None:
                    try:
                        local_reco = recommend_text(
                            size=int(base["size"]),
                            scene=str(base["scene"]),
                            brand=base.get("brand"),
                            budget=base.get("budget"),
                        )
                    except Exception:
                        local_reco = ""

                msgs = _build_llm_sales_messages(base, t, topn=topn, history=list(history_deque), local_reco=local_reco)
                ans = zhipu_chat_with_retry(msgs, temperature=0.25, retries=2)
                ans = _append_default_final_reco_if_needed(ans, base, topn, local_reco=local_reco)
                if ans:
                    return _ret(ans, None)
                return _ret("（导购生成失败：空输出）", None)
            except Exception:
                return _ret(_smart_fallback_text(base, t), None)
        else:
            return _ret(_smart_fallback_text(base, t), None)

    # -----------------------------------------------------
    # 非RAG：继续走本地流程输出候选/推荐
    # -----------------------------------------------------
    collected = []
    if base.get("brand_lock"):
        collected.append("品牌锁定=" + "、".join(base["brand_lock"]))
    elif base.get("brand"):
        collected.append(f"品牌={base['brand']}")
    elif base.get("brand_unlimited"):
        collected.append("品牌=不限")

    if base.get("budget_bucket") == "skip":
        collected.append("不限预算")
    elif base.get("budget_min") is not None and base.get("budget_max") is not None:
        collected.append(f"预算区间={base['budget_min']}-{base['budget_max']}")
    elif base.get("budget_max") is not None:
        collected.append(f"预算≤{base['budget_max']}")

    if base.get("size_unlimited"):
        collected.append("尺寸=不限")
    elif base.get("size") is not None:
        collected.append(f"尺寸={base['size']}寸")

    if base.get("scene") is not None:
        collected.append(f"场景={base['scene']}")

    header = f"（当前已收集：{'; '.join(collected) if collected else '暂无'}）\n\n"

    total, cands, _m = list_candidates(
        size=base.get("size"),
        brand=base.get("brand"),
        budget_min=base.get("budget_min"),
        budget_max=base.get("budget_max"),
        limit=10,
    )

    reply_parts: List[str] = []
    reply_parts.append(
        _format_candidates(
            size=base.get("size"),
            total=total,
            cands=cands,
            brand=base.get("brand"),
            budget_min=base.get("budget_min"),
            budget_max=base.get("budget_max"),
            budget_bucket=base.get("budget_bucket"),
        )
    )

    if base.get("scene") is not None and base.get("size") is not None:
        reply_parts.append("")
        reply_parts.append(
            recommend_text(
                size=int(base["size"]),
                scene=str(base["scene"]),
                brand=base.get("brand"),
                budget=base.get("budget"),
            )
        )

    q = next_question(base)
    if q:
        return _ret(header + "\n\n".join(reply_parts) + "\n\n" + q, None)

    return _ret(header + "\n\n".join(reply_parts), None)


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
        "zhipu_enabled": bool(bool(ZHIPU_API_KEY)),
        "zhipu_model": ZHIPU_MODEL,
        "zhipu_base_url": ZHIPU_BASE_URL or "",
        "zhipu_timeout_sec": ZHIPU_TIMEOUT_SEC,
        "enable_zhipu_qa": bool(ENABLE_ZHIPU_QA),
        "yaml_cache_ttl": _YAML_CACHE_TTL,
        "yaml_max_depth": _YAML_MAX_DEPTH,
        "session_ttl_sec": _SESSION_TTL,
        "session_max_turns": _SESSION_MAX_TURNS,
        "sessions_alive": len(_sessions),
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