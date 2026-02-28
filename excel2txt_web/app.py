# -*- coding: utf-8 -*-
"""
app.py（完整可一键复制粘贴）

功能：
- Web 上传 Excel（xlsx/xlsm）
- 点击生成 TXT
- 自动把 TXT 落盘到：D:\\Gen2\\{品牌}txt文件\\
- 目录不存在自动创建
- 支持内网访问：uvicorn --host 0.0.0.0

启动：
  /c/software/Anaconda3/python.exe -m pip install -r requirements.txt
  /c/software/Anaconda3/python.exe app.py
或：
  /c/software/Anaconda3/python.exe -m uvicorn app:app --host 0.0.0.0 --port 8010
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates

from excel_to_txt import excel_to_txt, detect_brand_from_filename

APP_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_ROOT / "templates"))

UPLOAD_DIR = APP_ROOT / "_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 你要求的固定落盘位置（Windows D盘）
BASE_OUT_DIR = Path(r"D:\Gen2")

MAX_UPLOAD_MB = 40
ALLOWED_EXT = {".xlsx", ".xlsm"}

app = FastAPI(title="Excel → TXT (Intranet)", version="1.0.0")


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_name(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5\-\._]+", "_", s)
    return s[:120] if s else "file"


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "ok": False})


@app.post("/api/gen_txt", response_class=HTMLResponse)
async def gen_txt(
    request: Request,
    file: UploadFile = File(...),
    sheet: Optional[str] = Form(None),
    brand: Optional[str] = Form(None),
):
    try:
        if not file.filename:
            raise ValueError("未选择文件")

        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            raise ValueError(f"不支持的文件类型：{ext}（仅支持 {sorted(ALLOWED_EXT)}）")

        # 落盘上传文件
        in_base = _safe_name(Path(file.filename).stem)
        upload_path = UPLOAD_DIR / f"{_now_tag()}__{in_base}{ext}"

        content = await file.read()
        if not content:
            raise ValueError("空文件")
        size_mb = len(content) / (1024 * 1024)
        if size_mb > MAX_UPLOAD_MB:
            raise ValueError(f"文件过大：{size_mb:.1f}MB（上限 {MAX_UPLOAD_MB}MB）")

        upload_path.write_bytes(content)

        # 品牌确定：优先用表单 brand；否则从文件名识别
        brand2 = (brand or "").strip()
        if not brand2:
            brand2 = detect_brand_from_filename(file.filename)

        # 生成 TXT 内容
        sheet2 = (sheet or "").strip() or None
        brand_final, txt = excel_to_txt(str(upload_path), filename_for_brand=file.filename, sheet=sheet2)
        # 如果用户手填 brand，以手填为准
        if brand2 and brand2 != "未知品牌":
            brand_final = brand2

        # 输出目录：D:\Gen2\{品牌}txt文件
        out_dir = BASE_OUT_DIR / f"{_safe_name(brand_final)}txt文件"
        out_dir.mkdir(parents=True, exist_ok=True)

        out_name = f"{in_base}__{_now_tag()}.txt"
        out_path = out_dir / out_name
        out_path.write_text(txt, encoding="utf-8")

        # 预览
        preview = "\n".join(txt.splitlines()[:250])

        # 为了下载：临时也提供一个“文件直读下载”，用绝对路径参数不安全
        # 所以这里把生成的文件复制一份到本服务的 _downloads（仅用于下载）
        dl_dir = APP_ROOT / "_downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)
        dl_path = dl_dir / f"{_safe_name(brand_final)}__{out_name}"
        dl_path.write_text(txt, encoding="utf-8")

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "ok": True,
                "out_name": out_name,
                "out_path": str(out_path),
                "download_url": f"/download/{dl_path.name}",
                "preview": preview,
            },
        )

    except Exception as e:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "ok": False, "error": str(e)},
        )


@app.get("/download/{filename}")
def download(filename: str):
    dl_dir = APP_ROOT / "_downloads"
    path = dl_dir / _safe_name(filename)
    if not path.exists():
        return HTMLResponse("文件不存在或已被清理", status_code=404)

    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type="text/plain; charset=utf-8",
    )


if __name__ == "__main__":
    import uvicorn

    # 内网访问关键：host=0.0.0.0
    uvicorn.run("app:app", host="0.0.0.0", port=8010, reload=False)