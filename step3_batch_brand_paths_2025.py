# -*- coding: utf-8 -*-
"""
Step3 BATCH（全量 2025 机型：按 tvlabs brand_path 批处理）
- brands.yaml 驱动（brands: [brand_path...]）
- brand page：抽取 2025 年卡片（detail_url / size_inch / price_cny / release_text）
- detail page：抓参数详情KV + 摘要KV + tier locator兜底
- merge / clean / normalize / map_to_spec
- 输出“每行带中文注释”的 YAML（纯文本模板写出）
- 断点续跑：若 YAML 已存在则跳过
- 失败记录：errors.log + screenshot + html

运行：
  /c/software/Anaconda3/python.exe step3_batch_brand_paths_2025.py --brands_yaml brands.yaml --target_year 2025 --out_root out_step3_2025 --headless 1
"""

import os, re, argparse, traceback
from datetime import datetime
from urllib.parse import urljoin
import yaml
from playwright.sync_api import sync_playwright


BASE = "https://tvlabs.cn"
NAV_TIMEOUT = 60000


def now_date():
    return datetime.now().strftime("%Y-%m-%d")


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def to_int(s):
    if s is None:
        return None
    m = re.search(r"(\d+)", str(s).replace(",", ""))
    return int(m.group(1)) if m else None


def to_float(s):
    if s is None:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(s).replace(",", ""))
    return float(m.group(1)) if m else None


def parse_release_ym(text: str):
    m = re.search(r"首发于\s*(\d{4})\s*年\s*(\d{1,2})\s*月", text or "")
    if not m:
        return None, None, None
    y = int(m.group(1))
    mo = int(m.group(2))
    return y, mo, f"{y:04d}-{mo:02d}"


def slug_product_id(brand_path: str, title: str):
    t = norm(title).lower()
    bp = norm(brand_path).lower()
    t = re.sub(rf"^{re.escape(bp)}\s*", "", t)
    t = re.sub(r"[^\w]+", "_", t).strip("_")
    return f"{bp}_{t}" if t else f"{bp}_item"


def extract_size_inch_from_title(title: str):
    t = norm(title or "")
    m = re.search(r"\b(\d{2,3})\b", t)
    if m:
        v = int(m.group(1))
        if 20 <= v <= 120:
            return v
    m2 = re.search(r"(\d{2,3})\s*英寸", t)
    return int(m2.group(1)) if m2 else None


def safe_filename(s: str, max_len=120):
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s)
    s = s.strip().strip(".")
    if len(s) > max_len:
        s = s[:max_len]
    return s or "item"


def block_assets(page):
    page.route(
        "**/*",
        lambda route, req: route.abort()
        if req.resource_type in ("image", "media")
        else route.continue_()
    )


def gentle_scroll(page, steps=10, dy=1400):
    for _ in range(steps):
        page.mouse.wheel(0, dy)
        page.wait_for_timeout(350)


def ensure_year_section(page, year: int, max_rounds=24):
    for _ in range(max_rounds):
        if page.locator(f"text={year} 年机型").count() > 0:
            return True
        page.mouse.wheel(0, 1600)
        page.wait_for_timeout(420)
    return False


