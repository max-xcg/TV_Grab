# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import time
from uuid import uuid4
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from tv_buy_1_0.run_reco import recommend_text, list_candidates, format_candidates

# tools 路由
from tv_buy_1_0.tools.tool_api import router as tools_router

# 你已有的报告路由（/api/report/contrast）
from tv_buy_1_0.g2_lab.api.router_report_contrast import router as g2_report_router

# 报告生成（用于 /api/g2/contrast_report 串联）
from tv_buy_1_0.g2_lab.report.contrast_report import generate_contrast_report
from tv_buy_1_0.g2_lab.report.postprocess import split_output


# =========================================================
# Root Paths (IMPORTANT)
# =========================================================
# 当前文件：tv_buy_1_0/web/app.py
TVBUY_ROOT = Path(__file__).resolve().parents[1]  # => tv_buy_1_0/


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
    """
    兼容 Windows Git Bash curl 可能发来的 GBK/CP936 编码 JSON
    优先 utf-8，失败回退 gbk/cp936
    """
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
    """
    ✅ 修复：split_output 需要 str，但某些 client 可能返回 LlmResult / dict / object
    """
    if isinstance(x, str):
        return x

    # 常见：{"text": "..."} / {"content": "..."}
    if isinstance(x, dict):
        for k in ("text", "content", "output_text", "message"):
            v = x.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return str(x)

    # 常见对象：LlmResult(text=..., content=...)
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
    """
    上传图片：保存到 tv_buy_1_0/data_raw/uploads/
    返回 image_id + 保存路径
    """
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
    """
    两张对比度截图（原生/有效） -> OCR -> YAML
    返回 yaml 文本，并落盘到 tv_buy_1_0/summaries/contrast_records/
    """
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
    """
    ✅ 一键：两张对比度截图 -> OCR YAML -> LLM 评测结论
    """
    try:
        native_path = _find_uploaded_image(req.native_image_id)
        effective_path = _find_uploaded_image(req.effective_image_id)

        from tv_buy_1_0.g2_lab.services.contrast_ocr_service import contrast_yaml_from_images

        # 1) OCR -> YAML
        yaml_text = contrast_yaml_from_images(native_path, effective_path)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = _safe_prefix(req.device_id, "contrast")

        yaml_path = CONTRAST_OUT_DIR / f"{prefix}_{ts}.yaml"
        yaml_path.write_text(yaml_text, encoding="utf-8")

        # 2) YAML -> dict（取 contrast_test_record）
        obj = None
        try:
            obj = __import__("yaml").safe_load(yaml_text)
        except Exception:
            obj = None

        if isinstance(obj, dict) and "contrast_test_record" in obj and isinstance(obj["contrast_test_record"], dict):
            contrast_record = obj["contrast_test_record"]
        elif isinstance(obj, dict):
            contrast_record = obj
        else:
            raise ValueError("OCR 生成的 YAML 无法解析为 dict")

        # 3) LLM 报告（修复 LlmResult）
        print("🔥 [contrast_report] generating report ...")
        meta, raw_output = generate_contrast_report(contrast_record)
        raw_output_text = _ensure_text(raw_output)

        analysis_text, editorial_yaml = split_output(raw_output_text)

        # 4) 落盘（分析文字）
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
# 场景 & 品牌词表
# =========================================================
SCENE_MAP = [
    ("ps5", ["ps5", "xsx", "xbox", "游戏", "电竞", "pc", "主机"]),
    ("movie", ["movie", "film", "电影", "观影", "暗场", "杜比", "影院", "追剧"]),
    ("bright", ["bright", "客厅", "白天", "很亮", "采光", "窗", "反光", "日照"]),
]

BRAND_ALIASES = {
    "tcl": ["tcl", "t.c.l", "只看tcl", "只要tcl", "我要tcl", "仅tcl", "我只看tcl"],
    "mi": ["mi", "小米", "xiaomi", "只看小米", "只要小米"],
    "hisense": ["海信", "hisense"],
    "sony": ["索尼", "sony"],
    "samsung": ["三星", "samsung"],
    "lg": ["lg"],
}


