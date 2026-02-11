# -*- coding: utf-8 -*-
"""
tv_compare.py
---------------------------------
对比 Top1 vs Top2：告诉用户“到底选谁”
- 纯 CLI
- stdout = JSON（Agent 安全）
- 复用 run_reco.get_top3（评分排序）
- 复用 reasons_v2（理由/不适合人群）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# =========================================================
# 确保可以 import tv_buy_1_0（解决直接 python 跑的问题）
# =========================================================
THIS_FILE = os.path.abspath(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_FILE, "../../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# =========================================================
# Core engine
# =========================================================
from tv_buy_1_0.run_reco import get_top3
from tv_buy_1_0.reasons_v2 import (
    reasons_ps5_v2,
    reasons_movie_v2,
    reasons_bright_v2,
)

# =========================================================
# Display labels
# =========================================================
FIELD_CN = {
    "input_lag_ms_60hz": "输入延迟(60Hz,ms)",
    "hdmi_2_1_ports": "HDMI2.1 口数",
    "allm": "ALLM(自动低延迟)",
    "vrr": "VRR(可变刷新)",
    "peak_brightness_nits": "峰值亮度(nits)",
    "local_dimming_zones": "控光分区(个)",
    "street_rmb": "到手价(￥)",
    "reflection_specular": "镜面反射(越低越好)",
    "uniformity_gray50_max_dev": "均匀性偏差(越低越好)",
}

SCENE_METRICS = {
    "ps5": [
        ("input_lag_ms_60hz", "neg"),   # 越低越好
        ("hdmi_2_1_ports", "pos"),
        ("allm", "pos_bool"),
        ("vrr", "pos_bool"),
        ("peak_brightness_nits", "pos"),
        ("local_dimming_zones", "pos"),
        ("street_rmb", "neg"),          # 越低越好（性价比）
    ],
    "movie": [
        ("local_dimming_zones", "pos"),
        ("peak_brightness_nits", "pos"),
        ("uniformity_gray50_max_dev", "neg"),
        ("reflection_specular", "neg"),
        ("street_rmb", "neg"),
    ],
    "bright": [
        ("peak_brightness_nits", "pos"),
        ("reflection_specular", "neg"),
        ("local_dimming_zones", "pos"),
        ("street_rmb", "neg"),
    ],
}

# =========================================================
# helpers
# =========================================================
def fmt_bool01(x: Any) -> str:
    if x is None:
        return "?"
    if isinstance(x, (int, float)):
        return "有" if float(x) != 0.0 else "无"
    if isinstance(x, bool):
        return "有" if x else "无"
    s = str(x).strip()
    if s in ("1", "true", "True", "支持", "有", "是"):
        return "有"
    if s in ("0", "false", "False", "不支持", "无", "否"):
        return "无"
    return s or "?"


def fmt_num(x: Any) -> str:
    if x is None:
        return "?"
    try:
        # 避免 12999.0 这种显示
        if isinstance(x, float) and x.is_integer():
            return str(int(x))
        return str(x)
    except Exception:
        return "?"


def extract_reasons(tv: Dict[str, Any], scene: str) -> Tuple[List[str], str]:
    if scene == "ps5":
        rs, not_fit = reasons_ps5_v2(tv)
    elif scene == "movie":
        rs, not_fit = reasons_movie_v2(tv)
    elif scene == "bright":
        rs, not_fit = reasons_bright_v2(tv)
    else:
        rs, not_fit = [], "—"
    return rs, str(not_fit)


def metric_value(tv: Dict[str, Any], k: str, mode: str) -> Any:
    v = tv.get(k)
    if mode.endswith("_bool"):
        return fmt_bool01(v)
    return v


def compare_two(a: Dict[str, Any], b: Dict[str, Any], scene: str) -> List[str]:
    """输出差异点（可读短句）"""
    diffs: List[str] = []

    for k, mode in SCENE_METRICS.get(scene, []):
        name = FIELD_CN.get(k, k)

        av = a.get(k)
        bv = b.get(k)

        # bool 展示
        if mode.endswith("_bool"):
            ashow = fmt_bool01(av)
            bshow = fmt_bool01(bv)
            if ashow == bshow:
                diffs.append(f"{name}：两者一致（{ashow}）")
            else:
                diffs.append(f"{name}：A={ashow}，B={bshow}")
            continue

        # 数值/缺失
        if av is None and bv is None:
            diffs.append(f"{name}：两者均缺失（?）")
            continue
        if av is None and bv is not None:
            diffs.append(f"{name}：A缺失（?），B={fmt_num(bv)}")
            continue
        if av is not None and bv is None:
            diffs.append(f"{name}：A={fmt_num(av)}，B缺失（?）")
            continue

        # 都有数值：判断优劣
        try:
            af = float(av)
            bf = float(bv)
        except Exception:
            diffs.append(f"{name}：A={fmt_num(av)}，B={fmt_num(bv)}")
            continue

        if af == bf:
            diffs.append(f"{name}：两者一致（{fmt_num(af)}）")
            continue

        if mode == "pos":
            better = "A更好" if af > bf else "B更好"
        else:  # neg
            better = "A更好" if af < bf else "B更好"

        diffs.append(f"{name}：A={fmt_num(af)}，B={fmt_num(bf)}（{better}）")

    return diffs


def pick_recommendation(a: Dict[str, Any], b: Dict[str, Any], scene: str) -> Dict[str, Any]:
    """给出“选A还是选B”的最终建议（优先按 score，其次按场景关键项）"""
    a_score = float(a.get("_score") or 0.0)
    b_score = float(b.get("_score") or 0.0)

    why: List[str] = []

    if a_score > b_score:
        pick = "A"
        why.append(f"综合得分 A 更高（{round(a_score,4)} > {round(b_score,4)}）")
    elif b_score > a_score:
        pick = "B"
        why.append(f"综合得分 B 更高（{round(b_score,4)} > {round(a_score,4)}）")
    else:
        pick = "A"
        why.append("综合得分相同，默认优先 A（更靠前排序）")

    # 给 1-2 条场景强相关解释（如果数据缺失就不硬编）
    key_metrics = SCENE_METRICS.get(scene, [])
    for k, mode in key_metrics[:3]:
        av = a.get(k)
        bv = b.get(k)
        name = FIELD_CN.get(k, k)

        if mode.endswith("_bool"):
            ashow = fmt_bool01(av)
            bshow = fmt_bool01(bv)
            if ashow != "?" and bshow != "?" and ashow != bshow:
                why.append(f"{name} 有差异：A={ashow}，B={bshow}")
            continue

        if av is None or bv is None:
            continue
        try:
            af, bf = float(av), float(bv)
        except Exception:
            continue
        if af == bf:
            continue

        if mode == "pos":
            better = "A" if af > bf else "B"
        else:
            better = "A" if af < bf else "B"
        why.append(f"{name}：{better} 更占优（A={fmt_num(af)}，B={fmt_num(bf)}）")

    return {"pick": pick, "why": why}


# =========================================================
# main
# =========================================================
def main():
    ap = argparse.ArgumentParser(description="TV Compare CLI (Top1 vs Top2)")
    ap.add_argument("--size", type=int, required=True, help="电视尺寸（英寸）")
    ap.add_argument("--scene", type=str, required=True, choices=["ps5", "movie", "bright"], help="使用场景")
    ap.add_argument("--brand", type=str, default=None, help="品牌限制（如 TCL）")
    ap.add_argument("--budget", type=int, default=None, help="预算上限（人民币）")
    ap.add_argument("--prefer_year", type=int, default=2026, help="优先年份")
    ap.add_argument("--request_id", type=str, default="dev", help="请求 ID（给 Agent 用）")
    args = ap.parse_args()

    ranked = get_top3(
        size=args.size,
        scene=args.scene,
        brand=args.brand,
        budget=args.budget,
        year_prefer=args.prefer_year,
    )

    # 至少要 2 个
    if len(ranked) < 2:
        out = {
            "request_id": args.request_id,
            "version": "tv-agent-cli/1.0",
            "ok": True,
            "data": {
                "filters": {
                    "size": args.size,
                    "scene": args.scene,
                    "brand": args.brand,
                    "budget_max": args.budget,
                    "prefer_year": args.prefer_year,
                },
                "count": len(ranked),
                "error": "当前条件下不足 2 台可对比（可能预算/品牌过滤过严或价格缺失导致硬过滤）。",
                "top": [
                    {
                        "rank": 1,
                        "brand": ranked[0].get("brand"),
                        "model": ranked[0].get("model"),
                        "size_inch": ranked[0].get("size_inch"),
                        "price_cny": ranked[0].get("street_rmb"),
                        "launch_date": ranked[0].get("launch_date"),
                        "launch_year": ranked[0].get("_year"),
                        "score": round(float(ranked[0].get("_score", 0.0)), 4),
                    }
                ] if ranked else [],
            },
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    a = ranked[0]
    b = ranked[1]

    a_reasons, a_notfit = extract_reasons(a, args.scene)
    b_reasons, b_notfit = extract_reasons(b, args.scene)

    diffs = compare_two(a, b, args.scene)
    reco = pick_recommendation(a, b, args.scene)

    out = {
        "request_id": args.request_id,
        "version": "tv-agent-cli/1.0",
        "ok": True,
        "data": {
            "filters": {
                "size": args.size,
                "scene": args.scene,
                "brand": args.brand,
                "budget_max": args.budget,
                "prefer_year": args.prefer_year,
            },
            "A": {
                "rank": 1,
                "brand": a.get("brand"),
                "model": a.get("model"),
                "size_inch": a.get("size_inch"),
                "price_cny": a.get("street_rmb"),
                "launch_date": a.get("launch_date"),
                "launch_year": a.get("_year"),
                "score": round(float(a.get("_score", 0.0)), 4),
                "reasons": a_reasons,
                "risks": [x.strip() for x in str(a_notfit).split("；") if x.strip()],
            },
            "B": {
                "rank": 2,
                "brand": b.get("brand"),
                "model": b.get("model"),
                "size_inch": b.get("size_inch"),
                "price_cny": b.get("street_rmb"),
                "launch_date": b.get("launch_date"),
                "launch_year": b.get("_year"),
                "score": round(float(b.get("_score", 0.0)), 4),
                "reasons": b_reasons,
                "risks": [x.strip() for x in str(b_notfit).split("；") if x.strip()],
            },
            "diffs": diffs,
            "recommendation": reco,
        },
    }

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
