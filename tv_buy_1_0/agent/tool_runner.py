# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import sys
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple


# =========================================================
# Paths
# =========================================================
# this file: tv_buy_1_0/agent/tool_runner.py
TVBUY_ROOT = Path(__file__).resolve().parents[1]          # .../tv_buy_1_0
PROJECT_ROOT = TVBUY_ROOT.parent                          # .../TV_Grab (repo root)

TOOLS_CLI_DIR = TVBUY_ROOT / "tools_cli"

SCRIPT_MAP = {
    "tv_search": TOOLS_CLI_DIR / "tv_search.py",
    "tv_rank": TOOLS_CLI_DIR / "tv_rank.py",
    "tv_compare": TOOLS_CLI_DIR / "tv_compare.py",
    "tv_pick": TOOLS_CLI_DIR / "tv_pick.py",
    # intent_parse 我这里内置实现（更快更稳，也避免再起子进程）
}

VERSION = "tv-agent-tools/1.0"


# =========================================================
# Tool schema (matches your /api/tools/schema output)
# =========================================================
TOOL_SCHEMA: List[Dict[str, Any]] = [
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


def get_schema() -> Dict[str, Any]:
    return {"ok": True, "version": VERSION, "tools": TOOL_SCHEMA}


# =========================================================
# Intent parse (fast heuristic)
# =========================================================
_INTENT_KW = {
    "ps5": ["ps5", "xbox", "xsx", "主机", "游戏", "电竞", "pc", "hdmi2.1", "vrr", "allm"],
    "movie": ["电影", "观影", "暗场", "杜比", "影院", "追剧", "hdr"],
    "bright": ["白天", "客厅", "采光", "窗", "反光", "明亮", "阳光"],
}

def _intent_parse(text: str) -> Dict[str, Any]:
    t = (text or "").strip().lower()
    if not t:
        return {"intent": "buy_tv", "confidence": 0.2, "scene_hint": None}

    # scene hint
    scene_scores = {k: 0 for k in _INTENT_KW.keys()}
    for scene, kws in _INTENT_KW.items():
        for kw in kws:
            if kw in t:
                scene_scores[scene] += 1
    scene_hint = max(scene_scores, key=lambda k: scene_scores[k]) if max(scene_scores.values()) > 0 else None

    # intent (very simple)
    # 你后续可以扩展：对比/最终选择/查候选等
    intent = "buy_tv"
    if any(x in t for x in ["对比", "比较", "a和b", "a vs b", "差异"]):
        intent = "compare"
    if any(x in t for x in ["我选a", "选a", "选b", "选c", "就它", "下单", "成交"]):
        intent = "pick"

    # confidence
    conf = 0.55
    if scene_hint:
        conf += 0.15
    if len(t) >= 8:
        conf += 0.1
    conf = min(conf, 0.95)

    return {"intent": intent, "confidence": round(conf, 2), "scene_hint": scene_hint}


# =========================================================
# Subprocess runner (reuse tools_cli results)
# =========================================================
def _run_cli(script: Path, args: List[str], timeout: int = 30) -> Dict[str, Any]:
    """
    Run tools_cli/*.py and parse JSON from stdout.
    Critical: set cwd=PROJECT_ROOT and PYTHONPATH=PROJECT_ROOT to avoid import errors.
    """
    if not script.exists():
        raise FileNotFoundError(f"tool script not found: {script}")

    env = os.environ.copy()
    # ensure `import tv_buy_1_0` works
    env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    cmd = [sys.executable, str(script)] + args

    p = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()

    if p.returncode != 0:
        raise RuntimeError(f"tool failed: {script.name} (code={p.returncode})\nSTDERR:\n{err}\nSTDOUT:\n{out}")

    # some scripts might print logs; find the last JSON object
    # strategy: locate first '{' from the end
    j = None
    if out:
        idx = out.rfind("{")
        if idx >= 0:
            try:
                j = json.loads(out[idx:])
            except Exception:
                j = None

    if j is None:
        raise RuntimeError(f"tool output is not valid JSON.\nSTDOUT:\n{out}\nSTDERR:\n{err}")

    return j


def _args_tv_search(arguments: Dict[str, Any]) -> List[str]:
    args: List[str] = []
    args += ["--size", str(arguments["size"])]
    args += ["--budget_max", str(arguments["budget_max"])]

    brand = arguments.get("brand")
    if brand:
        args += ["--brand", str(brand)]

    limit = arguments.get("limit")
    offset = arguments.get("offset")
    if limit is not None:
        args += ["--limit", str(int(limit))]
    if offset is not None:
        args += ["--offset", str(int(offset))]
    return args


def _args_tv_rank(arguments: Dict[str, Any]) -> List[str]:
    args: List[str] = []
    args += ["--size", str(arguments["size"])]
    args += ["--scene", str(arguments["scene"])]

    brand = arguments.get("brand")
    if brand:
        args += ["--brand", str(brand)]

    budget_max = arguments.get("budget_max")
    if budget_max is not None:
        args += ["--budget_max", str(int(budget_max))]

    prefer_year = arguments.get("prefer_year")
    if prefer_year is not None:
        args += ["--prefer_year", str(int(prefer_year))]

    top = arguments.get("top")
    if top is not None:
        args += ["--top", str(int(top))]
    return args


def _args_tv_compare(arguments: Dict[str, Any]) -> List[str]:
    args: List[str] = []
    args += ["--size", str(arguments["size"])]
    args += ["--scene", str(arguments["scene"])]

    brand = arguments.get("brand")
    if brand:
        args += ["--brand", str(brand)]

    budget_max = arguments.get("budget_max")
    if budget_max is not None:
        args += ["--budget_max", str(int(budget_max))]

    prefer_year = arguments.get("prefer_year")
    if prefer_year is not None:
        args += ["--prefer_year", str(int(prefer_year))]
    return args


def _args_tv_pick(arguments: Dict[str, Any]) -> List[str]:
    args: List[str] = []
    args += ["--size", str(arguments["size"])]
    args += ["--scene", str(arguments["scene"])]
    args += ["--pick", str(arguments["pick"])]

    brand = arguments.get("brand")
    if brand:
        args += ["--brand", str(brand)]

    budget = arguments.get("budget")
    if budget is not None:
        args += ["--budget", str(int(budget))]

    prefer_year = arguments.get("prefer_year")
    if prefer_year is not None:
        args += ["--prefer_year", str(int(prefer_year))]
    return args


# =========================================================
# Public entry: call_tool
# =========================================================
def call_tool(
    name: str,
    arguments: Dict[str, Any],
    request_id: str = "dev",
) -> Dict[str, Any]:
    """
    Unified tool call response:
      {"ok":true,"version":...,"request_id":...,"name":...,"data":...}
    """
    try:
        if name == "intent_parse":
            data = _intent_parse(arguments.get("text", ""))
            return {"ok": True, "version": VERSION, "request_id": request_id, "name": name, "data": data}

        if name == "tv_search":
            script = SCRIPT_MAP["tv_search"]
            cli_args = _args_tv_search(arguments)
            data = _run_cli(script, cli_args)
            # tools_cli/tv_search.py 通常直接输出 {"ok":true,"data":...} 或 {"filters":...}
            # 这里统一把核心放到 data
            return {"ok": True, "version": VERSION, "request_id": request_id, "name": name, "data": data.get("data", data)}

        if name == "tv_rank":
            script = SCRIPT_MAP["tv_rank"]
            cli_args = _args_tv_rank(arguments)
            data = _run_cli(script, cli_args)
            return {"ok": True, "version": VERSION, "request_id": request_id, "name": name, "data": data.get("data", data)}

        if name == "tv_compare":
            script = SCRIPT_MAP["tv_compare"]
            cli_args = _args_tv_compare(arguments)
            data = _run_cli(script, cli_args)
            return {"ok": True, "version": VERSION, "request_id": request_id, "name": name, "data": data.get("data", data)}

        if name == "tv_pick":
            script = SCRIPT_MAP["tv_pick"]
            cli_args = _args_tv_pick(arguments)
            data = _run_cli(script, cli_args)
            return {"ok": True, "version": VERSION, "request_id": request_id, "name": name, "data": data.get("data", data)}

        return {"ok": False, "version": VERSION, "request_id": request_id, "name": name, "error": f"unknown tool: {name}"}

    except Exception as e:
        return {"ok": False, "version": VERSION, "request_id": request_id, "name": name, "error": str(e)}


# =========================================================
# Local quick test
# =========================================================
if __name__ == "__main__":
    print(json.dumps(get_schema(), ensure_ascii=False, indent=2))

    r = call_tool(
        name="tv_rank",
        request_id="dbg_local",
        arguments={"size": 75, "scene": "ps5", "brand": "TCL", "budget_max": 20000, "prefer_year": 2026, "top": 3},
    )
    print(json.dumps(r, ensure_ascii=False, indent=2))
