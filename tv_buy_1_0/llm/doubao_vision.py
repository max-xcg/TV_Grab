# -*- coding: utf-8 -*-
"""
llm/doubao_vision.py

豆包 / 火山方舟(Ark) 多模态（视觉）调用封装：
1) chat_with_images(system_prompt, user_text, image_paths)  # 通用：文本 + 多图
2) contrast_yaml_from_two_images(native_path, effective_path, prompt_path=...)  # 专用：两张对比度图 -> YAML

依赖：
  pip install openai

环境变量：
  ARK_API_KEY        方舟 API Key（推荐）
  ARK_BASE_URL       默认 https://ark.cn-beijing.volces.com/api/v3
  ARK_VISION_MODEL   视觉 EndpointID（例如 ep-xxxx）
"""

from __future__ import annotations

import os
import base64
from pathlib import Path
from typing import List, Optional

from openai import OpenAI


# =========================
# 环境变量
# =========================
ARK_BASE_URL = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
ARK_API_KEY = os.getenv("ARK_API_KEY") or os.getenv("OPENAI_API_KEY")
ARK_VISION_MODEL = os.getenv("ARK_VISION_MODEL")

if not ARK_API_KEY:
    raise RuntimeError(
        "缺少 ARK_API_KEY（或 OPENAI_API_KEY）。请先在 Git Bash 中 export ARK_API_KEY=..."
    )
if not ARK_VISION_MODEL:
    raise RuntimeError(
        "缺少 ARK_VISION_MODEL（你的视觉 EndpointID，例如 ep-xxxx）。请先 export ARK_VISION_MODEL=..."
    )

_CLIENT = OpenAI(api_key=ARK_API_KEY, base_url=ARK_BASE_URL)


# =========================
# 工具函数
# =========================
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


def _read_text_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到文件：{p.as_posix()}")
    return p.read_text(encoding="utf-8").strip()


# =========================
# 通用：文本 + 多图
# =========================
def chat_with_images(system_prompt: str, user_text: str, image_paths: List[str]) -> str:
    """
    通用多模态对话：system_prompt + user_text + 多张图片
    返回模型输出文本（不做解析）
    """
    contents = [{"type": "text", "text": user_text}]
    for p in image_paths:
        contents.append({"type": "image_url", "image_url": {"url": _img_to_data_url(p)}})

    resp = _CLIENT.chat.completions.create(
        model=ARK_VISION_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": contents},
        ],
        temperature=0,
    )
    return (resp.choices[0].message.content or "").strip()


# =========================
# 专用：两张对比度图 -> YAML
# =========================
def contrast_yaml_from_two_images(
    native_path: str,
    effective_path: str,
    *,
    prompt_path: str = "g2_lab/prompts/contrast_extract_system.yaml",
    user_instruction: Optional[str] = None,
) -> str:
    """
    发送两张对比度测试图片，使用固定的系统 Prompt（G2 Lab 数据录入工程师指令集）
    让模型直接输出符合 G2 标准的 contrast_test_record YAML。

    native_path:    原生对比度测试图（Local Dimming OFF）
    effective_path: 有效对比度测试图（Local Dimming ON / High）
    prompt_path:    存放你那段“核心指令集”的文件路径
    user_instruction: 额外用户指令（可选）
    """
    system_prompt = _read_text_file(prompt_path)

    if not user_instruction:
        user_instruction = (
            "你将收到两张测试结果图片："
            "图片1为原生对比度（Local Dimming OFF），图片2为有效对比度（Local Dimming ON / High）。"
            "请严格按照系统指令集，将两张图转换为完整的 contrast_test_record YAML。"
            "只输出 YAML，不要输出任何解释文字。"
        )

    # 两张图同时发给模型
    return chat_with_images(
        system_prompt=system_prompt,
        user_text=user_instruction,
        image_paths=[native_path, effective_path],
    )


# =========================
# 命令行快速测试（可选）
# =========================
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("用法: python -m llm.doubao_vision native.png effective.png")
        sys.exit(1)

    n = sys.argv[1]
    e = sys.argv[2]
    out = contrast_yaml_from_two_images(n, e)
    print(out)
