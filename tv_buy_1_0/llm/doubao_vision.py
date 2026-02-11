# -*- coding: utf-8 -*-
"""
tv_buy_1_0/llm/doubao_vision.py

电视选购最终结论生成（仅输出结论正文，不输出列表/编号/Top3 等）
"""
from __future__ import annotations

import os
import json
from typing import Dict, Any, Optional, Tuple

from openai import OpenAI


# =========================================================
# 基础配置
# =========================================================
def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    return v if v else default


_CLIENT: Optional[OpenAI] = None


def _get_client_and_model() -> Tuple[OpenAI, str]:
    """
    兼容 Volcengine Ark OpenAI-compat endpoint：
      ARK_BASE_URL: https://ark.cn-beijing.volces.com/api/v3
      ARK_API_KEY:  ...
      ARK_VISION_MODEL: 你的模型名（必配，或用 OPENAI_MODEL fallback）
    """
    global _CLIENT

    base_url = _get_env("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    api_key = _get_env("ARK_API_KEY") or _get_env("OPENAI_API_KEY")

    # ✅ 给一个 fallback，避免你忘记配 ARK_VISION_MODEL 就直接崩
    model = _get_env("ARK_VISION_MODEL") or _get_env("OPENAI_MODEL")

    if not api_key:
        raise RuntimeError("缺少 ARK_API_KEY / OPENAI_API_KEY")
    if not model:
        raise RuntimeError("缺少 ARK_VISION_MODEL（或 OPENAI_MODEL 作为 fallback）")

    if _CLIENT is None:
        _CLIENT = OpenAI(api_key=api_key, base_url=base_url)

    return _CLIENT, model


def chat_text(system_prompt: str, user_text: str) -> str:
    client, model = _get_client_and_model()

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        temperature=0.3,
        # 有些兼容端支持 timeout，有些不支持；不强行传，避免不兼容
    )
    return (resp.choices[0].message.content or "").strip()


# =========================================================
# 最终结论生成（只输出结论，不含任何列表）
# =========================================================
def generate_reco_report(payload: Dict[str, Any]) -> str:
    """
    输入：
      {
        "query": {...},
        "items": [已排序候选，前三最重要]
      }
    输出：
      一段完整、自然的中文选购结论（无策略、无列表）
    """
    query = payload.get("query") or {}
    items = payload.get("items") or []
    if not items:
        return ""

    core_items = items[:3]

    system_prompt = (
        "你是一名非常克制、非常专业的电视选购顾问。\n\n"
        "【你正在做什么】\n"
        "你已经拿到了系统筛选和排序后的前三台核心候选电视。\n"
        "你的任务不是复述结果，而是帮用户真正理解“该怎么选”。\n\n"
        "【绝对禁止事项】\n"
        "1. 不要输出任何列表、编号、Top3、候选池、策略说明。\n"
        "2. 不要输出价格表、型号清单、YAML 路径。\n"
        "3. 不要提及“排序逻辑”“算法”“系统判断”。\n\n"
        "【允许且必须做的事】\n"
        "1. 用自然语言，对这三台电视做取舍式对比分析。\n"
        "2. 说明每一台被选择时，用户能获得的实际收益（而不是参数堆砌）。\n"
        "3. 说明每一台隐含的代价或不确定点（如发布时间、配置需确认）。\n"
        "4. 最后站在理性角度，给出一个“最稳妥、性价比最高”的最终建议。\n\n"
        "【证据规则】\n"
        "- 只能基于 payload 中出现的字段写结论。\n"
        "- 如关键能力未明确，请写“需要购买前确认”，禁止猜测。\n\n"
        "【语气】\n"
        "- 像一个认真帮你省钱、避免踩坑的朋友。\n"
        "- 不营销，不夸张，不下判断结论之外的承诺。\n"
    )

    user_text = json.dumps(
        {"query": query, "items": core_items},
        ensure_ascii=False,
    )

    return chat_text(system_prompt, user_text)


if __name__ == "__main__":
    print("This module is designed to be called by the agent/app layer.")
