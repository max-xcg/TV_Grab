# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from playwright.sync_api import sync_playwright


BASE_URL = "https://tvlabs.cn"


# ----------------------------
# utils
# ----------------------------
def today() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d")


def safe_name(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:120] or "unknown")


def to_float(x: str) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def to_int(x: str) -> Optional[int]:
    try:
        return int(float(x))
    except Exception:
        return None


def deep_set(d: Dict[str, Any], path: str, value: Any) -> None:
    """path like 'display.size_inch'"""
    cur = d
    parts = path.split(".")
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def dump_yaml(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(obj, f, allow_unicode=True, sort_keys=False)


def dump_txt(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        f.write(text)


def append_txt(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(text)


# ----------------------------
# extraction rules (regex over visible text)
# 你后续要更“准”，就继续往这里加规则即可
# ----------------------------
REGEX_RULES: List[Tuple[str, str, Any]] = [
    # 尺寸/分辨率
    (r"(\d{2})\s*英寸", "display.size_inch", lambda m: to_int(m.group(1))),
    (r"\b4K\b|3840\s*[x×]\s*2160", "display.resolution", lambda m: "4k"),
    (r"\b8K\b|7680\s*[x×]\s*4320", "display.resolution", lambda m: "8k"),

    # 峰值亮度
    (r"(?:峰值亮度|峰值)\s*[:：]?\s*([0-9]{3,5})\s*(?:nits|nit|尼特)?", "display.peak_brightness_nits", lambda m: to_int(m.group(1))),

    # 分区
    (r"(?:控光分区|分区)\s*[:：]?\s*([0-9]{2,5})\s*(?:个|区)?", "display.local_dimming_zones", lambda m: to_int(m.group(1))),

    # 色域
    (r"(?:DCI[-\s]?P3)\s*[:：]?\s*([0-9]{1,3}(?:\.[0-9]+)?)\s*%?", "display.color_gamut_dci_p3_pct", lambda m: float(m.group(1))),

    # 刷新率
    (r"(?:原生刷新率|刷新率)\s*[:：]?\s*([0-9]{2,3})\s*Hz", "refresh.native_hz", lambda m: to_int(m.group(1))),
    (r"(?:HSR|动态刷新加速|DLF)\s*(?:最高|至|:|：)?\s*([0-9]{2,3})\s*Hz", "refresh.dlf_max_hz", lambda m: to_int(m.group(1))),

    # MEMC
    (r"\bMEMC\b", "refresh.memc.supported", lambda m: True),

    # HDMI / USB
    (r"HDMI\s*2\.1", "interfaces.hdmi.version", lambda m: "2.1"),
    (r"(?:HDMI)\s*[:：]?\s*([0-9])\s*(?:个|口)?", "interfaces.hdmi.ports", lambda m: to_int(m.group(1))),
    (r"(?:48)\s*Gbps", "interfaces.hdmi.bandwidth_gbps", lambda m: 48),
    (r"USB\s*2\.0\s*[:：]?\s*([0-9])\s*(?:个|口)?", "interfaces.usb.usb_2_0", lambda m: to_int(m.group(1))),
    (r"USB\s*3\.0\s*[:：]?\s*([0-9])\s*(?:个|口)?", "interfaces.usb.usb_3_0", lambda m: to_int(m.group(1))),

    # Wi-Fi
    (r"Wi[-\s]?Fi\s*6\b|wifi\s*6\b", "network.wifi.standard", lambda m: "wifi_6"),
    (r"(?:双频|2\.4G\s*\+\s*5G)", "network.wifi.band", lambda m: "dual_band"),

    # 功率
    (r"(?:最大功率|功率)\s*[:：]?\s*([0-9]{2,4})\s*W", "power.max_power_w", lambda m: to_int(m.group(1))),

    # VESA
    (r"VESA\s*[:：]?\s*([0-9]{3,4})\s*[x×]\s*([0-9]{3,4})", "wall_mount.vesa", lambda m: {"width_mm": float(m.group(1)), "height_mm": float(m.group(2))}),

    # 机身尺寸（不含底座/含底座）
    (r"(?:不含底座|无底座).*?(?:宽|长度)\s*([0-9]{3,4}(?:\.[0-9]+)?)\s*mm", "dimensions.without_stand.width_mm", lambda m: float(m.group(1))),
    (r"(?:不含底座|无底座).*?(?:高)\s*([0-9]{3,4}(?:\.[0-9]+)?)\s*mm", "dimensions.without_stand.height_mm", lambda m: float(m.group(1))),
    (r"(?:不含底座|无底座).*?(?:厚|深)\s*([0-9]{1,3}(?:\.[0-9]+)?)\s*mm", "dimensions.without_stand.depth_mm", lambda m: float(m.group(1))),
    (r"(?:含底座).*?(?:高)\s*([0-9]{3,4}(?:\.[0-9]+)?)\s*mm", "dimensions.with_stand.height_mm", lambda m: float(m.group(1))),
    (r"(?:含底座).*?(?:厚|深)\s*([0-9]{1,4}(?:\.[0-9]+)?)\s*mm", "dimensions.with_stand.depth_mm", lambda m: float(m.group(1))),

    # 游戏特性
    (r"\bALLM\b|自动低延迟模式", "gaming_features.allm", lambda m: True),
    (r"\bVRR\b|可变刷新率", "gaming_features.vrr", lambda m: True),

    # HDR / Audio
    (r"\bDolby\s*Vision\b|杜比视界", "hdr_audio_support.hdr", lambda m: ["dolby_vision"]),
    (r"\bHDR10\+\b|HDR10\s*\+", "hdr_audio_support.hdr", lambda m: ["hdr10_plus"]),
    (r"\bDolby\s*Atmos\b|杜比全景声", "hdr_audio_support.audio_effect", lambda m: ["dolby_atmos"]),
]


def merge_list_field(existing: Any, new_list: List[str]) -> List[str]:
    out = []
    if isinstance(existing, list):
        out.extend(existing)
    out.extend(new_list)
    # 去重保持顺序
    seen = set()
    uniq = []
    for x in out:
        if x in seen:
            continue
        seen.add(x)
        uniq.append(x)
    return uniq


def apply_regex_rules(schema: Dict[str, Any], text: str) -> None:
    for pat, path, fn in REGEX_RULES:
        m = re.search(pat, text, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            continue
        val = fn(m)
        if val is None:
            continue

        # hdr/audio 支持：是 list 需要合并
        if path in ("hdr_audio_support.hdr", "hdr_audio_support.audio_effect"):
            # 先拿旧值
            parts = path.split(".")
            cur = schema
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            old = cur.get(parts[-1])
            cur[parts[-1]] = merge_list_field(old, val if isinstance(val, list) else [str(val)])
        elif path == "wall_mount.vesa" and isinstance(val, dict):
            schema.setdefault("wall_mount", {}).setdefault("vesa", {})
            schema["wall_mount"]["vesa"].update(val)
        else:
            deep_set(schema, path, val)


# ----------------------------
# schema template (按你给的结构)
# ----------------------------
def make_schema(product_id: str, brand: str, model: str, url: str) -> Dict[str, Any]:
    return {
        "product_id": product_id,
        "brand": brand,
        "model": model,
        "category": "tv",
        "positioning": {
            "tier": "unknown",
            "type": "unknown",
            "gaming_grade": "unknown",
        },
        "display": {
            "size_inch": "unknown",
            "resolution": "unknown",
            "technology": "unknown",
            "panel_type": "unknown",
            "backlight_type": "unknown",
            "peak_brightness_nits": "unknown",
            "local_dimming_zones": "unknown",
            "dimming_structure": "unknown",
            "color_gamut_dci_p3_pct": "unknown",
            "quantum_dot": "unknown",
            "anti_reflection": {
                "type": "unknown",
                "reflectance_pct": "unknown",
            },
        },
        "refresh": {
            "native_hz": "unknown",
            "dlf_max_hz": "unknown",
            "memc": {"supported": "unknown", "max_fps": "unknown"},
        },
        "dimensions": {
            "without_stand": {"width_mm": "unknown", "height_mm": "unknown", "depth_mm": "unknown"},
            "with_stand": {"height_mm": "unknown", "depth_mm": "unknown", "stand_distance_mm": "unknown"},
        },
        "wall_mount": {"vesa": {"width_mm": "unknown", "height_mm": "unknown"}, "notes": "壁挂参数以实际安装为准"},
        "processing": {"picture_chip": {"name": "unknown", "type": "unknown"}},
        "soc": {
            "vendor": "unknown",
            "model": "unknown",
            "cpu": {"architecture": "unknown", "cores": "unknown", "clock_ghz": "unknown"},
        },
        "memory": {"ram_gb": "unknown", "storage_gb": "unknown"},
        "interfaces": {"hdmi": {"version": "unknown", "bandwidth_gbps": "unknown", "ports": "unknown"}, "usb": {"usb_2_0": "unknown", "usb_3_0": "unknown"}},
        "network": {"wifi": {"standard": "unknown", "band": "unknown"}},
        "audio": {"speaker_channels": "unknown", "speaker_power_w": "unknown"},
        "power": {"max_power_w": "unknown"},
        "system": {"boot_ad": "unknown", "third_party_app_install": "unknown", "voice_assistant": "unknown"},
        "gaming_features": {"allm": "unknown", "vrr": "unknown", "input_lag_4k60hz_ms": "unknown"},
        "camera": {"built_in": "unknown"},
        "hdr_audio_support": {"hdr": [], "audio_effect": []},
        "meta": {
            "data_source": "tvlabs_spec_page",
            "source_url": url,
            "notes": [],
            "last_updated": dt.datetime.now().strftime("%Y-%m-%d"),
        },
    }


# ----------------------------
# crawling
# ----------------------------
def normalize_url(href: str) -> Optional[str]:
    if not href:
        return None
    href = href.strip()
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return BASE_URL + href
    return None


def collect_brand_product_links(page, brand_slug: str, max_links: int) -> List[str]:
    """
    优先打开 /tv/<brand_slug>
    然后收集所有 /tv/<brand_slug>/<model> 这种详情链接
    """
    url1 = f"{BASE_URL}/tv/{brand_slug}"
    page.goto(url1, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(800)

    links: List[str] = page.evaluate(
        """(brand) => {
          const out = new Set();
          const as = Array.from(document.querySelectorAll('a[href]'));
          for (const a of as) {
            const href = a.getAttribute('href') || '';
            if (href.startsWith('/tv/' + brand + '/')) {
              // 过滤掉可能的列表/对比页
              const parts = href.split('/').filter(Boolean);
              if (parts.length >= 3) out.add(href);
            }
          }
          return Array.from(out);
        }""",
        brand_slug,
    )

    full = []
    for h in links:
        u = normalize_url(h)
        if u:
            full.append(u)

    # 去重 + 截断
    uniq = []
    seen = set()
    for u in full:
        if u in seen:
            continue
        seen.add(u)
        uniq.append(u)
        if len(uniq) >= max_links:
            break
    return uniq


def extract_title_brand_model_from_url(url: str) -> Tuple[str, str, str]:
    # 例：https://tvlabs.cn/tv/hisense/hisense-85E8S
    # brand_slug=hisense, model_slug=hisense-85E8S
    m = re.search(r"/tv/([^/]+)/([^/?#]+)", url)
    if not m:
        return ("unknown", "unknown", "unknown")
    brand_slug = m.group(1)
    model_slug = m.group(2)

    # model 尽量从 slug 里提取后缀（例如 hisense-85E8S -> 85E8S）
    model = model_slug
    mm = re.search(r"-(\d{2}[A-Za-z0-9]+)$", model_slug)
    if mm:
        model = mm.group(1)
    return (brand_slug, brand_slug.capitalize(), model)


def scrape_one_product(page, url: str) -> Tuple[Dict[str, Any], str]:
    page.goto(url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(600)

    # 可见文本（对正则抽取最稳）
    visible_text: str = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    visible_text = visible_text.strip()

    brand_slug, brand_name, model_guess = extract_title_brand_model_from_url(url)

    # 页面标题里常含 “海信E8S 85英寸 参数详情”
    title = page.title() or ""
    # 尝试从可见文本前几行抓一个“主标题”
    headline = page.evaluate(
        """() => {
          const h = document.querySelector('h1,h2,h3');
          return h ? (h.innerText || '') : '';
        }"""
    ) or ""
    top = (headline or title).strip()

    # 从 top 里提取 brand/model 更准确一点（如果有）
    # 例：海信E8S 85英寸 参数详情 -> model=E8S size=85
    model = model_guess
    m1 = re.search(r"([A-Za-z0-9]+)\s*(?:\d{2}\s*英寸)?\s*参数详情", top)
    if m1:
        model = m1.group(1)

    # product_id 按你规则：brand_model（小写品牌 + 下划线 + 小写型号）
    product_id = f"{brand_slug}_{model}".lower()

    schema = make_schema(product_id=product_id, brand=brand_name, model=model, url=url)

    # 正则填充字段
    apply_regex_rules(schema, visible_text)

    # MEMC max_fps（如果文本里出现 120Hz/120fps 插帧）
    if re.search(r"MEMC.*?(120)\s*(?:fps|Hz)", visible_text, flags=re.IGNORECASE | re.DOTALL):
        schema["refresh"]["memc"]["supported"] = True
        schema["refresh"]["memc"]["max_fps"] = 120

    # 简单判断一些布尔项
    if re.search(r"(无开机广告|开机广告.*?无|开机无广告)", visible_text):
        schema["system"]["boot_ad"] = False
    if re.search(r"(支持第三方|可安装第三方|第三方应用安装)", visible_text):
        schema["system"]["third_party_app_install"] = True
    if re.search(r"(远场语音|远场麦克风|远场语控)", visible_text):
        schema["system"]["voice_assistant"] = "far_field_voice"

    # 额外记录：标题/抓取时间
    schema["meta"]["notes"].append(f"page_title={top}")
    schema["meta"]["notes"].append(f"fetched_at={dt.datetime.now().isoformat(timespec='seconds')}")

    return schema, visible_text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="hisense", help="tvlabs brand slug, e.g. hisense / tcl / xiaomi ...")
    ap.add_argument("--out", default="out")
    ap.add_argument("--headless", type=int, default=1, help="1=无头 0=可见浏览器")
    ap.add_argument("--max-products", type=int, default=200)
    ap.add_argument("--sleep", type=float, default=0.2, help="每个产品间隔秒数")
    args = ap.parse_args()

    out_root = Path(args.out).resolve()
    day_dir = out_root / today()
    products_root = day_dir / "products"
    all_txt = day_dir / "tvlabs_all.txt"

    # 清理当天旧结果
    if day_dir.exists():
        # 保守起见，不自动删整目录；你要删就手动删
        pass
    day_dir.mkdir(parents=True, exist_ok=True)

    append_txt(all_txt, f"# TVLabs YAML bundle\n# date: {today()}\n# brand: {args.brand}\n\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=bool(args.headless))
        ctx = browser.new_context()
        page = ctx.new_page()

        # 1) 收集品牌下所有产品链接
        links = collect_brand_product_links(page, args.brand, max_links=int(args.max_products))
        if not links:
            print(f"[ERR] 未在 {BASE_URL}/tv/{args.brand} 收集到产品详情链接。")
            print("      你可以：--headless 0 打开看看页面结构是否变化；或把品牌页HTML/截图发我我给你改选择器。")
            return 2

        print(f"[INFO] collected links: {len(links)}")

        # 2) 逐个抓详情页 -> 输出 YAML + raw
        for i, url in enumerate(links, start=1):
            try:
                schema, raw_text = scrape_one_product(page, url)
                brand = safe_name(str(schema.get("brand") or args.brand))
                model = safe_name(str(schema.get("model") or f"unknown_{i}"))
                folder = products_root / brand / model
                folder.mkdir(parents=True, exist_ok=True)

                dump_yaml(folder / "product.yaml", schema)
                dump_txt(folder / "raw.txt", raw_text)

                # 拼总 txt
                append_txt(all_txt, "\n---\n")
                append_txt(all_txt, f"# {i}/{len(links)}\n# url: {url}\n")
                append_txt(all_txt, yaml.safe_dump(schema, allow_unicode=True, sort_keys=False))

                print(f"[OK] {i}/{len(links)} {brand} {model}")
                time.sleep(float(args.sleep))
            except Exception as e:
                print(f"[WARN] failed {url}: {e}")

        browser.close()

    print(f"[DONE] day_dir: {day_dir}")
    print(f"[DONE] bundle : {all_txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())