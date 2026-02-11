# -*- coding: utf-8 -*-
"""
tv_buy_1_0/run_reco.py  （完整版｜可一键复制粘贴替换）

目标：
- CLI/规则推荐必须永远可跑（即使没装 openai / 没配 LLM）
- LLM 只做“可选增强”：ENABLE_LLM=True 且依赖可用时才启用
"""

import argparse
import sqlite3
import os
import re
import sys
import io
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional

import yaml

from tv_buy_1_0.reasons_v2 import (
    reasons_ps5_v2,
    reasons_movie_v2,
    reasons_bright_v2,
    top1_summary_ps5,
    top1_summary_movie,
    top1_summary_bright,
)

# =========================================================
# LLM 开关（软依赖）
# =========================================================
# 你的 config/settings.py 里已有 ENABLE_LLM（建议默认 False）
from tv_buy_1_0.config.settings import ENABLE_LLM  # noqa: E402

# 软依赖：没有 openai/相关依赖时，不允许 import 失败导致 CLI 不能跑
try:
    from tv_buy_1_0.llm.enhance import enhance_with_llm  # noqa: E402
    HAS_LLM = True
except Exception:
    enhance_with_llm = None  # type: ignore
    HAS_LLM = False

# =========================================================
# Windows / FastAPI 子进程中文输出不炸
# =========================================================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # => tv_buy_1_0/
DB = os.path.join(BASE_DIR, "db", "tv.sqlite")
PROFILES = os.path.join(BASE_DIR, "config", "profiles.yaml")

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
    "color_gamut_dci_p3": "DCI-P3 色域",
}

SCENE_DESC = {
    "bright": "明亮客厅（白天观看优先）：亮度/抗反射 > 价格价值 > 分区控光 > 色域。",
    "movie": "电影观影（暗场优先）：分区控光/对比 > 亮度 > 反射/均匀性 > 价格。",
    "ps5": "PS5 游戏：输入延迟（越低越好）> HDMI2.1/ALLM/VRR > 亮度/分区（HDR游戏观感）。",
}

# =========================
# utilities
# =========================
def months_ago(yyyymm: Any) -> Optional[int]:
    if not yyyymm:
        return None
    parts = str(yyyymm).strip().split("-")
    if len(parts) < 2:
        return None
    try:
        y, m = int(parts[0]), int(parts[1])
    except Exception:
        return None
    now = datetime.now()
    return (now.year - y) * 12 + (now.month - m)


def fmt(x: Any, suffix: str = "") -> str:
    if x is None:
        return "?"
    if isinstance(x, (int, float)) and suffix == "" and x in (0, 1):
        return "有" if int(x) == 1 else "无"
    if isinstance(x, bool) and suffix == "":
        return "有" if x else "无"
    return f"{x}{suffix}"