# =========================================================
# slot 解析
# =========================================================
def parse_slots(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    t = raw.lower()

    if any(k in t for k in ["重置", "清空", "重新开始", "reset"]):
        return {"_reset": True}

    size = None

    # 1) 明确带单位：75寸 / 75英寸 / 75"
    m = re.search(r"(\d{2,3})\s*(寸|英寸|吋|inch|in|\")", t)
    if m:
        v = int(m.group(1))
        if 40 <= v <= 120:
            size = v
    else:
        # 2) 句子里出现“尺寸/英寸/多大”等语义时，允许抓一个裸数字
        if any(k in t for k in ["尺寸", "英寸", "多大", "多大屏", "大屏", "inch", "in"]):
            m3 = re.search(r"\b(\d{2,3})\b", t)
            if m3:
                v = int(m3.group(1))
                if 40 <= v <= 120:
                    size = v

        # 3) 兜底：从整句抓“第一个合理尺寸数字”（避免把预算 13000 当尺寸）
        if size is None:
            nums = re.findall(r"\b(\d{2,3})\b", t)
            for s in nums:
                v = int(s)
                if 40 <= v <= 120:
                    size = v
                    break

    budget = None
    mb = re.search(r"预算\s*(\d{3,6})", t)
    if mb:
        budget = int(mb.group(1))
    if budget is None:
        mb2 = re.search(r"(\d{3,6})\s*预算", t)
        if mb2:
            budget = int(mb2.group(1))
    if budget is None:
        mb3 = re.search(r"(\d{3,6})\s*(以内|以下|不超过|之内)", t)
        if mb3:
            budget = int(mb3.group(1))
    if budget is None:
        mb4 = re.search(r"(\d+(\.\d+)?)\s*万\s*(以内|以下|不超过|之内)", t)
        if mb4:
            budget = int(float(mb4.group(1)) * 10000)
    if budget is None:
        mb5 = re.search(r"(\d{1,3})\s*k\s*(以内|以下|不超过|之内)?", t)
        if mb5:
            budget = int(mb5.group(1)) * 1000

    scene = None
    for s, kws in SCENE_MAP:
        if any(k in t for k in kws):
            scene = s
            break

    brand = None
    for key, kws in BRAND_ALIASES.items():
        if any(k in t for k in kws):
            if key == "tcl":
                brand = "TCL"
            elif key == "mi":
                brand = "mi"
            else:
                brand = key
            break

    mbrand = re.search(r"(只看|只要|仅看|我只看|我要)\s*([a-zA-Z\u4e00-\u9fa5]{2,12})", raw)
    if mbrand and brand is None:
        b = mbrand.group(2).strip()
        brand = "TCL" if b.lower() == "tcl" else b

    # ✅ 额外：不限品牌（dialog 里会用到 brand_any=True）
    brand_any = False
    if any(k in t for k in ["不限品牌", "品牌不限", "不限定品牌", "不挑品牌", "随便什么牌子", "随便", "都行", "不限"]):
        brand_any = True
        brand = None

    return {"size": size, "scene": scene, "budget": budget, "brand": brand, "brand_any": brand_any}


# =========================================================
# TV Buy 1.0 原有 /api/chat 的追问逻辑（保留不动）
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


class ChatResp(BaseModel):
    state: Dict[str, Any]
    reply: str


@app.post("/api/chat", response_model=ChatResp)
def chat(req: ChatReq):
    base = req.state or {"size": None, "scene": None, "budget": None, "brand": None}

    slots = parse_slots(req.text)
    if slots.get("_reset"):
        base = {"size": None, "scene": None, "budget": None, "brand": None}
        return ChatResp(state=base, reply="✅ 已重置。你想买多大尺寸的电视？比如：65 / 75 / 85")

    for k in ["size", "scene", "budget", "brand"]:
        v = slots.get(k)
        if v is not None:
            base[k] = v

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

    reply_parts = []
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

        if total == 0:
            reply_parts.append("\n💡 建议：提高预算 / 换尺寸 / 先不限定品牌试试。")

    q = next_question(base)
    if q:
        reply = header + "\n\n".join(reply_parts) + ("\n\n" if reply_parts else "") + q
        return ChatResp(state=base, reply=reply)

    reply = header + "\n\n".join(reply_parts)
    return ChatResp(state=base, reply=reply)


# =========================================================
# ✅ Dialog 3p2（Clawdbot 调用）
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
            "state": {"size": None, "budget": None, "scene": None, "brand": None, "brand_any": False},
            "_ts": _now_ts(),
            "last_reply_full": None,
            "last_reply_short": None,
            "last_structured": None,
            "last_state": None,  # ✅ 缓存上一轮完整 state（用于“更多”复用）
        }
        _SESS[sid] = pack
    pack["_ts"] = _now_ts()
    return sid, pack