def extract_cards_for_year(page, brand_url: str, year: int):
    js = r"""
    (year) => {
      function n(s){ return (s||'').replace(/\s+/g,' ').trim(); }

      const header = Array.from(document.querySelectorAll('*'))
        .find(el => n(el.innerText) === `${year} 年机型`);
      if (!header) return [];

      let root = header;
      for (let i=0;i<12;i++){
        if (!root.parentElement) break;
        root = root.parentElement;
        const t = n(root.innerText);
        if (t.includes(`${year} 年机型`) && t.includes(`首发于 ${year}年`)) break;
        if (t.length > 18000) break;
      }

      const yearRe = new RegExp(`首发于\\s*${year}\\s*年\\s*\\d{1,2}\\s*月`);
      const blocks = Array.from(root.querySelectorAll('div'))
        .filter(d => {
          const t = n(d.innerText);
          if (!t || t.length < 20 || t.length > 700) return false;
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


def click_to_get_detail_url(page, brand_url: str, title: str):
    old = page.url
    card = page.locator(f"div:has-text('{title}'):has-text('首发于')").first
    card.wait_for(timeout=20000)
    card.scroll_into_view_if_needed()
    page.wait_for_timeout(200)

    try:
        btns = card.locator("button")
        if btns.count() > 0:
            btns.nth(btns.count() - 1).click(timeout=3000)
            page.wait_for_timeout(600)
    except:
        pass

    try:
        page.wait_for_url(re.compile(r"/tv/.+/.+"), timeout=8000)
    except:
        pass

    if page.url != old and "/tv/" in page.url and page.url.rstrip("/") != brand_url.rstrip("/"):
        return page.url

    try:
        card.locator(f"text={title}").first.click(timeout=2500)
        page.wait_for_timeout(600)
        page.wait_for_url(re.compile(r"/tv/.+/.+"), timeout=8000)
    except:
        pass

    if page.url != old and "/tv/" in page.url and page.url.rstrip("/") != brand_url.rstrip("/"):
        return page.url

    return None


def extract_detail_kv(page):
    js = r"""
    () => {
      function n(s){ return (s||'').replace(/\s+/g,' ').trim(); }
      const all = Array.from(document.querySelectorAll('*'));
      const h = all.find(el => n(el.innerText) === '参数详情');
      let root = h || document.body;

      for (let i=0;i<12;i++){
        if (!root.parentElement) break;
        const p = root.parentElement;
        const t = n(p.innerText);
        if (t.includes('参数详情') && t.length < 13000) { root = p; break; }
        root = p;
      }

      const kv = {};
      const rows = Array.from(root.querySelectorAll('div'))
        .filter(el => {
          const kids = Array.from(el.children || []);
          if (kids.length < 2 || kids.length > 4) return false;
          const k = n(kids[0].innerText);
          const v = n(kids[1].innerText);
          if (!k || !v) return false;
          if (!/[\u4e00-\u9fa5]/.test(k)) return false;
          if (k.length > 16) return false;
          if (v.length > 120) return false;
          const badKey = ['现在看','选电视','品牌大全','参数对比','等级分类标准','缺失机型反馈'];
          if (badKey.some(b => k.includes(b))) return false;
          return true;
        });

      for (const r of rows) {
        const kids = Array.from(r.children || []);
        const k = n(kids[0].innerText);
        const v = n(kids[1].innerText);
        if (!kv[k]) kv[k] = v;
      }

      const rt = n(root.innerText);
      const extras = [];
      const patterns = [
        /长度\s*\d+(\.\d+)?\s*mm/g,
        /高度\s*\d+(\.\d+)?\s*mm/g,
        /(裸机厚度)\s*\d+(\.\d+)?\s*mm/g,
        /(含底座高度)\s*\d+(\.\d+)?\s*mm/g,
        /壁挂孔距高度\s*\d+(\.\d+)?\s*mm/g,
        /壁挂孔距宽度\s*\d+(\.\d+)?\s*mm/g
      ];
      for (const re of patterns) {
        const m = rt.match(re);
        if (m) extras.push(...m);
      }
      if (extras.length) kv["__extras__"] = Array.from(new Set(extras)).slice(0, 80);
      return kv;
    }
    """
    return page.evaluate(js) or {}


def extract_summary_kv(page):
    js = r"""
    () => {
      function n(s){ return (s||'').replace(/\s+/g,' ').trim(); }
      const labels = ['电视等级','游戏电视','WI-FI','输入延时','ALLM','VRR支持','扬声器','电源功率'];
      const badPieces = ['等级分类标准','游戏电视等级分类标准','分类标准','缺失机型反馈','参数详情','品牌大全','参数对比'];

      function isBad(t){ return badPieces.some(b => t.includes(b)); }

      function findBestLabelEl(lab){
        const all = Array.from(document.querySelectorAll('*'));
        let best = null, bestLen = 1e9;
        for (const el of all){
          const t = n(el.innerText);
          if (!t) continue;
          if (!t.includes(lab)) continue;
          if (t.length > 140) continue;
          if (t.length < bestLen){ best = el; bestLen = t.length; }
        }
        return best;
      }

      function pickValueFromRowText(rowText, lab){
        let t = n(rowText);
        if (!t) return null;
        for (const b of badPieces){ t = t.replaceAll(b, ' '); }
        t = n(t);
        if (t.includes(lab)){
          const idx = t.indexOf(lab);
          const after = n(t.slice(idx + lab.length));
          if (after && after.length <= 80) return after;
        }
        return null;
      }

      function findRowContainer(labelEl, lab){
        let row = labelEl;
        for (let i=0;i<10;i++){
          if (!row.parentElement) break;
          row = row.parentElement;
          const t = n(row.innerText);
          if (t.includes(lab) && t.length < 400) return row;
        }
        return labelEl.parentElement || labelEl;
      }

      const out = {};
      for (const lab of labels){
        const labelEl = findBestLabelEl(lab);
        if (!labelEl) continue;

        const row = findRowContainer(labelEl, lab);
        let val = pickValueFromRowText(row.innerText, lab);

        if (!val && row.parentElement){
          const sibs = Array.from(row.parentElement.children || []);
          for (const s of sibs){
            const tt = n(s.innerText);
            if (!tt) continue;
            if (!tt.includes(lab)) continue;
            const vv = pickValueFromRowText(tt, lab);
            if (vv) { val = vv; break; }
          }
        }

        if (!val){
          const texts = Array.from(row.querySelectorAll('*'))
            .map(x => n(x.innerText))
            .filter(t => t && !t.includes(lab) && !isBad(t) && t.length <= 80);
          if (texts.length){
            texts.sort((a,b)=>b.length-a.length);
            val = texts[0];
          }
        }
        if (val) out[lab] = val;
      }

      if (out['VRR 支持'] && !out['VRR支持']) out['VRR支持'] = out['VRR 支持'];
      delete out['VRR 支持'];
      return out;
    }
    """
    try:
        return page.evaluate(js) or {}
    except:
        return {}


def extract_tier_locator(page):
    wanted = ["中高端", "高端", "中端", "入门"]
    try:
        label = page.get_by_text("电视等级", exact=True).first
        label.wait_for(timeout=20000)

        for i in range(1, 9):
            row = label.locator(f"xpath=ancestor::*[{i}]")
            txt = row.inner_text(timeout=2000)
            txt = re.sub(r"\s+", " ", txt).strip()
            txt = txt.replace("等级分类标准", " ").replace("分类标准", " ")
            for w in wanted:
                if w in txt:
                    return w

        body = page.locator("body").inner_text(timeout=5000)
        body = re.sub(r"\s+", " ", body).replace("等级分类标准", " ").replace("分类标准", " ")
        m = re.search(r"电视等级\s*[:：]?\s*(中高端|高端|中端|入门)", body)
        return m.group(1) if m else None
    except:
        return None


def extract_price_from_detail_page(page):
    js = r"""
    () => {
      const t = (document.body.innerText || '').replace(/\s+/g,' ').trim();
      const m = t.match(/官方价\s*¥\s*([0-9]{1,9})/);
      return m ? parseInt(m[1], 10) : null;
    }
    """
    try:
        return page.evaluate(js)
    except:
        return None


def extract_size_inch_from_detail_page(page):
    js = r"""
    () => {
      const t = (document.body.innerText || '').replace(/\s+/g,' ').trim();
      const m = t.match(/(\d{2,3})\s*英寸/);
      return m ? parseInt(m[1], 10) : null;
    }
    """
    try:
        return page.evaluate(js)
    except:
        return None


def clean_kv(raw_kv: dict):
    drop_tail = ["等级分类标准", "游戏电视等级分类标准", "分类标准", "（MEMC）", "(MEMC)"]
    cleaned = {}
    for k, v in (raw_kv or {}).items():
        kk = norm(str(k))

        if kk == "__extras__":
            if isinstance(v, list):
                cleaned["__extras__"] = [norm(str(x)) for x in v if x is not None]
            else:
                cleaned["__extras__"] = []
            continue

        vv = None if v is None else norm(str(v))

        for tail in drop_tail:
            kk = kk.replace(tail, "").strip()
        kk = re.sub(r"\s+", " ", kk).strip()

        if not kk:
            continue

        if kk.startswith("官方价") or "京东购买" in kk or "查看详情" in kk or "同系列" in kk:
            continue

        if re.search(r"联发科\s*MT\d+", kk) and vv:
            cleaned["CPU"] = f"{kk} {vv}".strip()
            continue

        if kk not in cleaned:
            cleaned[kk] = vv
    return cleaned


def normalize_kv_keys(kv: dict):
    alias = {
        "LCD 形式": "LCD形式",
        "HDMI 接口": "HDMI接口",
        "USB 接口": "USB接口",
        "安装第三方安卓 APP": "安装第三方安卓APP",
        "VRR 支持": "VRR支持",
        "WI-FI": "WI-FI",
    }
    keep_english = {"ALLM", "WI-FI", "CPU", "__extras__", "VRR支持", "输入延时"}
    out = {}
    for k, v in (kv or {}).items():
        kk = norm(str(k)).replace("（", "(").replace("）", ")")
        kk = alias.get(kk, kk)
        if kk not in keep_english:
            if (not re.search(r"[\u4e00-\u9fa5]", kk)) or len(kk) > 20:
                continue
        out[kk] = v
    return out


def map_to_spec(brand_path: str, fields: dict):
    kv = fields["kv"]
    title = fields["title"]
    release_text = fields["release_text"]
    detail_url = fields["detail_url"]

    _, _, launch_ym = parse_release_ym(release_text or "")
    price_cny = fields.get("price_cny")

    tier_raw = kv.get("电视等级") or fields.get("tier_text_fallback")
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
            tier_enum = "midrange"

    # type兜底
    gaming_text = kv.get("游戏电视")
    pos_type = "non_gaming_tv"
    gaming_grade = "non_gaming_tv"
    if gaming_text:
        if "非游戏" in gaming_text:
            pos_type = "non_gaming_tv"
            gaming_grade = "non_gaming_tv"
        elif "游戏" in gaming_text:
            pos_type = "gaming_tv"
            gaming_grade = "flagship" if "旗舰" in gaming_text else None

    size_inch = fields.get("size_inch") or extract_size_inch_from_title(title)
    tech_raw = kv.get("显示技术")
    lcd_form = kv.get("LCD形式")
    backlight = kv.get("背光方式")
    peak = kv.get("峰值亮度")
    zones = kv.get("控光分区")
    gamut = kv.get("广色域")
    ar = kv.get("抗反射")

    technology = None
    quantum_dot = None
    if tech_raw:
        t = tech_raw.lower()
        if "oled" in t:
            technology = "oled"
        elif "mini" in t:
            if "qd" in t or "量子点" in tech_raw:
                technology = "qd_mini_led_lcd"
                quantum_dot = True
            else:
                technology = "mini_led_lcd"
        elif "普通液晶" in tech_raw or "液晶" in tech_raw:
            technology = "lcd"

    if gamut and ("量子点" in gamut):
        quantum_dot = True

    panel_type = None
    if lcd_form:
        panel_type = "soft" if ("软" in lcd_form) else ("hard" if ("硬" in lcd_form) else None)

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

    native_hz = to_int(kv.get("屏幕刷新率"))
    dlf_max_hz = to_int(kv.get("倍频技术"))
    memc = kv.get("运动补偿")
    memc_supported = None
    memc_max_fps = None
    if memc:
        memc_supported = False if "不支持" in memc else (True if "支持" in memc else None)
        memc_max_fps = to_int(memc)

    cpu_str = kv.get("CPU")
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

    ram_gb = to_float(kv.get("运行内存"))
    storage_gb = to_int(kv.get("存储空间"))

    hdmi = kv.get("HDMI接口")
    usb = kv.get("USB接口")
    hdmi_ver = None
    hdmi_bw = None
    hdmi_ports = None
    if hdmi:
        mv = re.search(r"HDMI\s*([0-9.]+)", hdmi, re.I)
        if mv:
            hdmi_ver = mv.group(1)
        mbw = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*Gbps", hdmi, re.I)
        if mbw:
            hdmi_bw = float(mbw.group(1))
        mp = re.search(r"[x×]\s*(\d+)", hdmi)
        if mp:
            hdmi_ports = int(mp.group(1))

    usb2 = None
    usb3 = None
    if usb:
        m2 = re.search(r"2\.0.*?[x×]\s*(\d+)", usb)
        m3 = re.search(r"3\.0.*?[x×]\s*(\d+)", usb)
        if m2:
            usb2 = int(m2.group(1))
        if m3:
            usb3 = int(m3.group(1))

    wf = kv.get("WI-FI")
    wifi_std = None
    wifi_band = None
    if wf:
        up = wf.upper()
        wifi_std = "wifi_6" if ("WIFI 6" in up or "WIFI6" in up) else None
        wifi_band = "dual_band" if ("双频" in wf or "DUAL" in up) else None

    spk = kv.get("扬声器")
    speaker_channels = None
    if spk:
        m = re.search(r"(\d+(?:\.\d+){1,3})", spk)
        if m:
            speaker_channels = m.group(1)

    pwr = kv.get("电源功率")
    max_power_w = None
    if pwr and "未知" not in pwr:
        max_power_w = to_int(pwr)

    boot_ad = None
    if kv.get("开机广告") is not None:
        boot_ad = False if ("无" in kv.get("开机广告")) else True  # True=有广告

    third_party = None
    if kv.get("安装第三方安卓APP") is not None:
        third_party = True if ("可" in kv.get("安装第三方安卓APP")) else False

    voice_assistant = None
    va = kv.get("语音助手")
    if va:
        voice_assistant = "far_field_voice" if ("远场" in va) else va

    allm = None
    if kv.get("ALLM") is not None:
        allm = True if ("支持" in kv.get("ALLM")) else (False if ("不支持" in kv.get("ALLM")) else None)

    vrr = None
    vr = kv.get("VRR支持")
    if vr:
        if "不支持" in vr:
            vrr = False
        elif "支持" in vr or "FreeSync" in vr or "G-SYNC" in vr:
            vrr = True

    input_lag = None
    lag = kv.get("输入延时")
    if lag and "未知" not in lag:
        input_lag = to_float(lag)

    cam = kv.get("摄像头")
    built_in_camera = None
    if cam is not None:
        built_in_camera = False if ("无" in cam) else True

    extras = kv.get("__extras__") or []
    length_mm = height_mm = thickness_mm = with_stand_height_mm = wall_v_mm = wall_h_mm = None
    heights = []
    for item in extras:
        s = str(item)
        m = re.search(r"长度\s*(\d+(?:\.\d+)?)\s*mm", s)
        if m: length_mm = float(m.group(1))
        m = re.search(r"裸机厚度\s*(\d+(?:\.\d+)?)\s*mm", s)
        if m: thickness_mm = float(m.group(1))
        m = re.search(r"含底座高度\s*(\d+(?:\.\d+)?)\s*mm", s)
        if m: with_stand_height_mm = float(m.group(1))
        m = re.search(r"壁挂孔距高度\s*(\d+(?:\.\d+)?)\s*mm", s)
        if m: wall_v_mm = float(m.group(1))
        m = re.search(r"壁挂孔距宽度\s*(\d+(?:\.\d+)?)\s*mm", s)
        if m: wall_h_mm = float(m.group(1))
        m = re.search(r"高度\s*(\d+(?:\.\d+)?)\s*mm", s)
        if m and ("含底座高度" not in s) and ("壁挂孔距高度" not in s):
            heights.append(float(m.group(1)))
    if heights:
        cand = [h for h in heights if h >= 700]
        height_mm = min(cand) if cand else min(heights)

    spec = {
        "meta": {
            "launch_date": launch_ym,
            "first_release": None,
            "data_source": "tvlabs.cn",
            "price_cny": price_cny,
            "last_updated": now_date(),
        },
        "product_id": slug_product_id(brand_path, title),
        "brand": brand_path,  # 用 brand_path 保证稳定
        "model": norm(title).replace(brand_path, "").strip(),
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
            "dimming_structure": "chessboard" if (zones and ("棋盘" in zones or "横盘" in zones)) else None,
            "color_gamut_dci_p3_pct": gamut_pct,
            "quantum_dot": quantum_dot,
            "anti_reflection": {"type": anti_ref_type, "reflectance_pct": reflectance_pct},
        },
        "refresh": {"native_hz": native_hz, "dlf_max_hz": dlf_max_hz, "memc": {"supported": memc_supported, "max_fps": memc_max_fps}},
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
        "dimensions_mm": {
            "length_mm": length_mm, "height_mm": height_mm, "thickness_mm": thickness_mm,
            "with_stand_height_mm": with_stand_height_mm, "wall_mount_v_hole_mm": wall_v_mm, "wall_mount_h_hole_mm": wall_h_mm,
        },
        "detail_url": detail_url,
    }
    return spec


def yml(v):
    if v is None: return "null"
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, (int, float)): return str(v)
    s = str(v)
    if re.search(r"[:#\n\r\t]", s):
        s = s.replace("'", "''")
        return f"'{s}'"
    return s


def write_spec_yaml_with_comments(spec: dict, out_path: str):
    s = spec
    text = f"""meta:
  launch_date: {yml(s['meta']['launch_date'])}                  # 首发时间：YYYY-MM（未知填 null）
  first_release: {yml(s['meta']['first_release'])}              # 是否为该系列首发批次（未知填 null）
  data_source: {yml(s['meta']['data_source'])}                  # 数据来源
  price_cny: {yml(s['meta']['price_cny'])}                      # 官方/标注价格（CNY；未知填 null）
  last_updated: {yml(s['meta']['last_updated'])}                # 更新时间：YYYY-MM-DD（未知填 null）
