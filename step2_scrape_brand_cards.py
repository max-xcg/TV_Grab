# -*- coding: utf-8 -*-
"""
Step 2 (FAST):
逐个品牌页抓取“卡片信息”（raw YAML，极速版）

核心优化：
- JS 内一次性抽卡片（避免 locator + inner_text）
- 快速滚动收敛
- 阻断图片/视频/字体资源
- 输出结构与后续 Step3 完全兼容

依赖：
  pip install playwright pyyaml
  playwright install
"""

import os
import re
import yaml
import hashlib
from datetime import datetime
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

# =========================
# 配置
# =========================
# BRANDS_YAML = "brands.yaml"
# OUT_DIR = "out_raw_cards"
BRANDS_YAML = "brands.yaml"
OUT_DIR = "out_raw_cards_2025"
TARGET_YEAR = 2025   # ✅ 只抓这一年的

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

def safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip(".")
    if len(name) > max_len:
        name = name[:max_len]
    return name or "item"

# =========================
# 滚动（快速版）
# =========================
def auto_scroll_fast(page, max_rounds=25, stable_rounds=3):
    last = 0
    stable = 0
    for _ in range(max_rounds):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)

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
# JS 内抽卡片（核心提速）
# =========================
def extract_cards_fast(page, brand_url: str):
    js = r"""
    () => {
        const results = [];
        const divs = Array.from(document.querySelectorAll('div'))
            .filter(d => d.innerText && d.innerText.includes('首发于'));

        for (const d of divs) {
            const text = d.innerText.replace(/\s+/g,' ').trim();
            if (!text) continue;

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
                raw_text: text.slice(0, 1200),
            });
        }
        return results;
    }
    """
    raw_cards = page.evaluate(js)

    brand_path = urlparse(brand_url).path.split("/tv/")[-1].strip("/")

    seen = set()
    cards = []

    for c in raw_cards:
        key = sha1(f"{c.get('product_name')}|{c.get('release_text')}|{c.get('official_price')}")
        if key in seen:
            continue
        seen.add(key)

        cards.append({
            "brand_path": brand_path,
            "brand_name": None,
            "product_name": c["product_name"],
            "model": None,
            "size_inch": c["size_inch"],
            "release_text": c["release_text"],
            "release_year": c["release_year"],
            "release_month": c["release_month"],
            "tech_tags": c["tech_tags"],
            "official_price": c["official_price"],
            "has_jd_buy": bool(c["has_jd_buy"]),
            "detail_url": None,
            "source_brand_url": brand_url,
            "scraped_at": now_str(),
            "raw": {
                "card_text_head": c["raw_text"],
                "price_text": str(c["official_price"]) if c["official_price"] else None,
            }
        })

    return cards

# =========================
# 写 YAML
# =========================
def write_card_yaml(out_dir: str, card: dict):
    brand = card["brand_path"] or "unknown"
    name = card["product_name"] or "item"

    brand_dir = os.path.join(out_dir, brand)
    os.makedirs(brand_dir, exist_ok=True)

    base = slugify(name)
    h = sha1(card["raw"]["card_text_head"] or name)
    fname = safe_filename(f"{base}_{h}.yaml")

    path = os.path.join(brand_dir, fname)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(card, f, allow_unicode=True, sort_keys=False)

# =========================
# main
# =========================
def main():
    if not os.path.exists(BRANDS_YAML):
        raise FileNotFoundError("brands.yaml 不存在，请先跑 Step1")

    with open(BRANDS_YAML, "r", encoding="utf-8") as f:
        brands = yaml.safe_load(f)["brands"]

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"[INFO] brands={len(brands)} out={os.path.abspath(OUT_DIR)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_viewport_size({"width": 1400, "height": 900})

        # 🚀 阻断重资源
        page.route("**/*", lambda route, req:
            route.abort() if req.resource_type in ("image", "media", "font")
            else route.continue_()
        )

        for i, b in enumerate(brands, 1):
            brand_url = b["brand_url"]
            brand_path = b["brand_path"]
            print(f"\n========== [{i:02d}/{len(brands)}] {brand_path} ==========")

            page.goto(brand_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(800)

            auto_scroll_fast(page)

            cards = extract_cards_fast(page, brand_url)
            # ✅ 只保留 2025
            cards_2025 = [c for c in cards if c.get("release_year") == TARGET_YEAR]

            print(f"[INFO] cards_extracted={len(cards)} cards_{TARGET_YEAR}={len(cards_2025)}")

            for c in cards_2025:
                # 可选：把 brands.yaml 里的 brand_name 写回 card，方便你后续展示
                c["brand_name"] = b.get("brand_name")
                write_card_yaml(OUT_DIR, c)

            print(f"[OK] saved -> {os.path.join(OUT_DIR, brand_path)}")

        browser.close()

    print("\n[DONE] Step2 FAST finished.")

if __name__ == "__main__":
    main()
