# -*- coding: utf-8 -*-
"""
Dialog 3p2 接口契约测试
--------------------------------
覆盖点：
1. 首次完整输入：
   - done = true
   - reply == reply_short（首屏短文）
   - reply_full 为完整长文
   - structured.top3 存在

2. 输入「更多」：
   - reply == reply_full
   - reply_short / structured 与上一轮完全一致（缓存复用）

3. 输入「重置」：
   - done = false
   - state 全部清空
   - reply_short / reply_full / structured == None

不依赖 requests / jq，纯标准库
"""

from __future__ import annotations
import argparse
import json
import sys
import urllib.request
import urllib.error
from typing import Any, Dict, Optional


# -----------------------------
# HTTP helper
# -----------------------------
def http_post_json(url: str, payload: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from None
    except Exception as e:
        raise RuntimeError(f"Request failed: {e}") from None


def must(cond: bool, msg: str):
    if not cond:
        raise AssertionError(msg)


def s(d: Dict[str, Any], k: str) -> Optional[str]:
    v = d.get(k)
    return v if isinstance(v, str) else None


# -----------------------------
# Main
# -----------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--session-id", default="t1")
    ap.add_argument("--verbose", type=int, default=1)
    args = ap.parse_args()

    url = args.base_url.rstrip("/") + "/api/dialog/3p2"
    sid = args.session_id

    def call(text: str) -> Dict[str, Any]:
        out = http_post_json(url, {"text": text, "session_id": sid})
        if args.verbose:
            print("\n>>>", text)
            print(json.dumps(out, ensure_ascii=False, indent=2))
        return out

    # 1 首次完整输入
    r1 = call("75 13k ps5 只要tcl")
    must(r1["ok"] is True, "r1 ok != true")
    must(r1["done"] is True, "r1 done 应为 true")

    must(s(r1, "reply") == s(r1, "reply_short"),
         "契约失败：首屏 reply 必须等于 reply_short")

    must(isinstance(r1.get("reply_full"), str), "reply_full 缺失")
    must(isinstance(r1.get("structured"), dict), "structured 缺失")
    must(isinstance(r1["structured"].get("top3"), list),
         "structured.top3 缺失")

    # 2 更多（展开）
    r2 = call("更多")
    must(r2["ok"] is True, "r2 ok != true")
    must(r2["done"] is True, "r2 done 应为 true")

    must(s(r2, "reply") == s(r2, "reply_full"),
         "契约失败：更多 时 reply 必须等于 reply_full")

    must(r2["reply_short"] == r1["reply_short"],
         "契约失败：更多 时 reply_short 应复用缓存")

    must(r2["structured"] == r1["structured"],
         "契约失败：更多 时 structured 应复用缓存")

    # 3 重置
    r3 = call("重置")
    must(r3["ok"] is True, "r3 ok != true")
    must(r3["done"] is False, "重置后 done 应为 false")

    st = r3["state"]
    must(st["size"] is None, "reset 后 size 应为 None")
    must(st["budget"] is None, "reset 后 budget 应为 None")
    must(st["scene"] is None, "reset 后 scene 应为 None")
    must(st["brand"] is None, "reset 后 brand 应为 None")

    must(r3.get("reply_short") is None, "reset 后 reply_short 应为 None")
    must(r3.get("reply_full") is None, "reset 后 reply_full 应为 None")
    must(r3.get("structured") is None, "reset 后 structured 应为 None")

    print("\n Dialog 3p2 contract ALL PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(" ASSERT FAIL:", e)
        sys.exit(2)
    except Exception as e:
        print(" ERROR:", e)
        sys.exit(1)
