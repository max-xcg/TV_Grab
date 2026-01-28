# -*- coding: utf-8 -*-
import re
from typing import Dict, Any, Optional

def parse_profile(text: str) -> Dict[str, Any]:
    t = text.strip().lower()
    p: Dict[str, Any] = {
        "budget_max_rmb": None,
        "size_inch": None,
        "use_gaming": False,
        "use_movie": False,
        "bright_room": False,
        "need_hdmi21_ports": None,
    }

    m = re.search(r"(\d+)\s*万", t)
    if m:
        p["budget_max_rmb"] = int(m.group(1)) * 10000
    else:
        m = re.search(r"\b(\d{4,6})\b", t)
        if m:
            p["budget_max_rmb"] = int(m.group(1))

    m = re.search(r"(\d{2,3})\s*寸", t)
    if m:
        p["size_inch"] = int(m.group(1))

    if any(k in t for k in ["ps5", "xbox", "xsx", "pc", "游戏"]):
        p["use_gaming"] = True
        p["need_hdmi21_ports"] = 2

    if any(k in t for k in ["电影", "观影", "影院", "暗场"]):
        p["use_movie"] = True

    if any(k in t for k in ["白天", "客厅亮", "采光强", "反光", "窗"]):
        p["bright_room"] = True

    m = re.search(r"hdmi\s*2\.1\s*(\d+)\s*口", t)
    if m:
        p["need_hdmi21_ports"] = int(m.group(1))

    return p
