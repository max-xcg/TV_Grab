# -*- coding: utf-8 -*-
"""
tools/ocr_extract_text.py

用“豆包/火山方舟(Ark) Vision Endpoint”代替本地 pytesseract。
对外保持同名函数：ocr_image(image_path, lang=..., numeric_only=..., crop_box=...)

依赖：
  pip install openai pillow
环境变量：
  ARK_API_KEY
  ARK_BASE_URL (默认 https://ark.cn-beijing.volces.com/api/v3)
  ARK_VISION_MODEL (你的视觉 EndpointID，例如 ep-xxxx)
"""

from __future__ import annotations

import os
import base64
import tempfile
from typing import Optional, Tuple

from PIL import Image
from openai import OpenAI


ARK_BASE_URL = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
ARK_API_KEY = os.getenv("ARK_API_KEY") or os.getenv("OPENAI_API_KEY")
ARK_VISION_MODEL = os.getenv("ARK_VISION_MODEL")

if not ARK_API_KEY:
    raise RuntimeError("缺少 ARK_API_KEY（或 OPENAI_API_KEY），请先 export 再运行。")
if not ARK_VISION_MODEL:
    raise RuntimeError("缺少 ARK_VISION_MODEL（你的视觉 EndpointID），请先 export 再运行。")

_CLIENT = OpenAI(api_key=ARK_API_KEY, base_url=ARK_BASE_URL)


def _img_to_data_url(image_path: str) -> str:
    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/png"
    if ext in [".jpg", ".jpeg"]:
        mime = "image/jpeg"
    elif ext == ".webp":
        mime = "image/webp"

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _maybe_crop(image_path: str, crop_box: Optional[Tuple[int, int, int, int]]) -> str:
    """有 crop_box 就先裁剪出 ROI 再发给模型，提高稳定性。返回实际发送的文件路径。"""
    if crop_box is None:
        return image_path

    img = Image.open(image_path).convert("RGB")
    img = img.crop(crop_box)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    tmp.close()
    img.save(tmp.name)
    return tmp.name


def ocr_image(
    image_path: str,
    *,
    lang: str = "eng",
    numeric_only: bool = False,
    crop_box: Optional[Tuple[int, int, int, int]] = None,
) -> str:
    """
    云端 OCR（豆包 Vision）
    - numeric_only=True：只输出数字（空格分隔），用于表格数值
    - numeric_only=False：输出图片中文字说明（中文/英文）
    - crop_box：先裁剪再识别
    """
    send_path = _maybe_crop(image_path, crop_box)

    system_prompt = "你是一个严谨的图像文字识别助手，只按要求输出结果，不要解释。"

    if numeric_only:
        user_text = (
            "请读取图片中的表格数据，只输出所有数字（包含小数），"
            "按从上到下、从左到右顺序排列，用空格分隔。"
            "不要输出任何中文、单位、符号、标题或多余文字。"
        )
    else:
        # 用于抓取“说明/备注”这类文字
        user_text = (
            "请识别图片中的文字说明（尤其是表格下方的说明/备注区域）。"
            "直接输出识别到的文字内容（中文/英文均可），不要解释，不要附加其它内容。"
        )

    try:
        resp = _CLIENT.chat.completions.create(
            model=ARK_VISION_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": _img_to_data_url(send_path)}},
                    ],
                },
            ],
            temperature=0,
        )
        return (resp.choices[0].message.content or "").strip()
    finally:
        # 清理裁剪产生的临时文件
        if crop_box is not None and send_path != image_path:
            try:
                os.remove(send_path)
            except Exception:
                pass
