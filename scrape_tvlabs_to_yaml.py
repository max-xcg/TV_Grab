# -*- coding: utf-8 -*-
"""
Scrape TVLabs spec page and export to YAML.

Target:
  https://tvlabs.cn/tv/hisense/hisense-85E8S

Features:
- Try requests (fast). If HTML is JS-rendered / empty -> fallback to Playwright (headless).
- Parse table/dl/div rows into key-value pairs.
- Fallback: regex match based on a known label list (Chinese labels) from the page text.
- Export normalized YAML.

Usage:
  python scrape_tvlabs_to_yaml.py --url "https://tvlabs.cn/tv/hisense/hisense-85E8S" --out hisense_85E8S.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Dict, List, Tuple, Optional

import requests
from bs4 import BeautifulSoup

try:
    import yaml  # PyYAML
except Exception as e:
    yaml = None


# -----------------------------
# Labels we care about (based on your pasted spec)
# -----------------------------
KNOWN_LABELS: List[str] = [
    "电视等级",
    "等级分类标准",
    "游戏电视",
    "游戏电视等级分类标准",
    "电视尺寸",
    "4K",
    "高度",
    "长度",
    "含底座高度",
    "裸机厚度",
    "含底座厚度",
    "显示技术",
    "LCD 形式",
    "背光方式",
    "屏幕刷新率",
    "倍频技术",
    "峰值亮度",
    "控光分区",
    "运动补偿 (MEMC)",
    "广色域",
    "抗反射",
    "画质处理芯片",
    "CPU",
    "运行内存",
    "存储空间",
    "HDMI 接口",
    "USB 接口",
    "开机广告",
    "安装第三方安卓 APP",
    "语音助手",
    "扬声器",
    "电源功率",
    "WI-FI",
    "输入延时",
    "ALLM",
    "摄像头",
    "HDR & 音效支持",
    "VRR 支持",
]


def fetch_html_requests(url: str, timeout: int = 20) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text or ""


def fetch_html_playwright(url: str, timeout_ms: int = 45000) -> str:
    """
    Use Playwright to render JS content and return page HTML.
    Requires:
      pip install playwright
      playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise RuntimeError(
            "Playwright not installed. Run:\n"
            "  pip install playwright\n"
            "  playwright install chromium\n"
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        # Optional extra wait (some sites lazy-load)
        page.wait_for_timeout(800)
        html = page.content()
        browser.close()
        return html or ""


def is_probably_js_shell(html: str) -> bool:
    """
    Heuristic: if HTML is extremely short, or lacks any meaningful text.
    """
    if not html:
        return True
    if len(html) < 2000:
        return True
    # Many SPA shells have very little body text
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    if len(text) < 200:
        return True
    return False


def parse_kv_from_tables_and_lists(soup: BeautifulSoup) -> Dict[str, str]:
    """
    Extract key-value pairs from common structures:
    - <table> with 2 columns
    - <dl><dt>k</dt><dd>v</dd>
    - rows of div/spans that look like "k: v"
    """
    kv: Dict[str, str] = {}

    # 1) Tables: find rows with 2 cells
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            cells = [c.get_text(" ", strip=True) for c in cells if c.get_text(strip=True)]
            if len(cells) == 2:
                k, v = cells[0], cells[1]
                if k and v and k not in kv:
                    kv[k] = v

    # 2) DL list
    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        if len(dts) == len(dds) and len(dts) > 0:
            for dt, dd in zip(dts, dds):
                k = dt.get_text(" ", strip=True)
                v = dd.get_text(" ", strip=True)
                if k and v and k not in kv:
                    kv[k] = v

    # 3) Inline patterns: "xxx：yyy" or "xxx: yyy"
    text_candidates = []
    for tag in soup.find_all(["p", "li", "div", "span"]):
        t = tag.get_text(" ", strip=True)
        if t and ("：" in t or ": " in t):
            text_candidates.append(t)

    # Reduce duplicates
    seen = set()
    for t in text_candidates:
        if t in seen:
            continue
        seen.add(t)
        # split only once
        if "：" in t:
            parts = t.split("：", 1)
        else:
            parts = t.split(":", 1)
        if len(parts) != 2:
            continue
        k = parts[0].strip()
        v = parts[1].strip()
        # Avoid super long garbage keys
        if 1 <= len(k) <= 40 and v and k not in kv:
            kv[k] = v

    return kv


def parse_kv_by_known_labels(full_text: str) -> Dict[str, str]:
    """
    Fallback: scan full page text, try to extract a value near each KNOWN_LABEL.
    Strategy:
      - Find occurrence of label
      - Take following window text and cut by next label or line break.
    """
    kv: Dict[str, str] = {}
    # Normalize whitespace
    t = re.sub(r"[ \t]+", " ", full_text)
    t = re.sub(r"\r", "\n", t)
    t = re.sub(r"\n{2,}", "\n", t)

    # Build regex that can detect "label value" patterns
    # We'll search label and then capture up to 120 chars until next label
    # (This is heuristic, but works well for many spec pages.)
    labels_sorted = sorted(KNOWN_LABELS, key=len, reverse=True)
    next_label_alt = "|".join(re.escape(x) for x in labels_sorted if x.strip())
    for label in labels_sorted:
        if not label.strip():
            continue
        # label maybe appears multiple times; pick first useful match
        pattern = re.compile(
            rf"({re.escape(label)})\s*[:：]?\s*(.{0,120}?)\s*(?=({next_label_alt})|\n|$)"
        )
        m = pattern.search(t)
        if not m:
            continue
        val = m.group(2).strip()
        val = re.sub(r"\s{2,}", " ", val)
        if val:
            kv[label] = val

    return kv


def normalize_spec(kv: Dict[str, str], url: str) -> Dict:
    """
    Build structured YAML with some normalization.
    Keep original kv under `raw`.
    """
    def pick(*keys: str) -> Optional[str]:
        for k in keys:
            if k in kv and kv[k].strip():
                return kv[k].strip()
        return None

    out: Dict = {
        "source": {
            "url": url,
        },
        "product": {
            "brand": "海信",
            "model": "85E8S",
        },
        "spec": {
            "电视等级": pick("电视等级"),
            "等级分类标准": pick("等级分类标准"),
            "游戏电视": pick("游戏电视"),
            "游戏电视等级分类标准": pick("游戏电视等级分类标准"),
            "电视尺寸": pick("电视尺寸"),
            "分辨率": "4K" if ("4K" in kv or "4K" in (pick("分辨率") or "")) else pick("分辨率"),
            "尺寸": {
                "高度": pick("高度"),
                "长度": pick("长度"),
                "含底座高度": pick("含底座高度"),
                "裸机厚度": pick("裸机厚度"),
                "含底座厚度": pick("含底座厚度"),
            },
            "显示": {
                "显示技术": pick("显示技术"),
                "LCD 形式": pick("LCD 形式"),
                "背光方式": pick("背光方式"),
                "屏幕刷新率": pick("屏幕刷新率"),
                "倍频技术": pick("倍频技术"),
                "峰值亮度": pick("峰值亮度"),
                "控光分区": pick("控光分区"),
                "运动补偿 (MEMC)": pick("运动补偿 (MEMC)"),
                "广色域": pick("广色域"),
                "抗反射": pick("抗反射"),
                "画质处理芯片": pick("画质处理芯片"),
            },
            "硬件": {
                "CPU": pick("CPU"),
                "运行内存": pick("运行内存"),
                "存储空间": pick("存储空间"),
            },
            "接口": {
                "HDMI 接口": pick("HDMI 接口"),
                "USB 接口": pick("USB 接口"),
            },
            "系统与功能": {
                "开机广告": pick("开机广告"),
                "安装第三方安卓 APP": pick("安装第三方安卓 APP"),
                "语音助手": pick("语音助手"),
                "ALLM": pick("ALLM"),
                "VRR 支持": pick("VRR 支持"),
                "输入延时": pick("输入延时"),
                "摄像头": pick("摄像头"),
            },
            "音响与功耗": {
                "扬声器": pick("扬声器"),
                "电源功率": pick("电源功率"),
            },
            "网络": {
                "WI-FI": pick("WI-FI"),
            },
            "HDR & 音效支持": pick("HDR & 音效支持"),
        },
        "raw": kv,
    }

    # Clean None fields (optional)
    def drop_none(obj):
        if isinstance(obj, dict):
            return {k: drop_none(v) for k, v in obj.items() if v is not None and drop_none(v) != {}}
        return obj

    return drop_none(out)


def dump_yaml(data: Dict) -> str:
    if yaml is None:
        raise RuntimeError("PyYAML not installed. Run: pip install pyyaml")
    return yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        width=120,
        default_flow_style=False,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="TVLabs product URL")
    parser.add_argument("--out", required=True, help="Output YAML file path")
    parser.add_argument("--timeout", type=int, default=20, help="requests timeout seconds")
    args = parser.parse_args()

    url = args.url.strip()

    # 1) Try requests
    html = ""
    try:
        html = fetch_html_requests(url, timeout=args.timeout)
    except Exception as e:
        print(f"[WARN] requests fetch failed: {e}", file=sys.stderr)

    # 2) If looks like JS shell, use Playwright
    if is_probably_js_shell(html):
        print("[INFO] Page seems JS-rendered. Falling back to Playwright...", file=sys.stderr)
        html = fetch_html_playwright(url)

    soup = BeautifulSoup(html, "html.parser")

    # Extract kv from structured DOM
    kv = parse_kv_from_tables_and_lists(soup)

    # Fallback scan using known labels
    full_text = soup.get_text("\n", strip=True)
    kv2 = parse_kv_by_known_labels(full_text)

    # Merge: DOM parsed kv has priority; fill blanks with kv2
    for k, v in kv2.items():
        if k not in kv or not kv[k].strip():
            kv[k] = v

    data = normalize_spec(kv, url)
    yml = dump_yaml(data)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(yml)

    print(f"[OK] Saved YAML: {args.out}")


if __name__ == "__main__":
    main()