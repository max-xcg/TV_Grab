# -*- coding: utf-8 -*-
from __future__ import annotations

import yaml
from pathlib import Path
from typing import Dict, Any

# 你可以替换成你真实使用的 client
# from openai import OpenAI
# client = OpenAI()

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "contrast_analysis.yaml"


def load_prompt() -> str:
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["system_prompt"]


def generate_contrast_report(
    contrast_test_record: Dict[str, Any],
    model: str = "gpt-4o-mini",  # 或你本地模型名
) -> str:
    """
    输入：
      - contrast_test_record（已完成计算的 dict）
    输出：
      - LLM 生成的完整结论文本（包含阶段一 + editorial_verdict YAML）
    """

    system_prompt = load_prompt()

    user_payload = yaml.safe_dump(
        {"contrast_test_record": contrast_test_record},
        allow_unicode=True,
        sort_keys=False,
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "以下是测试工程师提供的 contrast_test_record 数据：\n\n"
                f"{user_payload}"
            ),
        },
    ]

    # ===== 调用 API（示例，按你真实 client 改） =====
    """
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2,  # 工程分析必须低温
    )
    return resp.choices[0].message.content
    """

    # 占位（防止你现在还没接 API 报错）
    raise NotImplementedError("请接入你当前使用的 LLM API 客户端")