product_id: {yml(s['product_id'])}                              # 产品唯一ID
brand: {yml(s['brand'])}                                        # 品牌（brand_path）
model: {yml(s['model'])}                                        # 型号
category: {yml(s['category'])}                                  # 品类（tv）
positioning:
  tier: {yml(s['positioning']['tier'])}
  type: {yml(s['positioning']['type'])}
  gaming_grade: {yml(s['positioning']['gaming_grade'])}
display:
  size_inch: {yml(s['display']['size_inch'])}
  resolution: {yml(s['display']['resolution'])}
  technology: {yml(s['display']['technology'])}
  panel_type: {yml(s['display']['panel_type'])}
  backlight_type: {yml(s['display']['backlight_type'])}
  peak_brightness_nits: {yml(s['display']['peak_brightness_nits'])}
  local_dimming_zones: {yml(s['display']['local_dimming_zones'])}
  dimming_structure: {yml(s['display']['dimming_structure'])}
  color_gamut_dci_p3_pct: {yml(s['display']['color_gamut_dci_p3_pct'])}
  quantum_dot: {yml(s['display']['quantum_dot'])}
  anti_reflection:
    type: {yml(s['display']['anti_reflection']['type'])}
    reflectance_pct: {yml(s['display']['anti_reflection']['reflectance_pct'])}
