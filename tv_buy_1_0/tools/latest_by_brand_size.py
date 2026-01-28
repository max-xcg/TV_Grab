# -*- coding: utf-8 -*-
import sys, sqlite3

DB = "tv_buy_1_0/db/tv.sqlite"

def main():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 75
    lo, hi = target - 5, target + 5

    sql = """
    SELECT *
    FROM tv
    WHERE launch_date IS NOT NULL
      AND size_inch BETWEEN ? AND ?
    ORDER BY
      brand,
      launch_date DESC,
      -- 同品牌同月：更偏向“性价比”，而不是一味选最大尺寸
      street_rmb IS NULL,          -- 有价优先
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

    brands = sorted(best.keys(), key=lambda x: (x or "").lower())
    print(f"target_size≈{target}  brands={len(brands)}")
    print("-" * 90)
    for b in brands:
        tv = best[b]
        ld = tv.get("launch_date") or "未知"
        price = tv.get("street_rmb")
        price_s = f"¥{price}" if price is not None else "¥?"
        pb = tv.get("peak_brightness_nits")
        pb_s = f"{pb}nits" if pb is not None else "?"
        zones = tv.get("local_dimming_zones")
        zones_s = str(zones) if zones is not None else "?"
        warn = []
        if pb is not None and pb > 6000:
            warn.append("亮度口径⚠️")
        if zones is not None and zones > 6000:
            warn.append("分区口径⚠️")
        warn_s = (" [" + " ".join(warn) + "]") if warn else ""

        print(f"{b:10} | {tv.get('model','?'):24} | {tv.get('size_inch','?')}寸 | 首发 {ld} | {price_s} | 亮度 {pb_s} | 分区 {zones_s}{warn_s}")


if __name__ == "__main__":
    main()
