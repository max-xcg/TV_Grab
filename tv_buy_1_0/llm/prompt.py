# -*- coding: utf-8 -*-
"""
tv_buy_1_0/llm/prompt.py

作用：
- 给 web/app.py 提供“晓春哥 XCG 推荐”的提示词生成函数
- 自动加载本地素材：tv_buy_1_0/data_raw/xcg_notes/*.md
- 清洗 ChatGPT 导出的引用标记（:contentReference[...]）
- 让 LLM 输出“有依据的推荐”，不允许编造测评结论

web/app.py 会 import：
    importlib.import_module("tv_buy_1_0.llm.prompt")

并调用本模块暴露的函数（兼容不同命名）：
- build_user_prompt(filters, top_items)  （推荐）
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple


# =========================
# paths
# =========================
def _project_root() -> str:
    # .../tv_buy_1_0/llm/prompt.py -> .../tv_buy_1_0
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _xcg_notes_dir() -> str:
    return os.path.join(_project_root(), "data_raw", "xcg_notes")


# =========================
# cleaning
# =========================
_CREF_RE = re.compile(r":contentReference\[[^\]]+\]\{[^}]*\}", flags=re.IGNORECASE)
_OAICITE_RE = re.compile(r"\{index=\d+\}", flags=re.IGNORECASE)


def _clean_note_text(s: str) -> str:
    """
    清洗你从 ChatGPT UI 复制出来的引用标记，例如：
    :contentReference[oaicite:1]{index=1}
    """
    s = _CREF_RE.sub("", s)
    s = _OAICITE_RE.sub("", s)
    # 合并过多空行
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


# =========================
# load notes
# =========================
def _read_text_file(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def load_xcg_notes(max_files: int = 20, max_chars: int = 12000) -> Tuple[str, List[str]]:
    """
    返回：
      notes_text: 拼接后的素材文本（已清洗、截断）
      used_files: 使用到的文件名列表（用于提示 LLM “引用来源”）
    """
    d = _xcg_notes_dir()
    if not os.path.isdir(d):
        return "", []

    files = [
        fn for fn in os.listdir(d)
        if fn.lower().endswith((".md", ".txt"))
    ]
    files.sort()  # 01_... 02_... 的命名会自然排序

    used_files: List[str] = []
    chunks: List[str] = []
    total = 0

    for fn in files[:max_files]:
        p = os.path.join(d, fn)
        raw = _read_text_file(p)
        if not raw:
            continue
        cleaned = _clean_note_text(raw)
        if not cleaned:
            continue

        block = f"【素材文件：{fn}】\n{cleaned}\n"
        if total + len(block) > max_chars:
            # 还能塞一点就截断
            remain = max_chars - total
            if remain > 200:
                block = block[:remain] + "\n（后续内容已截断）\n"
                chunks.append(block)
                used_files.append(fn)
            break

        chunks.append(block)
        used_files.append(fn)
        total += len(block)

    notes_text = "\n".join(chunks).strip()
    return notes_text, used_files


# =========================
# formatting candidates
# =========================
def _fmt(v: Any) -> str:
    if v is None:
        return "?"
    return str(v)


def _cand_line(i: int, tv: Dict[str, Any]) -> str:
    brand = tv.get("brand") or "?"
    model = tv.get("model") or "?"
    size = tv.get("size_inch") or "?"
    date = tv.get("launch_date") or "?"
    price = tv.get("street_rmb")
    return f"{i}. {brand} {model} {size}寸 | 首发 {date} | ￥{_fmt(price)}"


# =========================
# exported function for web/app.py
# =========================
def build_user_prompt(filters: Dict[str, Any], top_items: List[Dict[str, Any]]) -> str:
    """
    给 web/app.py 调用：生成 user prompt
    filters: 例如 {brand, size, budget, scene...}
    top_items: 候选列表（app.py 通常传入排序后的前 N 条）
    """
    brand = (filters.get("brand") or "").strip()
    size = filters.get("size") or filters.get("size_inch") or ""
    budget = filters.get("budget")
    scene = (filters.get("scene") or "").strip()

    notes_text, used_files = load_xcg_notes()

    # 候选列表
    lines = ["你是测评博主“晓春哥 XCG”风格的电视导购。输出必须：口语但专业、结构清晰、结论可执行。"]
    lines.append("")
    lines.append("【用户筛选条件】")
    lines.append(f"- 品牌：{brand or '未指定'}")
    lines.append(f"- 尺寸：{size or '未指定'}")
    lines.append(f"- 预算：{budget if budget is not None else '未指定'}")
    lines.append(f"- 场景：{scene or '未指定'}")
    lines.append("")
    lines.append("【候选机型（已按系统规则过滤/排序）】")
    if not top_items:
        lines.append("（无候选）")
    else:
        for i, tv in enumerate(top_items, 1):
            lines.append(_cand_line(i, tv))

    # 素材注入
    lines.append("")
    lines.append("【可引用的 XCG/测评素材（只允许引用这些内容，禁止编造）】")
    if notes_text:
        lines.append("规则：")
        lines.append("1) 只能基于下方素材做“引用/转述/总结”，不要凭空说“实测数据/结论”。")
        lines.append("2) 如果素材没提到输入延迟/VRR/ALLM等，必须明确写“素材未提及/待核实”。")
        lines.append("3) 需要引用时，请尽量在句末标注来源文件名，例如（来源：01_xcg_q10m.md）。")
        lines.append("")
        lines.append(notes_text)
        lines.append("")
        lines.append(f"（本次可引用素材文件：{', '.join(used_files)}）")
    else:
        lines.append("（当前没有本地素材文件：tv_buy_1_0/data_raw/xcg_notes/*.md；请补充后再引用。）")

    # 输出要求
    lines.append("")
    lines.append("【输出要求】")
    lines.append("A) 先给 1 句总评（适合谁/不适合谁）")
    lines.append("B) 对候选逐台点评：每台 3~5 条（优点/风险/购买建议）")
    lines.append("C) 给出下单策略：现在买/观望/去门店验证哪些项（尽量可执行）")
    lines.append("D) 不要输出任何奇怪引用标记（例如 :contentReference[...] 这类内容）")
    lines.append("E) 全中文输出")

    return "\n".join(lines)


# 兼容旧命名（如果 app.py 调的不是 build_user_prompt）
def make_user_prompt(filters: Dict[str, Any], top_items: List[Dict[str, Any]]) -> str:
    return build_user_prompt(filters, top_items)


def get_user_prompt(filters: Dict[str, Any], top_items: List[Dict[str, Any]]) -> str:
    return build_user_prompt(filters, top_items)