def to_bool01(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return 1.0 if float(x) != 0.0 else 0.0
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    if isinstance(x, str):
        s = x.strip().lower()
        if s in ("true", "yes", "y", "1", "支持", "有", "是"):
            return 1.0
        if s in ("false", "no", "n", "0", "不支持", "无", "否"):
            return 0.0
    return None


def norm_pos(x, lo, hi) -> float:
    if x is None or hi <= lo:
        return 0.0
    x = max(lo, min(hi, float(x)))
    return (x - lo) / (hi - lo)


def norm_neg(x, lo, hi) -> float:
    return 1.0 - norm_pos(x, lo, hi)


def norm_brand(brand: Optional[str]) -> Optional[str]:
    if not brand:
        return None
    b = str(brand).strip().lower()
    if b in ("tcl", "t.c.l"):
        return "tcl"
    if b in ("mi", "小米", "xiaomi"):
        return "mi"
    if b in ("hisense", "海信"):
        return "hisense"
    if b in ("sony", "索尼"):
        return "sony"
    return b


def launch_year_from_date(d: Any) -> int:
    if not d:
        return 0
    try:
        return int(str(d)[:4])
    except Exception:
        return 0


def parse_price(p: Any) -> Optional[float]:
    """支持 12999 / '12,999' / '¥12999' / '￥12999' """
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


def date_rank(d: Any) -> int:
    """YYYY-MM / YYYY-MM-DD -> yyyymmdd int, 越大越新；无日期=0"""
    if not d:
        return 0
    s = str(d).strip()
    parts = s.split("-")
    try:
        y = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 1
        dd = int(parts[2]) if len(parts) > 2 else 1
        return y * 10000 + m * 100 + dd
    except Exception:
        return 0


# =========================
# data loading
# =========================
def load_profile(scene: str):
    with open(PROFILES, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    profiles = cfg.get("profiles", {})
    if scene not in profiles:
        raise SystemExit(f"Unknown scene: {scene}. Available: {list(profiles.keys())}")
    p = profiles[scene]
    weights = p.get("weights", {})
    negative = set(p.get("negative_metrics", []))
    penalties = p.get("penalties", [])
    boolean_metrics = set(p.get("boolean_metrics", []))
    return weights, negative, boolean_metrics, penalties


def minmax(cands: List[Dict[str, Any]], key: str):
    vals = [c.get(key) for c in cands if c.get(key) is not None]
    if not vals:
        return 0.0, 1.0
    return float(min(vals)), float(max(vals))


def all_by_size(target: int) -> List[Dict[str, Any]]:
    """返回尺寸区间内的全部机型（不按品牌去重）"""
    lo, hi = target - 5, target + 5
    sql = """
    SELECT *
    FROM tv
    WHERE launch_date IS NOT NULL
      AND size_inch BETWEEN ? AND ?
    """
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, (lo, hi)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def apply_filters(
    cands: List[Dict[str, Any]],
    brand: Optional[str] = None,
    budget: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """硬过滤：brand & budget"""
    out: List[Dict[str, Any]] = []
    bkey = norm_brand(brand)

    for tv in cands:
        if bkey:
            tvb = norm_brand(tv.get("brand"))
            if tvb != bkey:
                continue

        if budget is not None:
            price = parse_price(tv.get("street_rmb"))
            # 预算过滤：缺失价格直接排除（否则会混进来）
            if price is None:
                continue
            if price > float(budget):
                continue

        out.append(tv)

    return out


# =========================
# candidates preview (for chat UI)
# =========================
def list_candidates(
    size: int,
    brand: Optional[str] = None,
    budget: Optional[int] = None,
    limit: int = 10,
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    返回：过滤后的候选数量 + 前 limit 条
    排序：2026 优先 > 2025 > 其它；再按日期新->旧；再按价格低->高
    """
    cands = apply_filters(all_by_size(size), brand=brand, budget=budget)

    def year_bucket(tv: Dict[str, Any]) -> int:
        y = launch_year_from_date(tv.get("launch_date"))
        if y == 2026:
            return 0
        if y == 2025:
            return 1
        return 2

    def price_rank(tv: Dict[str, Any]) -> float:
        p = parse_price(tv.get("street_rmb"))
        return p if p is not None else 10**18

    cands.sort(
        key=lambda tv: (
            year_bucket(tv),
            -date_rank(tv.get("launch_date")),
            price_rank(tv),
        )
    )

    total = len(cands)
    return total, cands[:limit]


def format_candidates(
    size: int,
    total: int,
    cands: List[Dict[str, Any]],
    brand: Optional[str] = None,
    budget: Optional[int] = None,
) -> str:
    head = f"📌 当前筛选候选：{total} 台"
    cond = []
    if brand:
        cond.append(f"品牌={brand}")
    if budget is not None:
        cond.append(f"预算≤{budget}")
    cond.append(f"尺寸≈{size}寸(±5)")
    head += "（" + "，".join(cond) + "）"

    if total == 0:
        return head + "\n⚠️ 当前条件下没有候选。你可以：放宽品牌/提高预算/换尺寸。"

    lines = [head, "（展示前10）"]
    for i, tv in enumerate(cands, 1):
        lines.append(
            f"{i}. {tv.get('brand')} {tv.get('model')} {tv.get('size_inch')}寸 | 首发 {tv.get('launch_date')} | ￥{fmt(tv.get('street_rmb'))}"
        )
    return "\n".join(lines)


# =========================
# scoring recommendation
# =========================
def get_top3(
    size: int,
    scene: str,
    brand: Optional[str] = None,
    budget: Optional[int] = None,
    year_prefer: int = 2026,
) -> List[Dict[str, Any]]:
    """
    Top3 推荐：在【过滤后的候选集】内算分排序；
    year_prefer 只是“优先”，不会越过预算/品牌硬过滤。
    """
    weights, negative_metrics, boolean_metrics, penalties = load_profile(scene)

    cands = apply_filters(all_by_size(size), brand=brand, budget=budget)
    if not cands:
        return []

    # boolean 归一化
    for tv in cands:
        for k in boolean_metrics:
            if k in tv:
                tv[k] = to_bool01(tv.get(k))

    stat = {k: minmax(cands, k) for k in weights.keys()}

    ranked: List[Dict[str, Any]] = []
    for tv in cands:
        score = 0.0
        parts: Dict[str, float] = {}

        for k, w in weights.items():
            lo, hi = stat.get(k, (0.0, 1.0))
            raw = tv.get(k)
            if k in negative_metrics:
                s = norm_neg(raw, lo, hi)
            else:
                s = norm_pos(raw, lo, hi)
            parts[k] = s * float(w)
            score += parts[k]

        # penalties
        for pen in penalties:
            m = pen.get("metric")
            op = pen.get("op")
            val = pen.get("value")
            mul = float(pen.get("multiplier", 1.0))
            x = tv.get(m)

            if op == "is_null" and x is None:
                score *= mul
                continue
            if op == "not_null" and x is not None:
                score *= mul
                continue
            if x is None:
                continue

            try:
                if (
                    (op == ">" and x > val)
                    or (op == ">=" and x >= val)
                    or (op == "<" and x < val)
                    or (op == "<=" and x <= val)
                    or (op == "==" and x == val)
                ):
                    score *= mul
            except Exception:
                pass

        # 老机轻惩罚（>12个月）
        age = months_ago(tv.get("launch_date"))
        if age is not None and age > 12:
            score *= 0.92

        tv2 = dict(tv)
        tv2["_score"] = score
        tv2["_year"] = launch_year_from_date(tv.get("launch_date"))
        tv2["_parts"] = parts
        ranked.append(tv2)

    # ✅ 2026 优先，但只在已过滤集合内
    ranked.sort(
        key=lambda x: (
            0 if x.get("_year") == year_prefer else 1,
            -float(x.get("_score") or 0.0),
            -date_rank(x.get("launch_date")),
        )
    )
    return ranked[:3]


# =========================
# explanation text
# =========================
def reasons(tv: Dict[str, Any], scene: str) -> Tuple[List[str], str]:
    r: List[str] = []
    if scene == "ps5":
        r.append(f"输入延迟：{fmt(tv.get('input_lag_ms_60hz'), 'ms')}（越低越好）")
        r.append(f"HDMI2.1：{fmt(tv.get('hdmi_2_1_ports'), '口')}；ALLM：{fmt(tv.get('allm'))}；VRR：{fmt(tv.get('vrr'))}")
        r.append(f"HDR 游戏观感：亮度 {fmt(tv.get('peak_brightness_nits'), 'nits')}；分区 {fmt(tv.get('local_dimming_zones'))}")
        not_fit = []
        if tv.get("input_lag_ms_60hz") is None:
            not_fit.append("输入延迟数据缺失（建议线下确认/等实测）。")
        if (tv.get("hdmi_2_1_ports") or 0) < 2:
            not_fit.append("HDMI2.1 口数偏少。")
        if tv.get("vrr") is None:
            not_fit.append("VRR 数据缺失（建议确认是否支持）。")
        if not not_fit:
            not_fit.append("整体均衡。")
        return r, " ".join(not_fit)

    if scene == "bright":
        r.append(f"白天抗环境光：亮度 {fmt(tv.get('peak_brightness_nits'), 'nits')}")
        r.append(f"反射：{fmt(tv.get('reflection_specular'))}（越低越好；? 表示未采集）")
        r.append(f"暗场/对比辅助：分区 {fmt(tv.get('local_dimming_zones'))}；价格￥{fmt(tv.get('street_rmb'))}")
        return r, "夜间极致暗场党建议补齐均匀性/光晕实测。"

    if scene == "movie":
        r.append(f"暗场控光：分区 {fmt(tv.get('local_dimming_zones'))}")
        r.append(f"HDR 亮度：{fmt(tv.get('peak_brightness_nits'), 'nits')}")
        r.append(f"均匀性/反射：均匀性 {fmt(tv.get('uniformity_gray50_max_dev'))}；反射 {fmt(tv.get('reflection_specular'))}")
        return r, "白天很亮的客厅建议用 bright 再跑一次。"

    return r, "—"


def recommend_text(
    size: int,
    scene: str,
    brand: Optional[str] = None,
    budget: Optional[int] = None,
    year_prefer: int = 2026,
) -> str:
    top3 = get_top3(size=size, scene=scene, brand=brand, budget=budget, year_prefer=year_prefer)

    head = f"电视选购 1.0 | {size} 寸 | 场景={scene}"
    if brand:
        head += f" | 品牌={brand}"
    if budget is not None:
        head += f" | 预算≤{budget}"
    head += f" | 优先年份={year_prefer}"

    lines = [head, SCENE_DESC.get(scene, "")]

    if not top3:
        lines.append("")
        lines.append("⚠️ 没有找到符合【当前条件】的机型。")
        if budget is not None and year_prefer:
            lines.append(f"提示：可能是 {year_prefer} 年机型全部超预算/缺价格（已硬过滤）。")
        lines.append("你可以：放宽品牌 / 提高预算 / 换尺寸。")
        return "\n".join(lines)

    lines.append("")
    lines.append("Top 3 推荐（过滤后候选集内排序）")
    lines.append("-" * 70)

    for i, tv in enumerate(top3, 1):
        warn = ""
        if tv.get("peak_brightness_nits") and tv["peak_brightness_nits"] > 6000:
            warn = " ⚠️亮度口径偏激进"
        title = f"{tv.get('brand')} {tv.get('model')} {tv.get('size_inch')}寸"
        lines.append(f"{i}. {title} | 首发 {tv.get('launch_date')} | ￥{fmt(tv.get('street_rmb'))}{warn}")

        if scene == "ps5":
            rs, not_fit = reasons_ps5_v2(tv)
        elif scene == "movie":
            rs, not_fit = reasons_movie_v2(tv)
        elif scene == "bright":
            rs, not_fit = reasons_bright_v2(tv)
        else:
            rs, not_fit = reasons(tv, scene)

        for line in rs:
            lines.append(f"   - {line}")
        lines.append(f"   - 不适合：{not_fit}")
        lines.append("")

    lines.append("一句话结论：")
    if scene == "ps5":
        lines.append(top1_summary_ps5(top3[0]))
    elif scene == "movie":
        lines.append(top1_summary_movie(top3[0]))
    elif scene == "bright":
        lines.append(top1_summary_bright(top3[0]))
    else:
        lines.append(top1_summary_ps5(top3[0]))

    base_text = "\n".join(lines)

    # ✅ LLM 增强：必须同时满足 ENABLE_LLM=True 且依赖可用
    if ENABLE_LLM and HAS_LLM and enhance_with_llm is not None:
        try:
            llm_text = enhance_with_llm(
                top3=top3,
                size=size,
                scene=scene,
                budget=budget,
            )
            return base_text + "\n\n———\n\n🤖 AI 增强解读：\n" + llm_text
        except Exception as e:
            return base_text + f"\n\n⚠️ LLM 增强失败，已回退规则引擎结果：{e}"

    # ✅ 开了开关但没依赖：明确提示（不报错）
    if ENABLE_LLM and (not HAS_LLM):
        return base_text + "\n\n⚠️ 已开启 ENABLE_LLM，但本机未安装/不可用 LLM 依赖（例如 openai）。已使用规则引擎结果。"

    return base_text


# =========================
# CLI entry
# =========================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--scene", type=str, required=True, choices=["bright", "movie", "ps5"])
    ap.add_argument("--brand", type=str, default=None)
    ap.add_argument("--budget", type=int, default=None)
    ap.add_argument("--prefer_year", type=int, default=2026)
    ap.add_argument("--show_candidates", action="store_true", help="只展示当前筛选候选(前10)")
    args = ap.parse_args()

    if not os.path.exists(DB):
        raise SystemExit(f"DB not found: {DB}")
    if not os.path.exists(PROFILES):
        raise SystemExit(f"profiles.yaml not found: {PROFILES}")

    if args.show_candidates:
        total, cands = list_candidates(args.size, brand=args.brand, budget=args.budget, limit=10)
        print(format_candidates(args.size, total, cands, brand=args.brand, budget=args.budget))
        return

    print(
        recommend_text(
            args.size,
            args.scene,
            brand=args.brand,
            budget=args.budget,
            year_prefer=args.prefer_year,
        )
    )


if __name__ == "__main__":
    main()
