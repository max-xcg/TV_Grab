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

EXCEL_TCL_DIR = TVBUY_ROOT / "data_raw" / "excel_import_tcl_v2"

DEFAULT_DATA_DIRS = [
    # 其他品牌仍走旧库
    REPO_ROOT / "output_all_brands_2026_spec",
    REPO_ROOT / "out_step3_2025_spec",
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


def _scan_yaml_files(data_dirs: List[Path]) -> List[Path]:
    files: List[Path] = []
    for d in data_dirs:
        if not d.exists():
            continue
        files.extend(list(d.rglob("*.yaml")))
        files.extend(list(d.rglob("*.yml")))
    # 保序去重（同路径只留一次）
    uniq: Dict[str, Path] = {}
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
        cur = obj
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
        # Excel 导入目录：默认 TCL
        if "excel_import_tcl_v2" in str(fallback_path).replace("\\", "/").lower():
            brand = "TCL"
        else:
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
            if v and 40 <= v <= 130:
                return v

    # Excel YAML：多尺寸在 variants 里（这里不返回）
    if isinstance(obj.get("variants"), list):
        return None

    for s in _collect_all_strings(obj):
        m = re.search(r"(\d{2,3})\s*(寸|英寸)", s)
        if m:
            v = int(m.group(1))
            if 40 <= v <= 130:
                return v
    return None


def _is_reasonable_price(v: Optional[int]) -> bool:
    return isinstance(v, int) and 500 <= v <= 200000


def _extract_price_from_obj(obj: Dict[str, Any]) -> Optional[int]:
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


def _norm_model_base(model: str, size: int) -> str:
    """
    去重专用：把“尺寸前缀/尺寸后缀/空格/标点”都统一掉
    """
    s = (model or "").strip().lower()
    s = s.replace("英寸", "").replace("寸", "")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[()（）].*?[)）]", "", s)

    size_str = str(int(size))
    if s.startswith(size_str):
        s = s[len(size_str):]

    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def _display_model_for_excel(base_model: str, size: int) -> str:
    """
    Excel 多尺寸：展示为 “98X11L” 这类
    """
    m = (base_model or "").strip()
    if not m:
        return m
    if re.match(r"^\d{2,3}", m):
        return m
    return f"{int(size)}{m}"


# =========================================================
# Main search
# =========================================================
def search(size: int, budget_max: int, brand: Optional[str] = None, region: str = "CN") -> Dict[str, Any]:
    brand_norm = _norm_brand(brand) if brand else None

    # ✅ 关键：TCL 强制只用 Excel 数据（达到“替换旧数据”的效果）
    if brand_norm == "tcl":
        data_dirs = [EXCEL_TCL_DIR]
    else:
        data_dirs = DEFAULT_DATA_DIRS

    all_paths = _scan_yaml_files(data_dirs)
    rows: List[Dict[str, Any]] = []

    for p in all_paths:
        obj = _load_yaml(p)
        if not obj:
            continue

        b, m = _extract_brand_model_from_obj(obj, p)

        if brand_norm and _norm_brand(b) != brand_norm:
            continue

        # 1) Excel YAML: variants 多尺寸
        if isinstance(obj.get("variants"), list):
            for vv in obj["variants"]:
                if not isinstance(vv, dict):
                    continue
                s_in = _safe_int(vv.get("size_inch"))
                if s_in != size:
                    continue

                price = _safe_int(vv.get("price_cny"))
                if price is None:
                    price = _safe_int(vv.get("price_cny_no_subsidy"))

                if price is not None and price > budget_max:
                    continue

                rows.append(
                    {
                        "brand": b,
                        "model": _display_model_for_excel(m, size),
                        "size_inch": s_in,
                        "price": price,
                        "source": str(p.relative_to(REPO_ROOT)).replace("\\", "/"),
                        "_mkey": _norm_model_base(m, size),
                    }
                )
            continue

        # 2) 老 YAML：单尺寸
        s_in = _extract_size_from_obj(obj)
        if s_in != size:
            continue

        price = _extract_price_from_obj(obj)
        if price is not None and price > budget_max:
            continue

        rows.append(
            {
                "brand": b,
                "model": m,
                "size_inch": s_in,
                "price": price,
                "source": str(p.relative_to(REPO_ROOT)).replace("\\", "/"),
                "_mkey": _norm_model_base(m, size),
            }
        )

    # 去重：同 brand + base_model + size 只保留 1 条
    best: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for r in rows:
        key = (_norm_brand(r["brand"]), r["_mkey"], int(r["size_inch"]))
        cur = best.get(key)
        if cur is None:
            best[key] = r
            continue

        rp = r.get("price")
        cp = cur.get("price")
        if isinstance(rp, int) and not isinstance(cp, int):
            best[key] = r
        elif isinstance(rp, int) and isinstance(cp, int) and rp < cp:
            best[key] = r

    deduped = list(best.values())

    # 排序：有价在前，价低优先
    def sort_key(x: Dict[str, Any]):
        price = x.get("price")
        has_price = 0 if isinstance(price, int) else 1
        return (has_price, price if isinstance(price, int) else 10**9, x.get("brand", ""), x.get("model", ""))

    deduped.sort(key=sort_key)

    for r in deduped:
        r.pop("_mkey", None)

    return {
        "filters": {"size": size, "budget_max": budget_max, "brand": brand, "region": region},
        "count": len(deduped),
        "candidates": deduped,
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