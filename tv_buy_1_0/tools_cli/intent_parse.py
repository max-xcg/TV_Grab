# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
from typing import Dict, Any, Tuple


# =========================================================
# Intent Rules
# =========================================================
INTENTS = [
    # 电视选购 / 推荐
    ("tv_buy", [
        r"预算", r"\d+\s*元", r"\d+\s*块", r"\d+\s*千", r"\d+\s*w",
        r"推荐", r"选购", r"怎么买", r"怎么选", r"挑选",
        r"客厅", r"卧室", r"距离", r"观看距离", r"墙", r"电视柜",
        r"\d+\s*(寸|英寸)", r"75\s*(寸|英寸)", r"85\s*(寸|英寸)", r"65\s*(寸|英寸)",
        r"国补", r"补贴", r"京东", r"天猫", r"淘宝",
    ]),
    # 电视对比
    ("tv_compare", [
        r"对比", r"比较", r"哪个好", r"选哪个", r"A和B", r"vs", r"VS", r"PK",
        r"区别", r"差异",
    ]),
    # 电视故障 / 使用问题（可扩展）
    ("tv_troubleshoot", [
        r"黑屏", r"闪屏", r"花屏", r"重影", r"拖影", r"漏光", r"光晕",
        r"不开机", r"死机", r"卡顿", r"系统", r"广告",
        r"HDMI", r"ARC", r"eARC", r"杜比", r"声音", r"回音壁",
    ]),
]

# 场景关键词（可用于后续引导问答）
SCENE_HINTS = [
    ("movie", [r"电影", r"观影", r"杜比视界", r"暗场"]),
    ("sports", [r"体育", r"足球", r"篮球", r"比赛", r"看球"]),
    ("gaming", [r"ps5", r"xbox", r"主机", r"游戏", r"电竞", r"延迟", r"ALLM", r"VRR"]),
    ("bright", [r"白天", r"客厅很亮", r"反光", r"抗反射", r"窗户", r"采光"]),
]


def _score_text(text: str, patterns: list[str]) -> int:
    score = 0
    for pat in patterns:
        if re.search(pat, text, flags=re.IGNORECASE):
            score += 1
    return score


def detect_intent(text: str) -> Tuple[str, float, Dict[str, int], str]:
    """
    返回：
      intent: str
      confidence: float (0~1)
      debug_scores: dict
      scene_hint: str or ""
    """
    t = (text or "").strip()
    if not t:
        return "unknown", 0.0, {}, ""

    scores: Dict[str, int] = {}
    for intent, pats in INTENTS:
        scores[intent] = _score_text(t, pats)

    # 选最高分
    best_intent = max(scores, key=lambda k: scores[k])
    best_score = scores.get(best_intent, 0)

    # 场景提示
    scene = ""
    best_scene_score = 0
    for s, pats in SCENE_HINTS:
        sc = _score_text(t, pats)
        if sc > best_scene_score:
            best_scene_score = sc
            scene = s if sc > 0 else ""

    # 置信度：非常简单的可解释规则
    # - 0 分：unknown
    # - 1 分：0.55
    # - 2 分：0.70
    # - 3+ 分：0.85（封顶 0.95）
    if best_score <= 0:
        return "unknown", 0.0, scores, scene

    if best_score == 1:
        conf = 0.55
    elif best_score == 2:
        conf = 0.70
    else:
        conf = min(0.95, 0.85 + (best_score - 3) * 0.03)

    # tv_buy 和 tv_compare 冲突时，若同时出现“对比/哪个好/VS”，优先 tv_compare
    if scores.get("tv_compare", 0) > 0 and scores.get("tv_buy", 0) > 0:
        if scores["tv_compare"] >= scores["tv_buy"]:
            best_intent = "tv_compare"
            conf = max(conf, 0.75)

    return best_intent, float(conf), scores, scene


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True, help="用户输入文本")
    ap.add_argument("--debug", type=int, default=0, help="1=输出调试分数")
    args = ap.parse_args()

    intent, confidence, scores, scene_hint = detect_intent(args.text)

    out: Dict[str, Any] = {
        "ok": True,
        "data": {
            "intent": intent,
            "confidence": confidence,
            "scene_hint": scene_hint,
        },
    }
    if int(args.debug) == 1:
        out["data"]["scores"] = scores

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
