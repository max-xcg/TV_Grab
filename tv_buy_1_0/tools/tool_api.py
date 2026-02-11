# -*- coding: utf-8 -*-
"""
FastAPI Router: Tools
- GET  /api/tools/schema
- POST /api/tools/call
"""

from __future__ import annotations
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from tv_buy_1_0.tools.tool_schema import get_tools, VERSION as SCHEMA_VERSION
from tv_buy_1_0.tools.tool_runner import run_tool, VERSION as RUNNER_VERSION

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("/schema")
def tools_schema():
    return {
        "ok": True,
        "version": SCHEMA_VERSION,
        "tools": get_tools(),
    }


class ToolCallReq(BaseModel):
    name: str
    arguments: Dict[str, Any]
    request_id: Optional[str] = "dev"


@router.post("/call")
def tools_call(req: ToolCallReq):
    result = run_tool(req.name, req.arguments or {})
    return {
        "ok": True if result.get("ok") else False,
        "version": RUNNER_VERSION,
        "request_id": req.request_id,
        "name": req.name,
        **result,
    }
