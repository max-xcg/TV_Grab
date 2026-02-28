# -*- coding: utf-8 -*-
"""
Webhook Server (port 9000)
- POST /webhook  接收 {user_id, session_id, text}
- 支持“更多/详细分析”二次请求：同 session_id 返回上一轮 reply_full
- 内存 session store（可替换 Redis）

运行：
  python webhook_server.py --host 127.0.0.1 --port 9000

测试：
  curl -sS -X POST "http://127.0.0.1:9000/webhook" -H "Content-Type: application/json" \
    --data-binary '{"user_id":"u1","session_id":"t1","text":"75 13k ps5 只要tcl"}'

  curl -sS -X POST "http://127.0.0.1:9000/webhook" -H "Content-Type: application/json" \
    --data-binary '{"user_id":"u1","session_id":"t1","text":"更多"}'
"""

import argparse
import re
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel


# =========================
# Request/Response Models
# =========================
class WebhookIn(BaseModel):
    user_id: str
    session_id: str
    text: str


@dataclass
class TurnCache:
    ts: float
    user_text: str
    reply_short: str
    reply_full: str
    raw: Dict[str, Any]


# =========================
# Simple Session Store
# =========================
class SessionStore:
    """
    内存版 SessionStore：
    - key: session_id
    - value: TurnCache
    """
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl = ttl_seconds
        self._data: Dict[str, TurnCache] = {}

    def get(self, session_id: str) -> Optional[TurnCache]:
        item = self._data.get(session_id)
        if not item:
            return None
        if time.time() - item.ts > self.ttl:
            # 过期就清掉
            self._data.pop(session_id, None)
            return None
        return item

    def set(self, session_id: str, cache: TurnCache) -> None:
        self._data[session_id] = cache

    def clear(self, session_id: str) -> None:
        self._data.pop(session_id, None)


STORE = SessionStore(ttl_seconds=3600)


# =========================
# Helpers: Command detect
# =========================
MORE_PAT = re.compile(r"^(更多|详细|详情|详细分析|detail|more)\s*$", re.I)
RESET_PAT = re.compile(r"^(重来|清空|reset|restart)\s*$", re.I)
TOPN_PAT = re.compile(r"^\s*top\s*(\d+)\s*$", re.I)
COMPARE_PAT = re.compile(r"^\s*对比\s*(\d+)\s+(\d+)\s*$", re.I)


def parse_command(text: str) -> Tuple[str, Dict[str, Any]]:
    t = (text or "").strip()
    if MORE_PAT.match(t):
        return "more", {}
    if RESET_PAT.match(t):
        return "reset", {}
    m = TOPN_PAT.match(t)
    if m:
        n = int(m.group(1))
        return "topn", {"n": max(1, min(n, 20))}
    m = COMPARE_PAT.match(t)
    if m:
        a = int(m.group(1))
        b = int(m.group(2))
        return "compare", {"a": a, "b": b}
    return "recommend", {}


# =========================
# Engine (替换成你的真实推荐逻辑)
# =========================
def engine_recommend(text: str, session_id: str, user_id: str) -> Dict[str, Any]:
    """
    这里返回结构尽量贴近你现在 /webhook 的 raw
    你接入真实引擎时建议返回：
      {
        "ok": True,
        "session_id": session_id,
        "reply_short": "...",
        "reply_full": "...",
        "structured": {...},
        "state": {...},
      }
    """
    # TODO: 用你的真实引擎替换这里，比如：
    # from tv_buy_1_0.run_reco import recommend_text
    # result = recommend_text(text, session_id=session_id, user_id=user_id)
    # return result

    # ---- 演示：做一个最小可运行的假数据 ----
    reply_short = "一句话：示例返回（这里替换成你的真实推荐）\nTop3：\n1) TCL 75Q10M ￥12999\n（回复：更多 查看详细分析）"
    reply_full = (
        "电视选购 1.0 | 75 寸 | 场景=ps5 | 品牌=TCL | 预算≤13000 | 优先年份=2026\n"
        "Top 3 推荐：\n"
        "1) TCL 75Q10M 75寸 | ￥12999\n"
        "2) TCL 75Q9L Pro 75寸 | ￥6699\n"
        "3) TCL 75Q10L 75寸 | ￥8469\n"
        "\n购买前确认：输入延迟、VRR、供货/固件。"
    )
    structured = {
        "top3": [
            {"rank": 1, "model": "TCL 75Q10M", "size": 75, "price": 12999},
            {"rank": 2, "model": "TCL 75Q9L Pro", "size": 75, "price": 6699},
            {"rank": 3, "model": "TCL 75Q10L", "size": 75, "price": 8469},
        ],
        "one_liner": "示例 one_liner",
    }
    state = {"size": 75, "budget": 13000, "scene": "ps5", "brand": "TCL", "brand_any": False}
    return {
        "ok": True,
        "session_id": session_id,
        "reply_short": reply_short,
        "reply_full": reply_full,
        "structured": structured,
        "state": state,
        "done": True,
    }


