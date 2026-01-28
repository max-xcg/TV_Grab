# -*- coding: utf-8 -*-
import sqlite3
from typing import Dict, Any, List

DB_PATH = "tv_buy_1_0/db/tv.sqlite"

def filter_tvs(p: Dict[str, Any], limit: int = 200) -> List[Dict[str, Any]]:
    where = []
    params: List[Any] = []

    if p.get("budget_max_rmb"):
        where.append("street_rmb IS NOT NULL AND street_rmb <= ?")
        params.append(int(p["budget_max_rmb"]))

    if p.get("size_inch"):
        s = int(p["size_inch"])
        where.append("size_inch IS NOT NULL AND size_inch BETWEEN ? AND ?")
        params += [s - 5, s + 5]

    if p.get("need_hdmi21_ports"):
        where.append("hdmi_2_1_ports IS NOT NULL AND hdmi_2_1_ports >= ?")
        params.append(int(p["need_hdmi21_ports"]))

    if p.get("use_gaming"):
        where.append("(vrr = 1 OR allm = 1 OR hdmi_2_1_ports >= 2)")

    sql = "SELECT * FROM tv"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " LIMIT ?"
    params.append(limit)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]
