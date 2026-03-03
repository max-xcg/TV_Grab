# -*- coding: utf-8 -*-
"""
Step 2 (2026 COUNT + SAVE) - Enhanced (case-insensitive + fallback)

改进：
1) 全量加载：滚动 + 点击“加载更多/更多/查看更多”等按钮
2) 抽取优先用“详情链接锚点”，并做大小写不敏感匹配（解决 hisense/Casarte/Skyworth 等为 0）
3) 若锚点抽取失败（links_extracted==0），自动 fallback 到“文本包含首发于”的旧抽取逻辑（保证不再全 0）
4) 输出两种统计：
   - sku_count_2026（卡片/SKU数）
   - series_count_2026（系列数：对 product_name 做去尺寸归一化后去重）
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BRANDS_YAML = os.path.join(BASE_DIR, "brands.yaml")

OUT_ROOT = os.path.join(BASE_DIR, "2026_year")
TARGET_YEAR = 2026


# =========================
# utils
# =========================
def sha1(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8", "ignore")).hexdigest()[:10]

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def brand_path_from_url(brand_url: str) -> str:
    p = urlparse(brand_url).path
    return p.split("/tv/")[-1].strip("/")

def norm_series_name(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r"\s+", " ", s)

    # 去掉开头尺寸：75/85/98 等（含“英寸/吋/引号”）
    s = re.sub(r"^(\d{2,3})\s*(英寸|吋|\"|”)?\s*", "", s, flags=re.I)

    # 去掉“品牌 + 尺寸”形式：TCL 75C12L / 海信 85E8S / 华为 65V6
    s = re.sub(r"^([A-Za-z]+|[\u4e00-\u9fff]+)\s+(\d{2,3})\s*", r"\1 ", s)

    s = re.sub(r"\s+", " ", s).strip()
    return s


# =========================
# 页面全量加载：滚动 + 点击“加载更多”
# =========================
def auto_load_all(page, max_rounds: int = 60) -> None:
    last_anchor_count = 0
    stable = 0

    for _ in range(max_rounds):
        clicked = page.evaluate("""
        () => {
            const keywords = ['加载更多','加载更多机型','更多','展开更多','查看更多','查看全部','继续加载'];
            const isLike = (el) => {
                const t = (el.innerText || '').trim();
                if (!t) return false;
                return keywords.some(k => t.includes(k));
            };

            const els = Array.from(document.querySelectorAll('button, a, div'))
                .filter(el => isLike(el));

            for (const el of els) {
                const r = el.getBoundingClientRect();
                if (r.width < 20 || r.height < 10) continue;
                el.scrollIntoView({block:'center'});
                el.click();
                return true;
            }
            return false;
        }
        """)
        if clicked:
            page.wait_for_timeout(900)

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(700)

        anchor_count = page.evaluate("""
        () => {
            const as = Array.from(document.querySelectorAll('a[href]'));
            const hits = as.filter(a => {
                const href = a.getAttribute('href') || '';
                return href.includes('/tv/') && href.split('/').length >= 4;
            });
            return hits.length;
        }
        """)

        if anchor_count == last_anchor_count:
            stable += 1
        else:
            stable = 0
            last_anchor_count = anchor_count

        if stable >= 4:
            break


# =========================
# 抽取 A：优先用详情链接锚点（大小写不敏感）
# =========================
def extract_cards_by_anchor(page, brand_url: str) -> List[Dict[str, Any]]:
    brand_path = brand_path_from_url(brand_url)
    brand_path_l = brand_path.lower()

    js = r"""
    (brandPathLower) => {
        const out = [];
        const toAbs = (href) => {
            if (!href) return null;
            if (href.startsWith('http')) return href;
            if (href.startsWith('/')) return location.origin + href;
            return location.origin + '/' + href;
        };

        const anchors = Array.from(document.querySelectorAll('a[href]'))
            .filter(a => {
                const href = (a.getAttribute('href')||'').toLowerCase();
                return href.includes('/tv/' + brandPathLower + '/');
            });

        for (const a of anchors) {
            const hrefRaw = a.getAttribute('href') || '';
            const detailUrl = toAbs(hrefRaw);
            if (!detailUrl) continue;

            let node = a;
            let best = a;
            for (let i=0; i<5; i++) {
                if (!node || !node.parentElement) break;
                node = node.parentElement;
                const t = (node.innerText || '').trim();
                if (t.length > (best.innerText||'').length) best = node;
            }

            const text = (best.innerText || a.innerText || '').replace(/\s+/g,' ').trim();
            if (!text) continue;

            const nameGuess = (a.innerText || '').replace(/\s+/g,' ').trim();
            const productName = nameGuess || text.split('首发于')[0].trim() || null;

            out.push({
                detail_url: detailUrl,
                product_name: productName,
                card_text: text.slice(0, 2500),
            });
        }
        return out;
    }
    """
    raw = page.evaluate(js, brand_path_l)

    items: List[Dict[str, Any]] = []
    seen_url = set()

    for r in raw:
        url = r.get("detail_url")
        if not url or url in seen_url:
            continue
        seen_url.add(url)

        text = r.get("card_text") or ""
        rel = re.search(r"首发于\s*(\d{4})\s*年\s*(\d{1,2})\s*月", text)
        size = re.search(r"(\d{2,3})\s*(英寸|吋|\"|”)", text)
        price = re.search(r"¥\s*([0-9]{2,})", text.replace(",", ""))

        items.append({
            "brand_path": brand_path,
            "brand_name": None,
            "product_name": r.get("product_name"),
            "release_text": rel.group(0) if rel else None,
            "release_year": int(rel.group(1)) if rel else None,
            "release_month": int(rel.group(2)) if rel else None,
            "size_inch": int(size.group(1)) if size else None,
            "official_price": int(price.group(1)) if price else None,
            "has_jd_buy": ("京东" in text),
            "detail_url": url,
            "source_brand_url": brand_url,
            "scraped_at": now_str(),
            "raw": {"card_text_head": text},
        })

    return items


# =========================
# 抽取 B：fallback（旧逻辑：div innerText 包含“首发于”）
# =========================
def extract_cards_by_text(page, brand_url: str) -> List[Dict[str, Any]]:
    brand_path = brand_path_from_url(brand_url)

    js = r"""
    () => {
        const results = [];
        const divs = Array.from(document.querySelectorAll('div'))
            .filter(d => d.innerText && d.innerText.includes('首发于'));

        for (const d of divs) {
            const text = (d.innerText || '').replace(/\s+/g,' ').trim();
            if (!text) continue;

            const firstLine = text.split('首发于')[0].split('\n')[0].trim();

            const rel = text.match(/首发于\s*(\d{4})\s*年\s*(\d{1,2})\s*月/);
            const size = text.match(/(\d{2,3})\s*(英寸|吋|\"|”)/);
            const price = text.replace(/,/g,'').match(/¥\s*([0-9]{2,})/);

            results.push({
                product_name: firstLine || null,
                release_text: rel ? rel[0] : null,
                release_year: rel ? Number(rel[1]) : null,
                release_month: rel ? Number(rel[2]) : null,
                size_inch: size ? Number(size[1]) : null,
                official_price: price ? Number(price[1]) : null,
                has_jd_buy: text.includes('京东'),
                raw_text: text.slice(0, 2500),
            });
        }
        return results;
    }
    """
    raw_cards = page.evaluate(js)

    seen = set()
    items: List[Dict[str, Any]] = []
    for c in raw_cards:
        key = sha1(f"{c.get('product_name')}|{c.get('release_text')}|{c.get('official_price')}|{c.get('size_inch')}")
        if key in seen:
            continue
        seen.add(key)

        items.append({
            "brand_path": brand_path,
            "brand_name": None,
            "product_name": c.get("product_name"),
            "release_text": c.get("release_text"),
            "release_year": c.get("release_year"),
            "release_month": c.get("release_month"),
            "size_inch": c.get("size_inch"),
            "official_price": c.get("official_price"),
            "has_jd_buy": bool(c.get("has_jd_buy")),
            "detail_url": None,  # fallback 先不强求详情链接
            "source_brand_url": brand_url,
            "scraped_at": now_str(),
            "raw": {"card_text_head": c.get("raw_text")},
        })

    return items


# =========================
# 写入
# =========================
def write_brand(out_brand_dir: str, items_2026: List[Dict[str, Any]]):
    os.makedirs(out_brand_dir, exist_ok=True)

    list_path = os.path.join(out_brand_dir, "cards_2026.yaml")
    with open(list_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            {"target_year": TARGET_YEAR, "count": len(items_2026), "items": items_2026},
            f, allow_unicode=True, sort_keys=False
        )

    items_dir = os.path.join(out_brand_dir, "items")
    os.makedirs(items_dir, exist_ok=True)

    for it in items_2026:
        name = it.get("product_name") or "item"
        series = norm_series_name(name)
        h = sha1(it.get("detail_url") or it.get("raw", {}).get("card_text_head", "") or name)
        fname = re.sub(r'[\\/:*?"<>|]+', "_", f"{series}_{h}.yaml")[:160]
        with open(os.path.join(items_dir, fname), "w", encoding="utf-8") as f:
            yaml.safe_dump(it, f, allow_unicode=True, sort_keys=False)

def write_summary(out_root: str, rows: List[Dict[str, Any]]):
    csv_path = os.path.join(out_root, "summary_2026_counts.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["brand_path", "brand_name", "sku_count_2026", "series_count_2026"])
        w.writeheader()
        w.writerows(rows)

    yml_path = os.path.join(out_root, "summary_2026_counts.yaml")
    with open(yml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"generated_at": now_str(), "target_year": TARGET_YEAR, "rows": rows},
                       f, allow_unicode=True, sort_keys=False)

    return csv_path, yml_path


# =========================
# main
# =========================
def main():
    if not os.path.exists(BRANDS_YAML):
        raise FileNotFoundError(f"brands.yaml 不存在：{BRANDS_YAML}（请先跑 step1_scrape_brands.py 或从旧目录复制）")

    with open(BRANDS_YAML, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    brands = data.get("brands") or []
    if not brands:
        raise RuntimeError("brands.yaml 里没有 brands 数据。")

    os.makedirs(OUT_ROOT, exist_ok=True)
    print(f"[INFO] brands={len(brands)} OUT_ROOT={OUT_ROOT} TARGET_YEAR={TARGET_YEAR}")

    summary_rows: List[Dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_viewport_size({"width": 1400, "height": 900})

        # 提速：阻断大资源
        page.route("**/*", lambda route, req:
            route.abort() if req.resource_type in ("image", "media", "font")
            else route.continue_()
        )

        for i, b in enumerate(brands, 1):
            brand_url = b.get("brand_url")
            brand_path = b.get("brand_path") or (brand_path_from_url(brand_url) if brand_url else "unknown")
            brand_name = b.get("brand_name")

            out_brand_dir = os.path.join(OUT_ROOT, brand_path)
            os.makedirs(out_brand_dir, exist_ok=True)

            print(f"\n========== [{i:02d}/{len(brands)}] {brand_path} ==========")
            print(f"[INFO] url={brand_url}")

            try:
                page.goto(brand_url, wait_until="domcontentloaded", timeout=60000)
            except PWTimeoutError:
                print("[WARN] goto timeout, retry once...")
                page.goto(brand_url, wait_until="domcontentloaded", timeout=60000)

            page.wait_for_timeout(900)
            auto_load_all(page)

            # 先用锚点抽取（大小写不敏感）
            cards = extract_cards_by_anchor(page, brand_url)

            # 若锚点抽取为 0，则 fallback 到文本抽取（防止 hisense/skyworth 之类变 0）
            if len(cards) == 0:
                cards = extract_cards_by_text(page, brand_url)
                print("[WARN] anchor links extracted=0, fallback to text-based extraction")

            items_2026 = [c for c in cards if c.get("release_year") == TARGET_YEAR]

            for it in items_2026:
                it["brand_name"] = brand_name

            sku_count = len(items_2026)
            series_set = set()
            for it in items_2026:
                pn = it.get("product_name") or ""
                sn = norm_series_name(pn)
                if sn:
                    series_set.add(sn)
            series_count = len(series_set)

            write_brand(out_brand_dir, items_2026)

            summary_rows.append({
                "brand_path": brand_path,
                "brand_name": brand_name,
                "sku_count_2026": sku_count,
                "series_count_2026": series_count,
            })

            print(f"[OK] extracted={len(cards)} sku_2026={sku_count} series_2026={series_count} saved={out_brand_dir}")

        browser.close()

    csv_path, yml_path = write_summary(OUT_ROOT, summary_rows)

    sorted_rows = sorted(summary_rows, key=lambda x: (x["series_count_2026"], x["sku_count_2026"]), reverse=True)
    print("\n================ SUMMARY (Top 30, by series) ================")
    for r in sorted_rows[:30]:
        print(f"{r['brand_path']:>12} | series_2026={r['series_count_2026']:>3} | sku_2026={r['sku_count_2026']:>3} | name={r.get('brand_name')}")
    print("=============================================================")
    print(f"[DONE] summary_csv={csv_path}")
    print(f"[DONE] summary_yaml={yml_path}")

if __name__ == "__main__":
    main()