def build_webhook_response(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    对外统一格式：{ok, reply, raw}
    """
    reply = result.get("reply_short") or result.get("reply") or ""
    return {"ok": True, "reply": reply, "raw": result}


# =========================
# FastAPI App
# =========================
app = FastAPI()


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/webhook")
def webhook(body: WebhookIn):
    cmd, args = parse_command(body.text)
    session_id = body.session_id

    # 1) 重来
    if cmd == "reset":
        STORE.clear(session_id)
        return JSONResponse({"ok": True, "reply": "已清空本次会话。请重新输入需求（如：75 13k ps5 只要tcl）", "raw": {"reset": True}})

    # 2) 更多：直接回上一轮 reply_full（不重新跑推荐）
    if cmd == "more":
        last = STORE.get(session_id)
        if not last:
            return JSONResponse({"ok": True, "reply": "我这边没有找到上一轮内容。请先发一次需求（如：75 13k ps5 只要tcl）", "raw": {"hint": "no_cache"}})
        return JSONResponse({"ok": True, "reply": last.reply_full, "raw": last.raw})

    # 3) topN / 对比（演示：从缓存 structured 里取）
    last = STORE.get(session_id)
    if cmd == "topn":
        n = args["n"]
        if not last:
            return JSONResponse({"ok": True, "reply": "还没有候选列表，请先发一次需求。", "raw": {"hint": "no_cache"}})
        top = (last.raw.get("structured") or {}).get("top3") or []
        top = top[:n]
        lines = [f"Top{len(top)}："] + [f'{x.get("rank","-")}. {x.get("model")} ￥{x.get("price")}' for x in top]
        return JSONResponse({"ok": True, "reply": "\n".join(lines), "raw": last.raw})

    if cmd == "compare":
        a, b = args["a"], args["b"]
        if not last:
            return JSONResponse({"ok": True, "reply": "还没有候选列表，请先发一次需求。", "raw": {"hint": "no_cache"}})
        top = (last.raw.get("structured") or {}).get("top3") or []
        if a < 1 or b < 1 or a > len(top) or b > len(top):
            return JSONResponse({"ok": True, "reply": f"可对比范围是 1~{len(top)}，比如：对比 1 2", "raw": last.raw})
        A, B = top[a - 1], top[b - 1]
        reply = (
            f"对比 {a} vs {b}\n"
            f"- {A['model']}：￥{A['price']}\n"
            f"- {B['model']}：￥{B['price']}\n"
            f"（这里建议你接入真实参数差异：分区/亮度/HDMI2.1/输入延迟/VRR 等）"
        )
        return JSONResponse({"ok": True, "reply": reply, "raw": last.raw})

    # 4) 默认：跑推荐
    result = engine_recommend(body.text, session_id=session_id, user_id=body.user_id)

    # 写入缓存，供“更多”复用
    cache = TurnCache(
        ts=time.time(),
        user_text=body.text,
        reply_short=result.get("reply_short") or "",
        reply_full=result.get("reply_full") or "",
        raw=result,
    )
    STORE.set(session_id, cache)

    return JSONResponse(build_webhook_response(result))


def main():
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
