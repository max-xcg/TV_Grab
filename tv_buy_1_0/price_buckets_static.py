# -*- coding: utf-8 -*-
"""
tv_buy_1_0/price_buckets_static.py

尺寸 → 价格区间 → 人群选择百分比（人工纠偏版）
用途：
- 尺寸选择后的【第二步】价格区间按钮（带百分比）
- 推荐系统：把选中的区间转成 price_min / price_max
"""

from __future__ import annotations

from typing import Dict, List, Optional


PRICE_BUCKETS_VERSION = "jd_tv_size_price_v1_corrected"
CURRENCY = "CNY"

# 写死配置（按你提供的 size_price.yaml 内容）
PRICE_BUCKETS: Dict[int, List[dict]] = {
    43: [
        {"range": "0-1200", "min": 0, "max": 1200, "percent": 22},
        {"range": "1200-1800", "min": 1200, "max": 1800, "percent": 34},
        {"range": "1800-2500", "min": 1800, "max": 2500, "percent": 24},
        {"range": "2500-3500", "min": 2500, "max": 3500, "percent": 14},
        {"range": "3500+", "min": 3500, "max": None, "percent": 6},
    ],
    50: [
        {"range": "0-2200", "min": 0, "max": 2200, "percent": 20},
        {"range": "2200-3000", "min": 2200, "max": 3000, "percent": 32},
        {"range": "3000-4000", "min": 3000, "max": 4000, "percent": 26},
        {"range": "4000-5500", "min": 4000, "max": 5500, "percent": 15},
        {"range": "5500+", "min": 5500, "max": None, "percent": 7},
    ],
    55: [
        {"range": "0-2800", "min": 0, "max": 2800, "percent": 28},
        {"range": "2800-3800", "min": 2800, "max": 3800, "percent": 30},
        {"range": "3800-5000", "min": 3800, "max": 5000, "percent": 24},
        {"range": "5000-7000", "min": 5000, "max": 7000, "percent": 12},
        {"range": "7000+", "min": 7000, "max": None, "percent": 6},
    ],
    65: [
        {"range": "0-3500", "min": 0, "max": 3500, "percent": 18},
        {"range": "3500-5000", "min": 3500, "max": 5000, "percent": 30},
        {"range": "5000-7000", "min": 5000, "max": 7000, "percent": 26},
        {"range": "7000-10000", "min": 7000, "max": 10000, "percent": 16},
        {"range": "10000+", "min": 10000, "max": None, "percent": 10},
    ],
    75: [
        {"range": "0-6000", "min": 0, "max": 6000, "percent": 20},
        {"range": "6000-8500", "min": 6000, "max": 8500, "percent": 30},
        {"range": "8500-12000", "min": 8500, "max": 12000, "percent": 24},
        {"range": "12000-18000", "min": 12000, "max": 18000, "percent": 16},
        {"range": "18000+", "min": 18000, "max": None, "percent": 10},
    ],
    85: [
        {"range": "0-7000", "min": 0, "max": 7000, "percent": 6},
        {"range": "7000-10000", "min": 7000, "max": 10000, "percent": 18},
        {"range": "10000-14000", "min": 10000, "max": 14000, "percent": 30},
        {"range": "14000-20000", "min": 14000, "max": 20000, "percent": 28},
        {"range": "20000+", "min": 20000, "max": None, "percent": 18},
    ],
    98: [
        {"range": "0-7000", "min": 0, "max": 7000, "percent": 6},
        {"range": "7000-18000", "min": 7000, "max": 18000, "percent": 38},
        {"range": "18000-26000", "min": 18000, "max": 26000, "percent": 30},
        {"range": "26000-40000", "min": 26000, "max": 40000, "percent": 18},
        {"range": "40000+", "min": 40000, "max": None, "percent": 8},
    ],
    100: [
        {"range": "0-8000", "min": 0, "max": 8000, "percent": 16},
        {"range": "8000-13000", "min": 8000, "max": 13000, "percent": 28},
        {"range": "13000-18000", "min": 13000, "max": 18000, "percent": 26},
        {"range": "18000-20000", "min": 18000, "max": 20000, "percent": 18},
        {"range": "20000+", "min": 20000, "max": None, "percent": 12},
    ],
    115: [
        {"range": "50000-62000", "min": 50000, "max": 62000, "percent": 22},
        {"range": "62000-72000", "min": 62000, "max": 72000, "percent": 30},
        {"range": "72000-82000", "min": 72000, "max": 82000, "percent": 24},
        {"range": "82000-95000", "min": 82000, "max": 95000, "percent": 16},
        {"range": "95000+", "min": 95000, "max": None, "percent": 8},
    ],
    116: [
        {"range": "50000-62000", "min": 50000, "max": 62000, "percent": 22},
        {"range": "62000-72000", "min": 62000, "max": 72000, "percent": 30},
        {"range": "72000-82000", "min": 72000, "max": 82000, "percent": 24},
        {"range": "82000-95000", "min": 82000, "max": 95000, "percent": 16},
        {"range": "95000+", "min": 95000, "max": None, "percent": 8},
    ],
}


def get_price_buckets_by_size(size: int) -> List[dict]:
    return PRICE_BUCKETS.get(int(size), [])


def get_bucket_by_range(size: int, range_raw: str) -> Optional[dict]:
    rr = (range_raw or "").strip()
    for b in get_price_buckets_by_size(size):
        if str(b.get("range", "")).strip() == rr:
            return b
    return None


def get_meta() -> dict:
    return {
        "price_buckets_version": PRICE_BUCKETS_VERSION,
        "currency": CURRENCY,
        "sizes": sorted(PRICE_BUCKETS.keys()),
    }