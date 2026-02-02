# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from tv_buy_1_0.g2_lab.report.contrast_report import generate_contrast_report
from tv_buy_1_0.g2_lab.report.postprocess import split_output

router = APIRouter(prefix="/api/report", tags=["g2_report"])


# =========================================================
# 路径修复：确保 report 层使用相对路径时能找到 prompts
# - __file__ = tv_buy_1_0/g2_lab/api/router_report_contrast.py
# - parents[2] = tv_buy_1_0/
# =========================================================
TVBUY_ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def _chdir(temp_dir: Path):
    old = os.getcwd()
    os.chdir(str(temp_dir))
    try:
        yield
    finally:
        os.chdir(old)


# =========================================================
# Request schema
# =========================================================
class ContrastReportRequest(BaseModel):
    # 1) 直接给对象：{contrast_test_record: {...}}
    contrast_test_record: Optional[Dict[str, Any]] = None
    # 2) 直接给完整 yaml 文本（包含 contrast_test_record 或就是它本身）
    yaml_text: Optional[str] = None


def normalize_input(req: ContrastReportRequest) -> Dict[str, Any]:
    """
    兼容两种输入：
    - req.contrast_test_record: dict
    - req.yaml_text: str（可为完整 YAML，或只包含 contrast_test_record）
    """
    if req.contrast_test_record is not None and isinstance(req.contrast_test_record, dict):
        return req.contrast_test_record

    if req.yaml_text is not None and isinstance(req.yaml_text, str):
        data = yaml.safe_load(req.yaml_text)
        if isinstance(data, dict) and "contrast_test_record" in data:
            return data["contrast_test_record"]
        if isinstance(data, dict):
            return data

    raise ValueError("必须提供 contrast_test_record(dict) 或 yaml_text(str)")


@router.post("/contrast")
def report_contrast(req: ContrastReportRequest):
    """
    输入：contrast_test_record / yaml_text
    输出：analysis_text + editorial_verdict_yaml + raw_output
    """
    try:
        contrast_record = normalize_input(req)

        # ✅ 关键：临时切到 tv_buy_1_0 根目录，保证 report 层相对路径可用
        with _chdir(TVBUY_ROOT):
            meta, raw_output = generate_contrast_report(contrast_record)

        analysis_text, editorial_yaml = split_output(raw_output)

        editorial_obj = None
        if editorial_yaml:
            try:
                editorial_obj = yaml.safe_load(editorial_yaml)
            except Exception:
                editorial_obj = None

        return {
            "kind": "contrast",
            "model": meta.get("model"),
            "prompt": {
                "id": meta.get("prompt_id"),
                "version": meta.get("prompt_version"),
            },
            "analysis_text": analysis_text,
            "editorial_verdict_yaml": editorial_yaml,
            "editorial_verdict": editorial_obj,
            "raw_output": raw_output,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"report generation failed: {e}")
