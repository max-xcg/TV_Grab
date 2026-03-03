# -*- coding: utf-8 -*-
"""
Step3 BATCH（全量 2026 机型：按 tvlabs brand_path 批处理）
- brands.yaml 驱动（brands: [brand_path...]）
- brand page：抽取“2026 年机型”分块下所有卡片（title / release_text / detail_url / size_inch / price_cny）
- detail page：抓“参数详情”所有标题（值为空用 '-'），并补充全文兜底（壁挂孔距/尺寸/VRR 等）
- 输出两部分：
  1) raw_params：页面参数“最全原始KV”（标题全保留，空值 '-'）
  2) spec：结构化字段（给后续数据库/推荐系统用）
- 断点续跑：若 YAML 已存在则跳过
- 失败记录：errors.log + screenshot + html

运行（Git Bash）：
  python step3_scrape_2026_detail_specs.py --brands_yaml brands.yaml --target_year 2026 --out_root out_step3_2026 --headless 1
"""

from __future__ import annotations

import os
import re
import argparse
import traceback
from datetime import datetime
from urllib.parse import urljoin
from typing import Dict, Any, List, Optional, Tuple

import yaml
from playwright.sync_api import sync_playwright


BASE = "https://tvlabs.cn"
NAV_TIMEOUT = 60000


# ---------------------------
# Utils
# ---------------------------

def now_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def norm(s: Any) -> str:
    return re.sub(r"\s+", " ", ("" if s is None else str(s)).strip())


def to_int(s: Any) -> Optional[int]:
    if s is None:
        return None
    m = re.search(r"(\d+)", str(s).replace(",", ""))
    return int(m.group(1)) if m else None


def to_float(s: Any) -> Optional[float]:
    if s is None:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(s).replace(",", ""))
    return float(m.group(1)) if m else None


def parse_release_ym(text: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    m = re.search(r"首发于\s*(\d{4})\s*年\s*(\d{1,2})\s*月", text or "")
    if not m:
        return None, None, None
    y = int(m.group(1))
    mo = int(m.group(2))
    return y, mo, f"{y:04d}-{mo:02d}"


def safe_filename(s: str, max_len: int = 120) -> str:
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s)
    s = s.strip().strip(".")
    if len(s) > max_len:
        s = s[:max_len]
    return s or "item"


def slug_product_id(brand_path: str, title: str) -> str:
    # 允许中文：product_id 你希望“可读”，所以不强制英文
    bp = norm(brand_path).lower()
    t = norm(title)
    # 去掉前缀品牌名（大小写/中英都可能）
    t2 = re.sub(rf"^{re.escape(brand_path)}\s*", "", t, flags=re.I).strip()
    if not t2:
        t2 = t
    # 把非法文件名字符替换掉
    t2 = re.sub(r"[\\/:*?\"<>|]+", "_", t2)
    t2 = re.sub(r"\s+", "_", t2).strip("_")
    return f"{bp}_{t2}" if t2 else f"{bp}_item"


def extract_size_inch_from_title(title: str) -> Optional[int]:
    t = norm(title or "")
    m2 = re.search(r"(\d{2,3})\s*英寸", t)
    if m2:
        v = int(m2.group(1))
        if 20 <= v <= 120:
            return v
    m = re.search(r"\b(\d{2,3})\b", t)
    if m:
        v = int(m.group(1))
        if 20 <= v <= 120:
            return v
    return None


