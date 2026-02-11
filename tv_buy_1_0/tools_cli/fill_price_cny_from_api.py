# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import csv
import sys
import time
import json
import argparse
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple

import requests

# ----------------------------
# YAML loader/saver (prefer ruamel)
# ----------------------------
YAML_LIB = "pyyaml"
_ruamel_yaml = None
try:
    from ruamel.yaml import YAML  # type: ignore
    _ruamel_yaml = YAML()
    _ruamel_yaml.preserve_quotes = True
    _ruamel_yaml.width = 4096
    YAML_LIB = "ruamel"
except Exception:
    _ruamel_yaml = None

try:
    import yaml as pyyaml  # type: ignore
except Exception:
    pyyaml = None


def load_yaml(path: Path) -> Dict[str, Any]:
    if YAML_LIB == "ruamel" and _ruamel_yaml is not None:
        with path.open("r", encoding="utf-8") as f:
            data = _ruamel_yaml.load(f) or {}
        return data if isinstance(data, dict) else {}
    if pyyaml is None:
        raise RuntimeError("缺少 ruamel.yaml / pyyaml 任一库，无法读写 YAML。")
    with path.open("r", encoding="utf-8") as f:
        data = pyyaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def dump_yaml(path: Path, data: Dict[str, Any]) -> None:
    if YAML_LIB == "ruamel" and _ruamel_yaml is not None:
        with path.open("w", encoding="utf-8") as f:
            _ruamel_yaml.dump(data, f)
        return
    if pyyaml is None:
        raise RuntimeError("缺少 ruamel.yaml / pyyaml 任一库，无法写 YAML。")
    with path.open("w", encoding="utf-8") as f:
        pyyaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


# ----------------------------
# HTTP helpers
# ----------------------------
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    "Connection": "close",
}


def http_get_text(url: str, timeout: int, retries: int, sleep_s: float) -> Tuple[Optional[str], Optional[str]]:
    last_err = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text, None
        except Exception as e:
            last_err = str(e)
            if i < retries:
                time.sleep(sleep_s)
    return None, last_err


# ----------------------------
# Extractors
# ----------------------------
SKU_PATTERNS = [
    r"item\.jd\.com/(\d+)\.html",
    r"skuId[\"']?\s*[:=]\s*[\"']?(\d{6,20})",
    r"skuid[\"']?\s*[:=]\s*[\"']?(\d{6,20})",
    r"sku[\"']?\s*[:=]\s*[\"']?(\d{6,20})",
    r"data-sku[\"']\s*=\s*[\"'](\d{6,20})[\"']",
]


def extract_jd_sku_from_html(html: str) -> Optional[str]:
    for pat in SKU_PATTERNS:
        m = re.search(pat, html or "", flags=re.IGNORECASE)
        if m and m.group(1).isdigit():
            return m.group(1)
    return None


def extract_price_cny_from_tvlabs_html(html: str) -> Optional[int]:
    candidates: List[int] = []
    for m in re.finditer(r"[￥¥]\s*([0-9]{3,6})", html or ""):
        v = int(m.group(1))
        if 500 <= v <= 200000:
            candidates.append(v)
    if not candidates:
        for m in re.finditer(r"([0-9]{3,6})\s*元", html or ""):
            v = int(m.group(1))
            if 500 <= v <= 200000:
                candidates.append(v)
    return min(candidates) if candidates else None


def jd_price_api(sku: str, timeout: int, retries: int, sleep_s: float) -> Tuple[Optional[int], Optional[str]]:
    url = f"https://p.3.cn/prices/mgets?skuIds=J_{sku}&type=1"
    last_err = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list) and data:
                obj = data[0]
                for key in ("p", "op", "m"):
                    try:
                        f = float(obj.get(key))
                        if f > 0:
                            return int(round(f)), None
                    except Exception:
                        pass
            return None, "jd_price_parse_failed"
        except Exception as e:
            last_err = str(e)
            if i < retries:
                time.sleep(sleep_s)
    return None, last_err or "jd_price_failed"


# ----------------------------
# Main
# ----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rewrite", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=18)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--sleep", type=float, default=0.6)
    ap.add_argument("--tvlabs_only", type=int, default=0)
    ap.add_argument("--json", action="store_true", help="仅输出 JSON 汇总（无日志）")
    args = ap.parse_args()

    base_dir = Path(__file__).resolve().parents[2]
    scan_roots = [
        base_dir / "out_step3_2025_spec",
        base_dir / "output_all_brands_2026_spec",
    ]
    reports_dir = base_dir / "_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    def emit(msg: str):
        if not args.json:
            print(msg)

    yaml_files = [p for r in scan_roots if r.exists() for p in r.rglob("*.yaml")]

    emit(f"[INFO] YAML_LIB={YAML_LIB}")
    emit(f"[INFO] yaml_files={len(yaml_files)}")

    stat = {
        "updated": 0,
        "skipped_has_price": 0,
        "skipped_no_detail_url": 0,
        "skipped_no_sku": 0,
        "price_failed": 0,
    }

    for yf in yaml_files:
        try:
            doc = load_yaml(yf)
        except Exception:
            stat["price_failed"] += 1
            continue

        meta = doc.setdefault("meta", {})
        detail_url = doc.get("detail_url")
        if not detail_url:
            stat["skipped_no_detail_url"] += 1
            continue

        if isinstance(meta.get("price_cny"), (int, float)) and not args.rewrite:
            stat["skipped_has_price"] += 1
            continue

        html, _ = http_get_text(detail_url, args.timeout, args.retries, args.sleep)
        if not html:
            stat["skipped_no_sku"] += 1
            continue

        sku = extract_jd_sku_from_html(html)
        price = None

        if not args.tvlabs_only and sku:
            price, _ = jd_price_api(sku, args.timeout, args.retries, args.sleep)

        if not price:
            price = extract_price_cny_from_tvlabs_html(html)

        if price:
            meta["price_cny"] = int(price)
            if sku:
                meta["jd_sku"] = sku
            dump_yaml(yf, doc)
            stat["updated"] += 1
        else:
            stat["price_failed"] += 1

    if args.json:
        print(json.dumps({"ok": True, "stats": stat}, ensure_ascii=False))
    else:
        emit("\n========== DONE ==========")
        for k, v in stat.items():
            emit(f"[STAT] {k}={v}")
        emit("==========================\n")


if __name__ == "__main__":
    main()
