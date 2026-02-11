# -*- coding: utf-8 -*-
"""
tv_pick.py
---------------------------------
最终决策 CLI（电视选购 1.0 · 成交层）

职责：
- 基于 get_top3 的结果
- 输出「最终推荐 + 风险提示 + 购买前确认项」
- 面向：Clawdbot / Agent / API / 人类最终决策

stdout：严格 JSON（默认 -h/--help 仍走 argparse 文本；需要 JSON help 用 --help_json）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional


# =========================================================
# stdout/stderr 编码（安全版：不替换 sys.stdout 对象）
# =========================================================
def _safe_reconfigure_stdio() -> None:
    try:
        if hasattr(sys.stdout, "reconfigure") and not getattr(sys.stdout, "closed", False):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        if hasattr(sys.stderr, "reconfigure") and not getattr(sys.stderr, "closed", False):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_safe_reconfigure_stdio()


# =========================================================
# 确保能 import tv_buy_1_0
# =========================================================
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tv_buy_1_0.run_reco import get_top3  # noqa: E402

VERSION = "tv-agent-cli/1.1"


# =========================================================
# 工具函数（只基于 tv 字段，不编造）
# =========================================================
def _fmt(x: Any, suffix: str = "") -> str:
    if x is None:
        return "?"
    if isinstance(x, bool) and suffix == "":
        return "有" if x else "无"
    if isinstance(x, (int, float)) and suffix == "" and x in (0, 1):
        return "有" if int(x) == 1 else "无"
    return f"{x}{suffix}"


def _to_bool01(x: Any) -> Optional[int]:
    if x is None:
        return None
    if isinstance(x, bool):
        return 1 if x else 0
    if isinstance(x, (int, float)):
        return 1 if float(x) != 0 else 0
    if isinstance(x, str):
        s = x.strip().lower()
        if s in ("true", "yes", "y", "1", "支持", "有", "是"):
            return 1
        if s in ("false", "no", "n", "0", "不支持", "无", "否"):
            return 0
    return None


def _parse_price(p: Any) -> Optional[float]:
    if p is None:
        return None
    if isinstance(p, (int, float)):
        return float(p)
    s = str(p).strip().replace("￥", "").replace("¥", "").replace(",", "")
    m = re.search(r"(\d+(\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def _field(tv: Dict[str, Any], key: str) -> Any:
    return tv.get(key)


# =========================================================
# 自动生成 reasons/risks：只使用 DB 字段
# =========================================================
def build_reasons_and_risks(tv: Dict[str, Any], scene: str) -> tuple[list[str], list[str]]:
    reasons: List[str] = []
    risks: List[str] = []

    # 常用字段
    lag = _field(tv, "input_lag_ms_60hz")
    hdmi21 = _field(tv, "hdmi_2_1_ports")
    allm = _to_bool01(_field(tv, "allm"))
    vrr = _to_bool01(_field(tv, "vrr"))
    peak = _field(tv, "peak_brightness_nits")
    zones = _field(tv, "local_dimming_zones")
    refl = _field(tv, "reflection_specular")
    uni = _field(tv, "uniformity_gray50_max_dev")
    price = _parse_price(_field(tv, "street_rmb"))

    if scene == "ps5":
        # reasons（偏正向）
        if lag is not None:
            reasons.append(f"输入延迟：{_fmt(lag, 'ms')}（越低越好）")
        else:
            risks.append("输入延迟数据缺失：建议看实测/线下确认。")

        if hdmi21 is not None:
            reasons.append(f"HDMI 2.1 口数：{_fmt(hdmi21, '口')}")
            try:
                if int(hdmi21) < 2:
                    risks.append("HDMI 2.1 口数偏少：多主机/回音壁可能不够用。")
            except Exception:
                pass
        else:
            risks.append("HDMI 2.1 口数缺失：建议确认接口规格。")

        if allm is not None:
            reasons.append(f"ALLM：{'支持' if allm == 1 else '不支持'}")
            if allm == 0:
                risks.append("ALLM 不支持：需要手动切换低延迟模式。")
        else:
            risks.append("ALLM 信息缺失：建议确认是否支持。")

        if vrr is not None:
            reasons.append(f"VRR：{'支持' if vrr == 1 else '不支持'}")
            if vrr == 0:
                risks.append("VRR 不支持：游戏防撕裂能力受限。")
        else:
            risks.append("VRR 信息缺失：建议确认是否支持（含 PS5 兼容性）。")

        if peak is not None or zones is not None:
            reasons.append(f"HDR 观感：亮度 {_fmt(peak, 'nits')}；分区 {_fmt(zones)}")

        if price is not None:
            reasons.append(f"到手价：￥{int(price) if price.is_integer() else price}")
        else:
            risks.append("价格缺失：无法确认是否稳定在预算内。")

    elif scene == "movie":
        if zones is not None:
            reasons.append(f"暗场控光：分区 {_fmt(zones)}（越多通常越有利）")
        else:
            risks.append("分区数据缺失：暗场控光能力不确定。")

        if peak is not None:
            reasons.append(f"HDR 峰值亮度：{_fmt(peak, 'nits')}")
        else:
            risks.append("峰值亮度缺失：HDR 冲击力不确定。")

        if uni is not None:
            reasons.append(f"均匀性：{_fmt(uni)}（越低越好）")
        else:
            risks.append("均匀性数据缺失：可能存在脏屏/漏光需看实测。")

        if refl is not None:
            reasons.append(f"反射：{_fmt(refl)}（越低越好）")
        else:
            risks.append("反射数据缺失：白天/灯光环境表现不确定。")

        if price is not None:
            reasons.append(f"到手价：￥{int(price) if price.is_integer() else price}")
        else:
            risks.append("价格缺失：性价比难判断。")

    elif scene == "bright":
        if peak is not None:
            reasons.append(f"白天抗环境光：亮度 { _fmt(peak, 'nits') }")
        else:
            risks.append("峰值亮度缺失：白天抗光能力不确定。")

        if refl is not None:
            reasons.append(f"反射：{_fmt(refl)}（越低越好）")
        else:
            risks.append("反射数据缺失：可能更吃环境光控制。")

        if zones is not None:
            reasons.append(f"暗场辅助：分区 {_fmt(zones)}")
        else:
            risks.append("分区数据缺失：夜间对比/光晕表现不确定。")

        if price is not None:
            reasons.append(f"到手价：￥{int(price) if price.is_integer() else price}")
        else:
            risks.append("价格缺失：预算匹配存在不确定性。")

    return reasons, risks


# =========================================================
# 文案模板（规则引擎，不靠 LLM）
# =========================================================
def build_final_advice(tv: Dict[str, Any], scene: str) -> Dict[str, Any]:
    reasons, risks = build_reasons_and_risks(tv, scene)

    checklist: list[str] = []
    if scene == "ps5":
        checklist = [
            "确认 60Hz / 120Hz 输入延迟实测值（同型号不同尺寸可能不同）",
            "确认 VRR 是否在 PS5 下可用（固件/接口/设置）",
            "确认 HDMI 2.1 是否全带宽（48Gbps）与 eARC 占口情况",
        ]
    elif scene == "movie":
        checklist = [
            "确认分区控光算法：光晕、黑位抬升、字幕压制",
            "确认 HDR 格式：杜比视界 / HDR10+ / HLG 实际支持",
            "确认面板一致性：暗角、脏屏、漏光（看评测/线下样机）",
        ]
    elif scene == "bright":
        checklist = [
            "确认实测峰值亮度（不同窗口/持续亮度口径）",
            "确认抗反射：镜面反射、偏振膜/涂层表现",
            "确认白天观感：黑位是否泛白、视角与色偏",
        ]

    brand = tv.get("brand", "?")
    model = tv.get("model", "?")
    summary = (
        f"{brand} {model} 是当前条件下的更优成交选择，"
        f"在 {scene} 场景里综合关键指标更占优势。"
    )

    return {
        "summary": summary,
        "why_pick": reasons,
        "not_for": risks,
        "buy_checklist": checklist,
    }


# =========================================================
# JSON help（可选：严格 stdout JSON 的 help）
# =========================================================
def print_help_json() -> None:
    out = {
        "ok": True,
        "version": VERSION,
        "tool": "tv_pick",
        "description": "TV Buy Final Pick CLI (成交层 JSON 输出)",
        "args": [
            {"name": "--size", "type": "int", "required": True, "example": 75},
            {"name": "--scene", "type": "str", "required": True, "choices": ["ps5", "movie", "bright"], "example": "ps5"},
            {"name": "--brand", "type": "str", "required": False, "example": "TCL"},
            {"name": "--budget", "type": "int", "required": False, "example": 6000},
            {"name": "--prefer_year", "type": "int", "required": False, "default": 2026, "example": 2026},
            {"name": "--pick", "type": "str", "required": False, "choices": ["A", "B", "C"], "default": "A"},
            {"name": "--request_id", "type": "str", "required": False, "default": "dev"},
            {"name": "--help_json", "type": "flag", "required": False, "description": "输出 JSON help（严格 stdout JSON）"},
        ],
        "example": {
            "cmd": "python3 tv_buy_1_0/tools_cli/tv_pick.py --size 75 --scene ps5 --brand TCL --budget 6000 --request_id t1",
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


# =========================================================
# CLI 主入口
# =========================================================
def main():
    ap = argparse.ArgumentParser(description="TV Buy Final Pick CLI")
    ap.add_argument("--size", type=int, required=False)
    ap.add_argument("--scene", type=str, required=False, choices=["ps5", "movie", "bright"])
    ap.add_argument("--brand", type=str, default=None)
    ap.add_argument("--budget", type=int, default=None)
    ap.add_argument("--prefer_year", type=int, default=2026)
    ap.add_argument("--pick", type=str, default="A", choices=["A", "B", "C"])
    ap.add_argument("--request_id", type=str, default="dev")
    ap.add_argument("--help_json", action="store_true")

    args, unknown = ap.parse_known_args()

    if args.help_json:
        print_help_json()
        return

    # 保持 argparse 的 -h/--help 行为
    if args.size is None or args.scene is None:
        ap.print_help()
        return

    top3 = get_top3(
        size=args.size,
        scene=args.scene,
        brand=args.brand,
        budget=args.budget,
        year_prefer=args.prefer_year,
    )

    if not top3:
        out = {
            "request_id": args.request_id,
            "version": VERSION,
            "ok": False,
            "error": "NO_CANDIDATES",
            "message": "当前条件下没有可推荐机型",
            "filters": {
                "size": args.size,
                "scene": args.scene,
                "brand": args.brand,
                "budget": args.budget,
                "prefer_year": args.prefer_year,
                "pick": args.pick,
            },
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    pick_index = {"A": 0, "B": 1, "C": 2}.get(args.pick, 0)
    if pick_index >= len(top3):
        pick_index = 0

    tv = top3[pick_index]
    advice = build_final_advice(tv, args.scene)

    try:
        score = round(float(tv.get("_score", 0.0)), 4)
    except Exception:
        score = 0.0

    out = {
        "request_id": args.request_id,
        "version": VERSION,
        "ok": True,
        "data": {
            "pick": args.pick,
            "product": {
                "brand": tv.get("brand"),
                "model": tv.get("model"),
                "size_inch": tv.get("size_inch"),
                "price_cny": tv.get("street_rmb"),
                "launch_date": tv.get("launch_date"),
                "launch_year": tv.get("_year"),
                "score": score,
            },
            "final_advice": advice,
        },
    }

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
