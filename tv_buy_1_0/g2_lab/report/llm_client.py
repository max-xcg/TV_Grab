# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Any, Optional

"""
G2 Lab LLM Client (DeepSeek)

目标：
- /api/report/contrast 不再连接 127.0.0.1:8001
- 统一走 tv_buy_1_0.llm.deepseek_client.chat(system_prompt, user_prompt)

说明：
- deepseek_client 使用 OpenAI SDK 兼容写法，但 base_url 指向 DeepSeek。
- 只要 settings 里 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL 正确即可。
"""

@dataclass
class LlmResult:
    content: str
    raw: Any = None
    model: Optional[str] = None


class LlmClient:
    def __init__(self):
        # 不在这里初始化任何网络 client，避免 import 时就报错
        pass

    @staticmethod
    def _messages_to_system_user(messages: List[Dict[str, str]]) -> tuple[str, str]:
        """
        将 OpenAI 风格 messages 压缩成 (system_prompt, user_prompt)
        - system: 合并所有 system 角色内容
        - user_prompt: 合并 user/assistant 的对话为一段文本（保证上下文）
        """
        sys_parts: List[str] = []
        convo_parts: List[str] = []

        for m in messages or []:
            role = (m.get("role") or "").strip().lower()
            content = (m.get("content") or "").strip()
            if not content:
                continue

            if role == "system":
                sys_parts.append(content)
            elif role == "user":
                convo_parts.append(f"用户：{content}")
            elif role == "assistant":
                convo_parts.append(f"助手：{content}")
            else:
                convo_parts.append(content)

        system_prompt = "\n\n".join(sys_parts).strip()
        user_prompt = "\n\n".join(convo_parts).strip()

        # DeepSeek 要求至少有 user prompt
        if not user_prompt:
            user_prompt = "请根据提供内容生成结果。"

        return system_prompt, user_prompt

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.2, max_tokens: int = 1200) -> LlmResult:
        # 兼容参数：temperature/max_tokens 由 settings 控制，这里不强行覆盖
        from tv_buy_1_0.llm.deepseek_client import chat as deepseek_chat
        from tv_buy_1_0.config import settings as st

        system_prompt, user_prompt = self._messages_to_system_user(messages)

        # 如果上游没传 system，就给一个兜底，避免空 system
        if not system_prompt:
            system_prompt = "你是 G2 实验室的显示评测工程分析助手。"

        content = deepseek_chat(system_prompt=system_prompt, user_prompt=user_prompt)

        return LlmResult(content=content, raw=None, model=getattr(st, "DEEPSEEK_MODEL", None))


# =========================
# Backward compatibility
# 旧代码：from .llm_client import OpenAICompatClient
# 这里提供同名类，内部直接复用新的 LlmClient
# =========================
class OpenAICompatClient(LlmClient):
    def __init__(self):
        super().__init__()
        # 兼容 contrast_report.py 里引用的 client.model
        try:
            from tv_buy_1_0.config import settings as st
            self.model = getattr(st, 'DEEPSEEK_MODEL', None)
        except Exception:
            self.model = None