refresh:
  native_hz: {yml(s['refresh']['native_hz'])}
  dlf_max_hz: {yml(s['refresh']['dlf_max_hz'])}
  memc:
    supported: {yml(s['refresh']['memc']['supported'])}
    max_fps: {yml(s['refresh']['memc']['max_fps'])}
soc:
  vendor: {yml(s['soc']['vendor'])}
  model: {yml(s['soc']['model'])}
  cpu:
    architecture: {yml(s['soc']['cpu']['architecture'])}
    cores: {yml(s['soc']['cpu']['cores'])}
    clock_ghz: {yml(s['soc']['cpu']['clock_ghz'])}
memory:
  ram_gb: {yml(s['memory']['ram_gb'])}
  storage_gb: {yml(s['memory']['storage_gb'])}
interfaces:
  hdmi:
    version: {yml(s['interfaces']['hdmi']['version'])}
    bandwidth_gbps: {yml(s['interfaces']['hdmi']['bandwidth_gbps'])}
    ports: {yml(s['interfaces']['hdmi']['ports'])}
  usb:
    usb_2_0: {yml(s['interfaces']['usb']['usb_2_0'])}
    usb_3_0: {yml(s['interfaces']['usb']['usb_3_0'])}
network:
  wifi:
    standard: {yml(s['network']['wifi']['standard'])}
    band: {yml(s['network']['wifi']['band'])}
