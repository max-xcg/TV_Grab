# -*- coding: utf-8 -*-
"""
Clawdbot Tool Schema
- 提供 tools 列表（function calling schema）
"""

from __future__ import annotations
from typing import List, Dict, Any

VERSION = "tv-agent-tools/1.0"


def get_tools() -> List[Dict[str, Any]]:
    """
    返回：可被 Clawdbot 注册的 tool schema 列表
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "intent_parse",
                "description": "解析用户文本意图，输出 intent / confidence / scene_hint。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "用户输入文本"},
                    },
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tv_search",
                "description": "按尺寸/预算/品牌过滤电视候选，返回候选列表（可分页）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "size": {"type": "integer", "description": "尺寸（英寸），例如 75"},
                        "budget_max": {"type": "integer", "description": "预算上限（人民币），例如 6000"},
                        "brand": {"type": ["string", "null"], "description": "品牌（可选），例如 TCL / hisense"},
                        "limit": {"type": "integer", "description": "返回条数（默认 20）"},
                        "offset": {"type": "integer", "description": "分页偏移（默认 0）"},
                    },
                    "required": ["size", "budget_max"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tv_rank",
                "description": "按场景对候选电视打分排序，返回 TopN。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "size": {"type": "integer", "description": "尺寸（英寸），例如 75"},
                        "scene": {"type": "string", "enum": ["ps5", "movie", "bright"], "description": "场景"},
                        "brand": {"type": ["string", "null"], "description": "品牌（可选）"},
                        "budget_max": {"type": ["integer", "null"], "description": "预算上限（可选）"},
                        "prefer_year": {"type": "integer", "description": "优先年份（默认 2026）"},
                        "top": {"type": "integer", "description": "返回 TopN（默认 3）"},
                    },
                    "required": ["size", "scene"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tv_compare",
                "description": "对比 Top1(A) 与 Top2(B)，输出关键字段差异和推荐倾向。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "size": {"type": "integer"},
                        "scene": {"type": "string", "enum": ["ps5", "movie", "bright"]},
                        "brand": {"type": ["string", "null"]},
                        "budget_max": {"type": ["integer", "null"]},
                        "prefer_year": {"type": "integer"},
                    },
                    "required": ["size", "scene"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tv_pick",
                "description": "最终成交决策：从 Top3 里选 A/B/C 之一，并给出购买前确认项。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "size": {"type": "integer"},
                        "scene": {"type": "string", "enum": ["ps5", "movie", "bright"]},
                        "brand": {"type": ["string", "null"]},
                        "budget": {"type": ["integer", "null"]},
                        "prefer_year": {"type": "integer"},
                        "pick": {"type": "string", "enum": ["A", "B", "C"], "description": "选择 Top1/2/3"},
                    },
                    "required": ["size", "scene", "pick"],
                    "additionalProperties": False,
                },
            },
        },
    ]