def _normalize_state(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(state, dict):
        return {"size": None, "budget": None, "scene": None, "brand": None, "brand_any": False}
    return {
        "size": state.get("size"),
        "budget": state.get("budget"),
        "scene": state.get("scene"),
        "brand": state.get("brand"),
        "brand_any": bool(state.get("brand_any", False)),
    }


def _merge_state(base: Dict[str, Any], slots: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k in ["size", "budget", "scene", "brand"]:
        if slots.get(k) is not None:
            out[k] = slots.get(k)

    if slots.get("brand_any"):
        out["brand_any"] = True
        out["brand"] = None
    return out


def _next_missing_slot_4q(state: Dict[str, Any]) -> Optional[str]:
    if state.get("size") is None:
        return "size"
    if state.get("budget") is None:
        return "budget"
    if state.get("scene") is None:
        return "scene"
    if (state.get("brand") is None) and (not state.get("brand_any", False)):
        return "brand"
    return None


QUESTION_TEXT_4Q = {
    "size": "你想要多大尺寸？比如：75（也可以回“75寸”）",
    "budget": "预算大概多少？比如：13000（或 13k / 1.3万）",
    "scene": "主要用途是什么？回一个就行：ps5 / movie / bright（白天客厅很亮）",
    "brand": "有指定品牌吗？比如：TCL；如果没有就回：不限",
}


def _run_3p2(state: Dict[str, Any]) -> str:
    return recommend_text(
        size=int(state["size"]),
        scene=str(state["scene"]),
        brand=state.get("brand"),
        budget=state.get("budget"),
    )


def _build_short_and_structured(reply_full: str):
    """
    从 recommend_text 的长文里，抽取手机友好的短文 + 结构化数据
    """
    text = reply_full or ""
    structured = {"top3": [], "one_liner": None}

    m = re.search(r"一句话结论：\s*\n?(.+)", text)
    if m:
        structured["one_liner"] = m.group(1).strip()

    for line in text.splitlines():
        s = line.strip()
        mm = re.match(r"^(1|2|3)\.\s+(.+)$", s)
        if not mm:
            continue
        if "|" not in s:
            continue

        rank = int(mm.group(1))
        first = mm.group(2).strip()

        price = None
        mp = re.search(r"￥\s*([0-9]{3,6})", first)
        if mp:
            price = int(mp.group(1))

        size = None
        ms = re.search(r"(\d{2,3})\s*寸", first)
        if ms:
            size = int(ms.group(1))

        model = first.split("|")[0].strip()
        model = re.sub(r"\s*\d{2,3}\s*寸\s*$", "", model).strip()

        structured["top3"].append({"rank": rank, "model": model, "size": size, "price": price})

    structured["top3"] = sorted(structured["top3"], key=lambda x: x.get("rank", 99))

    lines_out = []
    if structured["one_liner"]:
        lines_out.append(f"一句话：{structured['one_liner']}")
    if structured["top3"]:
        lines_out.append("Top3：")
        for i in structured["top3"]:
            p = f"￥{i['price']}" if i.get("price") else "￥?"
            ss = f"{i['size']}寸" if i.get("size") else ""
            lines_out.append(f"{i['rank']}. {i['model']} {ss} {p}".strip())
        lines_out.append("（回复：更多 查看详细分析）")

    reply_short = "\n".join(lines_out).strip()
    return reply_short, structured


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
    return {"ok": True, "ts": _now_ts()}


@app.post("/api/dialog/parse")
async def api_dialog_parse(request: Request):
    try:
        data = await _read_json_body(request)
        req = DialogReq(**data)
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": f"parse body failed: {e}"})

    slots = parse_slots(req.text or "")
    base = _normalize_state(req.state)
    merged = _merge_state(base, slots)
    return JSONResponse(content={"ok": True, "slots": slots, "state": merged})


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
            state={"size": None, "budget": None, "scene": None, "brand": None, "brand_any": False},
            done=False,
        )

    sid, pack = _get_session(req.session_id)

    if req.state is not None:
        pack["state"] = _normalize_state(req.state)

    state = _normalize_state(pack.get("state"))
    text = (req.text or "").strip()

    t_norm = re.sub(r"\s+", "", text)
    t_norm = re.sub(r"[!！。.,，?？]+$", "", t_norm)
    if t_norm.lower() in ["更多", "展开", "详细", "详情", "全文", "more", "detail"]:
        last_full = pack.get("last_reply_full")
        last_short = pack.get("last_reply_short")
        last_struct = pack.get("last_structured")
        last_state = _normalize_state(pack.get("last_state") or pack.get("state"))

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
            reply="我还没有上一条结果可展开。你可以先发一句：例如“75 13k ps5 只要tcl”。",
            state=last_state,
            done=False,
        )

    slots = parse_slots(text)

    if slots.get("_reset"):
        pack["state"] = {"size": None, "budget": None, "scene": None, "brand": None, "brand_any": False}
        pack["last_reply_full"] = None
        pack["last_reply_short"] = None
        pack["last_structured"] = None
        pack["last_state"] = None
        return DialogResp(
            ok=True,
            session_id=sid,
            reply="✅ 已重置。你想要多大尺寸？比如：75（也可以回“75寸”）",
            state=pack["state"],
            done=False,
        )

    state = _merge_state(state, slots)
    pack["state"] = state

    missing = _next_missing_slot_4q(state)
    if missing:
        return DialogResp(
            ok=True,
            session_id=sid,
            reply=QUESTION_TEXT_4Q[missing],
            state=state,
            done=False,
        )

    try:
        t0 = time.perf_counter()
        reply_full = _run_3p2(state)
        cost = time.perf_counter() - t0
        print(f"[3p2] TOTAL(_run_3p2) cost={cost:.3f}s")

        reply_short, structured = _build_short_and_structured(reply_full)
        if not reply_short:
            reply_short = "已生成推荐（回复：更多 查看详细分析）"
    except Exception as e:
        return DialogResp(
            ok=False,
            session_id=sid,
            reply=f"❌ 生成推荐失败：{e}",
            state=state,
            done=False,
        )

    pack["last_reply_full"] = reply_full
    pack["last_reply_short"] = reply_short
    pack["last_structured"] = structured
    pack["last_state"] = dict(state)

    pack["state"] = {"size": None, "budget": None, "scene": None, "brand": None, "brand_any": False}

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


