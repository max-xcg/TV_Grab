# -*- coding: utf-8 -*-
import re
from typing import Optional, Dict, Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from tv_buy_1_0.run_reco import recommend_text, list_candidates, format_candidates

app = FastAPI()

# =========================
# 场景 & 品牌词表
# =========================
SCENE_MAP = [
    ("ps5", ["ps5", "xsx", "xbox", "游戏", "电竞", "pc", "主机"]),
    ("movie", ["电影", "观影", "暗场", "杜比", "影院"]),
    ("bright", ["客厅", "白天", "很亮", "采光", "窗", "反光"]),
]

BRAND_ALIASES = {
    "tcl": ["tcl", "t.c.l", "只看tcl", "只要tcl", "我要tcl", "仅tcl", "我只看tcl"],
    "mi": ["mi", "小米", "xiaomi", "只看小米", "只要小米"],
    "hisense": ["海信", "hisense"],
    "sony": ["索尼", "sony"],
    "samsung": ["三星", "samsung"],
    "lg": ["lg"],
}

# =========================
# slot 解析（核心修复点：预算“以内/以下/万/k”等）
# =========================
def parse_slots(text: str) -> Dict[str, Any]:
    raw = text.strip()
    t = raw.lower()

    # reset
    if any(k in t for k in ["重置", "清空", "重新开始", "reset"]):
        return {"_reset": True}

    # ---------- size: 65寸 / 85 / 85寸 ----------
    size = None
    m = re.search(r"(\d{2,3})\s*(寸|英寸)", t)
    if m:
        size = int(m.group(1))
    else:
        m2 = re.fullmatch(r"\s*(\d{2,3})\s*", t)
        if m2:
            size = int(m2.group(1))

    # ---------- budget ----------
    budget = None

    # 1) 预算10000 / 预算 10000
    mb = re.search(r"预算\s*(\d{3,6})", t)
    if mb:
        budget = int(mb.group(1))

    # 2) 10000预算
    if budget is None:
        mb2 = re.search(r"(\d{3,6})\s*预算", t)
        if mb2:
            budget = int(mb2.group(1))

    # 3) 10000以内 / 以下 / 不超过 / 之内
    if budget is None:
        mb3 = re.search(r"(\d{3,6})\s*(以内|以下|不超过|之内)", t)
        if mb3:
            budget = int(mb3.group(1))

    # 4) 1万以内 / 2万以下 / 1.3万以内
    if budget is None:
        mb4 = re.search(r"(\d+(\.\d+)?)\s*万\s*(以内|以下|不超过|之内)", t)
        if mb4:
            budget = int(float(mb4.group(1)) * 10000)

    # 5) 10k / 10k以内 / 13k以下
    if budget is None:
        mb5 = re.search(r"(\d{1,3})\s*k\s*(以内|以下|不超过|之内)?", t)
        if mb5:
            budget = int(mb5.group(1)) * 1000

    # ---------- scene ----------
    scene = None
    for s, kws in SCENE_MAP:
        if any(k in t for k in kws):
            scene = s
            break

    # ---------- brand ----------
    brand = None
    for key, kws in BRAND_ALIASES.items():
        if any(k in t for k in kws):
            if key == "tcl":
                brand = "TCL"
            elif key == "mi":
                brand = "mi"
            else:
                brand = key
            break

    # “只看XX”通用兜底抓取
    mbrand = re.search(r"(只看|只要|仅看|我只看|我要)\s*([a-zA-Z\u4e00-\u9fa5]{2,12})", raw)
    if mbrand and brand is None:
        b = mbrand.group(2).strip()
        if b.lower() == "tcl":
            brand = "TCL"
        else:
            brand = b

    return {"size": size, "scene": scene, "budget": budget, "brand": brand}


def next_question(state: Dict[str, Any]) -> Optional[str]:
    if state.get("size") is None:
        return "你想要多大尺寸？比如：65 / 75 / 85（直接回“75寸”也行）"
    if state.get("scene") is None:
        return "主要用途是什么？回一个就行：ps5 / movie / bright（白天客厅很亮）"
    return None


class ChatReq(BaseModel):
    text: str
    state: Optional[Dict[str, Any]] = None


class ChatResp(BaseModel):
    state: Dict[str, Any]
    reply: str


