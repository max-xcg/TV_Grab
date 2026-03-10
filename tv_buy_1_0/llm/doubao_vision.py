# -*- coding: utf-8 -*-
"""
tv_buy_1_0/llm/doubao_vision.py  （改为智谱通用调用｜完整版｜可一键复制粘贴替换）

说明：
- 原先如果是 Ark/豆包/或 OpenAI 兼容的实现，这里统一改为「智谱 Chat Completions」
- 让旧 import 不报错：保留同名函数/行为（返回文本）
- 不做图像多模态（你当前这步只要求替换 OpenAI；后面如果要“图片识别”再加 glm-4v 等）

环境变量：
- ZHIPU_API_KEY        必填
- ZHIPU_BASE_URL       可选，默认 https://open.bigmodel.cn/api/paas/v4
- ZHIPU_MODEL          可选，默认 glm-4-plus
- TVBUY_ZHIPU_TIMEOUT  可选，默认 6 秒
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key, "") or default).strip()


def _endpoint() -> str:
    base = _env("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    return f"{base}/chat/completions"


def _timeout() -> float:
    try:
        return float(_env("TVBUY_ZHIPU_TIMEOUT", "6") or "6")
    except Exception:
        return 6.0


def _post(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    api_key = _env("ZHIPU_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing ZHIPU_API_KEY")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            raw = resp.read()
            txt = raw.decode("utf-8", errors="replace")
            obj = json.loads(txt) if txt else {}
            return obj if isinstance(obj, dict) else {}
    except urllib.error.HTTPError as e:
        raw = b""
        try:
            raw = e.read() or b""
        except Exception:
            pass
        msg = raw.decode("utf-8", errors="replace") if raw else str(e)
        raise RuntimeError(f"Zhipu HTTPError {getattr(e, 'code', '')}: {msg}")
    except Exception as e:
        raise RuntimeError(f"Zhipu request failed: {e}")


def _extract(resp: Dict[str, Any]) -> str:
    try:
        choices = resp.get("choices")
        if isinstance(choices, list) and choices:
            c0 = choices[0] if isinstance(choices[0], dict) else {}
            msg = c0.get("message") if isinstance(c0.get("message"), dict) else {}
            content = msg.get("content")
            if isinstance(content, str):
                return content.strip()
    except Exception:
        pass
    return ""


def chat_text(messages: List[Dict[str, str]], model: Optional[str] = None, temperature: float = 0.4, max_tokens: int = 800) -> str:
    """
    messages: [{"role":"system/user/assistant","content":"..."}]
    """
    m = (model or _env("ZHIPU_MODEL", "glm-4-plus") or "glm-4-plus").strip()
    payload = {
        "model": m,
        "messages": messages,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }
    resp = _post(_endpoint(), payload)
    text = _extract(resp)
    if not text:
        raise RuntimeError(f"Empty response from Zhipu. raw={resp}")
    return text


# 兼容旧调用名（如果你其他代码里引用）
def call_llm(prompt: str, system: str = "你是专业的中文助手。") -> str:
    return chat_text(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
    )