# -*- coding: utf-8 -*-
"""
llm/deepseek_vision.py

兼容层：项目其它地方仍然 import llm.deepseek_vision.chat_with_images
但底层实际调用 豆包/火山方舟(Ark) 的视觉 Endpoint。

依赖环境变量：
  ARK_BASE_URL   默认 https://ark.cn-beijing.volces.com/api/v3
  ARK_API_KEY    你的方舟 API Key
  ARK_VISION_MODEL  你的 EndpointID（例如 ep-xxxx）
"""

from __future__ import annotations

from llm.doubao_vision import chat_with_images  # 直接复用你已跑通的实现

__all__ = ["chat_with_images"]
