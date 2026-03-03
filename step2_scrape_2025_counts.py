# -*- coding: utf-8 -*-
"""
Step 2 (2025 COUNT + SAVE):
- 读取 brands.yaml（Step1 生成）
- 逐个品牌页抓取卡片（只保留 release_year == 2025）
- 输出到: C:\\Users\\admin\\tvlabs_scraper\\TVLabs\\TV_Grab\\2025_year\\<brand>\\
- 生成每个品牌 2025 年机型数量统计：summary_2025_counts.csv / .yaml

依赖：
  pip install playwright pyyaml
  playwright install
"""

from __future__ import annotations

import os
import re
import csv
import yaml
import hashlib
from datetime import datetime
from urllib.parse import urlparse
from typing import Dict, Any, List

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

# =========================
# 配置（按你要求固定路径）
# =========================
BRANDS_YAML = os.path.join(os.path.dirname(__file__), "brands.yaml")

OUT_ROOT = r"C:\Users\admin\tvlabs_scraper\TVLabs\TV_Grab\2025_year"
TARGET_YEAR = 2025

# =========================
# utils
# =========================
def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def sha1(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8", "ignore")).hexdigest()[:10]

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def slugify(text: str) -> str:
    t = norm_space(text).lower()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return t or "item"

def safe_filename(name: str, max_len: int = 120) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip(".")
    if len(name) > max_len:
        name = name[:max_len]
    return name or "item"

def brand_path_from_url(brand_url: str) -> str:
    p = urlparse(brand_url).path
    # /tv/TCL -> TCL
    return p.split("/tv/")[-1].strip("/")

# =========================
# 滚动（快速收敛）
# =========================
def auto_scroll_fast(page, max_rounds=25, stable_rounds=3):
    last = 0
    stable = 0
    for _ in range(max_rounds):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(450)

        cur = page.evaluate("""
            () => {
                const t = document.body.innerText || '';
                return t.includes('首发于') ? t.split('首发于').length : 0;
            }
        """)

        if cur == last:
            stable += 1
        else:
            stable = 0
            last = cur

        if stable >= stable_rounds:
            break

# =========================
# JS 内抽卡片（提速核心）
# =========================
def extract_cards_fast(page, brand_url: str) -> List[Dict[str, Any]]:
    js = r"""
    () => {
        const results = [];
        const divs = Array.from(document.querySelectorAll('div'))
            .filter(d => d.innerText && d.innerText.includes('首发于'));

        for (const d of divs) {
            const text = (d.innerText || '').replace(/\s+/g,' ').trim();
            if (!text) continue;

            // 机型名：尽量取“首发于”前的第一段
            const firstLine = text.split('首发于')[0].split('\n')[0].trim();

            const rel = text.match(/首发于\s*(\d{4})\s*年\s*(\d{1,2})\s*月/);
            const size = text.match(/(\d{2,3})\s*(英寸|吋|\"|”)/);
            const price = text.replace(/,/g,'').match(/¥\s*([0-9]{2,})/);

            const techs = [];
            ['Mini LED','OLED','普通液晶','QLED','QD','量子点','激光','Micro LED']
                .forEach(k => { if (text.includes(k)) techs.push(k); });

            results.push({
                product_name: firstLine || null,
                release_text: rel ? rel[0] : null,
                release_year: rel ? Number(rel[1]) : null,
                release_month: rel ? Number(rel[2]) : null,
                size_inch: size ? Number(size[1]) : null,
                tech_tags: techs.length ? techs : null,
                official_price: price ? Number(price[1]) : null,
                has_jd_buy: text.includes('京东'),
                raw_text: text.slice(0, 2000),
            });
        }
        return results;
    }
    """
    raw_cards = page.evaluate(js)

    brand_path = brand_path_from_url(brand_url)
    seen = set()
    cards: List[Dict[str, Any]] = []

    for c in raw_cards:
        key = sha1(f"{c.get('product_name')}|{c.get('release_text')}|{c.get('official_price')}|{c.get('size_inch')}")
        if key in seen:
            continue
        seen.add(key)

        cards.append({
            "brand_path": brand_path,
            "brand_name": None,
            "product_name": c.get("product_name"),
            "model": None,
            "size_inch": c.get("size_inch"),
            "release_text": c.get("release_text"),
            "release_year": c.get("release_year"),
            "release_month": c.get("release_month"),
            "tech_tags": c.get("tech_tags"),
            "official_price": c.get("official_price"),
            "has_jd_buy": bool(c.get("has_jd_buy")),
            "detail_url": None,  # 这一步先不抓详情页
            "source_brand_url": brand_url,
            "scraped_at": now_str(),
            "raw": {
                "card_text_head": c.get("raw_text"),
            }
        })

    return cards

# =========================
# 写入：每个品牌一个 list.yaml
# =========================
def write_brand_list_yaml(out_brand_dir: str, cards_2025: List[Dict[str, Any]]):
    os.makedirs(out_brand_dir, exist_ok=True)
    out_path = os.path.join(out_brand_dir, "cards_2025.yaml")
    payload = {
        "target_year": TARGET_YEAR,
        "count": len(cards_2025),
        "items": cards_2025,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)

# =========================
# 写入：每个品牌逐条 item.yaml（可选，但我保留，后续兼容你现有 Step3 结构）
# =========================
def write_brand_items_yaml(out_brand_dir: str, cards_2025: List[Dict[str, Any]]):
    items_dir = os.path.join(out_brand_dir, "items")
    os.makedirs(items_dir, exist_ok=True)

    for c in cards_2025:
        name = c.get("product_name") or "item"
        base = slugify(name)
        h = sha1((c.get("raw") or {}).get("card_text_head") or name)
        fname = safe_filename(f"{base}_{h}.yaml")
        path = os.path.join(items_dir, fname)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(c, f, allow_unicode=True, sort_keys=False)

# =========================
# summary 输出
# =========================
def write_summary(out_root: str, summary_rows: List[Dict[str, Any]]):
    # CSV
    csv_path = os.path.join(out_root, "summary_2025_counts.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["brand_path", "brand_name", "count_2025"])
        w.writeheader()
        w.writerows(summary_rows)

    # YAML
    yml_path = os.path.join(out_root, "summary_2025_counts.yaml")
    payload = {
        "generated_at": now_str(),
        "target_year": TARGET_YEAR,
        "brand_count": len(summary_rows),
        "rows": summary_rows,
    }
    with open(yml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)

    return csv_path, yml_path

# =========================
# main
# =========================
def main():
    if not os.path.exists(BRANDS_YAML):
        raise FileNotFoundError(f"brands.yaml 不存在：{BRANDS_YAML}（请先跑 step1_scrape_brands.py）")

    with open(BRANDS_YAML, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    brands = data.get("brands") or []
    if not brands:
        raise RuntimeError("brands.yaml 里没有 brands 数据，请先检查 Step1 是否抓到了品牌列表。")

    os.makedirs(OUT_ROOT, exist_ok=True)
    print(f"[INFO] brands={len(brands)} OUT_ROOT={OUT_ROOT} TARGET_YEAR={TARGET_YEAR}")

    summary_rows: List[Dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_viewport_size({"width": 1400, "height": 900})

        # 阻断重资源提速
        page.route("**/*", lambda route, req:
            route.abort() if req.resource_type in ("image", "media", "font")
            else route.continue_()
        )

        for i, b in enumerate(brands, 1):
            brand_url = b.get("brand_url")
            brand_path = b.get("brand_path") or (brand_path_from_url(brand_url) if brand_url else "unknown")
            brand_name = b.get("brand_name")

            out_brand_dir = os.path.join(OUT_ROOT, brand_path)
            os.makedirs(out_brand_dir, exist_ok=True)  # ✅ 即使 0 个，也先建目录

            print(f"\n========== [{i:02d}/{len(brands)}] {brand_path} ==========")
            print(f"[INFO] url={brand_url}")

            try:
                page.goto(brand_url, wait_until="domcontentloaded", timeout=60000)
            except PWTimeoutError:
                print("[WARN] goto timeout, retry once...")
                page.goto(brand_url, wait_until="domcontentloaded", timeout=60000)

            page.wait_for_timeout(700)
            auto_scroll_fast(page)

            cards = extract_cards_fast(page, brand_url)
            cards_2025 = [c for c in cards if c.get("release_year") == TARGET_YEAR]

            # 回填品牌名
            for c in cards_2025:
                c["brand_name"] = brand_name

            write_brand_list_yaml(out_brand_dir, cards_2025)
            write_brand_items_yaml(out_brand_dir, cards_2025)

            summary_rows.append({
                "brand_path": brand_path,
                "brand_name": brand_name,
                "count_2025": len(cards_2025),
            })

            print(f"[OK] cards_extracted={len(cards)} cards_2025={len(cards_2025)} saved={out_brand_dir}")

        browser.close()

    csv_path, yml_path = write_summary(OUT_ROOT, summary_rows)

    # 控制台展示 Top
    summary_rows_sorted = sorted(summary_rows, key=lambda x: x["count_2025"], reverse=True)
    print("\n================ SUMMARY (Top 30) ================")
    for r in summary_rows_sorted[:30]:
        print(f"{r['brand_path']:>12} | 2025_count={r['count_2025']:>3} | name={r.get('brand_name')}")
    print("==================================================")
    print(f"[DONE] summary_csv={csv_path}")
    print(f"[DONE] summary_yaml={yml_path}")

if __name__ == "__main__":
    main()