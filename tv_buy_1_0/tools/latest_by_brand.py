# -*- coding: utf-8 -*-
import sqlite3
from collections import defaultdict

DB = "tv_buy_1_0/db/tv.sqlite"

SQL = """
SELECT *
FROM tv
WHERE launch_date IS NOT NULL
ORDER BY brand, launch_date DESC, size_inch DESC, peak_brightness_nits DESC, street_rmb ASC
"""

def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(SQL).fetchall()
    conn.close()

    best = {}
    for r in rows:
        b = r["brand"]
        if b not in best:
            best[b] = dict(r)

    # 输出
    brands = sorted(best.keys(), key=lambda x: (x or "").lower())
    print(f"brands={len(brands)}  (one best model per brand)")
    print("-" * 80)
    for b in brands:
        tv = best[b]
        ld = tv.get("launch_date") or "未知"
        price = tv.get("street_rmb")
        price_s = f"¥{price}" if price is not None else "¥?"
        pb = tv.get("peak_brightness_nits")
        pb_s = f"{pb}nits" if pb is not None else "?"
        zones = tv.get("local_dimming_zones")
        zones_s = str(zones) if zones is not None else "?"
        print(f"{b:10} | {tv.get('model','?'):20} | {tv.get('size_inch','?')}寸 | 首发 {ld} | {price_s} | 亮度 {pb_s} | 分区 {zones_s}")

if __name__ == "__main__":
    main()
