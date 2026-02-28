# -*- coding: utf-8 -*-
"""
tv_buy_1_0/llm/enhance.py  （2.0｜支持“晓春哥 XCG”本地素材注入｜可一键复制粘贴替换）

用法：
- run_reco.py 调用 enhance_with_llm(top3, size, scene, budget) -> str
- 会尝试读取 tv_buy_1_0/data_raw/xcg_notes/ 下的文本作为“参考资料”
- 模型被要求：只能引用资料中出现的观点/结论；资料缺失则不提及
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def _get_client() -> OpenAI:
    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is empty")

    base_url = _env("OPENAI_BASE_URL")
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def _get_model() -> str:
    model = _env("OPENAI_MODEL", "gpt-5.2")
    return model or "gpt-5.2"


def _to_brief_candidate(tv: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "brand",
        "model",
        "size_inch",
        "street_rmb",
        "launch_date",
        "input_lag_ms_60hz",
        "hdmi_2_1_ports",
        "allm",
        "vrr",
        "peak_brightness_nits",
        "local_dimming_zones",
        "reflection_specular",
        "uniformity_gray50_max_dev",
        "color_gamut_dci_p3",
        "_score",
        "_year",
        "_source",
    ]
    out: Dict[str, Any] = {}
    for k in keys:
        if k in tv:
            out[k] = tv.get(k)
    return out


def _load_xcg_notes(max_files: int = 6, max_chars_per_file: int = 3500) -> Tuple[str, List[str]]:
    """
    读取本地 XCG 素材（你自己整理/转写的文字）
    返回：(拼好的参考资料文本, 使用到的文件名列表)
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tv_buy_1_0/
    notes_dir = os.path.join(base_dir, "data_raw", "xcg_notes")
    if not os.path.isdir(notes_dir):
        return "", []

    exts = (".md", ".txt", ".yaml", ".yml")
    files = [f for f in os.listdir(notes_dir) if f.lower().endswith(exts)]
    files.sort()  # 你可以用文件名控制优先级，比如 01_xxx.md

    used: List[str] = []
    chunks: List[str] = []
    for fn in files[:max_files]:
        path = os.path.join(notes_dir, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                txt = f.read()
        except Exception:
            continue

        txt = (txt or "").strip()
        if not txt:
            continue

        if len(txt) > max_chars_per_file:
            txt = txt[:max_chars_per_file] + "\n...（已截断）"

        used.append(fn)
        chunks.append(f"[来源：{fn}]\n{txt}")

    if not chunks:
        return "", []

    ref = "\n\n---\n\n".join(chunks)
    return ref, used


def enhance_with_llm(
    top3: List[Dict[str, Any]],
    size: int,
    scene: str,
    budget: Optional[int] = None,
) -> str:
    client = _get_client()
    model = _get_model()

    brief = [_to_brief_candidate(x) for x in (top3 or [])]

    xcg_ref, xcg_files = _load_xcg_notes()

    system_prompt = (
        "你是显示/电视评测工程师 + 主机游戏玩家顾问。"
        "你要基于提供的候选数据给出冷静、可执行、可对比的建议。\n"
        "硬规则：\n"
        "1) 不要编造不存在的参数；缺失就写“缺失/需实测/需确认”。\n"
        "2) 如果提供了【晓春哥XCG参考资料】，只能引用资料中明确出现的观点/结论，不能脑补。\n"
        "3) 输出短、信息密度高：对比点 + 风险点 + 适合/不适合人群。\n"
        "4) 避免夸张营销词。"
    )

    user_prompt = (
        f"用户条件：品牌=TCL，尺寸≈{size}寸，场景={scene}，预算≤{budget if budget is not None else '未知'}。\n"
        f"候选Top3（JSON）：\n{brief}\n\n"
    )

    if xcg_ref:
        user_prompt += (
            "【晓春哥XCG参考资料｜仅可引用其中出现的观点/口径】\n"
            f"{xcg_ref}\n\n"
            f"请在输出中单独增加一节：'XCG 观点对照'，并注明引用来自哪些文件（从这些文件名里选：{xcg_files}）。\n"
        )
    else:
        user_prompt += (
            "未提供任何XCG参考资料：请不要提及“晓春哥XCG”。\n"
        )

    user_prompt += (
        "请输出：\n"
        "A) 1句总评（偏结论）\n"
        "B) 3台逐台点评（每台3~5条：优势/风险/建议确认项）\n"
        "C) 下单/观望建议（告诉用户缺失项如何补齐验证）\n"
    )

    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    text = (resp.output_text or "").strip()
    if not text:
        raise RuntimeError("LLM returned empty output_text")
    return text