audio:
  speaker_channels: {yml(s['audio']['speaker_channels'])}
power:
  max_power_w: {yml(s['power']['max_power_w'])}
system:
  boot_ad: {yml(s['system']['boot_ad'])}                        # 是否有开机广告（true=有，false=无；未知填 null）
  third_party_app_install: {yml(s['system']['third_party_app_install'])}
  voice_assistant: {yml(s['system']['voice_assistant'])}
gaming_features:
  allm: {yml(s['gaming_features']['allm'])}
  vrr: {yml(s['gaming_features']['vrr'])}
  input_lag_4k60hz_ms: {yml(s['gaming_features']['input_lag_4k60hz_ms'])}
camera:
  built_in: {yml(s['camera']['built_in'])}
hdr_audio_support:
  hdr: {yml(s['hdr_audio_support']['hdr'])}
  audio_effect: {yml(s['hdr_audio_support']['audio_effect'])}
dimensions_mm:
  length_mm: {yml(s['dimensions_mm']['length_mm'])}
  height_mm: {yml(s['dimensions_mm']['height_mm'])}
  thickness_mm: {yml(s['dimensions_mm']['thickness_mm'])}
  with_stand_height_mm: {yml(s['dimensions_mm']['with_stand_height_mm'])}
  wall_mount_v_hole_mm: {yml(s['dimensions_mm']['wall_mount_v_hole_mm'])}
  wall_mount_h_hole_mm: {yml(s['dimensions_mm']['wall_mount_h_hole_mm'])}
