# -*- coding: utf-8 -*-
"""
Clawdbot Tool Runner
- 统一入口：run_tool(name, args)
- 不走 subprocess，直接调用你现有的 python 代码（更稳更快）
"""

from __future__ import annotations
from typing import Any, Dict, Optional, List, Tuple
import re
import os
import sys
import traceback

# 确保 import 路径正确（从 tools/ 回到 tv_buy_1_0/ 的父目录）
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tv_buy_1_0.run_reco import (
    list_candidates as _list_candidates_preview,
    get_top3 as _get_top3,
)

VERSION = "tv-agent-tools/1.0"


# ============ intent_parse ============

SCENE_MAP = [
    ("ps5", ["ps5", "xsx", "xbox", "游戏", "电竞", "pc", "主机"]),
    ("movie", ["movie", "film", "电影", "观影", "暗场", "杜比", "影院", "追剧"]),
    ("bright", ["bright", "客厅", "白天", "很亮", "采光", "窗", "反光", "日照"]),
]

INTENT_KEYWORDS = {
    "tv_buy": ["预算", "寸", "英寸", "电视", "买电视", "选电视", "推荐", "对比", "挑一台"],
}

def _intent_parse(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    t = raw.lower()

    intent = "unknown"
    confidence = 0.50

    # 简单规则：出现购买电视信号词
    if any(k.lower() in t for k in INTENT_KEYWORDS["tv_buy"]):
        intent = "tv_buy"
        confidence = 0.88

    scene_hint = None
    for s, kws in SCENE_MAP:
        if any(k in t for k in kws):
            scene_hint = s
            break

    return {"intent": intent, "confidence": confidence, "scene_hint": scene_hint}


# ============ tv_search ============

def _tv_search(size: int, budget_max: int, brand: Optional[str], limit: int, offset: int) -> Dict[str, Any]:
    # run_reco.list_candidates 只有 preview top10，我们这里先用它做“候选预览”
    # count 用 total；candidates 用 preview + 伪分页（够你第一阶段用）
    # 如果你希望严格分页/全量返回，下一步我们把 sqlite 查询抽成 tv_search_core。
    total, cands = _list_candidates_preview(size=size, brand=brand, budget=budget_max, limit=max(limit, 10))
    # 伪分页：在 preview 上切片
    sliced = cands[offset: offset + limit]

    out = []
    for tv in sliced:
        out.append({
            "brand": tv.get("brand"),
            "model": tv.get("model"),
            "size_inch": tv.get("size_inch"),
            "price": tv.get("street_rmb"),
            "source": tv.get("source"),  # 如果你的 db 里存了 source
            "launch_date": tv.get("launch_date"),
        })

    return {
        "filters": {"size": size, "budget_max": budget_max, "brand": brand, "region": "CN"},
        "count": total,
        "candidates": out,
        "paging": {"limit": limit, "offset": offset},
    }


# ============ tv_rank / tv_compare / tv_pick ============

def _tv_rank(size: int, scene: str, brand: Optional[str], budget_max: Optional[int], prefer_year: int, top: int) -> Dict[str, Any]:
    top3 = _get_top3(size=size, scene=scene, brand=brand, budget=budget_max, year_prefer=prefer_year)
    topn = top3[: max(1, min(top, 10))]

    out = []
    for i, tv in enumerate(topn, 1):
        out.append({
            "rank": i,
            "brand": tv.get("brand"),
            "model": tv.get("model"),
            "size_inch": tv.get("size_inch"),
            "price_cny": tv.get("street_rmb"),
            "launch_date": tv.get("launch_date"),
            "launch_year": tv.get("_year"),
            "score": round(float(tv.get("_score", 0.0)), 4),
            "reasons": tv.get("reasons", []) if isinstance(tv.get("reasons"), list) else [],
            "risks": tv.get("risks", []) if isinstance(tv.get("risks"), list) else [],
        })

    return {
        "filters": {"size": size, "scene": scene, "brand": brand, "budget_max": budget_max, "prefer_year": prefer_year},
        "count": len(out),
        "top": out,
    }


FIELD_CN = {
    "input_lag_ms_60hz": "输入延迟(60Hz,ms)",
    "hdmi_2_1_ports": "HDMI2.1 口数",
    "allm": "ALLM(自动低延迟)",
    "vrr": "VRR(可变刷新)",
    "peak_brightness_nits": "峰值亮度(nits)",
    "local_dimming_zones": "控光分区(个)",
    "street_rmb": "到手价(￥)",
}

def _fmt_bool(x: Any) -> str:
    if x is None:
        return "?"
    if isinstance(x, (int, float)):
        if float(x) == 0:
            return "无"
        if float(x) == 1:
            return "有"
    if isinstance(x, bool):
        return "有" if x else "无"
    s = str(x).strip().lower()
    if s in ("true", "1", "yes", "y", "支持", "有"):
        return "有"
    if s in ("false", "0", "no", "n", "不支持", "无"):
        return "无"
    return str(x)

def _tv_compare(size: int, scene: str, brand: Optional[str], budget_max: Optional[int], prefer_year: int) -> Dict[str, Any]:
    top3 = _get_top3(size=size, scene=scene, brand=brand, budget=budget_max, year_prefer=prefer_year)
    if len(top3) < 2:
        return {
            "filters": {"size": size, "scene": scene, "brand": brand, "budget_max": budget_max, "prefer_year": prefer_year},
            "A": None,
            "B": None,
            "diffs": [],
            "recommendation": {"pick": "A", "why": ["候选不足 2 台，默认选 A"]},
        }

    A, B = top3[0], top3[1]

    diffs: List[str] = []
    for k, cn in FIELD_CN.items():
        av = A.get(k)
        bv = B.get(k)

        # bool 字段输出更友好
        if k in ("allm", "vrr"):
            a_str = _fmt_bool(av)
            b_str = _fmt_bool(bv)
        else:
            a_str = "?" if av is None else str(av)
            b_str = "?" if bv is None else str(bv)

        if a_str == b_str:
            diffs.append(f"{cn}：两者一致（{a_str}）")
        else:
            better = "A更好" if cn in ("到手价(￥)", "输入延迟(60Hz,ms)") else "需结合偏好"
            diffs.append(f"{cn}：A={a_str}，B={b_str}（{better}）")

    pick = "A"
    why = []
    if float(A.get("_score", 0.0)) >= float(B.get("_score", 0.0)):
        pick = "A"
        why.append(f"综合得分 A 更高（{round(float(A.get('_score', 0.0)),4)} > {round(float(B.get('_score', 0.0)),4)}）")
    else:
        pick = "B"
        why.append(f"综合得分 B 更高（{round(float(B.get('_score', 0.0)),4)} > {round(float(A.get('_score', 0.0)),4)}）")

    def _pack(tv: Dict[str, Any], rank: int) -> Dict[str, Any]:
        return {
            "rank": rank,
            "brand": tv.get("brand"),
            "model": tv.get("model"),
            "size_inch": tv.get("size_inch"),
            "price_cny": tv.get("street_rmb"),
            "launch_date": tv.get("launch_date"),
            "launch_year": tv.get("_year"),
            "score": round(float(tv.get("_score", 0.0)), 4),
            "reasons": tv.get("reasons", []) if isinstance(tv.get("reasons"), list) else [],
            "risks": tv.get("risks", []) if isinstance(tv.get("risks"), list) else [],
        }

    return {
        "filters": {"size": size, "scene": scene, "brand": brand, "budget_max": budget_max, "prefer_year": prefer_year},
        "A": _pack(A, 1),
        "B": _pack(B, 2),
        "diffs": diffs,
        "recommendation": {"pick": pick, "why": why},
    }


def _tv_pick(size: int, scene: str, brand: Optional[str], budget: Optional[int], prefer_year: int, pick: str) -> Dict[str, Any]:
    top3 = _get_top3(size=size, scene=scene, brand=brand, budget=budget, year_prefer=prefer_year)
    if not top3:
        return {"pick": pick, "product": None, "final_advice": {"summary": "无候选", "why_pick": [], "not_for": [], "buy_checklist": []}}

    idx = {"A": 0, "B": 1, "C": 2}.get(pick, 0)
    if idx >= len(top3):
        idx = 0
    tv = top3[idx]

    checklist = []
    if scene == "ps5":
        checklist = [
            "确认 60Hz / 120Hz 输入延迟实测值",
            "确认 VRR 是否在 PS5 下可用（需固件支持）",
            "确认 HDMI 2.1 是否全带宽（48Gbps）",
        ]
    elif scene == "movie":
        checklist = ["确认暗场光晕控制", "确认字幕压制表现", "确认杜比视界/HDR10+ 实际支持"]
    else:
        checklist = ["确认实测峰值亮度（非标称）", "确认抗反射表现", "确认白天泛白情况"]

    summary = (
        f"{tv.get('brand')} {tv.get('model')} 是当前条件下的 {pick} 号选择，"
        f"适配场景={scene}。如果你能接受部分参数仍需等实测确认，它可以直接作为候选结论。"
    )

    return {
        "pick": pick,
        "product": {
            "brand": tv.get("brand"),
            "model": tv.get("model"),
            "size_inch": tv.get("size_inch"),
            "price_cny": tv.get("street_rmb"),
            "launch_date": tv.get("launch_date"),
            "launch_year": tv.get("_year"),
            "score": round(float(tv.get("_score", 0.0)), 4),
        },
        "final_advice": {
            "summary": summary,
            "why_pick": tv.get("reasons", []) if isinstance(tv.get("reasons"), list) else [],
            "not_for": tv.get("risks", []) if isinstance(tv.get("risks"), list) else [],
            "buy_checklist": checklist,
        },
    }


# =========================================================
# 统一入口
# =========================================================
def run_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    统一执行入口：返回 {ok, data/error}
    """
    try:
        if name == "intent_parse":
            text = str(args.get("text", ""))
            return {"ok": True, "data": _intent_parse(text)}

        if name == "tv_search":
            size = int(args["size"])
            budget_max = int(args["budget_max"])
            brand = args.get("brand", None)
            limit = int(args.get("limit", 20))
            offset = int(args.get("offset", 0))
            return {"ok": True, "data": _tv_search(size, budget_max, brand, limit, offset)}

        if name == "tv_rank":
            size = int(args["size"])
            scene = str(args["scene"])
            brand = args.get("brand", None)
            budget_max = args.get("budget_max", None)
            budget_max = int(budget_max) if budget_max is not None else None
            prefer_year = int(args.get("prefer_year", 2026))
            top = int(args.get("top", 3))
            return {"ok": True, "data": _tv_rank(size, scene, brand, budget_max, prefer_year, top)}

        if name == "tv_compare":
            size = int(args["size"])
            scene = str(args["scene"])
            brand = args.get("brand", None)
            budget_max = args.get("budget_max", None)
            budget_max = int(budget_max) if budget_max is not None else None
            prefer_year = int(args.get("prefer_year", 2026))
            return {"ok": True, "data": _tv_compare(size, scene, brand, budget_max, prefer_year)}

        if name == "tv_pick":
            size = int(args["size"])
            scene = str(args["scene"])
            brand = args.get("brand", None)
            budget = args.get("budget", None)
            budget = int(budget) if budget is not None else None
            prefer_year = int(args.get("prefer_year", 2026))
            pick = str(args.get("pick", "A"))
            return {"ok": True, "data": _tv_pick(size, scene, brand, budget, prefer_year, pick)}

        return {"ok": False, "error": "UNKNOWN_TOOL", "detail": f"Unknown tool: {name}"}

    except Exception as e:
        return {
            "ok": False,
            "error": "TOOL_RUNTIME_ERROR",
            "detail": str(e),
            "trace": traceback.format_exc(),
        }
