# -*- coding: utf-8 -*-
"""
TCL Step3 TEST（规范 YAML + 注释版，抓 price/size/gaming_grade + 摘要区 ALLM/WI-FI 等）
- 进入 TCL 品牌页
- 抽取 2025 年机型卡片（从卡片上抓：detail_url / size_inch / price_cny）
- 进入详情页抓：
    1) “参数详情”KV（中文 key）
    2) “摘要区”KV（电视等级/游戏电视/WI-FI/ALLM/VRR/输入延时/扬声器/电源功率）
  然后 merge、清洗、归一化
- 映射到规范 schema，并输出“每行带中文注释”的 YAML（纯文本模板写出）

运行：
  /c/software/Anaconda3/python.exe test_brand_2025_tcl.py
"""

import os
import re
from datetime import datetime
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

BASE = "https://tvlabs.cn"
BRAND_URL = "https://tvlabs.cn/tv/TCL"

TARGET_YEAR = 2025
TAKE_N = 1
HEADLESS = True

OUT_DIR = "output_step3_test"
os.makedirs(OUT_DIR, exist_ok=True)


# ---------------- utils ----------------
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


def slug_product_id(title: str):
    # title: "TCL 85S12L"
    t = norm(title).lower()
    t = re.sub(r"^tcl\s*", "", t)
    t = re.sub(r"[^\w]+", "_", t).strip("_")
    return f"tcl_{t}" if t else "tcl_item"


def extract_size_inch_from_title(title: str):
    """
    title 里经常是：TCL 85S12L  => 85
    """
    t = norm(title or "")
    m = re.search(r"\bTCL\s*(\d{2,3})", t, re.I)
    if m:
        return int(m.group(1))
    # 兜底：出现“85 英寸”
    m2 = re.search(r"(\d{2,3})\s*英寸", t)
    return int(m2.group(1)) if m2 else None


def block_assets(page):
    page.route(
        "**/*",
        lambda route, req: route.abort()
        if req.resource_type in ("image", "media", "font")
        else route.continue_()
    )


def gentle_scroll(page, steps=6, dy=1400):
    for _ in range(steps):
        page.mouse.wheel(0, dy)
        page.wait_for_timeout(350)