detail_url: {yml(s['detail_url'])}
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)


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


def load_brand_paths(path: str):
    with open(path, "r", encoding="utf-8") as f:
        y = yaml.safe_load(f) or {}
    brands = y.get("brands") or []
    # 兼容你给的“对象列表”格式：[{brand_path:..}, ...]
    if brands and isinstance(brands[0], dict) and "brand_path" in brands[0]:
        brands = [b["brand_path"] for b in brands if b.get("brand_path")]
    return [str(b).strip() for b in brands if str(b).strip()]


def run_one_brand(page, brand_path: str, target_year: int, out_root: str, skip_if_exists: bool, max_items: int | None):
    brand_url = f"{BASE}/tv/{brand_path}"
    out_dir = os.path.join(out_root, brand_path)
    err_dir = os.path.join(out_dir, "_errors")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(err_dir, exist_ok=True)

    print(f"\n========== BRAND: {brand_path} ==========")
    print("[1] 打开品牌页：", brand_url)
    page.goto(brand_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    page.wait_for_timeout(1200)

    ensure_year_section(page, target_year)
    gentle_scroll(page, steps=12, dy=1400)

    print(f"[2] 抽取 {target_year} 区块卡片")
    cards = extract_cards_for_year(page, brand_url, target_year)
    print(f"    cards_found={len(cards)}")
    if not cards:
        raise RuntimeError(f"{brand_path}: cards_found=0（滚动不到区块或结构变化）")

    if max_items is not None:
        cards = cards[:max_items]
        print(f"    MAX_ITEMS={max_items} -> take {len(cards)}")

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

            if page.url.rstrip("/") != brand_url.rstrip("/"):
                page.goto(brand_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                page.wait_for_timeout(800)
                ensure_year_section(page, target_year)
                gentle_scroll(page, steps=10, dy=1400)

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
        out_path = os.path.join(out_dir, f"{pid}_spec.yaml")

        if skip_if_exists and os.path.exists(out_path):
            skip += 1
            print(f"[{idx:03d}] SKIP exists -> {pid}")
            continue

        print(f"\n[{idx:03d}] 进入详情页：{detail_url}")

        try:
            # ✅ 关键：不要用 networkidle
            page.goto(detail_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            page.wait_for_selector("text=参数详情", timeout=25000)
            page.wait_for_timeout(200)

            tier_text_fallback = extract_tier_locator(page)
            raw_detail_kv = extract_detail_kv(page)
            raw_summary_kv = extract_summary_kv(page)

            merged_raw = {}
            merged_raw.update(raw_detail_kv or {})
            merged_raw.update(raw_summary_kv or {})
            kv = normalize_kv_keys(clean_kv(merged_raw))

            if x.get("price_cny") is None:
                x["price_cny"] = extract_price_from_detail_page(page)
            if x.get("size_inch") is None:
                x["size_inch"] = extract_size_inch_from_detail_page(page) or extract_size_inch_from_title(title)

            spec = map_to_spec(brand_path, {
                "title": title,
                "release_text": release_text,
                "detail_url": detail_url,
                "price_cny": x.get("price_cny"),
                "size_inch": x.get("size_inch"),
                "kv": kv,
                "tier_text_fallback": tier_text_fallback,
            })

            write_spec_yaml_with_comments(spec, out_path)

            ok += 1
            print(f"     tier_fallback={tier_text_fallback} -> tier_enum={spec['positioning']['tier']}")
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
    ap.add_argument("--target_year", type=int, default=2025)
    ap.add_argument("--out_root", default="out_step3_2025")
    ap.add_argument("--headless", type=int, default=1)
    ap.add_argument("--max_items", type=int, default=-1, help="-1=全量")
    ap.add_argument("--skip_if_exists", type=int, default=1)
    args = ap.parse_args()

    brands = load_brand_paths(args.brands_yaml)
    if not brands:
        raise RuntimeError("brands_yaml 里 brands 为空")

    headless = (args.headless == 1)
    max_items = None if args.max_items < 0 else args.max_items
    skip_if_exists = (args.skip_if_exists == 1)

    os.makedirs(args.out_root, exist_ok=True)

    print("========== Step3 Brand Paths ==========")
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
                ok, sk, fl = run_one_brand(page, bp, args.target_year, args.out_root, skip_if_exists, max_items)
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
