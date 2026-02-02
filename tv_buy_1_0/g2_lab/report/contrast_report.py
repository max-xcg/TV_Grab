# -*- coding: utf-8 -*-
from __future__ import annotations

print("🔥 USING contrast_report FROM:", __file__)

from pathlib import Path
from typing import Any, Dict, Tuple, List

import yaml

from .llm_client import OpenAICompatClient

# 当前文件：tv_buy_1_0/g2_lab/report/contrast_report.py
# parents[1] => tv_buy_1_0/g2_lab
PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "contrast_analysis.yaml"


def load_prompt_cfg() -> Dict[str, Any]:
    if not PROMPT_FILE.exists():
        raise FileNotFoundError(f"找不到提示词文件: {PROMPT_FILE}")

    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    if not isinstance(cfg, dict):
        raise ValueError(f"提示词文件格式不正确（应为 YAML dict）: {PROMPT_FILE}")

    return cfg


def _get_system_prompt(cfg: Dict[str, Any]) -> str:
    sp = cfg.get("system_prompt")
    if isinstance(sp, str) and sp.strip():
        return sp.strip()

    sp2 = cfg.get("prompt")
    if isinstance(sp2, str) and sp2.strip():
        return sp2.strip()

    raise KeyError("contrast_analysis.yaml 缺少 system_prompt 或 prompt 字段")


def _as_text(x: Any) -> str:
    """
    兼容 OpenAICompatClient.chat() 可能返回：
    - str
    - LlmResult(content/text/response/message...)
    - dict
    """
    if x is None:
        return ""

    if isinstance(x, str):
        return x

    # 常见字段：content / text
    for attr in ["content", "text", "output", "result"]:
        if hasattr(x, attr):
            v = getattr(x, attr)
            if isinstance(v, str):
                return v

    # OpenAI SDK 兼容：choices[0].message.content
    try:
        if hasattr(x, "choices"):
            ch0 = x.choices[0]
            msg = getattr(ch0, "message", None)
            if msg is not None:
                c = getattr(msg, "content", None)
                if isinstance(c, str):
                    return c
    except Exception:
        pass

    # dict 兜底
    if isinstance(x, dict):
        for k in ["content", "text", "output", "result"]:
            v = x.get(k)
            if isinstance(v, str):
                return v
        return yaml.safe_dump(x, allow_unicode=True, sort_keys=False)

    # 最后兜底
    return str(x)


def build_messages(system_prompt: str, contrast_record: Dict[str, Any]) -> List[Dict[str, str]]:
    user_yaml = yaml.safe_dump(
        {"contrast_test_record": contrast_record},
        allow_unicode=True,
        sort_keys=False,
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"以下是测试工程师提供的 contrast_test_record 数据：\n\n{user_yaml}"},
    ]


def generate_contrast_report(contrast_record: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    cfg = load_prompt_cfg()
    system_prompt = _get_system_prompt(cfg)

    client = OpenAICompatClient()
    messages = build_messages(system_prompt, contrast_record)

    raw = client.chat(messages=messages, temperature=0.2)
    out_text = _as_text(raw)

    meta = {
        "prompt_id": cfg.get("id", "contrast_analysis"),
        "prompt_version": cfg.get("version", "unknown"),
        "model": getattr(client, "model", "unknown"),
    }
    return meta, out_text
