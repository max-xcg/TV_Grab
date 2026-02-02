# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from PIL import Image

@dataclass
class OcrResult:
    text: str


class OcrEngine:
    def image_to_text(self, image: Image.Image) -> OcrResult:
        raise NotImplementedError


class TesseractOcrEngine(OcrEngine):
    """
    依赖：
      pip install pytesseract pillow
    系统层还需安装 tesseract 可执行文件，并确保在 PATH
    """
    def __init__(self, lang: str = "chi_sim+eng"):
        self.lang = lang
        try:
            import pytesseract  # noqa
        except Exception as e:
            raise RuntimeError(
                "未检测到 pytesseract。请执行：pip install pytesseract pillow\n"
                "并安装 tesseract OCR（Windows 可用 tesseract-ocr 安装包）。"
            ) from e

    def image_to_text(self, image: Image.Image) -> OcrResult:
        import pytesseract
        txt = pytesseract.image_to_string(image, lang=self.lang)
        return OcrResult(text=txt)
