# -*- coding: utf-8 -*-
"""
Step 1: 抓取 TVLabs 品牌列表
- 访问 https://tvlabs.cn/tv
- 自动等待页面加载
- 解析品牌入口（brand_path / brand_url / brand_name）
- 输出 brands.yaml

依赖：
  pip install playwright pyyaml
  playwright install
"""

import re
import os
import yaml
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

BASE_URL = "https://tvlabs.cn/tv"
OUT_FILE = "brands.yaml"

def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def is_brand_url(href: str) -> bool:
    """
    TVLabs 品牌页通常长这样：
      /tv/TCL
      https://tvlabs.cn/tv/TCL
    过滤掉 /tv 本身、以及带 query 的杂项。
    """
    if not href:
        return False
    href = href.strip()

    # 统一成 path 判断
    try:
        u = urlparse(href if "://" in href else urljoin(BASE_URL, href))
        path = u.path or ""
    except Exception:
        return False

    # 需要形如 /tv/<something>
    if not path.startswith("/tv/"):
        return False

    # /tv/ 后面必须有非空路径段
    tail = path[len("/tv/"):]
    if not tail or tail == "/":
        return False

    # 排除一些明显不是品牌的情况（如果未来页面结构变了，可再加规则）
    # 例如 /tv?xxx 这种（这里 path 不会是 /tv/<...>，所以基本进不来）
    return True

def extract_brand_path(brand_url: str) -> str:
    u = urlparse(brand_url)
    path = u.path  # /tv/TCL
    tail = path.split("/tv/", 1)[-1]
    tail = tail.strip("/")
    # 保险：只取第一段
    return tail.split("/")[0] if tail else ""

def scrape_brands() -> list[dict]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)

        # 有些站点需要一点时间渲染
        page.wait_for_timeout(1200)

        # 尝试等待页面出现一些品牌链接
        try:
            page.wait_for_function(
                """() => {
                    const as = Array.from(document.querySelectorAll('a[href]'));
                    return as.some(a => (a.getAttribute('href') || '').includes('/tv/'));
                }""",
                timeout=15000,
            )
        except PWTimeoutError:
            pass  # 不阻塞，后面照样解析

        # 抓取所有 a[href]，筛出品牌链接
        anchors = page.eval_on_selector_all(
            "a[href]",
            """(els) => els.map(a => ({
                href: a.getAttribute('href'),
                text: a.innerText || a.textContent || ''
            }))"""
        )

        browser.close()

    # 过滤与去重
    seen = set()
    brands = []
    for a in anchors:
        href = (a.get("href") or "").strip()
        if not is_brand_url(href):
            continue

        full_url = href if "://" in href else urljoin(BASE_URL, href)
        brand_path = extract_brand_path(full_url)
        if not brand_path:
            continue

        key = brand_path.upper()
        if key in seen:
            continue
        seen.add(key)

        brand_name = norm_text(a.get("text") or "")
        # 有些 a 的 text 可能是空（例如卡片区域），先允许为空，后续可补
        brands.append({
            "brand_path": brand_path,
            "brand_url": full_url,
            "brand_name": brand_name or None,
        })

    # 排序：按 brand_path
    brands.sort(key=lambda x: (x["brand_path"] or "").lower())
    return brands

def main():
    brands = scrape_brands()

    out = {
        "source": BASE_URL,
        "brand_count": len(brands),
        "brands": brands,
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(out, f, allow_unicode=True, sort_keys=False)

    print(f"[OK] brand_count={len(brands)} -> {os.path.abspath(OUT_FILE)}")
    # 方便你快速确认
    for i, b in enumerate(brands[:30], 1):
        print(f"{i:02d}. {b['brand_path']:>12} | name={b['brand_name']} | {b['brand_url']}")

if __name__ == "__main__":
    main()
