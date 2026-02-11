# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # pip install pyyaml
except Exception:
    yaml = None


# =========================================================
# Paths
# =========================================================
TVBUY_ROOT = Path(__file__).resolve().parents[1]  # => tv_buy_1_0/
REPO_ROOT = TVBUY_ROOT.parents[0]  # => TV_Grab/

DATA_DIRS = [
    REPO_ROOT / "out_step3_2025_spec",
    REPO_ROOT / "output_all_brands_2026_spec",
]


# =========================================================
# Generic utils
# =========================================================
def _norm_brand(x: str) -> str:
    return (x or "").strip().lower()


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


def _load_yaml(path: Path) -> Optional[Dict[str, Any]]:
    if yaml is None:
        return None
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
        obj = yaml.safe_load(txt)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _scan_yaml_files() -> List[Path]:
    files: List[Path] = []
    for d in DATA_DIRS:
        if not d.exists():
            continue
        files.extend(list(d.rglob("*.yaml")))
        files.extend(list(d.rglob("*.yml")))
    uniq = {}
    for p in files:
        uniq[str(p)] = p
    return list(uniq.values())


# =========================================================
# Recursive walkers
# =========================================================
def _iter_leaf_values(obj: Any, path: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            k_str = str(k)
            new_path = f"{path}.{k_str}" if path else k_str
            yield from _iter_leaf_values(v, new_path)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            new_path = f"{path}[{i}]"
            yield from _iter_leaf_values(v, new_path)
    else:
        yield (path, path.split(".")[-1] if path else "", obj)


def _collect_all_strings(obj: Any) -> List[str]:
    out: List[str] = []
    for _, _, v in _iter_leaf_values(obj):
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


# =========================================================
# Field extractors
# =========================================================
def _extract_brand_model_from_obj(obj: Dict[str, Any], fallback_path: Path) -> Tuple[str, str]:
    brand = ""
    model = ""

    for kpath in [("brand",), ("meta", "brand"), ("basic", "brand")]:
        cur: Any = obj
        ok = True
        for kk in kpath:
            if isinstance(cur, dict) and kk in cur:
                cur = cur[kk]
            else:
                ok = False
                break
        if ok and cur:
            brand = str(cur).strip()
            break

    for kpath in [("model",), ("meta", "model"), ("basic", "model"), ("name",)]:
        cur: Any = obj
        ok = True
        for kk in kpath:
            if isinstance(cur, dict) and kk in cur:
                cur = cur[kk]
            else:
                ok = False
                break
        if ok and cur:
            model = str(cur).strip()
            break

    if not brand:
        brand = fallback_path.parent.name

    if not model:
        stem = fallback_path.stem
        stem = re.sub(r"_spec$", "", stem)
        stem = stem.replace(brand.lower() + "_", "").replace(brand + "_", "")
        model = stem

    return brand, model


def _extract_size_from_obj(obj: Dict[str, Any]) -> Optional[int]:
    keys = [
        ("display", "size_inch"),
        ("display", "size"),
        ("spec", "size_inch"),
        ("spec", "size"),
        ("size_inch",),
        ("size",),
    ]
    for k in keys:
        cur: Any = obj
        ok = True
        for kk in k:
            if isinstance(cur, dict) and kk in cur:
                cur = cur[kk]
            else:
                ok = False
                break
        if ok:
            v = _safe_int(cur)
            if v and 40 <= v <= 120:
                return v

    for s in _collect_all_strings(obj):
        m = re.search(r"(\d{2,3})\s*(寸|英寸)", s)
        if m:
            v = int(m.group(1))
            if 40 <= v <= 120:
                return v
    return None


def _is_reasonable_price(v: Optional[int]) -> bool:
    return isinstance(v, int) and 500 <= v <= 200000


def _extract_price_from_obj(obj: Dict[str, Any]) -> Optional[int]:
    """
    只认“像价格的价格”：
    - 结构化优先：meta.price_cny / price_cny / meta.price / price / jd_price...
    - 合理范围：500 ~ 200000
    - 文本兜底：￥xxxx / xxxx元（同样套范围）
    """
    # 1) 结构化优先（最可靠）
    preferred_paths = [
        ("meta", "price_cny"),
        ("price_cny",),
        ("meta", "price"),
        ("price",),
        ("meta", "jd_price"),
        ("jd_price",),
        ("meta", "current_price"),
        ("current_price",),
        ("meta", "sale_price"),
        ("sale_price",),
    ]
    for kpath in preferred_paths:
        cur: Any = obj
        ok = True
        for kk in kpath:
            if isinstance(cur, dict) and kk in cur:
                cur = cur[kk]
            else:
                ok = False
                break
        if ok:
            v = _safe_int(cur)
            if _is_reasonable_price(v):
                return v

    # 2) 递归扫“像价格字段名”的字段，但严格限制范围，避免 100Hz/120Hz 被误判
    key_patterns = [
        "price", "售价", "参考价", "京东价", "jd", "到手价", "现价", "首发价", "官方价"
    ]
    candidates: List[int] = []

    for path, key, val in _iter_leaf_values(obj):
        key_low = str(key).lower()
        path_low = str(path).lower()
        if not any(kp.lower() in key_low or kp.lower() in path_low for kp in key_patterns):
            continue

        v = _safe_int(val)
        if _is_reasonable_price(v):
            candidates.append(v)

        if isinstance(val, str) and val.strip():
            for mm in re.finditer(r"(?:￥|¥)\s*([1-9]\d{2,6})", val):
                vv = _safe_int(mm.group(1))
                if _is_reasonable_price(vv):
                    candidates.append(vv)
            for mm in re.finditer(r"([1-9]\d{2,6})\s*元", val):
                vv = _safe_int(mm.group(1))
                if _is_reasonable_price(vv):
                    candidates.append(vv)

    if candidates:
        # 价格字段内出现多个（原价/券后价），取最小更贴近到手
        return min(candidates)

    # 3) 文本兜底：全量字符串找 ￥xxxx 或 xxxx元（同样限制范围）
    all_text = "\n".join(_collect_all_strings(obj))
    m = re.search(r"(?:￥|¥)\s*([1-9]\d{2,6})", all_text)
    if m:
        v = _safe_int(m.group(1))
        if _is_reasonable_price(v):
            return v

    m2 = re.search(r"([1-9]\d{2,6})\s*元", all_text)
    if m2:
        v = _safe_int(m2.group(1))
        if _is_reasonable_price(v):
            return v

    return None


# =========================================================
# Main search
# =========================================================
def search(size: int, budget_max: int, brand: Optional[str] = None, region: str = "CN") -> Dict[str, Any]:
    brand_norm = _norm_brand(brand) if brand else None

    all_paths = _scan_yaml_files()
    rows: List[Dict[str, Any]] = []

    for p in all_paths:
        obj = _load_yaml(p)
        if not obj:
            continue

        b, m = _extract_brand_model_from_obj(obj, p)

        if brand_norm and _norm_brand(b) != brand_norm:
            continue

        s = _extract_size_from_obj(obj)
        if s != size:
            continue

        price = _extract_price_from_obj(obj)
        # 预算筛选：只对“有价格”的做硬过滤；没价格的先保留
        if price is not None and price > budget_max:
            continue

        rows.append(
            {
                "brand": b,
                "model": m,
                "size_inch": s,
                "price": price,
                "source": str(p.relative_to(REPO_ROOT)).replace("\\", "/"),
            }
        )

    # 排序：有价在前，价低优先；无价靠后
    def sort_key(x: Dict[str, Any]):
        price = x.get("price")
        has_price = 0 if isinstance(price, int) else 1
        return (has_price, price if isinstance(price, int) else 10**9, x.get("brand", ""), x.get("model", ""))

    rows.sort(key=sort_key)

    return {
        "filters": {"size": size, "budget_max": budget_max, "brand": brand, "region": region},
        "count": len(rows),
        "candidates": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--budget_max", type=int, required=True)
    ap.add_argument("--brand", type=str, default=None)
    ap.add_argument("--region", type=str, default="CN")
    args = ap.parse_args()

    data = search(size=args.size, budget_max=args.budget_max, brand=args.brand, region=args.region)
    out = {"ok": True, "data": data}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
