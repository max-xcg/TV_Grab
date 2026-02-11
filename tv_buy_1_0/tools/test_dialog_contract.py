# -*- coding: utf-8 -*-
"""
Dialog 3p2 接口契约测试（增强版）
--------------------------------
覆盖点：
1) 首次完整输入：
   - done = true
   - reply == reply_short（首屏短文）
   - reply_full 为完整长文
   - structured.top3 存在

2) 输入「更多」：
   - reply == reply_full
   - reply_short / structured 与上一轮完全一致（缓存复用）

3) 输入「重置」：
   - done = false
   - state 全部清空
   - reply_short / reply_full / structured == None

增强项：
- --timeout: 单次请求超时（秒）
- --retries: 失败重试次数（timeout/连接失败/HTTPError）
- --loops: 循环跑 N 次（每次=首问→更多→重置）
- --sleep: 每轮之间 sleep
- --slow-ms: 慢请求阈值（毫秒），超过则打印耗时
- 失败时打印最后一次错误/响应体

不依赖 requests/jq，纯标准库
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


# -----------------------------
# HTTP helper
# -----------------------------
def http_post_json(
    url: str,
    payload: Dict[str, Any],
    timeout: float = 15.0,
) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        # 服务端一般是 utf-8；保险起见 replace
        return json.loads(raw.decode("utf-8", errors="replace"))


def http_post_json_with_retries(
    url: str,
    payload: Dict[str, Any],
    timeout: float,
    retries: int,
) -> Tuple[Dict[str, Any], float]:
    """
    返回 (json, elapsed_seconds)
    """
    last_err: Optional[str] = None
    t0_all = time.time()

    for attempt in range(1, retries + 2):  # 1..retries+1
        t0 = time.time()
        try:
            out = http_post_json(url, payload, timeout=timeout)
            return out, (time.time() - t0)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = "<failed to read body>"
            last_err = f"HTTPError {e.code}: {body}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

        if attempt <= retries:
            # 轻微退避，避免瞬时抖动
            time.sleep(min(0.2 * attempt, 1.0))

    raise RuntimeError(f"Request failed after retries. Last error: {last_err} (elapsed_total={time.time()-t0_all:.2f}s)")


def must(cond: bool, msg: str):
    if not cond:
        raise AssertionError(msg)


def s(d: Dict[str, Any], k: str) -> Optional[str]:
    v = d.get(k)
    return v if isinstance(v, str) else None


# -----------------------------
# Contract checks
# -----------------------------
def check_contract_once(
    url: str,
    sid: str,
    verbose: int,
    timeout: float,
    retries: int,
    slow_ms: int,
):
    def call(text: str) -> Dict[str, Any]:
        payload = {"text": text, "session_id": sid}
        out, elapsed = http_post_json_with_retries(url, payload, timeout=timeout, retries=retries)

        if elapsed * 1000 >= slow_ms:
            print(f"[SLOW] {elapsed*1000:.0f}ms text={text!r} sid={sid}")

        if verbose:
            print("\n>>>", text)
            print(json.dumps(out, ensure_ascii=False, indent=2))
        return out

    # 1) 首次完整输入
    r1 = call("75 13k ps5 只要tcl")
    must(r1.get("ok") is True, "r1 ok != true")
    must(r1.get("done") is True, "r1 done 应为 true")

    must(s(r1, "reply") == s(r1, "reply_short"),
         "契约失败：首屏 reply 必须等于 reply_short")

    must(isinstance(r1.get("reply_full"), str), "reply_full 缺失或不是 str")
    must(isinstance(r1.get("structured"), dict), "structured 缺失或不是 dict")
    must(isinstance(r1["structured"].get("top3"), list), "structured.top3 缺失或不是 list")

    # 2) 更多（展开）
    r2 = call("更多")
    must(r2.get("ok") is True, "r2 ok != true")
    must(r2.get("done") is True, "r2 done 应为 true")

    must(s(r2, "reply") == s(r2, "reply_full"),
         "契约失败：更多 时 reply 必须等于 reply_full")

    must(r2.get("reply_short") == r1.get("reply_short"),
         "契约失败：更多 时 reply_short 应复用缓存")

    must(r2.get("structured") == r1.get("structured"),
         "契约失败：更多 时 structured 应复用缓存")

    # 3) 重置
    r3 = call("重置")
    must(r3.get("ok") is True, "r3 ok != true")
    must(r3.get("done") is False, "重置后 done 应为 false")

    st = r3.get("state") or {}
    must(st.get("size") is None, "reset 后 size 应为 None")
    must(st.get("budget") is None, "reset 后 budget 应为 None")
    must(st.get("scene") is None, "reset 后 scene 应为 None")
    must(st.get("brand") is None, "reset 后 brand 应为 None")

    must(r3.get("reply_short") is None, "reset 后 reply_short 应为 None")
    must(r3.get("reply_full") is None, "reset 后 reply_full 应为 None")
    must(r3.get("structured") is None, "reset 后 structured 应为 None")


# -----------------------------
# Main
# -----------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--session-id", default="t1")
    ap.add_argument("--verbose", type=int, default=0, help="1=打印每次响应")
    ap.add_argument("--timeout", type=float, default=15.0, help="单次请求超时（秒）")
    ap.add_argument("--retries", type=int, default=0, help="失败重试次数（默认0）")
    ap.add_argument("--loops", type=int, default=1, help="循环次数（每次=首问→更多→重置）")
    ap.add_argument("--sleep", type=float, default=0.0, help="每轮之间休眠秒数")
    ap.add_argument("--slow-ms", type=int, default=1500, help="慢请求阈值（毫秒）")
    args = ap.parse_args()

    url = args.base_url.rstrip("/") + "/api/dialog/3p2"
    sid = args.session_id

    for i in range(1, args.loops + 1):
        t0 = time.time()
        try:
            check_contract_once(
                url=url,
                sid=sid,
                verbose=args.verbose,
                timeout=args.timeout,
                retries=args.retries,
                slow_ms=args.slow_ms,
            )
        except AssertionError as e:
            print(f"❌ ASSERT FAIL (round={i}): {e}")
            return 2
        except Exception as e:
            print(f"❌ ERROR (round={i}): {e}")
            return 1

        dt = time.time() - t0
        print(f"✅ ALL PASS (round={i}/{args.loops}): /api/dialog/3p2 contract ok. ({dt:.2f}s)")

        if args.sleep > 0 and i < args.loops:
            time.sleep(args.sleep)

    return 0


if __name__ == "__main__":
    sys.exit(main())