@app.post("/api/chat", response_model=ChatResp)
def chat(req: ChatReq):
    base = req.state or {"size": None, "scene": None, "budget": None, "brand": None}

    slots = parse_slots(req.text)
    if slots.get("_reset"):
        base = {"size": None, "scene": None, "budget": None, "brand": None}
        return ChatResp(state=base, reply="✅ 已重置。你想买多大尺寸的电视？比如：65 / 75 / 85")

    # merge slots
    for k in ["size", "scene", "budget", "brand"]:
        v = slots.get(k)
        if v is not None:
            base[k] = v

    # header: 当前已收集
    collected = []
    if base.get("brand"):
        collected.append(f"品牌={base['brand']}")
    if base.get("budget") is not None:
        collected.append(f"预算≤{base['budget']}")
    if base.get("size") is not None:
        collected.append(f"尺寸≈{base['size']}寸")
    if base.get("scene") is not None:
        collected.append(f"场景={base['scene']}")
    header = f"（当前已收集：{'; '.join(collected) if collected else '暂无'}）\n\n"

    reply_parts = []

    # ✅ 只要 size 有，就展示候选（预算/品牌都生效）
    if base.get("size") is not None:
        total, cands = list_candidates(
            size=int(base["size"]),
            brand=base.get("brand"),
            budget=base.get("budget"),
            limit=10,
        )
        reply_parts.append(
            format_candidates(
                size=int(base["size"]),
                total=total,
                cands=cands,
                brand=base.get("brand"),
                budget=base.get("budget"),
            )
        )

        # scene 也有：再输出 Top3
        if base.get("scene") is not None:
            reply_parts.append("")
            reply_parts.append(
                recommend_text(
                    size=int(base["size"]),
                    scene=str(base["scene"]),
                    brand=base.get("brand"),
                    budget=base.get("budget"),
                )
            )

        if total == 0:
            reply_parts.append("\n💡 建议：提高预算 / 换尺寸 / 先不限定品牌试试。")

    # 还缺槽位：继续追问
    q = next_question(base)
    if q:
        reply = header + "\n\n".join(reply_parts) + ("\n\n" if reply_parts else "") + q
        return ChatResp(state=base, reply=reply)

    # 都齐了
    reply = header + "\n\n".join(reply_parts)
    return ChatResp(state=base, reply=reply)


# =========================
# HTML 页面（必须保留，否则 NameError）
# =========================
HTML = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>电视选购 1.0（聊天）</title>
  <style>
    body { font-family: Arial, "PingFang SC", "Microsoft YaHei"; max-width: 980px; margin: 28px auto; }
    .box { border: 1px solid #e5e7eb; border-radius: 14px; padding: 16px; }
    textarea { width: 100%; height: 80px; padding: 10px; }
    button { padding: 10px 18px; cursor: pointer; }
    .chat { margin-top: 14px; }
    .msg { padding: 12px 14px; border-radius: 12px; margin: 10px 0; white-space: pre-wrap; line-height: 1.55; }
    .me { background: #eef6ff; }
    .bot { background: #f7f7f7; }
    .hint { color: #666; font-size: 14px; margin-bottom: 10px;}
    .top { display:flex; gap: 10px; align-items: center; }
    .pill { font-size: 12px; color:#555; background:#f3f4f6; padding:4px 8px; border-radius: 999px; }
  </style>
</head>
<body>
  <h1>电视选购 1.0（聊天入口）</h1>
  <div class="hint">例：75寸 ps5 预算8000 白天客厅很亮 / 我只看TCL / 10000以内 / 1.3万以内 / 10k以内 / 重置</div>

  <div class="box">
    <div class="top">
      <button onclick="send()">发送</button>
      <span class="pill" id="statepill">state: empty</span>
    </div>
    <div style="margin-top:10px;">
      <textarea id="q" placeholder="输入你的需求...">我想买个tcl电视机</textarea>
    </div>

    <div class="chat" id="chat"></div>
  </div>

<script>
let state = {size:null, scene:null, budget:null, brand:null};

function renderState(){
  const s = [];
  if(state.brand) s.push("品牌="+state.brand);
  if(state.budget!==null) s.push("预算≤"+state.budget);
  if(state.size!==null) s.push("尺寸≈"+state.size);
  if(state.scene) s.push("场景="+state.scene);
  document.getElementById("statepill").textContent = "已收集：" + (s.length? s.join("，") : "暂无");
}

function addMsg(role, text){
  const div = document.createElement("div");
  div.className = "msg " + (role==="me" ? "me" : "bot");
  div.textContent = text;
  document.getElementById("chat").appendChild(div);
  div.scrollIntoView({behavior:"smooth"});
}

async function send(){
  const text = document.getElementById("q").value.trim();
  if(!text) return;
  addMsg("me", text);
  document.getElementById("q").value = "";

  const r = await fetch("/api/chat", {
    method:"POST",
    headers:{ "Content-Type":"application/json" },
    body: JSON.stringify({ text, state })
  });

  const data = await r.json();
  state = data.state;
  renderState();
  addMsg("bot", data.reply);
}

renderState();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(HTML)
