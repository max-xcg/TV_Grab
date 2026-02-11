# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any, Dict, Optional

TOOL_DEFAULT_BASE = "http://127.0.0.1:8000"


class ToolClient:
    """
    调用本地工具服务：
      POST /api/tools/call
    """

    def __init__(self, base_url: str = TOOL_DEFAULT_BASE, timeout: int = 25):
        self.base_url = (base_url or TOOL_DEFAULT_BASE).rstrip("/")
        self.timeout = int(timeout)

    # -------------------------
    # low-level
    # -------------------------
    def call(self, name: str, arguments: Dict[str, Any], request_id: str = "dev") -> Dict[str, Any]:
        url = f"{self.base_url}/api/tools/call"
        payload = {
            "request_id": request_id,
            "name": name,
            "arguments": arguments or {},
        }

        # 尽量用 requests；没有就降级 urllib
        try:
            import requests  # type: ignore
            r = requests.post(url, json=payload, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            # urllib fallback（极少用到）
            try:
                import urllib.request
                req = urllib.request.Request(
                    url=url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
            except Exception as e2:
                raise RuntimeError(f"ToolClient.call failed: {e} / {e2}")

        if not isinstance(data, dict) or not data.get("ok"):
            raise RuntimeError(f"tool call failed: name={name}, resp={data}")

        return data.get("data") or {}

    # -------------------------
    # high-level tool wrappers
    # -------------------------
    def tv_search(
        self,
        size: int,
        budget_max: int,
        brand: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        request_id: str = "dev",
    ) -> Dict[str, Any]:
        return self.call(
            "tv_search",
            {
                "size": int(size),
                "budget_max": int(budget_max),
                "brand": brand,
                "limit": int(limit),
                "offset": int(offset),
            },
            request_id=request_id,
        )

    def tv_rank(
        self,
        size: int,
        scene: str,
        brand: Optional[str] = None,
        budget_max: Optional[int] = None,
        prefer_year: int = 2026,
        top: int = 50,
        request_id: str = "dev",
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {
            "size": int(size),
            "scene": str(scene),
            "brand": brand,
            "budget_max": budget_max,
            "prefer_year": int(prefer_year),
            "top": int(top),
        }
        return self.call("tv_rank", args, request_id=request_id)

    def tv_compare(
        self,
        size: int,
        scene: str,
        brand: Optional[str] = None,
        budget_max: Optional[int] = None,
        prefer_year: int = 2026,
        request_id: str = "dev",
    ) -> Dict[str, Any]:
        return self.call(
            "tv_compare",
            {
                "size": int(size),
                "scene": str(scene),
                "brand": brand,
                "budget_max": budget_max,
                "prefer_year": int(prefer_year),
            },
            request_id=request_id,
        )

    def tv_pick(
        self,
        size: int,
        scene: str,
        pick: str,
        brand: Optional[str] = None,
        budget: Optional[int] = None,
        prefer_year: int = 2026,
        request_id: str = "dev",
    ) -> Dict[str, Any]:
        return self.call(
            "tv_pick",
            {
                "size": int(size),
                "scene": str(scene),
                "brand": brand,
                "budget": budget,
                "prefer_year": int(prefer_year),
                "pick": str(pick).upper(),
            },
            request_id=request_id,
        )
