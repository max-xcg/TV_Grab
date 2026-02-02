# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageEnhance
import pytesseract


def _autodetect_tesseract() -> str:
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    raise RuntimeError(
        "未找到系统 tesseract.exe。\n"
        "请确认已安装 Tesseract-OCR，并检查路径：\n"
        "  C:\\Program Files\\Tesseract-OCR\\tesseract.exe\n"
        "  C:\\Program Files (x86)\\Tesseract-OCR\\tesseract.exe"
    )


def ocr_image(
    image_path: str,
    lang: str = "eng",
    numeric_only: bool = False,
    crop_box: Optional[Tuple[int, int, int, int]] = None,  # (left, top, right, bottom)
) -> str:
    """
    OCR 图片 -> 文本
    - numeric_only=True：只识别数字和小数点，减少噪声
    - crop_box：只对指定区域 OCR（解决“整页噪声数字”问题）
    """
    pytesseract.pytesseract.tesseract_cmd = _autodetect_tesseract()

    img = Image.open(image_path).convert("L")

    if crop_box:
        img = img.crop(crop_box)

    # 轻量增强：对比度 + 锐化
    img = ImageEnhance.Contrast(img).enhance(1.8)
    img = ImageEnhance.Sharpness(img).enhance(1.6)

    # 放大（提高小字体识别）
    w, h = img.size
    img = img.resize((int(w * 2.0), int(h * 2.0)))

    if numeric_only:
        config = "--psm 6 -c tessedit_char_whitelist=0123456789."
        text = pytesseract.image_to_string(img, lang="eng", config=config)
    else:
        text = pytesseract.image_to_string(img, lang=lang)

    return (text or "").strip()
