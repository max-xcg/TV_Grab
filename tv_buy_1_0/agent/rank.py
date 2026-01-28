# -*- coding: utf-8 -*-
import yaml
from typing import Dict, Any, List, Tuple

def load_profiles(path="tv_buy_1_0/config/profiles.yaml") -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["profiles"]

def norm_pos(x, lo, hi):
    if x is None: return 0.0
    if hi == lo: return 0.0
    x = max(lo, min(hi, float(x)))
    return (x - lo) / (hi - lo)

def norm_neg(x, lo, hi):
    return 1.0 - norm_pos(x, lo, hi)

def compute_stats(cands: List[Dict[str, Any]]) -> Dict[str, Tuple[float, float]]:
    keys = [
        "street_rmb",
        "peak_brightness_nits",
        "local_dimming_zones",
        "color_gamut_dci_p3",
        "reflection_specular",
        "uniformity_gray50_max_dev",
        "hdmi_2_1_ports",
        "input_lag_ms_60hz",
    ]
    stats = {}
    for k in keys:
        vals = [float(x[k]) for x in cands if x.get(k) is not None]
        stats[k] = (min(vals), max(vals)) if vals else (0.0, 1.0)
    return stats

def score_one(tv: Dict[str, Any], weights: Dict[str, float], stats: Dict[str, Tuple[float, float]]):
    parts = {}
    total = 0.0

    for k, w in weights.items():
        if k == "price_value":
            lo, hi = stats["street_rmb"]
            s = norm_neg(tv.get("street_rmb"), lo, hi)
        elif k in ("reflection_specular", "uniformity_gray50_max_dev", "input_lag_ms_60hz"):
            lo, hi = stats.get(k, (0.0, 1.0))
            s = norm_neg(tv.get(k), lo, hi)
        elif k in ("vrr", "allm"):
            s = 1.0 if tv.get(k) == 1 else 0.0
        else:
            lo, hi = stats.get(k, (0.0, 1.0))
            s = norm_pos(tv.get(k), lo, hi)

        parts[k] = s * w
        total += parts[k]

    return total, parts

def rank(cands: List[Dict[str, Any]], profile_name: str) -> List[Dict[str, Any]]:
    profiles = load_profiles()
    weights = profiles[profile_name]["weights"]
    stats = compute_stats(cands)

    out = []
    for tv in cands:
        total, parts = score_one(tv, weights, stats)
        t2 = dict(tv)
        t2["_score_total"] = total
        t2["_score_parts"] = parts
        out.append(t2)

    out.sort(key=lambda x: x["_score_total"], reverse=True)
    return out
