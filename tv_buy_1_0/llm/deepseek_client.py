# -*- coding: utf-8 -*-
"""
llm/deepseek_client.py

兼容层：项目其它地方仍然 import llm.deepseek_client.chat
但底层实际调用 豆包/火山方舟(Ark) 文本模型 / Endpoint。

环境变量：
  ARK_BASE_URL      默认 https://ark.cn-beijing.volces.com/api/v3
  ARK_API_KEY       方舟 API Key
  ARK_TEXT_MODEL    文本模型或文本 EndpointID（如果你没有单独文本 endpoint，也可以先复用 ARK_VISION_MODEL）
"""

from __future__ import annotations

import os
from openai import OpenAI

ARK_BASE_URL = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
ARK_API_KEY = os.getenv("ARK_API_KEY") or os.getenv("OPENAI_API_KEY")

# 文本模型（建议你在控制台创建一个文本 endpoint；如果暂时没有，就先用 ARK_VISION_MODEL 顶一下）
ARK_TEXT_MODEL = os.getenv("ARK_TEXT_MODEL") or os.getenv("ARK_VISION_MODEL")

if not ARK_API_KEY:
    raise RuntimeError("缺少 ARK_API_KEY（或 OPENAI_API_KEY），请先 export 再运行。")
if not ARK_TEXT_MODEL:
    raise RuntimeError("缺少 ARK_TEXT_MODEL（或 ARK_VISION_MODEL），请先 export 再运行。")

_CLIENT = OpenAI(api_key=ARK_API_KEY, base_url=ARK_BASE_URL)


def chat(system_prompt: str, user_prompt: str) -> str:
    resp = _CLIENT.chat.completions.create(
        model=ARK_TEXT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()
