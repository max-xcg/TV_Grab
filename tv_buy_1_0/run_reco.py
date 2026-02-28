# -*- coding: utf-8 -*-
"""
tv_buy_1_0/run_reco.py  （最终版｜可一键复制粘贴替换）

目标：
- 规则推荐必须永远可跑（即使没装 openai / 没配 LLM）
- LLM 只做“可选增强”：ENABLE_LLM=True 且依赖可用时才启用
- ✅ 接入 Excel 导入的 YAML 库（默认 tv_buy_1_0/data_raw/excel_import_all_v1）
  - 递归扫描所有子目录（TCL/海信/小米/雷鸟/Vidda/创维...）
  - 展开 variants：每个尺寸一条记录（size_inch/price_cny/price_before_subsidy_cny...）
- ✅ 可选 sqlite fallback（默认关闭）：TVBUY_USE_SQLITE_FALLBACK=1 才启用
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import io
import time
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Iterable

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
from tv_buy_1_0.config.settings import ENABLE_LLM  # noqa: E402

OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY", "") or "").strip()
OPENAI_BASE_URL = (os.getenv("OPENAI_BASE_URL", "") or "").strip() or None
OPENAI_MODEL = (
    (os.getenv("OPENAI_MODEL", "") or "").strip()
    or (os.getenv("TVBUY_OPENAI_MODEL", "") or "").strip()
    or "gpt-5.2"
)

try:
    from openai import OpenAI  # noqa: F401
    HAS_OPENAI_PKG = True
except Exception:
    HAS_OPENAI_PKG = False

HAS_LLM = bool(HAS_OPENAI_PKG and OPENAI_API_KEY)

try:
    from tv_buy_1_0.llm.enhance import enhance_with_llm  # noqa: E402
except Exception:
    enhance_with_llm = None  # type: ignore


# =========================================================
# Windows / FastAPI 子进程中文输出不炸
# =========================================================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent  # => tv_buy_1_0/
PROFILES = BASE_DIR / "config" / "profiles.yaml"

# ✅ Excel 导入 YAML 目录（默认：excel_import_all_v1）
YAML_PRODUCTS_DIR = Path(
    os.environ.get("TVBUY_PRODUCTS_YAML_DIR", str(BASE_DIR / "data_raw" / "excel_import_all_v1"))
)

# ✅ sqlite fallback（默认关闭）
USE_SQLITE_FALLBACK = (os.environ.get("TVBUY_USE_SQLITE_FALLBACK", "0").strip() == "1")
SQLITE_DB = Path(os.environ.get("TVBUY_SQLITE_DB", str(BASE_DIR / "db" / "tv.sqlite")))

# =========================================================
# 文案
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
    "color_gamut_dci_p3": "DCI-P3 色域",
}

SCENE_DESC = {
    "bright": "明亮客厅（白天观看优先）：亮度/抗反射 > 价格价值 > 分区控光 > 色域。",
    "movie": "电影观影（暗场优先）：分区控光/对比 > 亮度 > 反射/均匀性 > 价格。",
    "ps5": "PS5 游戏：输入延迟（越低越好）> HDMI2.1/ALLM/VRR > 亮度/分区（HDR游戏观感）。",
}

# =========================================================
# utilities
# =========================================================
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
    try:
        x = max(lo, min(hi, float(x)))
        return (x - lo) / (hi - lo)
    except Exception:
        return 0.0


def norm_neg(x, lo, hi) -> float:
    return 1.0 - norm_pos(x, lo, hi)


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
        s = s.replace(",", "")
        s = re.sub(r"[^\d.]", "", s)
        if not s:
            return None
        return int(float(s))
    except Exception:
        return None


def _normalize_date_like(x: Any) -> Optional[str]:
    """
    统一到 YYYY-MM 或 YYYY-MM-DD（尽量 YYYY-MM）
    """
    if not x:
        return None
    s = str(x).strip()
    if not s:
        return None
    s = s.replace(".", "-").replace("/", "-")
    m = re.match(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$", s)
    if m:
        y = int(m.group(1))
        mm = int(m.group(2))
        dd = m.group(3)
        if dd is None:
            return f"{y:04d}-{mm:02d}"
        return f"{y:04d}-{mm:02d}-{int(dd):02d}"
    m2 = re.search(r"(\d{4})[-/\.](\d{1,2})", s)
    if m2:
        y = int(m2.group(1))
        mm = int(m2.group(2))
        return f"{y:04d}-{mm:02d}"
    return None


def _normalize_brand(brand: Optional[str]) -> Optional[str]:
    if not brand:
        return None
    s = str(brand).strip()
    if not s:
        return None
    sl = s.lower()
    if sl in ("tcl", "t.c.l"):
        return "TCL"
    if sl in ("hisense",):
        return "海信"
    if sl in ("xiaomi", "mi"):
        return "小米"
    if sl in ("ffalcon", "f-falcon", "falcon"):
        return "雷鸟"
    if sl in ("vidda",):
        return "Vidda"
    return s


def _flatten_yaml_obj(obj: Any) -> List[Dict[str, Any]]:
    """
    兼容 excel_import_all_v1 的单文件结构：
      - dict: {brand, model, first_release, spec, variants:[{size_inch, price_cny, ...}, ...]}
    也兼容其它结构：
      - list[dict]
      - dict{items:[...]} / dict{rows:[...]} / dict{products:[...]} / dict{data:[...]} / dict{models:[...]}
      - dict 单条
    """
    if obj is None:
        return []
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        for k in ("items", "rows", "products", "data", "models"):
            v = obj.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        return [obj]
    return []


def _pick(d: Dict[str, Any], keys: Iterable[str]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, "", []):
            return d[k]
    return None


def _expand_excel_import_row(row: Dict[str, Any], source_name: str) -> List[Dict[str, Any]]:
    """
    把 excel_import_all_v1 的单条结构展开为“每个尺寸一行”:
      - base: brand/model/first_release/spec...
      - variants: size_inch/price_cny/peak_brightness_nits/dimming_zones/price_before_subsidy_cny...
    输出字段（尽量对齐 run_reco 旧字段）：
      brand, model, size_inch, street_rmb, launch_date, hdmi_2_1_ports, allm, vrr,
      peak_brightness_nits, local_dimming_zones, ...
    """
    brand = _normalize_brand(_pick(row, ["brand", "品牌"])) or "未知"
    base_model = _pick(row, ["model", "型号", "机型", "name"])
    if base_model is None:
        base_model = ""
    base_model = str(base_model).strip()

    launch_date = _normalize_date_like(_pick(row, ["first_release", "发布时间", "发布", "launch_date", "上市时间", "首发", "release"]))

    spec = row.get("spec") if isinstance(row.get("spec"), dict) else {}
    hdmi_2_1_ports = None
    allm = None
    vrr = None
    try:
        hdmi = spec.get("hdmi") if isinstance(spec.get("hdmi"), dict) else {}
        hdmi_2_1_ports = _safe_int(hdmi.get("hdmi_2_1_ports"))
        # 这里不强行从文本猜 allm/vrr，避免编造；留 None
    except Exception:
        pass

    variants = row.get("variants")
    if isinstance(variants, list) and base_model:
        out: List[Dict[str, Any]] = []
        for v in variants:
            if not isinstance(v, dict):
                continue
            size_inch = _safe_int(v.get("size_inch"))
            if not size_inch:
                continue
            # 价格：优先国补价 price_cny
            price_cny = _safe_int(v.get("price_cny"))
            # 兼容一些字段名
            if price_cny is None:
                price_cny = _safe_int(v.get("price")) or _safe_int(v.get("street_rmb"))

            peak = _safe_int(v.get("peak_brightness_nits"))
            zones = _safe_int(v.get("dimming_zones"))
            if zones is None:
                zones = _safe_int(v.get("local_dimming_zones"))

            # 模型名：不重复前缀就加尺寸
            model_name = base_model
            if not re.match(r"^\d{2,3}", model_name):
                model_name = f"{size_inch}{model_name}"

            rec: Dict[str, Any] = {
                "brand": brand,
                "model": model_name,
                "size_inch": int(size_inch),
                "street_rmb": price_cny,
                "launch_date": launch_date,
                "input_lag_ms_60hz": None,
                "hdmi_2_1_ports": hdmi_2_1_ports,
                "allm": allm,
                "vrr": vrr,
                "peak_brightness_nits": peak,
                "local_dimming_zones": zones,
                "_source": f"yaml:{source_name}",
                "_variant": v,
                "_spec": spec,
            }
            out.append(rec)
        if out:
            return out

    # 没 variants 的兜底
    return [
        {
            "brand": brand,
            "model": base_model or None,
            "size_inch": _safe_int(_pick(row, ["size_inch", "尺寸", "size", "inch"])),
            "street_rmb": _safe_int(_pick(row, ["price_cny", "价格", "price", "street_rmb"])),
            "launch_date": launch_date,
            "input_lag_ms_60hz": None,
            "hdmi_2_1_ports": hdmi_2_1_ports,
            "allm": allm,
            "vrr": vrr,
            "peak_brightness_nits": _safe_int(_pick(row, ["peak_brightness_nits"])),
            "local_dimming_zones": _safe_int(_pick(row, ["dimming_zones", "local_dimming_zones"])),
            "_source": f"yaml:{source_name}",
            "_spec": spec,
        }
    ]


_yaml_cache: Dict[str, Any] = {"ts": 0.0, "files_sig": None, "items": [], "loaded": 0}
_YAML_CACHE_TTL = 2.0  # 秒


def load_all_yaml_products() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    now = time.time()
    if now - float(_yaml_cache.get("ts", 0.0)) < _YAML_CACHE_TTL:
        return list(_yaml_cache.get("items") or []), {
            "yaml_dir": str(YAML_PRODUCTS_DIR),
            "yaml_loaded": int(_yaml_cache.get("loaded") or 0),
        }

    paths: List[str] = []
    if YAML_PRODUCTS_DIR.exists():
        paths.extend([str(p) for p in YAML_PRODUCTS_DIR.rglob("*.yml")])
        paths.extend([str(p) for p in YAML_PRODUCTS_DIR.rglob("*.yaml")])

    sig = "|".join([f"{p}:{os.path.getmtime(p)}" for p in sorted(paths)]) if paths else ""
    if sig and sig == _yaml_cache.get("files_sig"):
        _yaml_cache["ts"] = now
        return list(_yaml_cache.get("items") or []), {
            "yaml_dir": str(YAML_PRODUCTS_DIR),
            "yaml_loaded": int(_yaml_cache.get("loaded") or 0),
        }

    items: List[Dict[str, Any]] = []
    loaded = 0

    for p in sorted(paths):
        try:
            text = Path(p).read_text(encoding="utf-8")
        except Exception:
            try:
                text = Path(p).read_text(encoding="utf-8-sig")
            except Exception:
                continue

        try:
            obj = yaml.safe_load(text)
        except Exception:
            continue

        rows = _flatten_yaml_obj(obj)
        if not rows:
            continue

        loaded += 1
        for row in rows:
            for it in _expand_excel_import_row(row, source_name=Path(p).name):
                # 必须要有 brand/model/size 的基本信息
                if not it.get("brand") or not it.get("model") or not it.get("size_inch"):
                    continue
                items.append(it)

    _yaml_cache.update({"ts": now, "files_sig": sig, "items": items, "loaded": loaded})
    return list(items), {"yaml_dir": str(YAML_PRODUCTS_DIR), "yaml_loaded": loaded}


# =========================
# sqlite fallback（可选）
# =========================
def _sqlite_find_table_and_cols(conn: sqlite3.Connection) -> Tuple[Optional[str], Dict[str, str]]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall() if r and r[0]]

    prefer = ["tv", "tvs", "tv_models", "models", "products"]
    tables_sorted = sorted(tables, key=lambda x: (0 if x in prefer else 1, x))

    def cols_of(t: str) -> List[str]:
        try:
            cur.execute(f"PRAGMA table_info({t})")
            return [r[1] for r in cur.fetchall() if r and len(r) > 1]
        except Exception:
            return []

    brand_keys = ["brand", "品牌"]
    model_keys = ["model", "型号", "name", "机型"]
    size_keys = ["size_inch", "size", "尺寸", "inch"]
    price_keys = ["price_cny", "price", "价格", "street_rmb"]
    launch_keys = ["launch_date", "release_date", "首发", "发布时间", "publish_date"]

    for t in tables_sorted:
        cols = cols_of(t)
        if not cols:
            continue

        def pick(keys: List[str]) -> Optional[str]:
            for k in keys:
                if k in cols:
                    return k
            return None

        bm = pick(brand_keys)
        mm = pick(model_keys)
        sm = pick(size_keys)
        pm = pick(price_keys)
        if bm and mm and sm and pm:
            lm = pick(launch_keys)
            return t, {"brand": bm, "model": mm, "size": sm, "price": pm, "launch": lm or ""}

    return None, {}


def sqlite_query_products(size: int, brand: Optional[str] = None, budget: Optional[int] = None) -> List[Dict[str, Any]]:
    if not USE_SQLITE_FALLBACK:
        return []
    if not SQLITE_DB.exists():
        return []

    conn = sqlite3.connect(str(SQLITE_DB))
    conn.row_factory = sqlite3.Row
    try:
        tname, cmap = _sqlite_find_table_and_cols(conn)
        if not tname:
            return []

        lo, hi = size - 5, size + 5

        where = [f"{cmap['size']} BETWEEN ? AND ?"]
        args: List[Any] = [int(lo), int(hi)]

        if brand:
            where.append(f"{cmap['brand']} = ?")
            args.append(brand)
        if budget is not None:
            where.append(f"{cmap['price']} <= ?")
            args.append(int(budget))

        where_sql = " WHERE " + " AND ".join(where)
        sql = f"SELECT * FROM {tname}{where_sql} ORDER BY {cmap['price']} ASC LIMIT 500"
        rows = conn.execute(sql, args).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            b = _normalize_brand(d.get(cmap["brand"]))
            m = d.get(cmap["model"])
            s = _safe_int(d.get(cmap["size"]))
            p = _safe_int(d.get(cmap["price"]))
            launch = d.get(cmap["launch"]) if cmap.get("launch") else None

            out.append(
                {
                    "brand": b,
                    "model": m,
                    "size_inch": s,
                    "street_rmb": p,
                    "launch_date": _normalize_date_like(launch) if launch else None,
                    "input_lag_ms_60hz": d.get("input_lag_ms_60hz"),
                    "hdmi_2_1_ports": d.get("hdmi_2_1_ports"),
                    "allm": d.get("allm"),
                    "vrr": d.get("vrr"),
                    "peak_brightness_nits": d.get("peak_brightness_nits"),
                    "local_dimming_zones": d.get("local_dimming_zones"),
                    "_source": f"sqlite:{SQLITE_DB.name}",
                }
            )
        return out
    finally:
        conn.close()


# =========================
# profiles & scoring
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
    vals = []
    for c in cands:
        v = c.get(key)
        if v is None:
            continue
        try:
            vals.append(float(v))
        except Exception:
            continue
    if not vals:
        return 0.0, 1.0
    return float(min(vals)), float(max(vals))


def _merge_products(yaml_items: List[Dict[str, Any]], sqlite_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def norm_key(it: Dict[str, Any]) -> str:
        b = str(it.get("brand") or "").strip().lower()
        m = str(it.get("model") or "").strip().lower().replace(" ", "")
        s = str(it.get("size_inch") or "").strip()
        return f"{b}::{m}::{s}"

    mp: Dict[str, Dict[str, Any]] = {}
    for it in sqlite_items:
        k = norm_key(it)
        if k and k not in mp:
            mp[k] = it
    for it in yaml_items:
        k = norm_key(it)
        if not k:
            continue
        mp[k] = it  # YAML 覆盖 sqlite
    return list(mp.values())


def all_by_size(size: int, brand: Optional[str] = None) -> List[Dict[str, Any]]:
    yaml_items, _ = load_all_yaml_products()

    lo, hi = size - 5, size + 5
    out_yaml = [
        r
        for r in yaml_items
        if isinstance(r.get("size_inch"), int)
        and lo <= int(r["size_inch"]) <= hi
        and (brand is None or r.get("brand") == _normalize_brand(brand))
    ]

    out_sqlite = sqlite_query_products(size=size, brand=_normalize_brand(brand) if brand else None) if USE_SQLITE_FALLBACK else []
    return _merge_products(out_yaml, out_sqlite)


def apply_filters(
    cands: List[Dict[str, Any]],
    brand: Optional[str] = None,
    budget: Optional[int] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    nb = _normalize_brand(brand) if brand else None

    for tv in cands:
        if nb:
            if _normalize_brand(tv.get("brand")) != nb:
                continue

        if budget is not None:
            price = parse_price(tv.get("street_rmb"))
            if price is None:
                # 没价格就无法做“预算≤”过滤：直接剔除（保持你之前逻辑一致）
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

        tv2 = dict(tv)
        tv2["_score"] = score
        tv2["_year"] = launch_year_from_date(tv.get("launch_date"))
        tv2["_parts"] = parts
        ranked.append(tv2)

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
        lines.append("你可以：放宽品牌 / 提高预算 / 换尺寸。")
        return "\n".join(lines)

    lines.append("")
    lines.append("Top 3 推荐（过滤后候选集内排序）")
    lines.append("-" * 70)

    for i, tv in enumerate(top3, 1):
        warn = ""
        if tv.get("peak_brightness_nits") and isinstance(tv["peak_brightness_nits"], (int, float)) and tv["peak_brightness_nits"] > 6000:
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

    if ENABLE_LLM and (not HAS_LLM):
        reason = []
        if not OPENAI_API_KEY:
            reason.append("未设置 OPENAI_API_KEY")
        if not HAS_OPENAI_PKG:
            reason.append("未安装 openai 包")
        why = "；".join(reason) if reason else "LLM 未满足启用条件"
        return base_text + f"\n\n⚠️ ENABLE_LLM=1，但 {why}，已使用规则引擎结果。"

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

    if not PROFILES.exists():
        raise SystemExit(f"profiles.yaml not found: {PROFILES}")

    # YAML 目录检查：不存在也不直接退出（因为你可能想只走 sqlite fallback）
    if not YAML_PRODUCTS_DIR.exists():
        print(f"⚠️ YAML dir not found: {YAML_PRODUCTS_DIR}", file=sys.stderr)

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