@app.post("/api/dialog/3p2/reset")
def api_dialog_3p2_reset(req: DialogReq):
    sid = req.session_id or ""
    if not sid:
        return JSONResponse(status_code=400, content={"ok": False, "error": "session_id required"})
    _SESS.pop(sid, None)
    return JSONResponse(content={"ok": True, "session_id": sid})


# =========================================================
# ✅ /webhook 适配层：给 Clawdbot / gateway 用
# =========================================================
@app.post("/webhook")
async def webhook(request: Request):
    """
    兼容 Clawdbot/Gateway 常见入参：
      { "user_id":"u1", "session_id":"t1", "text":"..." }

    返回统一格式：
      { "ok": true, "reply": "...", "raw": { ... } }

    逻辑：
    - 直接复用 /api/dialog/3p2 的行为（含“更多”缓存）
    - done=true: reply=短文（或更多时长文）
    - done=false: reply=追问句
    """
    try:
        data = await _read_json_body(request)
    except Exception as e:
        return _json_err(f"bad json: {e}", status_code=400)

    user_id = str(data.get("user_id") or "")
    session_id = str(data.get("session_id") or "")
    text = str(data.get("text") or "").strip()

    if not session_id:
        # 若 gateway 不给 session_id，就给一个；但建议 gateway 传
        session_id = uuid4().hex

    if not text:
        return _json_err("missing text", status_code=400)

    # 组装成 DialogReq，复用同一套 session/cache
    req = DialogReq(text=text, session_id=session_id, state=data.get("state"))

    # 直接调用内部逻辑（等价于 /api/dialog/3p2）
    # 为了不重复解析 body，这里复制 /api/dialog/3p2 的核心流程（轻量）
    sid, pack = _get_session(req.session_id)

    if req.state is not None:
        pack["state"] = _normalize_state(req.state)

    state = _normalize_state(pack.get("state"))
    text2 = (req.text or "").strip()

    t_norm = re.sub(r"\s+", "", text2)
    t_norm = re.sub(r"[!！。.,，?？]+$", "", t_norm)
    if t_norm.lower() in ["更多", "展开", "详细", "详情", "全文", "more", "detail"]:
        last_full = pack.get("last_reply_full")
        if last_full:
            raw = {
                "ok": True,
                "session_id": sid,
                "reply": last_full,
                "state": _normalize_state(pack.get("last_state") or pack.get("state")),
                "done": True,
                "reply_short": pack.get("last_reply_short"),
                "reply_full": last_full,
                "structured": pack.get("last_structured"),
            }
            return _json_ok(last_full, raw=raw)
        return _json_ok("我还没有上一条结果可展开。你可以先发一句：例如“75 13k ps5 只要tcl”。", raw={"ok": True, "session_id": sid})

    slots = parse_slots(text2)

    if slots.get("_reset"):
        pack["state"] = {"size": None, "budget": None, "scene": None, "brand": None, "brand_any": False}
        pack["last_reply_full"] = None
        pack["last_reply_short"] = None
        pack["last_structured"] = None
        pack["last_state"] = None
        raw = {"ok": True, "session_id": sid, "done": False, "state": pack["state"]}
        return _json_ok("✅ 已重置。你想要多大尺寸？比如：75（也可以回“75寸”）", raw=raw)

    state = _merge_state(state, slots)
    pack["state"] = state

    missing = _next_missing_slot_4q(state)
    if missing:
        q = QUESTION_TEXT_4Q[missing]
        raw = {"ok": True, "session_id": sid, "done": False, "state": state}
        return _json_ok(q, raw=raw)

    try:
        reply_full = _run_3p2(state)
        reply_short, structured = _build_short_and_structured(reply_full)
        if not reply_short:
            reply_short = "已生成推荐（回复：更多 查看详细分析）"
    except Exception as e:
        return _json_err(f"generate failed: {e}", status_code=500)

    pack["last_reply_full"] = reply_full
    pack["last_reply_short"] = reply_short
    pack["last_structured"] = structured
    pack["last_state"] = dict(state)
    pack["state"] = {"size": None, "budget": None, "scene": None, "brand": None, "brand_any": False}

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


# =========================================================
# Web page
# =========================================================
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
