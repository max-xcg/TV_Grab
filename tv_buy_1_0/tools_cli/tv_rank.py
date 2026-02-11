# -*- coding: utf-8 -*-
"""
tv_rank tool (CLI / Tool Runner)

目标（按你的最新要求）：
- 只按“新机型优先”排序（prefer_year 默认 2026；其余按年月从新到旧）
- 同月内：价格高 -> 低（更偏向高价机型，利于佣金）
- 预算内过滤：price <= budget_max
- 缺价机型直接剔除（不让 None 价格进入 Top / 备选池）
- 输出不依赖 score（score 字段不输出；若上层 schema 需要，也会填 null）

注意：
- 数据来源：SQLite(tv_buy_1_0/db/tv.sqlite) 的 tv 表（street_rmb / launch_date 等）
- 候选抽取：复用 tv_buy_1_0.run_reco.list_candidates（你现有的 DB 入口）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# 让脚本直跑也能 import tv_buy_1_0
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tv_buy_1_0/
PROJ_DIR = os.path.dirname(BASE_DIR)  # repo root
if PROJ_DIR not in sys.path:
    sys.path.insert(0, PROJ_DIR)

from tv_buy_1_0.run_reco import list_candidates  # 你已有（从 sqlite 读）


VERSION = "tv-agent-cli/3.2.newest-first"


def _as_int(x: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if x is None:
            return default
        if isinstance(x, bool):
            return default
        if isinstance(x, int):
            return x
        if isinstance(x, float):
            return int(x)
        s = str(x).strip().lower()
        if not s:
            return default
        s = s.replace(",", "").replace(" ", "")
        if s.endswith("k"):
            return int(float(s[:-1]) * 1000)
        if "万" in s:
            s2 = s.replace("万", "")
            return int(float(s2) * 10000)
        # 只保留数字
        digits = "".join(ch for ch in s if ch.isdigit())
        return int(digits) if digits else default
    except Exception:
        return default


def _norm_scene(scene: Any) -> str:
    s = (scene or "").strip().lower()
    if s in ("ps5", "movie", "bright", "sport"):
        return s
    # 兜底：默认 movie
    return "movie"


def _norm_brand(brand: Optional[str]) -> Optional[str]:
    if brand is None:
        return None
    b = str(brand).strip()
    if not b:
        return None
    # 你 DB 里品牌一般是 TCL / Hisense / SONY 这种
    # 这里对常见输入做一下规范化
    low = b.lower()
    if low == "tcl":
        return "TCL"
    if low == "hisense":
        return "Hisense"
    if low == "sony":
        return "SONY"
    if low == "samsung":
        return "SAMSUNG"
    # 其它：原样返回（尽量不乱改）
    return b


def _launch_key(launch_date: Any) -> int:
    """
    "2026-01" -> 202601
    "2025-12-05" -> 20251205（也兼容）
    None/异常 -> 0
    """
    if not launch_date:
        return 0
    s = str(launch_date).strip()
    if not s:
        return 0
    parts = s.split("-")
    try:
        y = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 1
        d = int(parts[2]) if len(parts) > 2 else 1
        return y * 10000 + m * 100 + d
    except Exception:
        return 0


def _launch_year(launch_date: Any) -> int:
    if not launch_date:
        return 0
    try:
        return int(str(launch_date)[:4])
    except Exception:
        return 0


def _recent_bucket(launch_date: Any, prefer_year: int) -> int:
    """
    bucket 越小越靠前：
    0: prefer_year（默认 2026）
    1: 其它年份（按日期新->旧再排）
    2: 没日期
    """
    y = _launch_year(launch_date)
    if y == prefer_year:
        return 0
    if y > 0:
        return 1
    return 2


def _safe_price(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        s = str(v).strip()
        if not s:
            return None
        digits = "".join(ch for ch in s if ch.isdigit())
        return int(digits) if digits else None
    except Exception:
        return None


def _filter_no_price(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        p = _safe_price(r.get("street_rmb"))
        if p is None or p <= 0:
            continue
        rr = dict(r)
        rr["street_rmb"] = p
        out.append(rr)
    return out


def rank_newest_first(
    size: int,
    scene: str,
    brand: Optional[str],
    budget_max: Optional[int],
    prefer_year: int,
    limit_pool: int = 500,
) -> List[Dict[str, Any]]:
    """
    返回已经排序好的候选列表（含 brand/model/size_inch/street_rmb/launch_date...）
    """
    # list_candidates：从 sqlite 取候选（它内部会做尺寸/品牌/预算等过滤；但对缺价不一定过滤）
    total, cands = list_candidates(
        size=int(size),
        brand=brand,
        budget=budget_max,
        limit=int(limit_pool),
    )

    # 去掉缺价（你明确要求：没有价格就不要出现）
    cands = _filter_no_price(cands)

    # 新机型优先排序：
    # 1) bucket：prefer_year 在最前
    # 2) launch_date：新->旧
    # 3) 同月：价格高->低（更偏高价）
    cands.sort(
        key=lambda x: (
            _recent_bucket(x.get("launch_date"), int(prefer_year)),
            -_launch_key(x.get("launch_date")),
            -int(_safe_price(x.get("street_rmb")) or 0),
        )
    )
    return cands


def tool_call(arguments: Dict[str, Any]) -> Dict[str, Any]:
    size = _as_int(arguments.get("size"))
    scene = _norm_scene(arguments.get("scene"))
    brand = _norm_brand(arguments.get("brand"))
    budget_max = _as_int(arguments.get("budget_max"))
    prefer_year = _as_int(arguments.get("prefer_year"), 2026) or 2026
    topn = _as_int(arguments.get("top"), 3) or 3
    topn = max(1, min(50, topn))  # 允许上层拿更多做 3+2

    if size is None:
        raise ValueError("size is required")

    ranked = rank_newest_first(
        size=size,
        scene=scene,
        brand=brand,
        budget_max=budget_max,
        prefer_year=prefer_year,
        limit_pool=800,
    )

    out_items: List[Dict[str, Any]] = []
    for i, tv in enumerate(ranked[:topn], 1):
        out_items.append(
            {
                "rank": i,
                "brand": tv.get("brand"),
                "model": tv.get("model"),
                "size_inch": tv.get("size_inch"),
                "price_cny": int(tv.get("street_rmb")) if tv.get("street_rmb") is not None else None,
                "launch_date": tv.get("launch_date"),
                "launch_year": _launch_year(tv.get("launch_date")),
                # 为兼容旧 schema：score 仍给出，但永远为 null（上层不显示即可）
                "score": None,
                "reasons": [],
                "risks": [],
            }
        )

    return {
        "version": VERSION,
        "filters": {
            "size": size,
            "scene": scene,
            "brand": brand,
            "budget_max": budget_max,
            "prefer_year": prefer_year,
        },
        "count": len(out_items),
        "top": out_items,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--scene", type=str, required=True, choices=["ps5", "movie", "bright", "sport"])
    ap.add_argument("--brand", type=str, default=None)
    ap.add_argument("--budget_max", type=int, default=None)
    ap.add_argument("--prefer_year", type=int, default=2026)
    ap.add_argument("--top", type=int, default=3)
    args = ap.parse_args()

    data = tool_call(
        {
            "size": args.size,
            "scene": args.scene,
            "brand": args.brand,
            "budget_max": args.budget_max,
            "prefer_year": args.prefer_year,
            "top": args.top,
        }
    )
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