# ---------------- brand page cards ----------------
def extract_cards_for_year(page, brand_url: str, year: int):
    """
    抽取 2025 年机型区块内的卡片，并从卡片文本里解析：
    - title
    - release_text
    - href (detail url)
    - size_inch (如 85 英寸)
    - price_cny (如 官方价 ¥8999)
    """
    js = r"""
    (year) => {
      function n(s){ return (s||'').replace(/\s+/g,' ').trim(); }

      // 找到 “2025 年机型” 的标题元素
      const header = Array.from(document.querySelectorAll('*'))
        .find(el => n(el.innerText) === `${year} 年机型`);
      if (!header) return [];

      // 往上找一个容器，尽量包含卡片但不要包含整页
      let root = header;
      for (let i=0;i<12;i++){
        if (!root.parentElement) break;
        root = root.parentElement;
        const t = n(root.innerText);
        if (t.includes(`${year} 年机型`) && t.includes(`首发于 ${year}年`)) break;
        if (t.length > 16000) break;
      }

      const yearRe = new RegExp(`首发于\\s*${year}\\s*年\\s*\\d{1,2}\\s*月`);
      const blocks = Array.from(root.querySelectorAll('div'))
        .filter(d => {
          const t = n(d.innerText);
          if (!t || t.length < 20 || t.length > 520) return false;
          if (!yearRe.test(t)) return false;
          if (!(t.includes('官方价') || t.includes('暂无报价') || t.includes('京东购买'))) return false;
          return true;
        });

      const out = [];
      for (const d of blocks) {
        const text = n(d.innerText);

        // release
        const rel = text.match(new RegExp(`(首发于\\s*${year}\\s*年\\s*\\d{1,2}\\s*月)`));
        const release_text = rel ? rel[1] : null;

        // title
        const title = release_text ? text.split(release_text)[0].trim() : text.split('首发于')[0].trim();

        // href
        const a = d.querySelector("a[href*='/tv/']");
        const href = a ? a.getAttribute('href') : null;

        // size_inch: "85 英寸"
        let size_inch = null;
        const ms = text.match(/(\d{2,3})\s*英寸/);
        if (ms) size_inch = parseInt(ms[1], 10);

        // price: "官方价 ¥8999"
        let price_cny = null;
        const mp = text.match(/官方价\s*¥\s*([0-9]{1,9})/);
        if (mp) price_cny = parseInt(mp[1], 10);

        out.push({ title, release_text, href, size_inch, price_cny });
      }

      // dedup (title+release)
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
    """
    没 href 时：尝试点击卡片的 “+” 或标题获取跳转 URL
    """
    old = page.url
    card = page.locator(f"div:has-text('{title}'):has-text('首发于')").first
    card.wait_for(timeout=20000)
    card.scroll_into_view_if_needed()
    page.wait_for_timeout(300)

    # 优先点绿色 +
    try:
        btns = card.locator("button")
        if btns.count() > 0:
            btns.nth(btns.count() - 1).click(timeout=3000)
            page.wait_for_timeout(700)
    except:
        pass

    try:
        page.wait_for_url(re.compile(r"/tv/.+/.+"), timeout=6000)
    except:
        pass

    if page.url != old and "/tv/" in page.url and page.url.rstrip("/") != brand_url.rstrip("/"):
        return page.url

    # 兜底：点标题
    try:
        card.locator(f"text={title}").first.click(timeout=2500)
        page.wait_for_timeout(700)
        page.wait_for_url(re.compile(r"/tv/.+/.+"), timeout=6000)
    except:
        pass

    if page.url != old and "/tv/" in page.url and page.url.rstrip("/") != brand_url.rstrip("/"):
        return page.url

    return None


# ---------------- detail page kv extraction ----------------
def extract_detail_kv(page):
    """
    只在“参数详情”模块内抓 key/value（中文 key）
    """
    js = r"""
    () => {
      function n(s){ return (s||'').replace(/\s+/g,' ').trim(); }

      const all = Array.from(document.querySelectorAll('*'));
      const h = all.find(el => n(el.innerText) === '参数详情');

      let root = h || document.body;

      // 往上找一个合理容器（别太大）
      for (let i=0;i<12;i++){
        if (!root.parentElement) break;
        const p = root.parentElement;
        const t = n(p.innerText);
        if (t.includes('参数详情') && t.length < 11000) { root = p; break; }
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
          if (k.length > 14) return false;
          if (v.length > 80) return false;

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

      // extras：尺寸信息
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
      if (extras.length) kv["__extras__"] = Array.from(new Set(extras)).slice(0, 60);

      return kv;
    }
    """
    return page.evaluate(js) or {}


def extract_summary_kv(page):
    """
    抓“参数详情”上方摘要区（电视等级/游戏电视/WI-FI/输入延时/ALLM/VRR支持/扬声器/电源功率）
    关键点：label 元素的 innerText 往往包含“等级分类标准”等小字，所以不能用 == 精确匹配
    改为：在全量元素中找“包含 label 的最小文本元素”，再在同一行/相邻行找 value
    """
    js = r"""
    () => {
      function n(s){ return (s||'').replace(/\s+/g,' ').trim(); }

      const labels = [
        '电视等级','游戏电视','WI-FI','输入延时','ALLM','VRR支持','扬声器','电源功率'
      ];

      const badPieces = ['等级分类标准','游戏电视等级分类标准','分类标准','缺失机型反馈','参数详情','品牌大全','参数对比'];

      function isBad(t){
        return badPieces.some(b => t.includes(b));
      }

      // 在全页面里找“最像 label 本体”的元素：包含 label 且文本长度最短
      function findBestLabelEl(lab){
        const all = Array.from(document.querySelectorAll('*'));
        let best = null;
        let bestLen = 1e9;

        for (const el of all){
          const t = n(el.innerText);
          if (!t) continue;
          if (!t.includes(lab)) continue;

          // 过滤掉明显大块容器
          if (t.length > 120) continue;

          // 有些是 "电视等级 等级分类标准" 也允许
          // 但如果 t 里没有 lab 且 t 很杂，就跳过（上面已 includes）
          const l = t.length;
          if (l < bestLen){
            best = el;
            bestLen = l;
          }
        }
        return best;
      }

      // 从“行容器”里找 value：去掉 label 和各种提示，取更像值的那段
      function pickValueFromRowText(rowText, lab){
        let t = n(rowText);
        if (!t) return null;

        // 去掉常见提示
        for (const b of badPieces){
          t = t.replaceAll(b, ' ');
        }
        t = n(t);

        // 常见结构： "电视等级 中端" / "游戏电视 非游戏电视"
        // 尝试把 lab 之后的文本当 value
        if (t.includes(lab)){
          const idx = t.indexOf(lab);
          const after = n(t.slice(idx + lab.length));
          if (after && after.length <= 60) return after;
        }

        return null;
      }

      // 找到“行容器”：从 label 元素向上找一个文本不太长、且包含 label 的 div/section
      function findRowContainer(labelEl, lab){
        let row = labelEl;
        for (let i=0;i<10;i++){
          if (!row.parentElement) break;
          row = row.parentElement;
          const t = n(row.innerText);
          if (t.includes(lab) && t.length < 300) return row;
        }
        return labelEl.parentElement || labelEl;
      }

      const out = {};

      for (const lab of labels){
        const labelEl = findBestLabelEl(lab);
        if (!labelEl) continue;

        const row = findRowContainer(labelEl, lab);

        // 1) 先从该行整体文本里抽
        let val = pickValueFromRowText(row.innerText, lab);

        // 2) 如果没抽到，再试“同级兄弟节点”（有些布局 label/value 分两列，value 在旁边）
        if (!val && row.parentElement){
          const sibs = Array.from(row.parentElement.children || []);
          for (const s of sibs){
            const tt = n(s.innerText);
            if (!tt) continue;
            if (!tt.includes(lab)) continue;
            // 在包含 lab 的兄弟块里抽
            const vv = pickValueFromRowText(tt, lab);
            if (vv) { val = vv; break; }
          }
        }

        // 3) 再兜底：在 row 内找最短的“非 label 文本块”当 value
        if (!val){
          const texts = Array.from(row.querySelectorAll('*'))
            .map(x => n(x.innerText))
            .filter(t => t && !t.includes(lab) && !isBad(t) && t.length <= 60);

          if (texts.length){
            // value 通常比 label 长一点，取最长更稳
            texts.sort((a,b)=>b.length-a.length);
            val = texts[0];
          }
        }

        if (val) out[lab] = val;
      }

      // 兼容“VRR 支持”这种写法
      if (out['VRR 支持'] && !out['VRR支持']) out['VRR支持'] = out['VRR 支持'];
      delete out['VRR 支持'];

      return out;
    }
    """
    try:
        return page.evaluate(js) or {}
    except:
        return {}

def block_assets(page):
    page.route(
        "**/*",
        lambda route, req: route.abort()
        if req.resource_type in ("image", "media")   # ✅ 不要拦 font
        else route.continue_()
    )


def extract_tier_from_page_text(page):
    """
    超稳版：用 locator 找到“电视等级”所在行容器，再在该行内直接匹配值
    """
    wanted = ["中高端", "高端", "中端", "入门"]

    try:
        # 找到包含“电视等级”的最小块（避免抓到大容器）
        label = page.get_by_text("电视等级", exact=True).first
        label.wait_for(timeout=20000)

        # 向上找一个“行容器”：通常是 div 并且同行会包含“中端/高端...”
        # 这里用 xpath 往上爬 1~8 层，找到第一个包含候选值的祖先
        for i in range(1, 9):
            row = label.locator(f"xpath=ancestor::*[{i}]")
            txt = row.inner_text(timeout=2000).replace("\n", " ").replace("\t", " ")
            txt = re.sub(r"\s+", " ", txt).strip()
            # 去掉提示小字噪音
            txt = txt.replace("等级分类标准", " ").replace("分类标准", " ")
            for w in wanted:
                if w in txt:
                    return w

        # 兜底：整页文本
        body = page.locator("body").inner_text(timeout=5000)
        body = re.sub(r"\s+", " ", body)
        body = body.replace("等级分类标准", " ").replace("分类标准", " ")
        m = re.search(r"电视等级\s*[:：]?\s*(中高端|高端|中端|入门)", body)
        return m.group(1) if m else None

    except Exception:
        return None




def extract_price_from_detail_page(page):
    """
    详情页右侧卡片有“官方价 ¥xxxx”，兜底
    """
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
    """
    详情页右侧卡片常有 “85 英寸” 标签，兜底
    """
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


# ---------------- clean & normalize kv ----------------
def clean_kv(raw_kv: dict):
    """
    - key 去掉说明尾巴
    - “联发科MT9653: A73四核1.4GHz”这种错位：合并到 CPU
    - __extras__ 保留为 list[str]
    """
    drop_tail = [
        "等级分类标准",
        "游戏电视等级分类标准",
        "分类标准",
        "（MEMC）",
        "(MEMC)",
    ]

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

        # 过滤明显噪音 key
        if kk.startswith("官方价") or "京东购买" in kk or "查看详情" in kk or "同系列" in kk:
            continue

        # “联发科MT9653”被当成 key 的情况：把它变成 CPU
        if re.search(r"联发科\s*MT\d+", kk) and vv:
            cpu_str = f"{kk} {vv}".strip()
            cleaned["CPU"] = cpu_str
            continue

        if kk not in cleaned:
            cleaned[kk] = vv

    return cleaned


def normalize_kv_keys(kv: dict):
    """
    key 归一化（把空格/中英符号统一）
    注意：这里保留 ALLM/WI-FI/VRR支持/输入延时 等英文 key
    """
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
            if (not re.search(r"[\u4e00-\u9fa5]", kk)) or len(kk) > 18:
                continue

        out[kk] = v
    return out


# ---------------- mapping to spec ----------------
def map_to_spec(fields: dict):
    kv = fields["kv"]
    title = fields["title"]
    release_text = fields["release_text"]
    detail_url = fields["detail_url"]

    # ---- meta ----
    _, _, launch_ym = parse_release_ym(release_text or "")

    # price 优先品牌卡片，其次详情页兜底
    price_cny = fields.get("price_cny")

    # ---- positioning ----
    tier_raw = kv.get("电视等级")

    # ✅ 页面文本兜底
    if not tier_raw:
        tier_raw = fields.get("tier_text_fallback")

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

    gaming_text = kv.get("游戏电视")
    pos_type = None
    gaming_grade = None
    if gaming_text:
        if "非游戏" in gaming_text:
            pos_type = "non_gaming_tv"
            gaming_grade = "non_gaming_tv"  # 你要求：非游戏电视填英文
        elif "游戏" in gaming_text:
            pos_type = "gaming_tv"
            if "旗舰" in gaming_text:
                gaming_grade = "flagship"

    # ---- display ----
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
        if m:
            gamut_pct = float(m.group(1))
        else:
            gamut_pct = to_float(gamut)

    anti_ref_type = None
    reflectance_pct = None
    if ar:
        if "低反" in ar:
            anti_ref_type = "low_reflection_coating"
        elif "磨砂" in ar:
            anti_ref_type = "matte"
        if "%" in ar:
            reflectance_pct = to_float(ar)

    # ---- refresh ----
    native_hz = to_int(kv.get("屏幕刷新率"))
    dlf_max_hz = to_int(kv.get("倍频技术"))
    memc = kv.get("运动补偿")
    memc_supported = None
    memc_max_fps = None
    if memc:
        if "不支持" in memc:
            memc_supported = False
        elif "支持" in memc:
            memc_supported = True
        memc_max_fps = to_int(memc)

    # ---- processing ----
    pic = kv.get("画质处理芯片")
    pic_name = None
    pic_type = None
    if pic:
        if "TSR" in pic:
            pic_name = "TSR"
        if "TCON" in pic.upper():
            pic_type = "tcon_board_solution"

    # ---- SoC / CPU ----
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

    # ---- memory ----
    ram_gb = to_float(kv.get("运行内存"))
    storage_gb = to_int(kv.get("存储空间"))

    # ---- interfaces ----
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

    # ---- network（来自摘要区 WI-FI） ----
    wf = kv.get("WI-FI")
    wifi_std = None
    wifi_band = None
    if wf:
        up = wf.upper()
        wifi_std = "wifi_6" if ("WIFI 6" in up or "WIFI6" in up) else None
        wifi_band = "dual_band" if ("双频" in wf or "DUAL" in up) else None

    # ---- audio / power / system / gaming_features / camera ----
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
        boot_ad = False if ("无" in kv.get("开机广告")) else True

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

    # 输入延时：未知 => null
    input_lag = None
    lag = kv.get("输入延时")
    if lag and "未知" not in lag:
        input_lag = to_float(lag)

    cam = kv.get("摄像头")
    built_in_camera = None
    if cam is not None:
        built_in_camera = False if ("无" in cam) else True

    # ---- dimensions from __extras__ ----
    extras = kv.get("__extras__") or []
    length_mm = None
    height_mm = None
    thickness_mm = None
    with_stand_height_mm = None
    wall_v_mm = None
    wall_h_mm = None

    heights = []
    for item in extras:
        s = str(item)

        m = re.search(r"长度\s*(\d+(?:\.\d+)?)\s*mm", s)
        if m:
            length_mm = float(m.group(1))

        m = re.search(r"裸机厚度\s*(\d+(?:\.\d+)?)\s*mm", s)
        if m:
            thickness_mm = float(m.group(1))

        m = re.search(r"含底座高度\s*(\d+(?:\.\d+)?)\s*mm", s)
        if m:
            with_stand_height_mm = float(m.group(1))

        m = re.search(r"壁挂孔距高度\s*(\d+(?:\.\d+)?)\s*mm", s)
        if m:
            wall_v_mm = float(m.group(1))

        m = re.search(r"壁挂孔距宽度\s*(\d+(?:\.\d+)?)\s*mm", s)
        if m:
            wall_h_mm = float(m.group(1))

        # “高度 xxx mm” 过滤掉孔距高度/含底座高度，避免误入
        m = re.search(r"高度\s*(\d+(?:\.\d+)?)\s*mm", s)
        if m and ("含底座高度" not in s) and ("壁挂孔距高度" not in s):
            heights.append(float(m.group(1)))

    if heights:
        # 过滤掉明显不可能的 400mm 这种（一般是孔距/噪音误入）
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
        "product_id": slug_product_id(title),
        "brand": "TCL",
        "model": norm(title).replace("TCL", "").strip(),
        "category": "tv",
        "positioning": {
            "tier": tier_enum,
            "type": pos_type,
            "gaming_grade": gaming_grade,
        },
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
        "refresh": {
            "native_hz": native_hz,
            "dlf_max_hz": dlf_max_hz,
            "memc": {"supported": memc_supported, "max_fps": memc_max_fps},
        },
        "processing": {"picture_chip": {"name": pic_name, "type": pic_type}},
        "soc": {
            "vendor": soc_vendor,
            "model": soc_model,
            "cpu": {"architecture": cpu_arch, "cores": cpu_cores, "clock_ghz": cpu_clock},
        },
        "memory": {"ram_gb": ram_gb, "storage_gb": storage_gb},
        "interfaces": {
            "hdmi": {"version": hdmi_ver, "bandwidth_gbps": hdmi_bw, "ports": hdmi_ports},
            "usb": {"usb_2_0": usb2, "usb_3_0": usb3},
        },
        "network": {"wifi": {"standard": wifi_std, "band": wifi_band}},
        "audio": {"speaker_channels": speaker_channels},
        "power": {"max_power_w": max_power_w},
        "system": {
            "boot_ad": boot_ad,
            "third_party_app_install": third_party,
            "voice_assistant": voice_assistant,
        },
        "gaming_features": {"allm": allm, "vrr": vrr, "input_lag_4k60hz_ms": input_lag},
        "camera": {"built_in": built_in_camera},
        "hdr_audio_support": {"hdr": True, "audio_effect": True},
        "dimensions_mm": {
            "length_mm": length_mm,
            "height_mm": height_mm,
            "thickness_mm": thickness_mm,
            "with_stand_height_mm": with_stand_height_mm,
            "wall_mount_v_hole_mm": wall_v_mm,
            "wall_mount_h_hole_mm": wall_h_mm,
        },
        "detail_url": detail_url,
    }
    return spec


def yml(v):
    """
    把 python 值转成 YAML 标量（不依赖 yaml 库，以便我们控制 null / 引号）
    """
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


def write_spec_yaml_with_comments(spec: dict, out_path: str):
    """
    按你给的“规范示例”格式写出：英文 key + 每行中文注释 + 没有的用 null
    """
    s = spec

    text = f"""meta:                                   # 元信息
  launch_date: {yml(s['meta']['launch_date'])}                  # 首发时间：YYYY-MM（未知填 null）
  first_release: {yml(s['meta']['first_release'])}                   # 是否为该系列首发批次（未知填 null）
  data_source: {yml(s['meta']['data_source'])}                # 数据来源
  price_cny: {yml(s['meta']['price_cny'])}                       # 官方/标注价格（CNY；未知填 null）
  last_updated: {yml(s['meta']['last_updated'])}            # 更新时间：YYYY-MM-DD（未知填 null）
product_id: {yml(s['product_id'])}                  # 产品唯一ID（建议 brand_model_size）
brand: {yml(s['brand'])}                              # 品牌
model: {yml(s['model'])}                           # 型号
category: {yml(s['category'])}                            # 品类（tv）
positioning:                            # 市场定位
  tier: {yml(s['positioning']['tier'])}                        # 档位枚举（entry_level/midrange/upper_midrange/high_end）或 null
  type: {yml(s['positioning']['type'])}                            # 类型枚举（gaming_tv/non_gaming_tv）或 null    
  gaming_grade: {yml(s['positioning']['gaming_grade'])}                    # 游戏定位（flagship/...；非游戏电视用 non_gaming_tv）或 null
display:                                # 显示参数
  size_inch: {yml(s['display']['size_inch'])}                         # 屏幕尺寸（英寸；未知填 null）
  resolution: {yml(s['display']['resolution'])}                        # 分辨率（4k/8k/...；未知填 null）
  technology: {yml(s['display']['technology'])}                       # 显示技术枚举（lcd/mini_led_lcd/qd_mini_led_lcd/oled/...；未知填 null）
  panel_type: {yml(s['display']['panel_type'])}                      # 面板类型（soft/hard；未知填 null）
  backlight_type: {yml(s['display']['backlight_type'])}            # 背光方式（direct_lit/edge_lit；未知填 null）
  peak_brightness_nits: {yml(s['display']['peak_brightness_nits'])}             # 峰值亮度（尼特；未知填 null）
  local_dimming_zones: {yml(s['display']['local_dimming_zones'])}             # 控光分区数量（不支持/未知填 null）
  dimming_structure: {yml(s['display']['dimming_structure'])}               # 分区结构（chessboard/...；未知填 null）
  color_gamut_dci_p3_pct: {yml(s['display']['color_gamut_dci_p3_pct'])}          # DCI-P3 色域覆盖率（%；未知填 null）
  quantum_dot: {yml(s['display']['quantum_dot'])}                     # 是否量子点（true/false；未知填 null）
  anti_reflection:                      # 抗反射
    type: {yml(s['display']['anti_reflection']['type'])}                          # 抗反射类型（low_reflection_coating/matte/...；未知填 null）
    reflectance_pct: {yml(s['display']['anti_reflection']['reflectance_pct'])}               # 反射率（%；未知填 null）
refresh:                                # 刷新与运动
  native_hz: {yml(s['refresh']['native_hz'])}                        # 原生刷新率（Hz；未知填 null）
  dlf_max_hz: {yml(s['refresh']['dlf_max_hz'])}                       # DLG/DLF 倍频最高刷新率（Hz；未知填 null）
  memc:                                 # 运动补偿（MEMC）
    supported: {yml(s['refresh']['memc']['supported'])}                     # 是否支持 MEMC（true/false；未知填 null）
    max_fps: {yml(s['refresh']['memc']['max_fps'])}                        # 最大插帧帧率（fps；未知填 null）
processing:                             # 画质处理
  picture_chip:                         # 画质芯片
    name: {yml(s['processing']['picture_chip']['name'])}                               # 芯片名称（未知填 null）
    type: {yml(s['processing']['picture_chip']['type'])}                               # 芯片类型（未知填 null）
soc:                                    # 主控 SoC
  vendor: {yml(s['soc']['vendor'])}                      # SoC 厂商（mediatek/...；未知填 null）
  model: {yml(s['soc']['model'])}                         # SoC 型号（mt9655/...；未知填 null）
  cpu:                                  # CPU
    architecture: {yml(s['soc']['cpu']['architecture'])}               # CPU 架构（arm_a73/...；未知填 null）
    cores: {yml(s['soc']['cpu']['cores'])}                            # CPU 核心数（未知填 null）
    clock_ghz: {yml(s['soc']['cpu']['clock_ghz'])}                      # CPU 主频（GHz；未知填 null）
memory:                                 # 内存与存储
  ram_gb: {yml(s['memory']['ram_gb'])}                           # 运行内存（GB；未知填 null）
  storage_gb: {yml(s['memory']['storage_gb'])}                        # 存储空间（GB；未知填 null）
interfaces:                             # 接口
  hdmi:                                 # HDMI
    version: {yml(s['interfaces']['hdmi']['version'])}                      # HDMI 版本（未知填 null）
    bandwidth_gbps: {yml(s['interfaces']['hdmi']['bandwidth_gbps'])}                # HDMI 带宽（Gbps；未知填 null）
    ports: {yml(s['interfaces']['hdmi']['ports'])}                            # HDMI 数量（未知填 null）
  usb:                                  # USB
    usb_2_0: {yml(s['interfaces']['usb']['usb_2_0'])}                          # USB 2.0 数量（未知填 null）
    usb_3_0: {yml(s['interfaces']['usb']['usb_3_0'])}                          # USB 3.0 数量（未知填 null）
network:                                # 网络
  wifi:                                 # Wi-Fi
    standard: {yml(s['network']['wifi']['standard'])}                    # Wi-Fi 标准（wifi_6/...；未知填 null）
    band: {yml(s['network']['wifi']['band'])}                     # Wi-Fi 频段（dual_band/...；未知填 null）
audio:                                  # 音频
  speaker_channels: {yml(s['audio']['speaker_channels'])}               # 声道（如 2.1/2.2.2；未知填 null）
power:                                  # 功耗
  max_power_w: {yml(s['power']['max_power_w'])}                          # 最大功率（W；未知填 null）
system:                                 # 系统特性
  boot_ad: {yml(s['system']['boot_ad'])}                        # 是否无开机广告（true/false；未知填 null）
  third_party_app_install: {yml(s['system']['third_party_app_install'])}         # 是否支持安装第三方 APP（true/false；未知填 null）
  voice_assistant: {yml(s['system']['voice_assistant'])}      # 语音助手（如 far_field_voice；未知填 null）
gaming_features:                        # 游戏特性
  allm: {yml(s['gaming_features']['allm'])}                            # ALLM（true/false；未知填 null）
  vrr: {yml(s['gaming_features']['vrr'])}                                  # VRR（true/false；未知填 null）
  input_lag_4k60hz_ms: {yml(s['gaming_features']['input_lag_4k60hz_ms'])}             # 输入延迟（4K60；ms；未知填 null）
camera:                                 # 摄像头
  built_in: {yml(s['camera']['built_in'])}                       # 是否内置摄像头（true/false；未知填 null）
hdr_audio_support:                      # HDR/音效增强
  hdr: {yml(s['hdr_audio_support']['hdr'])}                             # 是否支持 HDR（默认 true）
  audio_effect: {yml(s['hdr_audio_support']['audio_effect'])}                    # 是否支持音效增强（默认 true）
dimensions_mm:                          # 由 __extras__ 解析出的尺寸信息（毫米）
  length_mm: {yml(s['dimensions_mm']['length_mm'])}                     # 长度（mm；未知填 null）
  height_mm: {yml(s['dimensions_mm']['height_mm'])}                     # 高度（mm；裸机高度；未知填 null）
  thickness_mm: {yml(s['dimensions_mm']['thickness_mm'])}                    # 厚度（mm；裸机厚度；未知填 null）
  with_stand_height_mm: {yml(s['dimensions_mm']['with_stand_height_mm'])}          # 含底座高度（mm；未知填 null）
  wall_mount_v_hole_mm: {yml(s['dimensions_mm']['wall_mount_v_hole_mm'])}           # 壁挂孔距高度（mm；VESA 竖向；未知填 null）
  wall_mount_h_hole_mm: {yml(s['dimensions_mm']['wall_mount_h_hole_mm'])}           # 壁挂孔距宽度（mm；VESA 横向；未知填 null）
detail_url: {yml(s['detail_url'])} # 参数详情页链接
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------- main ----------------
def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page()
        page.set_viewport_size({"width": 1400, "height": 900})
        block_assets(page)

        print("[1] 打开品牌页：", BRAND_URL)
        page.goto(BRAND_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1200)
        gentle_scroll(page, steps=6, dy=1400)

        print(f"[2] 抽取 {TARGET_YEAR} 区块卡片")
        cards = extract_cards_for_year(page, BRAND_URL, TARGET_YEAR)
        print(f"    cards_found={len(cards)}")

        if not cards:
            raise RuntimeError("cards_found=0：页面可能没滚到 2025 区块或结构变了")

        picked = []
        for c in cards:
            if len(picked) >= TAKE_N:
                break

            title = c["title"]
            release_text = c["release_text"]
            detail_url = c["detail_url"]
            size_inch = c.get("size_inch")
            price_cny = c.get("price_cny")

            if not detail_url:
                print(f"    [i] 无 href，尝试点击获取详情页：{title}")
                detail_url = click_to_get_detail_url(page, BRAND_URL, title)

                # 回到品牌页继续
                if page.url != BRAND_URL:
                    page.goto(BRAND_URL, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(800)
                    gentle_scroll(page, steps=6, dy=1400)

            if detail_url:
                picked.append({
                    "title": title,
                    "release_text": release_text,
                    "detail_url": detail_url,
                    "size_inch": size_inch,
                    "price_cny": price_cny,
                })

        if not picked:
            raise RuntimeError("未拿到任何 detail_url")

        for x in picked:
            title = x["title"]
            release_text = x["release_text"]
            detail_url = x["detail_url"]

            print(f"\n[3] 进入详情页：{detail_url}")
            page.goto(detail_url, wait_until="networkidle", timeout=60000)
            page.wait_for_selector("text=电视等级", timeout=20000)
            page.wait_for_timeout(300)
            tier_text_fallback = extract_tier_from_page_text(page)
            print("    tier_text_fallback =", tier_text_fallback)

            # 1) 参数详情 KV
            raw_detail_kv = extract_detail_kv(page)

            # 2) 摘要区 KV（电视等级/游戏电视/WI-FI/ALLM/VRR/输入延时/扬声器/电源功率）
            raw_summary_kv = extract_summary_kv(page)

            # merge：摘要区覆盖/补齐
            merged_raw = {}
            merged_raw.update(raw_detail_kv or {})
            merged_raw.update(raw_summary_kv or {})

            kv = normalize_kv_keys(clean_kv(merged_raw))

            # 兜底：价格/尺寸
            if x.get("price_cny") is None:
                x["price_cny"] = extract_price_from_detail_page(page)
            if x.get("size_inch") is None:
                x["size_inch"] = extract_size_inch_from_detail_page(page) or extract_size_inch_from_title(title)

            print(f"    kv_items(detail)={len(raw_detail_kv)}  kv_items(summary)={len(raw_summary_kv)}  kv_items(final)={len(kv)}")
            print(f"    price_cny={x.get('price_cny')}  size_inch={x.get('size_inch')}")
            print(f"    summary_keys={list((raw_summary_kv or {}).keys())}")
            print(f"    keys_sample={list(kv.keys())[:14]}")

            spec = map_to_spec({
                "title": title,
                "release_text": release_text,
                "detail_url": detail_url,
                "price_cny": x.get("price_cny"),
                "size_inch": x.get("size_inch"),
                "kv": kv,
                "tier_text_fallback": tier_text_fallback,  # ✅ 关键
            })

            out_path = os.path.join(OUT_DIR, f"{spec['product_id']}_spec.yaml")
            write_spec_yaml_with_comments(spec, out_path)
            print(f"[OK] saved: {out_path}")

        browser.close()
        print("\n[DONE] Step3 spec test done.")


if __name__ == "__main__":
    main()
