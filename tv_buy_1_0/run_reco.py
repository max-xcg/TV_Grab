# -*- coding: utf-8 -*-
"""
tv_buy_1_0/run_reco.py  （完整版｜可一键复制粘贴替换）

目标：
- CLI/规则推荐必须永远可跑（即使没装 openai / 没配 LLM）
- LLM 只做“可选增强”：ENABLE_LLM=True 且依赖可用时才启用
- ✅ 强制接入 TCL Excel 新库（tv_buy_1_0/data_raw/excel_import_tcl_v2）：
  - 当 brand=TCL 时，候选只从 excel_import_tcl_v2 目录下的 YAML 读取并展开 variants（每个尺寸独立价格）
  - ✅ 不再调用旧库 out_step3_2025_spec / output_all_brands_2026_spec，也不走 sqlite tv.sqlite（针对 TCL）

重要修改（本次需求）：
- ✅ 去掉“尺寸±5”的容差：选哪个尺寸就是哪个尺寸（严格相等）
  - DB 查询：size_inch = target
  - TCL YAML：size_inch == target
  - UI 文案：尺寸=xx寸（不再显示 ≈ / ±5）

新增需求（本次）：
- ✅ 品牌权重顺序：海信、TCL、VIDDA、雷鸟、创维、小米、索尼（其它品牌权重不要高）
- ✅ Top3 最终展示：价格从高到低排序

本次新增（你的新需求）：
- ✅ PS5 的结论/话术不要出现 VRR（因为大部分产品 VRR 数据缺失）
  - 做法：过滤掉输出中包含 “VRR/可变刷新/变刷新” 的行
- ✅ 不要出现“不适合”字样：统一改为“备注：”
- ✅ 一句话结论加强：确保永远有内容、且更像“专业报告”的收尾

✅ 重要：OpenAI 已彻底移除，LLM 改为智谱：
- 使用环境变量：ZHIPU_API_KEY / ZHIPU_BASE_URL / ZHIPU_MODEL / TVBUY_ZHIPU_TIMEOUT

✅ 本次修复：
- ✅ 首发时间格式统一（展示统一为 YYYY-MM）
- ✅ 只保留这 10 个品牌参与候选/推荐：
  海信 / TCL / Vidda / 雷鸟 / 创维 / 小米 / 索尼 / 三星 / LG / 东芝
- ✅ 红米并入小米（redmi/红米 -> mi）
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

from tv_buy_1_0.config.settings import ENABLE_LLM  # noqa: E402

ZHIPU_API_KEY = (os.getenv("ZHIPU_API_KEY", "") or "").strip()
HAS_LLM = bool(ZHIPU_API_KEY)

try:
    from tv_buy_1_0.llm.enhance import enhance_with_llm  # noqa: E402
except Exception:
    enhance_with_llm = None  # type: ignore

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "db", "tv.sqlite")
PROFILES = os.path.join(BASE_DIR, "config", "profiles.yaml")

TCL_EXCEL_DIR = os.path.join(BASE_DIR, "data_raw", "excel_import_tcl_v2")
TCL_SOURCE_ONLY_EXCEL = True

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
    "ps5": "PS5 游戏：输入延迟（越低越好）> HDMI2.1/ALLM > 亮度/分区（HDR游戏观感）。",
}

ALLOWED_BRANDS = {
    "hisense",
    "tcl",
    "vidda",
    "ffalcon",
    "skyworth",
    "mi",
    "sony",
    "samsung",
    "lg",
    "toshiba",
}

BRAND_MULTIPLIER: Dict[str, float] = {
    "hisense": 1.12,
    "tcl": 1.10,
    "vidda": 1.08,
    "ffalcon": 1.06,
    "skyworth": 1.04,
    "mi": 1.02,
    "sony": 1.01,
    "samsung": 1.00,
    "lg": 1.00,
    "toshiba": 1.00,
}
OTHER_BRAND_MULTIPLIER = 0.95

BRAND_RANK: Dict[str, int] = {
    "hisense": 0,
    "tcl": 1,
    "vidda": 2,
    "ffalcon": 3,
    "skyworth": 4,
    "mi": 5,
    "sony": 6,
    "samsung": 7,
    "lg": 8,
    "toshiba": 9,
}

_VRR_BLOCK_KWS = ("vrr", "可变刷新", "变刷新")


def _drop_vrr_lines(lines: List[str]) -> List[str]:
    out: List[str] = []
    for ln in lines:
        tl = (ln or "").strip().lower()
        if any(k in tl for k in _VRR_BLOCK_KWS):
            continue
        out.append(ln)
    return out


def _drop_vrr_text(text: str) -> str:
    if not text:
        return text
    keep = []
    for ln in str(text).splitlines():
        tl = ln.strip().lower()
        if any(k in tl for k in _VRR_BLOCK_KWS):
            continue
        keep.append(ln)
    return "\n".join(keep)


def _ps5_fallback_reasons(tv: Dict[str, Any]) -> List[str]:
    r: List[str] = []
    r.append(f"输入延迟：{fmt(tv.get('input_lag_ms_60hz'), 'ms')}（越低越好；?=未采集）")
    r.append(f"HDMI2.1：{fmt(tv.get('hdmi_2_1_ports'), '口')}；ALLM：{fmt(tv.get('allm'))}")
    r.append(f"HDR 游戏观感：亮度 {fmt(tv.get('peak_brightness_nits'), 'nits')}；分区 {fmt(tv.get('local_dimming_zones'))}")
    return r


def _note_clean(note: Any, scene: str) -> str:
    s = str(note or "").strip()
    if scene == "ps5":
        s = _drop_vrr_text(s)

    s = s.replace("不适合：", "").replace("不适合", "").strip(" ：:;；")

    if not s:
        if scene == "ps5":
            return "部分关键游戏指标未完整采集，建议以权威评测/实测为准。"
        if scene == "movie":
            return "建议结合暗场光晕、均匀性等实测/评测确认最终观感。"
        if scene == "bright":
            return "建议结合抗反射与高亮维持能力的评测确认白天观感。"
        return "参数未完整采集，建议以实测/评测为准。"
    return s


def _parse_ymd_any(v: Any) -> Optional[Tuple[int, int, int]]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("none", "null", "nan", "-", "未知"):
        return None

    s = re.split(r"[ T\+]", s, maxsplit=1)[0]
    s = (
        s.replace("/", "-")
        .replace(".", "-")
        .replace("年", "-")
        .replace("月", "")
        .replace("日", "")
    ).strip()

    m = re.match(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$", s)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        dd = int(m.group(3)) if m.group(3) else 1
        if 1 <= mo <= 12:
            if not (1 <= dd <= 31):
                dd = 1
            return y, mo, dd
        return None

    m = re.match(r"^(\d{4})(\d{2})(\d{2})?$", s)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        dd = int(m.group(3)) if m.group(3) else 1
        if 1 <= mo <= 12:
            if not (1 <= dd <= 31):
                dd = 1
            return y, mo, dd
        return None

    m = re.match(r"^(\d{4})$", s)
    if m:
        return int(m.group(1)), 1, 1

    m = re.search(r"(\d{4})\D+(\d{1,2})", s)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            return y, mo, 1

    return None


def fmt_launch_yyyy_mm(v: Any) -> str:
    ymd = _parse_ymd_any(v)
    if not ymd:
        return "-"
    y, m, _ = ymd
    return f"{y:04d}-{m:02d}"


def _norm_launch_yyyy_mmdd(v: Any) -> Optional[str]:
    ymd = _parse_ymd_any(v)
    if not ymd:
        return None
    y, m, d = ymd
    return f"{y:04d}-{m:02d}-{d:02d}"


def _price_band_hint(price: Optional[float], budget: Optional[int]) -> str:
    if price is None or budget is None or budget <= 0:
        return ""
    try:
        r = float(price) / float(budget)
    except Exception:
        return ""
    if r >= 0.92:
        return "（接近预算上限，偏“冲顶配”买法）"
    if r >= 0.70:
        return "（预算中高位，偏“均衡稳妥”买法）"
    return "（预算较保守，偏“够用性价比”买法）"


def _ps5_strong_summary(tv: Dict[str, Any], budget: Optional[int]) -> str:
    brand = tv.get("brand") or ""
    model = tv.get("model") or ""
    size = tv.get("size_inch") or "?"
    launch = fmt_launch_yyyy_mm(tv.get("launch_date"))
    price_v = parse_price(tv.get("street_rmb"))
    price_txt = fmt(tv.get("street_rmb"))

    lag = tv.get("input_lag_ms_60hz")
    hdmi21 = tv.get("hdmi_2_1_ports")
    allm = tv.get("allm")
    bright = tv.get("peak_brightness_nits")
    zones = tv.get("local_dimming_zones")

    lag_txt = f"{fmt(lag, 'ms')}" if lag is not None else "未采集"
    hdmi_txt = f"x{fmt(hdmi21)}" if hdmi21 is not None else "口数未采集"
    allm_txt = fmt(allm)

    hdr_bits = []
    if bright is not None:
        hdr_bits.append(f"亮度{fmt(bright, 'nits')}")
    if zones is not None:
        hdr_bits.append(f"分区{fmt(zones)}")
    hdr_txt = " / ".join(hdr_bits) if hdr_bits else "HDR参数未完整采集"

    band = _price_band_hint(price_v, budget)

    who = []
    if lag is not None and isinstance(lag, (int, float)) and float(lag) <= 8:
        who.append("更适合偏竞技/动作类玩家")
    else:
        who.append("更适合日常休闲 + 轻度竞技玩家")

    if hdmi21 is not None and int(hdmi21) >= 2:
        who.append("多设备接入更从容")
    else:
        who.append("多设备接入需注意接口规划")

    who_txt = "，".join(who)

    return (
        f"{brand} {model} {size}寸（首发{launch}）作为 PS5 取向："
        f"输入延迟 {lag_txt}、HDMI2.1 {hdmi_txt}、ALLM {allm_txt}；"
        f"HDR 观感看 {hdr_txt}；到手价约￥{price_txt}{band}，{who_txt}。"
    ).strip()


def _movie_strong_summary(tv: Dict[str, Any], budget: Optional[int]) -> str:
    brand = tv.get("brand") or ""
    model = tv.get("model") or ""
    size = tv.get("size_inch") or "?"
    launch = fmt_launch_yyyy_mm(tv.get("launch_date"))
    price_v = parse_price(tv.get("street_rmb"))
    price_txt = fmt(tv.get("street_rmb"))

    zones = tv.get("local_dimming_zones")
    bright = tv.get("peak_brightness_nits")
    uni = tv.get("uniformity_gray50_max_dev")
    refl = tv.get("reflection_specular")

    bits = []
    if zones is not None:
        bits.append(f"分区{fmt(zones)}（控光/暗场层次更稳）")
    if bright is not None:
        bits.append(f"HDR亮度{fmt(bright, 'nits')}")
    if uni is not None:
        bits.append(f"均匀性{fmt(uni)}")
    if refl is not None:
        bits.append(f"反射{fmt(refl)}")

    core = "；".join(bits) if bits else "关键观影参数未完整采集"
    band = _price_band_hint(price_v, budget)

    return (
        f"{brand} {model} {size}寸（首发{launch}）更偏电影观影："
        f"{core}；到手价约￥{price_txt}{band}，适合追求暗场层次与观影氛围的用户。"
    ).strip()


def _bright_strong_summary(tv: Dict[str, Any], budget: Optional[int]) -> str:
    brand = tv.get("brand") or ""
    model = tv.get("model") or ""
    size = tv.get("size_inch") or "?"
    launch = fmt_launch_yyyy_mm(tv.get("launch_date"))
    price_v = parse_price(tv.get("street_rmb"))
    price_txt = fmt(tv.get("street_rmb"))

    bright = tv.get("peak_brightness_nits")
    refl = tv.get("reflection_specular")
    zones = tv.get("local_dimming_zones")

    bits = []
    if bright is not None:
        bits.append(f"亮度{fmt(bright, 'nits')}（白天更抗环境光）")
    if refl is not None:
        bits.append(f"反射{fmt(refl)}（越低越好）")
    if zones is not None:
        bits.append(f"分区{fmt(zones)}（控光更稳，亮暗切换更干净）")

    core = "；".join(bits) if bits else "白天观感相关参数未完整采集"
    band = _price_band_hint(price_v, budget)

    return (
        f"{brand} {model} {size}寸（首发{launch}）更偏明亮客厅："
        f"{core}；到手价约￥{price_txt}{band}，适合白天观看/客厅开灯场景。"
    ).strip()


def months_ago(yyyymm: Any) -> Optional[int]:
    ymd = _parse_ymd_any(yyyymm)
    if not ymd:
        return None
    y, m, _ = ymd
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
    try:
        x = max(lo, min(hi, float(x)))
        return (x - lo) / (hi - lo)
    except Exception:
        return 0.0


def norm_neg(x, lo, hi) -> float:
    return 1.0 - norm_pos(x, lo, hi)


def norm_brand(brand: Optional[str]) -> Optional[str]:
    if not brand:
        return None
    b = str(brand).strip().lower()

    if b in ("tcl", "t.c.l"):
        return "tcl"
    if b in ("mi", "小米", "xiaomi", "redmi", "红米"):
        return "mi"
    if b in ("hisense", "海信"):
        return "hisense"
    if b in ("sony", "索尼"):
        return "sony"
    if b in ("vidda", "vidda发现", "发现"):
        return "vidda"
    if b in ("雷鸟", "ffalcon", "f-falcon", "falcon", "f falcon"):
        return "ffalcon"
    if b in ("创维", "skyworth"):
        return "skyworth"
    if b in ("三星", "samsung"):
        return "samsung"
    if b in ("lg",):
        return "lg"
    if b in ("东芝", "toshiba"):
        return "toshiba"
    return b


def brand_multiplier(brand: Optional[str]) -> float:
    b = norm_brand(brand)
    if not b:
        return OTHER_BRAND_MULTIPLIER
    return float(BRAND_MULTIPLIER.get(b, OTHER_BRAND_MULTIPLIER))


def brand_rank(brand: Optional[str]) -> int:
    b = norm_brand(brand)
    if not b:
        return 999
    return int(BRAND_RANK.get(b, 999))


def launch_year_from_date(d: Any) -> int:
    ymd = _parse_ymd_any(d)
    if not ymd:
        return 0
    return int(ymd[0])


def parse_price(p: Any) -> Optional[float]:
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
    ymd = _parse_ymd_any(d)
    if not ymd:
        return 0
    y, m, dd = ymd
    return y * 10000 + m * 100 + dd


def _safe_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        if isinstance(x, bool):
            return None
        if isinstance(x, (int, float)):
            return int(x)
        s = str(x).strip()
        if not s:
            return None
        s = re.sub(r"[^\d]", "", s)
        return int(s) if s else None
    except Exception:
        return None


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


def _load_yaml_file(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = yaml.safe_load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _normalize_first_release(x: Any) -> Optional[str]:
    s = str(x).strip() if x is not None else ""
    if not s:
        return None
    n = _norm_launch_yyyy_mmdd(s)
    return n


def _build_tcl_model_name(base_model: str, size_inch: int) -> str:
    bm = (base_model or "").strip()
    if not bm:
        return f"{size_inch}TCL"
    if re.match(r"^\d{2,3}", bm):
        return bm
    return f"{size_inch}{bm}"


def load_tcl_excel_variants() -> List[Dict[str, Any]]:
    if not os.path.isdir(TCL_EXCEL_DIR):
        return []

    out: List[Dict[str, Any]] = []
    for fn in os.listdir(TCL_EXCEL_DIR):
        if not (fn.lower().endswith(".yaml") or fn.lower().endswith(".yml")):
            continue
        path = os.path.join(TCL_EXCEL_DIR, fn)
        obj = _load_yaml_file(path)
        if not obj:
            continue

        brand = obj.get("brand") or "TCL"
        base_model = obj.get("model") or ""
        first_release = _normalize_first_release(obj.get("first_release"))
        spec = obj.get("spec") if isinstance(obj.get("spec"), dict) else {}
        variants = obj.get("variants") if isinstance(obj.get("variants"), list) else []

        hdmi_2_1_ports = None
        allm = None
        vrr = None
        peak_brightness_nits = None
        local_dimming_zones = None

        try:
            hdmi = spec.get("hdmi") if isinstance(spec.get("hdmi"), dict) else {}
            hdmi_2_1_ports = _safe_int(hdmi.get("hdmi_2_1_ports"))
        except Exception:
            pass

        for v in variants:
            if not isinstance(v, dict):
                continue
            size_inch = _safe_int(v.get("size_inch"))
            if not size_inch:
                continue

            price_cny = _safe_int(v.get("price_cny"))
            peak = _safe_int(v.get("peak_brightness_nits"))
            zones = _safe_int(v.get("dimming_zones"))

            model_name = _build_tcl_model_name(str(base_model), int(size_inch))

            rec: Dict[str, Any] = {
                "brand": brand,
                "model": model_name,
                "size_inch": int(size_inch),
                "street_rmb": price_cny,
                "launch_date": first_release,
                "input_lag_ms_60hz": None,
                "hdmi_2_1_ports": hdmi_2_1_ports,
                "allm": allm,
                "vrr": vrr,
                "peak_brightness_nits": peak if peak is not None else peak_brightness_nits,
                "local_dimming_zones": zones if zones is not None else local_dimming_zones,
                "_source": f"excel:{fn}",
                "_source_path": path,
                "_base_model": base_model,
                "_variant": v,
                "_spec": spec,
            }
            out.append(rec)

    return out


def all_by_size_from_db(target: int) -> List[Dict[str, Any]]:
    sql = """
    SELECT *
    FROM tv
    WHERE launch_date IS NOT NULL
      AND size_inch = ?
    """
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, (int(target),)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["launch_date"] = _norm_launch_yyyy_mmdd(d.get("launch_date")) or d.get("launch_date")
        out.append(d)
    return out


def all_by_size(target: int, brand: Optional[str] = None) -> List[Dict[str, Any]]:
    bkey = norm_brand(brand)
    if bkey == "tcl":
        xs = load_tcl_excel_variants()
        return [
            r for r in xs
            if isinstance(r.get("size_inch"), int) and int(r["size_inch"]) == int(target)
        ]
    return all_by_size_from_db(target)


def apply_filters(cands: List[Dict[str, Any]], brand: Optional[str] = None, budget: Optional[int] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    bkey = norm_brand(brand)

    for tv in cands:
        tvb = norm_brand(tv.get("brand"))

        if tvb not in ALLOWED_BRANDS:
            continue

        if bkey:
            if tvb != bkey:
                continue

        if budget is not None:
            price = parse_price(tv.get("street_rmb"))
            if price is None:
                continue
            if price > float(budget):
                continue

        out.append(tv)

    return out


def list_candidates(size: int, brand: Optional[str] = None, budget: Optional[int] = None, limit: int = 10) -> Tuple[int, List[Dict[str, Any]]]:
    cands = apply_filters(all_by_size(size, brand=brand), brand=brand, budget=budget)

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
    return len(cands), cands[:limit]


def format_candidates(size: int, total: int, cands: List[Dict[str, Any]], brand: Optional[str] = None, budget: Optional[int] = None) -> str:
    head = f"📌 当前筛选候选：{total} 台"
    cond = []
    if brand:
        cond.append(f"品牌={brand}")
    if budget is not None:
        cond.append(f"预算≤{budget}")
    cond.append(f"尺寸={size}寸")
    head += "（" + "，".join(cond) + "）"

    if total == 0:
        return head + "\n⚠️ 当前条件下没有候选。你可以：放宽品牌/提高预算/换尺寸。"

    lines = [head, "（展示前10）"]
    for i, tv in enumerate(cands, 1):
        launch_mm = fmt_launch_yyyy_mm(tv.get("launch_date"))
        lines.append(
            f"{i}. {tv.get('brand')} {tv.get('model')} {tv.get('size_inch')}寸 | 首发 {launch_mm} | ￥{fmt(tv.get('street_rmb'))}"
        )
    return "\n".join(lines)


def get_top3(size: int, scene: str, brand: Optional[str] = None, budget: Optional[int] = None, year_prefer: int = 2026) -> List[Dict[str, Any]]:
    weights, negative_metrics, boolean_metrics, penalties = load_profile(scene)

    cands = apply_filters(all_by_size(size, brand=brand), brand=brand, budget=budget)
    if not cands:
        return []

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

        age = months_ago(tv.get("launch_date"))
        if age is not None and age > 12:
            score *= 0.92

        bmul = brand_multiplier(tv.get("brand"))
        score *= bmul

        tv2 = dict(tv)
        tv2["_score"] = score
        tv2["_year"] = launch_year_from_date(tv.get("launch_date"))
        tv2["_parts"] = parts
        tv2["_brand_rank"] = brand_rank(tv.get("brand"))
        tv2["_brand_mul"] = bmul
        ranked.append(tv2)

    ranked.sort(
        key=lambda x: (
            0 if x.get("_year") == year_prefer else 1,
            int(x.get("_brand_rank") or 999),
            -float(x.get("_score") or 0.0),
            -date_rank(x.get("launch_date")),
        )
    )
    return ranked[:3]


def reasons(tv: Dict[str, Any], scene: str) -> Tuple[List[str], str]:
    r: List[str] = []
    if scene == "ps5":
        r.append(f"输入延迟：{fmt(tv.get('input_lag_ms_60hz'), 'ms')}（越低越好）")
        r.append(f"HDMI2.1：{fmt(tv.get('hdmi_2_1_ports'), '口')}；ALLM：{fmt(tv.get('allm'))}")
        r.append(f"HDR 游戏观感：亮度 {fmt(tv.get('peak_brightness_nits'), 'nits')}；分区 {fmt(tv.get('local_dimming_zones'))}")
        note = []
        if tv.get("input_lag_ms_60hz") is None:
            note.append("输入延迟数据缺失，建议参考实测/评测。")
        if (tv.get("hdmi_2_1_ports") or 0) < 2:
            note.append("HDMI2.1 口数偏少，多设备接入需注意。")
        if not note:
            note.append("整体参数匹配 PS5 日常游玩。")
        return r, " ".join(note)

    if scene == "bright":
        r.append(f"白天抗环境光：亮度 {fmt(tv.get('peak_brightness_nits'), 'nits')}")
        r.append(f"反射：{fmt(tv.get('reflection_specular'))}（越低越好；? 表示未采集）")
        r.append(f"暗场/对比辅助：分区 {fmt(tv.get('local_dimming_zones'))}；价格￥{fmt(tv.get('street_rmb'))}")
        return r, "建议结合抗反射与高亮维持能力的评测确认白天观感。"

    if scene == "movie":
        r.append(f"暗场控光：分区 {fmt(tv.get('local_dimming_zones'))}")
        r.append(f"HDR 亮度：{fmt(tv.get('peak_brightness_nits'), 'nits')}")
        r.append(f"均匀性/反射：均匀性 {fmt(tv.get('uniformity_gray50_max_dev'))}；反射 {fmt(tv.get('reflection_specular'))}")
        return r, "建议结合暗场光晕、均匀性等实测/评测确认最终观感。"

    return r, "参数未完整采集，建议以实测/评测为准。"


def recommend_text(size: int, scene: str, brand: Optional[str] = None, budget: Optional[int] = None, year_prefer: int = 2026) -> str:
    top3 = get_top3(size=size, scene=scene, brand=brand, budget=budget, year_prefer=year_prefer)

    def _p(tv: Dict[str, Any]) -> float:
        v = parse_price(tv.get("street_rmb"))
        return float(v) if v is not None else -1.0

    top3_display = sorted(top3, key=lambda x: _p(x), reverse=True)

    head = f"电视选购 1.0 | {size} 寸 | 场景={scene}"
    if brand:
        head += f" | 品牌={brand}"
    if budget is not None:
        head += f" | 预算≤{budget}"
    head += f" | 优先年份={year_prefer}"

    lines = [head, SCENE_DESC.get(scene, "")]

    if not top3_display:
        lines.append("")
        lines.append("⚠️ 没有找到符合【当前条件】的机型。")
        if budget is not None and year_prefer:
            lines.append(f"提示：可能是 {year_prefer} 年机型全部超预算/缺价格（已硬过滤）。")
        lines.append("你可以：放宽品牌 / 提高预算 / 换尺寸。")
        return "\n".join(lines)

    lines.append("")
    lines.append("Top 3 推荐（最终展示：按价格从高到低）")
    lines.append("-" * 70)

    for i, tv in enumerate(top3_display, 1):
        warn = ""
        if tv.get("peak_brightness_nits") and isinstance(tv["peak_brightness_nits"], (int, float)) and tv["peak_brightness_nits"] > 6000:
            warn = " ⚠️亮度口径偏激进"
        title = f"{tv.get('brand')} {tv.get('model')} {tv.get('size_inch')}寸"
        launch_mm = fmt_launch_yyyy_mm(tv.get("launch_date"))
        lines.append(f"{i}. {title} | 首发 {launch_mm} | ￥{fmt(tv.get('street_rmb'))}{warn}")

        if scene == "ps5":
            rs, note = reasons_ps5_v2(tv)
            rs = _drop_vrr_lines(list(rs or []))
            note = _drop_vrr_text(str(note or ""))

            if len([x for x in rs if (x or "").strip()]) < 2:
                rs = _ps5_fallback_reasons(tv)

            note = _note_clean(note, scene="ps5")

        elif scene == "movie":
            rs, note = reasons_movie_v2(tv)
            note = _note_clean(note, scene="movie")
        elif scene == "bright":
            rs, note = reasons_bright_v2(tv)
            note = _note_clean(note, scene="bright")
        else:
            rs, note = reasons(tv, scene)
            note = _note_clean(note, scene=scene)

        for line in rs:
            lines.append(f"   - {line}")

        lines.append(f"   - 备注：{note}")
        lines.append("")

    lines.append("一句话结论：")
    summary = ""
    if scene == "ps5":
        try:
            summary = _drop_vrr_text(top1_summary_ps5(top3_display[0]) or "")
        except Exception:
            summary = ""
        summary = (summary or "").strip()
        if not summary:
            summary = _ps5_strong_summary(top3_display[0], budget)

    elif scene == "movie":
        try:
            summary = (top1_summary_movie(top3_display[0]) or "").strip()
        except Exception:
            summary = ""
        if not summary:
            summary = _movie_strong_summary(top3_display[0], budget)

    elif scene == "bright":
        try:
            summary = (top1_summary_bright(top3_display[0]) or "").strip()
        except Exception:
            summary = ""
        if not summary:
            summary = _bright_strong_summary(top3_display[0], budget)

    else:
        summary = _ps5_strong_summary(top3_display[0], budget)

    lines.append(summary)

    base_text = "\n".join(lines)

    if ENABLE_LLM and HAS_LLM and enhance_with_llm is not None:
        try:
            llm_text = enhance_with_llm(
                top3=top3_display,
                size=size,
                scene=scene,
                budget=budget,
            )
            if scene == "ps5":
                llm_text = _drop_vrr_text(llm_text or "")
            return base_text + "\n\n———\n\n🤖 AI 增强解读：\n" + (llm_text or "")
        except Exception as e:
            return base_text + f"\n\n⚠️ LLM 增强失败，已回退规则引擎结果：{e}"

    if ENABLE_LLM and (not HAS_LLM):
        return base_text + "\n\n⚠️ ENABLE_LLM=1，但未设置 ZHIPU_API_KEY，已使用规则引擎结果。"

    if ENABLE_LLM and enhance_with_llm is None:
        return base_text + "\n\n⚠️ ENABLE_LLM=1，但 tv_buy_1_0.llm.enhance 未正确加载，已使用规则引擎结果。"

    return base_text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--scene", type=str, required=True, choices=["bright", "movie", "ps5"])
    ap.add_argument("--brand", type=str, default=None)
    ap.add_argument("--budget", type=int, default=None)
    ap.add_argument("--prefer_year", type=int, default=2026)
    ap.add_argument("--show_candidates", action="store_true", help="只展示当前筛选候选(前10)")
    args = ap.parse_args()

    if norm_brand(args.brand) != "tcl":
        if not os.path.exists(DB):
            raise SystemExit(f"DB not found: {DB}")

    if not os.path.exists(PROFILES):
        raise SystemExit(f"profiles.yaml not found: {PROFILES}")

    if norm_brand(args.brand) == "tcl" and TCL_SOURCE_ONLY_EXCEL:
        if not os.path.isdir(TCL_EXCEL_DIR):
            raise SystemExit(f"[TCL] excel yaml dir not found: {TCL_EXCEL_DIR}")
        has_yaml = any(
            fn.lower().endswith((".yaml", ".yml")) for fn in os.listdir(TCL_EXCEL_DIR)
        )
        if not has_yaml:
            raise SystemExit(f"[TCL] no yaml files in: {TCL_EXCEL_DIR}")

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