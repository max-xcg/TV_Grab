# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # pip install pyyaml
except Exception:
    yaml = None


TVBUY_ROOT = Path(__file__).resolve().parents[1]  # => tv_buy_1_0/
TCL_EXCEL_DIR = TVBUY_ROOT / "data_raw" / "excel_import_tcl_v2"


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


def _load_yaml(p: Path) -> Optional[Dict[str, Any]]:
    if yaml is None:
        return None
    try:
        obj = yaml.safe_load(p.read_text(encoding="utf-8", errors="replace"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _display_model(base_model: str, size: int) -> str:
    """
    Excel 多尺寸：展示为 '98X11L' / '75Q10M' 等，和你 tv_search.py 保持一致
    """
    m = (base_model or "").strip()
    if not m:
        return m
    if re.match(r"^\d{2,3}", m):
        return m
    return f"{int(size)}{m}"


def iter_tcl_excel_candidates() -> List[Dict[str, Any]]:
    """
    读取 excel_import_tcl_v2 下所有 TCL 型号 YAML
    输出为“按尺寸展开”的候选列表：
      {
        brand, model, size_inch, price_cny, price_cny_no_subsidy,
        first_release, positioning, display_tech, panel, picture_chip,
        peak_brightness_nits, dimming_zones,
        source
      }
    """
    out: List[Dict[str, Any]] = []
    if not TCL_EXCEL_DIR.exists():
        return out

    for p in sorted(TCL_EXCEL_DIR.glob("*.yaml")):
        obj = _load_yaml(p)
        if not obj:
            continue

        brand = str(obj.get("brand") or "TCL").strip() or "TCL"
        if brand.strip().lower() != "tcl":
            continue

        base_model = str(obj.get("model") or "").strip()
        first_release = obj.get("first_release")
        positioning = obj.get("positioning")
        display_tech = obj.get("display_tech")
        panel = obj.get("panel")
        picture_chip = obj.get("picture_chip")

        spec = obj.get("spec") if isinstance(obj.get("spec"), dict) else {}
        variants = obj.get("variants") if isinstance(obj.get("variants"), list) else []

        for v in variants:
            if not isinstance(v, dict):
                continue
            size_inch = _safe_int(v.get("size_inch"))
            if size_inch is None:
                continue

            price_cny = _safe_int(v.get("price_cny"))
            price_cny_no_subsidy = _safe_int(v.get("price_cny_no_subsidy"))

            peak_brightness_nits = _safe_int(v.get("peak_brightness_nits"))
            dimming_zones = _safe_int(v.get("dimming_zones"))

            out.append(
                {
                    "brand": "TCL",
                    "model": _display_model(base_model, size_inch),
                    "size_inch": size_inch,
                    "price_cny": price_cny,
                    "price_cny_no_subsidy": price_cny_no_subsidy,
                    "first_release": first_release,
                    "positioning": positioning,
                    "display_tech": display_tech,
                    "panel": panel,
                    "picture_chip": picture_chip,
                    "peak_brightness_nits": peak_brightness_nits,
                    "dimming_zones": dimming_zones,
                    "spec": spec,
                    "source": f"excel:{p.name}",
                    "_path": str(p),
                }
            )

    return out


def main():
    xs = iter_tcl_excel_candidates()
    print(f"count={len(xs)}")
    print("first5:")
    for r in xs[:5]:
        print(r["model"], r["size_inch"], r.get("price_cny"), r["source"])


if __name__ == "__main__":
    main()