def yml(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if re.search(r"[:#\n\r\t]", s):
        s = s.replace("'", "''")
        return f"'{s}'"
    return s


# ---------------------------
# Playwright helpers
# ---------------------------

def block_assets(page):
    # 不拦 CSS/JS，否则会导致渲染/路由不完整；只拦图片/媒体
    page.route(
        "**/*",
        lambda route, req: route.abort()
        if req.resource_type in ("image", "media", "font")
        else route.continue_()
    )


def gentle_scroll(page, steps: int = 12, dy: int = 1500):
    for _ in range(steps):
        page.mouse.wheel(0, dy)
        page.wait_for_timeout(380)


def ensure_year_section(page, year: int, max_rounds: int = 40) -> bool:
    for _ in range(max_rounds):
        if page.locator(f"text={year} 年机型").count() > 0:
            return True
        page.mouse.wheel(0, 1700)
        page.wait_for_timeout(450)
    return False


# ---------------------------
# Extract from brand page
# ---------------------------

def extract_cards_for_year(page, brand_url: str, year: int) -> List[Dict[str, Any]]:
    """
    从品牌页的 “YYYY 年机型”分块下抽取卡片：
    - title
    - release_text（首发于 YYYY年X月）
    - href（如果存在 a[href]）
    - size_inch（卡片上英寸）
    - price_cny（卡片“官方价 ¥xxxx”）
    """
    js = r"""
    (year) => {
      function n(s){ return (s||'').replace(/\s+/g,' ').trim(); }

      const header = Array.from(document.querySelectorAll('*'))
        .find(el => n(el.innerText) === `${year} 年机型`);
      if (!header) return [];

      let root = header;
      for (let i=0;i<14;i++){
        if (!root.parentElement) break;
        root = root.parentElement;
        const t = n(root.innerText);
        if (t.includes(`${year} 年机型`) && t.includes(`首发于 ${year}年`)) break;
        if (t.length > 22000) break;
      }

      const yearRe = new RegExp(`首发于\\s*${year}\\s*年\\s*\\d{1,2}\\s*月`);
      const blocks = Array.from(root.querySelectorAll('div'))
        .filter(d => {
          const t = n(d.innerText);
          if (!t || t.length < 20 || t.length > 900) return false;
          if (!yearRe.test(t)) return false;
          if (!(t.includes('官方价') || t.includes('暂无报价') || t.includes('京东购买'))) return false;
          return true;
        });

      const out = [];
      for (const d of blocks) {
        const text = n(d.innerText);

        const rel = text.match(new RegExp(`(首发于\\s*${year}\\s*年\\s*\\d{1,2}\\s*月)`));
        const release_text = rel ? rel[1] : null;

        const title = release_text ? text.split(release_text)[0].trim() : text.split('首发于')[0].trim();

        const a = d.querySelector("a[href*='/tv/']");
        const href = a ? a.getAttribute('href') : null;

        let size_inch = null;
        const ms = text.match(/(\d{2,3})\s*英寸/);
        if (ms) size_inch = parseInt(ms[1], 10);

        let price_cny = null;
        const mp = text.match(/官方价\s*¥\s*([0-9]{1,9})/);
        if (mp) price_cny = parseInt(mp[1], 10);

        out.push({ title, release_text, href, size_inch, price_cny });
      }

      const seen = new Set();
      const dedup = [];
      for (const x of out) {
        const k = `${x.title}|${x.release_text}`;
        if (seen.has(k)) continue;
        seen.add(k);
        dedup.push(x);
      }
      return dedup;
    }
    """
    cards = page.evaluate(js, year) or []
    res = []
    for c in cards:
        href = c.get("href")
        detail_url = href if (href and "://" in href) else (urljoin(brand_url, href) if href else None)
        res.append({
            "title": c.get("title"),
            "release_text": c.get("release_text"),
            "detail_url": detail_url,
            "size_inch": c.get("size_inch"),
            "price_cny": c.get("price_cny"),
        })
    return res


def click_to_get_detail_url(page, brand_url: str, title: str) -> Optional[str]:
    """
    当卡片里没有 a[href] 时，用“点击卡片/按钮/标题”触发路由跳转，拿到详情页 URL
    """
    old = page.url
    card = page.locator(f"div:has-text('{title}'):has-text('首发于')").first
    card.wait_for(timeout=20000)
    card.scroll_into_view_if_needed()
    page.wait_for_timeout(250)

    # 先尝试点卡片内部按钮（有些右上角有 +）
    try:
        btns = card.locator("button")
        if btns.count() > 0:
            btns.nth(btns.count() - 1).click(timeout=3000)
            page.wait_for_timeout(700)
    except:
        pass

    try:
        page.wait_for_url(re.compile(r"/tv/.+/.+"), timeout=9000)
    except:
        pass

    if page.url != old and "/tv/" in page.url and page.url.rstrip("/") != brand_url.rstrip("/"):
        return page.url

    # 再尝试点击标题
    try:
        card.locator(f"text={title}").first.click(timeout=3000)
        page.wait_for_timeout(700)
        page.wait_for_url(re.compile(r"/tv/.+/.+"), timeout=9000)
    except:
        pass

    if page.url != old and "/tv/" in page.url and page.url.rstrip("/") != brand_url.rstrip("/"):
        return page.url

    return None


# ---------------------------
# Extract from detail page
# ---------------------------

def page_text(page) -> str:
    try:
        return norm(page.locator("body").inner_text(timeout=8000))
    except:
        try:
            return norm(page.content())
        except:
            return ""


def extract_detail_kv_keep_all_titles(page) -> Dict[str, str]:
    """
    详情页“参数详情”区域：保留所有标题
    - 如果 value 为空：写 '-'（你要求）
    """
    js = r"""
    () => {
      function n(s){ return (s||'').replace(/\s+/g,' ').trim(); }

      const all = Array.from(document.querySelectorAll('*'));
      const h = all.find(el => n(el.innerText) === '参数详情');

      let root = h || document.body;
      for (let i=0;i<14;i++){
        if (!root.parentElement) break;
        const p = root.parentElement;
        const t = n(p.innerText);
        // 让 root 覆盖更大一点，能包含“壁挂尺寸”等
        if (t.includes('参数详情') && t.length < 26000) { root = p; break; }
        root = p;
      }

      const kv = {};
      const badKey = ['现在看','选电视','品牌大全','参数对比','等级分类标准','缺失机型反馈','游戏电视等级分类标准'];

      // 找“类似两列表格”的行
      const candidates = Array.from(root.querySelectorAll('div'))
        .filter(el => {
          const kids = Array.from(el.children || []);
          if (kids.length < 2 || kids.length > 4) return false;
          const k = n(kids[0].innerText);
          // key 必须存在
          if (!k) return false;
          // 过滤导航/说明
          if (badKey.some(b => k.includes(b))) return false;
          // key 长度合理
          if (k.length > 24) return false;
          return true;
        });

      for (const r of candidates) {
        const kids = Array.from(r.children || []);
        const k = n(kids[0].innerText);
        let v = kids[1] ? n(kids[1].innerText) : '';
        if (!v) v = '-';
        // 去掉“i”提示符等垃圾（通常是单字符）
        if (v === 'i' || v === 'I') v = '-';
        if (!kv[k]) kv[k] = v;
      }

      return kv;
    }
    """
    kv = page.evaluate(js) or {}
    # 再做一次 Python 清洗：去掉明显垃圾 key，但不“杀中文/英文”
    out: Dict[str, str] = {}
    for k, v in kv.items():
        kk = norm(k)
        vv = "-" if (v is None or norm(v) == "") else norm(v)
        if kk in ("参数详情",):
            continue
        # 过滤明显导航项
        if any(x in kk for x in ["现在看", "选电视", "品牌大全", "参数对比", "等级分类标准", "缺失机型反馈"]):
            continue
        out[kk] = vv
    return out


def extract_summary_kv_loose(page) -> Dict[str, str]:
    """
    详情页上方摘要：尽量抓到 电视等级 / 游戏电视 / VRR / ALLM / 输入延时 / WI-FI / 扬声器 / 电源功率
    没抓到就不返回；不强制中文键过滤
    """
    js = r"""
    () => {
      function n(s){ return (s||'').replace(/\s+/g,' ').trim(); }
      const labels = ['电视等级','游戏电视','WI-FI','输入延时','ALLM','VRR 支持','VRR支持','扬声器','电源功率'];
      const badPieces = ['等级分类标准','游戏电视等级分类标准','分类标准','缺失机型反馈','参数详情','品牌大全','参数对比'];

      function cleanText(t){
        t = n(t);
        for (const b of badPieces){ t = t.replaceAll(b, ' '); }
        return n(t);
      }

      const out = {};
      for (const lab of labels){
        const all = Array.from(document.querySelectorAll('*'));
        let best = null, bestLen = 1e9;
        for (const el of all){
          const t = n(el.innerText);
          if (!t || !t.includes(lab)) continue;
          if (t.length > 160) continue;
          if (t.length < bestLen){ best = el; bestLen = t.length; }
        }
        if (!best) continue;

        let row = best;
        for (let i=0;i<10;i++){
          if (!row.parentElement) break;
          row = row.parentElement;
          const t = n(row.innerText);
          if (t.includes(lab) && t.length < 520) break;
        }

        const trow = cleanText(row.innerText);
        if (!trow.includes(lab)) continue;

        const idx = trow.indexOf(lab);
        const after = n(trow.slice(idx + lab.length));
        let val = after || null;

        if (!val){
          const texts = Array.from(row.querySelectorAll('*'))
            .map(x => cleanText(x.innerText))
            .filter(t => t && !t.includes(lab) && t.length <= 90);
          if (texts.length){
            texts.sort((a,b)=>b.length-a.length);
            val = texts[0];
          }
        }
        if (val) out[lab.replace('VRR 支持','VRR支持')] = val;
      }
      return out;
    }
    """
    try:
        out = page.evaluate(js) or {}
    except:
        out = {}
    # 空值变 '-'
    cleaned: Dict[str, str] = {}
    for k, v in out.items():
        kk = norm(k)
        vv = "-" if (v is None or norm(v) == "") else norm(v)
        cleaned[kk] = vv
    return cleaned


def extract_extras_from_fulltext(fulltext: str) -> Dict[str, Optional[float]]:
    """
    从全文兜底抓尺寸类：
    - 长度/高度/裸机厚度/含底座高度
    - 壁挂孔距高度/宽度
    """
    t = fulltext or ""
    def pick_float(pat: str) -> Optional[float]:
        m = re.search(pat, t)
        return float(m.group(1)) if m else None

    return {
        "length_mm": pick_float(r"长度\s*(\d+(?:\.\d+)?)\s*mm"),
        "height_mm": pick_float(r"高度\s*(\d+(?:\.\d+)?)\s*mm"),
        "thickness_mm": pick_float(r"裸机厚度\s*(\d+(?:\.\d+)?)\s*mm"),
        "with_stand_height_mm": pick_float(r"含底座高度\s*(\d+(?:\.\d+)?)\s*mm"),
        "wall_mount_v_hole_mm": pick_float(r"壁挂孔距高度\s*(\d+(?:\.\d+)?)\s*mm"),
        "wall_mount_h_hole_mm": pick_float(r"壁挂孔距宽度\s*(\d+(?:\.\d+)?)\s*mm"),
    }


def parse_ports_sum(text: str, key: str) -> Optional[int]:
    """
    从文本里累计类似：USB 2.0 x 1 / HDMI 2.1 (≥40Gbps) x 4
    key 例：'USB 2.0'、'USB 3.0'、'HDMI'
    """
    if not text:
        return None
    t = text
    total = 0
    found = False

    if key.upper().startswith("USB 2.0"):
        pats = [
            r"USB\s*2\.0\s*[x×]\s*(\d+)",
            r"USB\s*2\.0.*?\b(\d+)\b\s*个",  # 兜底
        ]
    elif key.upper().startswith("USB 3.0"):
        pats = [
            r"USB\s*3\.0\s*[x×]\s*(\d+)",
            r"USB\s*3\.0.*?\b(\d+)\b\s*个",
        ]
    elif key.upper().startswith("HDMI"):
        pats = [
            r"HDMI.*?[x×]\s*(\d+)",
        ]
    else:
        pats = [rf"{re.escape(key)}.*?[x×]\s*(\d+)"]

    for pat in pats:
        for m in re.finditer(pat, t, flags=re.I):
            found = True
            try:
                total += int(m.group(1))
            except:
                pass

    return total if found else None


def extract_price_from_fulltext(fulltext: str) -> Optional[int]:
    m = re.search(r"官方价\s*¥\s*([0-9]{1,9})", fulltext or "")
    return int(m.group(1)) if m else None


def extract_size_inch_from_fulltext(fulltext: str) -> Optional[int]:
    m = re.search(r"(\d{2,3})\s*英寸", fulltext or "")
    if m:
        v = int(m.group(1))
        if 20 <= v <= 120:
            return v
    return None


def detect_vrr_from_fulltext(fulltext: str) -> Optional[bool]:
    t = (fulltext or "").upper()
    if "VRR" not in t and "FREESYNC" not in t and "G-SYNC" not in t and "GSYNC" not in t:
        return None
    # 只要出现这些关键词，认为支持（页面通常只展示支持项）
    if "不支持" in (fulltext or "") and "VRR" in (fulltext or ""):
        return False
    return True


# ---------------------------
# Mapping to structured spec
# ---------------------------

def map_to_spec(brand_path: str, title: str, release_text: str, detail_url: str,
                raw_params: Dict[str, str], fulltext: str,
                price_hint: Optional[int], size_hint: Optional[int]) -> Dict[str, Any]:
    """
    raw_params：尽可能完整的“页面参数”
    spec：结构化字段（允许为 null）
    """
    _, _, launch_ym = parse_release_ym(release_text or "")

    # price / size 兜底
    price_cny = price_hint if price_hint is not None else extract_price_from_fulltext(fulltext)
    size_inch = size_hint if size_hint is not None else (extract_size_inch_from_title(title) or extract_size_inch_from_fulltext(fulltext))

    # 常见 key（中英都可能）
    def pick(*keys: str) -> Optional[str]:
        for k in keys:
            if k in raw_params:
                v = raw_params.get(k)
                if v and v != "-":
                    return v
        return None

    tier_raw = pick("电视等级")
    tier_enum = None
    if tier_raw:
        if "中高" in tier_raw:
            tier_enum = "upper_midrange"
        elif "高" in tier_raw:
            tier_enum = "high_end"
        elif "中" in tier_raw:
            tier_enum = "midrange"
        elif "入门" in tier_raw or "低" in tier_raw:
            tier_enum = "entry_level"
        else:
            tier_enum = None

    gaming_text = pick("游戏电视")
    pos_type = "non_gaming_tv"
    gaming_grade = "non_gaming_tv"
    if gaming_text:
        if "非游戏" in gaming_text:
            pos_type = "non_gaming_tv"
            gaming_grade = "non_gaming_tv"
        elif "游戏" in gaming_text:
            pos_type = "gaming_tv"
            gaming_grade = "flagship" if ("旗舰" in gaming_text) else None

    tech_raw = pick("显示技术", "Display Technology", "显示技术 ")
    lcd_form = pick("LCD形式", "LCD 形式")
    backlight = pick("背光方式")
    peak = pick("峰值亮度")
    zones = pick("控光分区")
    gamut = pick("广色域")
    ar = pick("抗反射")

    # technology
    technology = None
    quantum_dot = None
    if tech_raw:
        tl = tech_raw.lower()
        if "oled" in tl:
            technology = "oled"
        elif "mini" in tl:
            # qd or 量子点
            if "qd" in tl or "量子点" in tech_raw:
                technology = "qd_mini_led_lcd"
                quantum_dot = True
            else:
                technology = "mini_led_lcd"
        elif "液晶" in tech_raw:
            technology = "lcd"

    # quantum dot 兜底：任何字段出现“量子点”都认定 true
    if quantum_dot is None:
        if ("量子点" in (tech_raw or "")) or ("量子点" in (gamut or "")) or ("量子点" in fulltext):
            quantum_dot = True

    # panel
    panel_type = None
    if lcd_form:
        panel_type = "soft" if ("软" in lcd_form) else ("hard" if ("硬" in lcd_form) else None)

    # backlight
    backlight_type = None
    if backlight:
        backlight_type = "direct_lit" if ("直下" in backlight) else ("edge_lit" if ("侧入" in backlight) else None)

    peak_nits = to_int(peak)

    zones_int = None
    if zones:
        if "不支持" in zones or zones == "无":
            zones_int = None
        else:
            zones_int = to_int(zones)

    dimming_structure = None
    if zones and ("棋盘" in zones or "横盘" in zones):
        dimming_structure = "chessboard"

    gamut_pct = None
    if gamut:
        m = re.search(r"DCI-?P3\s*([0-9]{1,3}(?:\.\d+)?)\s*%", gamut, re.I)
        gamut_pct = float(m.group(1)) if m else to_float(gamut)

    anti_ref_type = None
    reflectance_pct = None
    if ar:
        if "低反" in ar:
            anti_ref_type = "low_reflection_coating"
        elif "磨砂" in ar:
            anti_ref_type = "matte"
        if "%" in ar:
            reflectance_pct = to_float(ar)

    native_hz = to_int(pick("屏幕刷新率"))
    dlf_max_hz = to_int(pick("倍频技术"))
    memc = pick("运动补偿", "运动补偿(MEMC)", "运动补偿 （MEMC）", "运动补偿（MEMC）")
    memc_supported = None
    memc_max_fps = None
    if memc:
        if "不支持" in memc:
            memc_supported = False
        elif "支持" in memc:
            memc_supported = True
        memc_max_fps = to_int(memc)

    cpu_str = pick("CPU")
    soc_vendor = None
    soc_model = None
    cpu_arch = None
    cpu_cores = None
    cpu_clock = None
    if cpu_str:
        if "联发科" in cpu_str or "MT" in cpu_str.upper():
            soc_vendor = "mediatek"
        m = re.search(r"(MT\d+)", cpu_str.upper())
        if m:
            soc_model = m.group(1).lower()
        if "A73" in cpu_str.upper():
            cpu_arch = "arm_a73"
        if "四核" in cpu_str:
            cpu_cores = 4
        elif "八核" in cpu_str:
            cpu_cores = 8
        mclk = re.search(r"(\d+(?:\.\d+)?)\s*GHz", cpu_str, re.I)
        if mclk:
            cpu_clock = float(mclk.group(1))

    ram_gb = to_float(pick("运行内存"))
    storage_gb = to_int(pick("存储空间"))

    # HDMI / USB：优先 raw_params 的文字，再用全文兜底累计
    hdmi_text = pick("HDMI接口", "HDMI 接口")
    usb_text = pick("USB接口", "USB 接口")

    hdmi_ver = None
    hdmi_bw = None
    hdmi_ports = None
    if hdmi_text:
        mv = re.search(r"HDMI\s*([0-9.]+)", hdmi_text, re.I)
        if mv:
            hdmi_ver = mv.group(1)
        mbw = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*Gbps", hdmi_text, re.I)
        if mbw:
            hdmi_bw = float(mbw.group(1))
        # ports: 从文本累计（可能出现多次）
        hdmi_ports = parse_ports_sum(hdmi_text, "HDMI")
    if hdmi_ports is None:
        hdmi_ports = parse_ports_sum(fulltext, "HDMI")

    usb2 = None
    usb3 = None
    # 累计 USB 2.0 / 3.0
    usb2 = parse_ports_sum(usb_text or "", "USB 2.0") or parse_ports_sum(fulltext, "USB 2.0")
    usb3 = parse_ports_sum(usb_text or "", "USB 3.0") or parse_ports_sum(fulltext, "USB 3.0")

    # Wi-Fi
    wf = pick("WI-FI", "Wi-Fi", "WIFI", "WIFI ")
    wifi_std = None
    wifi_band = None
    if wf:
        up = wf.upper()
        wifi_std = "wifi_6" if ("WIFI 6" in up or "WIFI6" in up) else None
        wifi_band = "dual_band" if ("双频" in wf or "DUAL" in up) else None

    # speaker channels
    spk = pick("扬声器")
    speaker_channels = None
    if spk:
        m = re.search(r"(\d+(?:\.\d+){1,3})", spk)
        if m:
            speaker_channels = m.group(1)

    # power
    pwr = pick("电源功率")
    max_power_w = None
    if pwr and "未知" not in pwr:
        max_power_w = to_int(pwr)

    # system
    boot_ad = None
    ka = pick("开机广告")
    if ka is not None:
        if ka == "-":
            boot_ad = None
        else:
            boot_ad = False if ("无" in ka) else True

    third_party = None
    tp = pick("安装第三方安卓APP", "安装第三方安卓 APP")
    if tp is not None:
        if tp == "-":
            third_party = None
        else:
            third_party = True if ("可" in tp) else (False if ("不" in tp) else None)

    voice_assistant = None
    va = pick("语音助手")
    if va:
        voice_assistant = "far_field_voice" if ("远场" in va) else va

    allm = None
    allm_text = pick("ALLM")
    if allm_text:
        if "不支持" in allm_text:
            allm = False
        elif "支持" in allm_text:
            allm = True

    vrr = None
    vrr_text = pick("VRR支持", "VRR 支持")
    if vrr_text:
        if "不支持" in vrr_text:
            vrr = False
        elif "支持" in vrr_text or "FreeSync" in vrr_text or "G-SYNC" in vrr_text or "G-Sync" in vrr_text:
            vrr = True
    if vrr is None:
        vrr = detect_vrr_from_fulltext(fulltext)

    input_lag = None
    lag = pick("输入延时")
    if lag and "未知" not in lag and lag != "-":
        input_lag = to_float(lag)

    cam = pick("摄像头")
    built_in_camera = None
    if cam is not None:
        if cam == "-":
            built_in_camera = None
        else:
            built_in_camera = False if ("无" in cam) else True

    # dimensions extras
    extras = extract_extras_from_fulltext(fulltext)

    spec = {
        "meta": {
            "launch_date": launch_ym,
            "first_release": None,
            "data_source": "tvlabs.cn",
            "price_cny": price_cny,
            "last_updated": now_date(),
        },
        "product_id": slug_product_id(brand_path, title),
        "brand": brand_path,
        "model": norm(title).replace(brand_path, "").strip() or norm(title),
        "category": "tv",
        "positioning": {"tier": tier_enum, "type": pos_type, "gaming_grade": gaming_grade},
        "display": {
            "size_inch": size_inch,
            "resolution": "4k",
            "technology": technology,
            "panel_type": panel_type,
            "backlight_type": backlight_type,
            "peak_brightness_nits": peak_nits,
            "local_dimming_zones": zones_int,
            "dimming_structure": dimming_structure,
            "color_gamut_dci_p3_pct": gamut_pct,
            "quantum_dot": quantum_dot,
            "anti_reflection": {"type": anti_ref_type, "reflectance_pct": reflectance_pct},
        },
        "refresh": {
            "native_hz": native_hz,
            "dlf_max_hz": dlf_max_hz,
            "memc": {"supported": memc_supported, "max_fps": memc_max_fps},
        },
        "soc": {"vendor": soc_vendor, "model": soc_model, "cpu": {"architecture": cpu_arch, "cores": cpu_cores, "clock_ghz": cpu_clock}},
        "memory": {"ram_gb": ram_gb, "storage_gb": storage_gb},
        "interfaces": {"hdmi": {"version": hdmi_ver, "bandwidth_gbps": hdmi_bw, "ports": hdmi_ports}, "usb": {"usb_2_0": usb2, "usb_3_0": usb3}},
        "network": {"wifi": {"standard": wifi_std, "band": wifi_band}},
        "audio": {"speaker_channels": speaker_channels},
        "power": {"max_power_w": max_power_w},
        "system": {"boot_ad": boot_ad, "third_party_app_install": third_party, "voice_assistant": voice_assistant},
        "gaming_features": {"allm": allm, "vrr": vrr, "input_lag_4k60hz_ms": input_lag},
        "camera": {"built_in": built_in_camera},
        "hdr_audio_support": {"hdr": True, "audio_effect": True},
        "dimensions_mm": extras,
        "detail_url": detail_url,
    }
    return spec


# ---------------------------
# YAML writer
# ---------------------------

def write_yaml(out_path: str, brand_path: str, title: str, release_text: str, detail_url: str,
              raw_params: Dict[str, str], spec: Dict[str, Any]):
    """
    你要的“参数标题全输出 + 空值 '-'”，这里 raw_params 作为第一部分写入。
    spec 部分保留结构化字段（允许 null）。
    """
    # raw_params 排序：让输出稳定一点
    raw_items = sorted(raw_params.items(), key=lambda x: x[0])

    lines: List[str] = []

    lines.append(f"# brand_path: {brand_path}")
    lines.append(f"# title: {title}")
    lines.append(f"# release_text: {release_text}")
    lines.append(f"# detail_url: {detail_url}")
    lines.append("")

    lines.append("raw_params:")  # ✅ 这里是“页面参数最全原样”
    if not raw_items:
        lines.append("  {}")
    else:
        for k, v in raw_items:
            # key 可能含冒号等，做 yaml 安全处理
            kk = k
            vv = v if v else "-"
            # key 一律用引号包起来，避免 YAML 解析问题
            lines.append(f"  {yml(kk)}: {yml(vv)}")

    lines.append("")
    lines.append("spec:")  # ✅ 结构化字段（给数据库/推荐用）

    # 用 YAML dump 写 spec（保持 null / bool / 数字正确类型）
    spec_yaml = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)
    for ln in spec_yaml.splitlines():
        lines.append(f"  {ln}" if ln else "  ")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------
