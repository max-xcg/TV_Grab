# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from uuid import uuid4
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from tv_buy_1_0.run_reco import recommend_text, list_candidates, format_candidates

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
    返回：
      - yaml: OCR生成的yaml文本
      - analysis: 工程分析文字（阶段一）
      - editorial_verdict_yaml: 结构化观点（阶段二）
    并落盘：
      - summaries/contrast_records/*.yaml
      - summaries/contrast_analysis/*.txt
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
    m = re.search(r"(\d{2,3})\s*(寸|英寸)", t)
    if m:
        size = int(m.group(1))
    else:
        m2 = re.fullmatch(r"\s*(\d{2,3})\s*", t)
        if m2:
            size = int(m2.group(1))

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

    return {"size": size, "scene": scene, "budget": budget, "brand": brand}


def next_question(state: Dict[str, Any]) -> Optional[str]:
    if state.get("size") is None:
        return "你想要多大尺寸？比如：65 / 75 / 85（直接回“75寸”也行）"
    if state.get("scene") is None:
        return "主要用途是什么？回一个就行：ps5 / movie / bright（白天客厅很亮）"
    return None


# =========================================================
# Chat API (TV Buy 1.0)
# =========================================================
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
            # ✅ 这里 recommend_text 内部才决定是否调用 LLM（你要去 run_reco.py 打开 ENABLE_LLM）
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
# Web page
# =========================================================
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
