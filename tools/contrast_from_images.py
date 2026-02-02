# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path

from tv_buy_1_0.g2_prompts.contrast_prompt import SYSTEM_PROMPT, USER_PROMPT
from tv_buy_1_0.llm.deepseek_client import chat
from tools.ocr_extract_text import ocr_image


def _build_user_prompt(native_ocr: str, effective_ocr: str) -> str:
    return f"""
以下是两张测试结果截图经 OCR 提取的原始文本（可能包含噪声），请你严格按 G2 Lab 规则处理并输出 YAML。

【图片1：原生对比度（Local Dimming OFF） OCR文本】
{native_ocr}

【图片2：有效对比度（Local Dimming ON / High） OCR文本】
{effective_ocr}

{USER_PROMPT}
""".strip()


def main():
    ap = argparse.ArgumentParser(description="G2 Contrast: images -> contrast_test_record.yaml (OCR -> LLM)")
    ap.add_argument("--native", required=True, help="图片1：原生对比度（LD OFF）")
    ap.add_argument("--effective", required=True, help="图片2：有效对比度（LD ON/HIGH）")
    ap.add_argument("--out", default="", help="输出 yaml 路径（不填则只打印）")
    ap.add_argument("--lang", default="eng", help="tesseract OCR 语言：默认 eng")
    args = ap.parse_args()

    native_path = Path(args.native)
    effective_path = Path(args.effective)
    if not native_path.exists():
        raise FileNotFoundError(f"图片不存在：{native_path}")
    if not effective_path.exists():
        raise FileNotFoundError(f"图片不存在：{effective_path}")

    # 1) OCR
    native_ocr = ocr_image(str(native_path), lang=args.lang)
    effective_ocr = ocr_image(str(effective_path), lang=args.lang)

    # 2) 拼 prompt -> DeepSeek（纯文本）
    user_prompt = _build_user_prompt(native_ocr, effective_ocr)
    yaml_text = chat(system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt)

    # 3) 输出
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(yaml_text, encoding="utf-8")
        print(f"[OK] saved -> {out_path}")
    else:
        print(yaml_text)


if __name__ == "__main__":
    main()
