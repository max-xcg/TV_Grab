# -*- coding: utf-8 -*-
"""
tv_buy_1_0/llm/enhance.py  （完整版｜可一键复制粘贴替换）

目标：
- 移除 OpenAI 依赖
- 改为调用 智谱AI（open.bigmodel.cn） /paas/v4/chat/completions
- 对外暴露 enhance_with_llm(...) 给 run_reco.py 使用

环境变量：
- ZHIPU_API_KEY        必填
- ZHIPU_BASE_URL       可选，默认 https://open.bigmodel.cn/api/paas/v4
- ZHIPU_MODEL          可选，默认 glm-4-plus
- TVBUY_ZHIPU_TIMEOUT  可选，默认 6 秒
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key, "") or default).strip()


def _zhipu_endpoint() -> str:
    base = _env("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    return f"{base}/chat/completions"


def _zhipu_timeout() -> float:
    try:
        return float(_env("TVBUY_ZHIPU_TIMEOUT", "6") or "6")
    except Exception:
        return 6.0


def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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


def _extract_chat_content(resp: Dict[str, Any]) -> str:
    """
    兼容常见返回结构：
    resp["choices"][0]["message"]["content"]
    """
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


def _build_prompt(top3: List[Dict[str, Any]], size: int, scene: str, budget: Optional[int]) -> str:
    """
    这里用“补充解读”定位：不要改你主推荐逻辑，只做更像咨询报告的扩写。
    """
    lines: List[str] = []
    lines.append("你是电视选购顾问。请基于给定 Top3 机型，输出一段「简洁但专业」的增强解读。")
    lines.append("要求：")
    lines.append("- 不要编造不存在的参数；未知就说“未采集/需实测”。")
    lines.append("- 不要出现“VRR/可变刷新/变刷新”。")
    lines.append("- 不要出现“不适合”。如要提示风险，统一写“备注：……”。")
    lines.append("- 中文输出，结构清晰，偏报告口吻。")
    lines.append("")
    lines.append(f"用户条件：尺寸={size}寸；场景={scene}；预算上限={budget if budget is not None else '未给出'}")
    lines.append("")
    lines.append("Top3（JSON，字段缺失即未采集）：")
    lines.append(json.dumps(top3, ensure_ascii=False))
    lines.append("")
    lines.append("请输出：")
    lines.append("1) 总体建议（3-5 句）")
    lines.append("2) Top3 分别 2-3 句要点（不要罗列太多参数）")
    lines.append("3) 一句话结论（像咨询报告收尾）")
    return "\n".join(lines)


def enhance_with_llm(top3: List[Dict[str, Any]], size: int, scene: str, budget: Optional[int] = None) -> str:
    api_key = _env("ZHIPU_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing ZHIPU_API_KEY")

    model = _env("ZHIPU_MODEL", "glm-4-plus") or "glm-4-plus"
    url = _zhipu_endpoint()
    timeout = _zhipu_timeout()

    prompt = _build_prompt(top3=top3, size=size, scene=scene, budget=budget)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是专业、谨慎、不会编造数据的电视选购顾问。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 900,
    }

    t0 = time.time()
    resp = _post_json(url=url, headers=headers, payload=payload, timeout=timeout)
    text = _extract_chat_content(resp)

    if not text:
        raise RuntimeError(f"Empty response from Zhipu. raw={resp}")

    # 最后兜底过滤：避免模型输出带到 VRR 或 “不适合”
    low = text.lower()
    if ("vrr" in low) or ("可变刷新" in text) or ("变刷新" in text):
        # 粗暴剔除含关键字的行
        kept = []
        for ln in text.splitlines():
            lnl = ln.strip().lower()
            if ("vrr" in lnl) or ("可变刷新" in ln) or ("变刷新" in ln):
                continue
            kept.append(ln)
        text = "\n".join(kept).strip()

    text = text.replace("不适合：", "备注：").replace("不适合", "备注：")
    _ = time.time() - t0
    return text.strip()