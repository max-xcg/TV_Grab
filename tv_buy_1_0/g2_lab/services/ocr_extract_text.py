# -*- coding: utf-8 -*-
"""
g2_lab/services/ocr_extract_text.py

提供一个通用 OCR 函数 ocr_image，供 contrast_ocr_service.py 调用。
依赖：
  pip install pytesseract pillow
并且系统需要安装 Tesseract OCR（Windows）
"""

from __future__ import annotations

from typing import Optional, Tuple
from PIL import Image
import pytesseract


def ocr_image(
    image_path: str,
    *,
    lang: str = "eng",
    numeric_only: bool = False,
    crop_box: Optional[Tuple[int, int, int, int]] = None,
) -> str:
    """
    对图片做 OCR，返回识别文本（字符串）。

    - numeric_only=True：用字符白名单强化数字识别（适合表格数值）
    - crop_box=(l,t,r,b)：先裁剪再识别，提升准确率
    """
    img = Image.open(image_path).convert("RGB")
    if crop_box is not None:
        img = img.crop(crop_box)

    config = ""

    if numeric_only:
        # 只允许数字 + 小数点（你表格就是这种）
        config = "--psm 6 -c tessedit_char_whitelist=0123456789."
    else:
        config = "--psm 6"

    text = pytesseract.image_to_string(img, lang=lang, config=config)
    return text
