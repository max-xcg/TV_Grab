# -*- coding: utf-8 -*-
import sys
import sqlite3
import yaml
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.abspath(os.path.join(BASE_DIR, "..", "db", "tv.sqlite"))
PROFILES = os.path.abspath(os.path.join(BASE_DIR, "..", "config", "profiles.yaml"))



def norm_pos(x, lo, hi):
    if x is None or hi <= lo:
        return 0.0
    x = max(lo, min(hi, float(x)))
    return (x - lo) / (hi - lo)


def norm_neg(x, lo, hi):
    return 1.0 - norm_pos(x, lo, hi)


def load_profile(scene):
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



def latest_by_brand_size(target):
    lo, hi = target - 5, target + 5
    sql = """
    SELECT *
    FROM tv
    WHERE launch_date IS NOT NULL
      AND size_inch BETWEEN ? AND ?
    ORDER BY
      brand,
      launch_date DESC,
      street_rmb IS NULL,
      street_rmb ASC,
      peak_brightness_nits DESC,
      local_dimming_zones DESC
    """
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, (lo, hi)).fetchall()
    conn.close()

    best = {}
    for r in rows:
        b = r["brand"]
        if b not in best:
            best[b] = dict(r)
    return list(best.values())


def minmax(cands, key):
    vals = [c[key] for c in cands if c.get(key) is not None]
    if not vals:
        return 0.0, 1.0
    return float(min(vals)), float(max(vals))


def fmt(x, suffix=""):
    if x is None:
        return "?"
    if isinstance(x, (int, float)) and suffix == "" and x in (0, 1):
        return "有" if int(x) == 1 else "无"
    if isinstance(x, bool) and suffix == "":
        return "有" if x else "无"
    return f"{x}{suffix}"

from datetime import datetime

def months_ago(yyyymm):
    if not yyyymm:
        return None
    s = str(yyyymm).strip()
    # 兼容 YYYY-MM / YYYY-MM-DD
    try:
        y, m = map(int, s.split("-")[:2])
    except:
        return None
    now = datetime.now()
    return (now.year - y) * 12 + (now.month - m)


def main():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 75
    scene = sys.argv[2].lower() if len(sys.argv) > 2 else "bright"

    weights, negative_metrics, boolean_metrics, penalties = load_profile(scene)
    cands = latest_by_brand_size(target)

    conn = sqlite3.connect(DB)

    conn.close()


    stat = {
        "peak_brightness_nits": minmax(cands, "peak_brightness_nits"),
        "local_dimming_zones": minmax(cands, "local_dimming_zones"),
        "street_rmb": minmax(cands, "street_rmb"),
        "input_lag_ms_60hz": minmax(cands, "input_lag_ms_60hz"),
        "reflection_specular": minmax(cands, "reflection_specular"),
        "uniformity_gray50_max_dev": minmax(cands, "uniformity_gray50_max_dev"),
        "color_gamut_dci_p3": minmax(cands, "color_gamut_dci_p3"),
        "hdmi_2_1_ports": minmax(cands, "hdmi_2_1_ports"),
    }

    ranked = []
    for tv in cands:
        score = 0.0
        parts = {}

        for k, w in weights.items():
            x = tv.get(k)

            # boolean 指标：True/1/支持 -> 1.0；False/0/不支持 -> 0.0；None -> None
            if k in boolean_metrics:
                if x is None:
                    x = None
                elif isinstance(x, str):
                    x = 1.0 if x.strip().lower() in ("true", "yes", "y", "1", "支持") else 0.0
                else:
                    x = 1.0 if bool(x) else 0.0
                tv[k] = x  # 写回，供 penalties/打印复用

            lo, hi = stat.get(k, (0.0, 1.0))
            if k in negative_metrics:
                s = norm_neg(tv.get(k), lo, hi)
            else:
                s = norm_pos(tv.get(k), lo, hi)

            parts[k] = s * float(w)
            score += parts[k]

        # ===== 新老代轻惩罚（>12个月打 0.92 折）=====
        age = months_ago(tv.get("launch_date"))
        if age is not None and age > 12:
            score *= 0.92

        # penalties：异常口径 / 缺失字段惩罚
        for pen in penalties:
            m = pen.get("metric")
            op = pen.get("op")
            val = pen.get("value")
            mul = float(pen.get("multiplier", 1.0))

            x = tv.get(m)

            # 支持 is_null / not_null
            if op == "is_null":
                if x is None:
                    score *= mul
                continue
            if op == "not_null":
                if x is not None:
                    score *= mul
                continue

            if x is None:
                continue

            hit = False
            if op == ">" and x > val:
                hit = True
            elif op == ">=" and x >= val:
                hit = True
            elif op == "<" and x < val:
                hit = True
            elif op == "<=" and x <= val:
                hit = True
            elif op == "==" and x == val:
                hit = True

            if hit:
                score *= mul


        tv2 = dict(tv)
        tv2["_score"] = score
        tv2["_parts"] = parts
        ranked.append(tv2)

    ranked.sort(key=lambda x: x["_score"], reverse=True)

    print(f"target≈{target}寸 | scene={scene} | candidates={len(ranked)}")
    print("-" * 90)

    for i, tv in enumerate(ranked[:3], 1):
        warn = ""
        if tv.get("peak_brightness_nits") and tv["peak_brightness_nits"] > 6000:
            warn = " ⚠️亮度口径偏激进"
        print(
            f"{i}. {tv.get('brand')} {tv.get('model')} {tv.get('size_inch')}寸 | "
            f"首发 {tv.get('launch_date')} | ¥{tv.get('street_rmb')} | "
            f"延迟 {fmt(tv.get('input_lag_ms_60hz'), 'ms')} | "
            f"HDMI2.1 {fmt(tv.get('hdmi_2_1_ports'), '口')} | "
            f"VRR {fmt(tv.get('vrr'))} | ALLM {fmt(tv.get('allm'))} | "
            f"亮度 {tv.get('peak_brightness_nits')}nits | "
            f"分区 {tv.get('local_dimming_zones')}{warn}"
        )

        top_parts = sorted(
            tv["_parts"].items(), key=lambda kv: kv[1], reverse=True
        )[:3]
        for k, v in top_parts:
            raw = tv.get(k)
            print(f"   - {k}: {raw}")

if __name__ == "__main__":
    main()
