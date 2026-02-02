# -*- coding: utf-8 -*-
"""
Tool Runner (MVP)
- Input: JSON from stdin (or --json)
- Dispatch: call tools_cli/*.py as subprocess
- Output: JSON to stdout (ok/data/error)
"""

import argparse
import json
import os
import subprocess
import sys
from typing import Dict, Any, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tv_buy_1_0/
TOOLS_DIR = os.path.join(BASE_DIR, "tools_cli")

TOOL_MAP = {
    "tv_search": os.path.join(TOOLS_DIR, "tv_search.py"),
    "tv_rank": os.path.join(TOOLS_DIR, "tv_rank.py"),
    "tv_compare": os.path.join(TOOLS_DIR, "tv_compare.py"),
}


def _fail(msg: str, code: str = "bad_request", extra: Dict[str, Any] | None = None):
    out = {"ok": False, "error": {"code": code, "message": msg}}
    if extra:
        out["error"]["extra"] = extra
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(2)


def _load_request(args) -> Dict[str, Any]:
    if args.json:
        try:
            return json.loads(args.json)
        except Exception as e:
            _fail(f"--json 不是合法 JSON: {e}")
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            _fail("未收到请求：请通过 stdin 传入 JSON 或使用 --json")
        return json.loads(raw)
    except Exception as e:
        _fail(f"stdin 不是合法 JSON: {e}")


def _to_cli_args(d: Dict[str, Any]) -> List[str]:
    """{"size":75,"budget_max":6000} -> ["--size","75","--budget_max","6000"]"""
    out = []
    for k, v in d.items():
        if v is None:
            continue
        key = "--" + str(k)
        if isinstance(v, bool):
            out.append(key)
            out.append("1" if v else "0")
        else:
            out.append(key)
            out.append(str(v))
    return out


def _run_tool(tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
    if tool_name not in TOOL_MAP:
        _fail(f"未知 tool: {tool_name}", code="unknown_tool", extra={"allowed": list(TOOL_MAP.keys())})

    tool_path = TOOL_MAP[tool_name]
    if not os.path.exists(tool_path):
        _fail(f"tool 文件不存在: {tool_path}", code="tool_missing")

    cmd = [sys.executable, tool_path] + _to_cli_args(tool_args or {})
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        _fail(f"tool 超时: {tool_name}", code="tool_timeout", extra={"cmd": cmd})
    except Exception as e:
        _fail(f"执行 tool 失败: {e}", code="tool_exec_error", extra={"cmd": cmd})

    if p.returncode != 0:
        _fail(
            f"tool 返回非 0: {tool_name}",
            code="tool_failed",
            extra={"cmd": cmd, "stderr": (p.stderr or "")[-2000:], "stdout": (p.stdout or "")[-2000:]},
        )

    # 约定：tool stdout 必须是 JSON
    try:
        tool_json = json.loads(p.stdout)
    except Exception:
        _fail(
            f"tool 输出不是 JSON: {tool_name}",
            code="tool_bad_output",
            extra={"cmd": cmd, "stdout_tail": (p.stdout or "")[-2000:]},
        )

    return tool_json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="直接传入请求 JSON（可选）")
    args = ap.parse_args()

    req = _load_request(args)

    tool = req.get("tool")
    tool_args = req.get("args", {})

    if not tool or not isinstance(tool, str):
        _fail("请求必须包含 tool 字段（字符串）")
    if tool_args is not None and not isinstance(tool_args, dict):
        _fail("args 必须是 object/dict")

    data = _run_tool(tool, tool_args)

    out = {"ok": True, "tool": tool, "data": data}
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