# Error logging
# ---------------------------

def log_error(err_dir: str, tag: str, detail_url: str, title: str, err: str):
    p = os.path.join(err_dir, "errors.log")
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"[{now_date()}] {tag}\n  title={title}\n  url={detail_url}\n  err={err}\n\n")


def save_debug(err_dir: str, page, prefix: str):
    try:
        png = os.path.join(err_dir, f"{prefix}.png")
        html = os.path.join(err_dir, f"{prefix}.html")
        page.screenshot(path=png, full_page=True)
        with open(html, "w", encoding="utf-8") as f:
            f.write(page.content())
    except:
        pass


# ---------------------------
# Brand list
# ---------------------------

def load_brand_paths(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        y = yaml.safe_load(f) or {}
    brands = y.get("brands") or []
    # 兼容对象列表：[{brand_path:..}, ...]
    if brands and isinstance(brands[0], dict) and "brand_path" in brands[0]:
        brands = [b["brand_path"] for b in brands if b.get("brand_path")]
    return [str(b).strip() for b in brands if str(b).strip()]


# ---------------------------
# Main runner
# ---------------------------

def run_one_brand(page, brand_path: str, target_year: int, out_root: str,
                  skip_if_exists: bool, max_items: Optional[int],
                  click_year_filter: bool = False):
    brand_url = f"{BASE}/tv/{brand_path}"
    out_dir = os.path.join(out_root, brand_path)
    err_dir = os.path.join(out_dir, "_errors")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(err_dir, exist_ok=True)

    print(f"\n========== BRAND: {brand_path} ==========")
    print("[1] 打开品牌页：", brand_url)
    page.goto(brand_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    page.wait_for_timeout(1200)

    # 你说“无需点按钮”，页面按年份分块 —— 这里就是滚动找到分块
    ok_year = ensure_year_section(page, target_year)
    gentle_scroll(page, steps=14, dy=1500)

    if not ok_year:
        print(f"[WARN] 没滚到 {target_year} 年机型 标题（可能在更下面）继续尝试更深滚动")
        gentle_scroll(page, steps=20, dy=1700)

    print(f"[2] 抽取 {target_year} 区块卡片")
    cards = extract_cards_for_year(page, brand_url, target_year)
    print(f"    cards_found={len(cards)}")
    if not cards:
        raise RuntimeError(f"{brand_path}: cards_found=0（滚动不到区块或结构变化）")

    if max_items is not None:
        cards = cards[:max_items]
        print(f"    MAX_ITEMS={max_items} -> take {len(cards)}")

    # 补齐 detail_url（卡片没有 a[href] 就点击）
    fixed = []
    for i, c in enumerate(cards, 1):
        title = c["title"]
        detail_url = c["detail_url"]
        if not detail_url:
            print(f"    [{i:03d}] 补齐 detail_url: {title}")
            try:
                detail_url = click_to_get_detail_url(page, brand_url, title)
            except:
                detail_url = None

            # 回到品牌页继续
            if page.url.rstrip("/") != brand_url.rstrip("/"):
                page.goto(brand_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                page.wait_for_timeout(900)
                ensure_year_section(page, target_year)
                gentle_scroll(page, steps=12, dy=1500)

        c["detail_url"] = detail_url
        fixed.append(c)

    todo = [c for c in fixed if c.get("detail_url")]
    print(f"[3] 有效 detail_url={len(todo)} / {len(cards)}")

    ok = skip = fail = 0

    for idx, x in enumerate(todo, 1):
        title = x["title"]
        release_text = x["release_text"]
        detail_url = x["detail_url"]

        pid = slug_product_id(brand_path, title)
        out_path = os.path.join(out_dir, f"{pid}.yaml")

        if skip_if_exists and os.path.exists(out_path):
            skip += 1
            print(f"[{idx:03d}] SKIP exists -> {pid}")
            continue

        print(f"\n[{idx:03d}] 进入详情页：{detail_url}")

        try:
            page.goto(detail_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            page.wait_for_timeout(600)

            # 等“参数详情”出现（有些页面需要一点时间）
            try:
                page.wait_for_selector("text=参数详情", timeout=25000)
            except:
                # 截图后继续尝试抽取（有些页面可能不含该字样但仍有参数）
                pass

            fulltext = page_text(page)

            raw_detail_kv = extract_detail_kv_keep_all_titles(page)
            raw_summary_kv = extract_summary_kv_loose(page)

            # 合并：summary 覆盖 detail（更靠近摘要的往往更干净）
            raw_params: Dict[str, str] = {}
            raw_params.update(raw_detail_kv or {})
            raw_params.update(raw_summary_kv or {})

            # 你要求：没内容用 '-'，这里再兜底一次
            for k in list(raw_params.keys()):
                if raw_params[k] is None or norm(raw_params[k]) == "":
                    raw_params[k] = "-"

            # price/size hint：优先卡片上的
            price_hint = x.get("price_cny")
            size_hint = x.get("size_inch")

            spec = map_to_spec(
                brand_path=brand_path,
                title=title,
                release_text=release_text,
                detail_url=detail_url,
                raw_params=raw_params,
                fulltext=fulltext,
                price_hint=price_hint,
                size_hint=size_hint,
            )

            write_yaml(
                out_path=out_path,
                brand_path=brand_path,
                title=title,
                release_text=release_text,
                detail_url=detail_url,
                raw_params=raw_params,
                spec=spec,
            )

            ok += 1
            print(f"     saved: {out_path}")

        except Exception as e:
            fail += 1
            err = f"{repr(e)}\n{traceback.format_exc()}"
            print("     [FAIL]", repr(e))
            log_error(err_dir, "DETAIL_PAGE_FAIL", detail_url, title, err)

            prefix = safe_filename(f"{idx:03d}_{pid}")
            save_debug(err_dir, page, prefix)
            continue

    print("\n[BRAND DONE]", brand_path, f"OK={ok} SKIP={skip} FAIL={fail}")
    return ok, skip, fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands_yaml", required=True)
    ap.add_argument("--target_year", type=int, default=2026)
    ap.add_argument("--out_root", default="out_step3_2026")
    ap.add_argument("--headless", type=int, default=1)
    ap.add_argument("--max_items", type=int, default=-1, help="-1=全量")
    ap.add_argument("--skip_if_exists", type=int, default=1)
    ap.add_argument("--only_brand", type=str, default="", help="只跑某一个 brand_path（可选）")
    args = ap.parse_args()

    brands = load_brand_paths(args.brands_yaml)
    if not brands:
        raise RuntimeError("brands_yaml 里 brands 为空")

    if args.only_brand.strip():
        brands = [b for b in brands if b.lower() == args.only_brand.strip().lower()]
        if not brands:
            raise RuntimeError(f"only_brand={args.only_brand} 没匹配到 brands.yaml 中的品牌")

    headless = (args.headless == 1)
    max_items = None if args.max_items < 0 else args.max_items
    skip_if_exists = (args.skip_if_exists == 1)

    os.makedirs(args.out_root, exist_ok=True)

    print("========== Step3 Detail Specs (2026) ==========")
    print("brands:", len(brands), brands)
    print("target_year:", args.target_year)
    print("out_root:", args.out_root)
    print("headless:", headless)
    print("max_items:", max_items)
    print("skip_if_exists:", skip_if_exists)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.set_viewport_size({"width": 1400, "height": 900})
        block_assets(page)

        total_ok = total_skip = total_fail = 0
        for bp in brands:
            try:
                ok, sk, fl = run_one_brand(
                    page=page,
                    brand_path=bp,
                    target_year=args.target_year,
                    out_root=args.out_root,
                    skip_if_exists=skip_if_exists,
                    max_items=max_items,
                )
                total_ok += ok
                total_skip += sk
                total_fail += fl
            except Exception as e:
                print(f"[BRAND FATAL] {bp} -> {repr(e)}")
                continue

        browser.close()

    print("\n========== ALL DONE ==========")
    print("TOTAL_OK  :", total_ok)
    print("TOTAL_SKIP:", total_skip)
    print("TOTAL_FAIL:", total_fail)
    print("OUT_ROOT  :", args.out_root)


if __name__ == "__main__":
    main()