# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List, Optional
import yaml


def _ensure_list(v) -> List[float]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _to_float_or_none(v):
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _to_str_or_none(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _strip_code_fence(text: str) -> str:
    """
    去掉 ```yaml ... ``` 包裹（防止 LLM 输出带围栏污染最终 YAML）
    """
    t = (text or "").strip()
    if t.startswith("```"):
        # 去掉首行 ```yaml 或 ```
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # 去掉末行 ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return t


def canonize_contrast_record(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    把任何“类似结构”的输入，强制归一化为宪法结构：
    - 所有字段必须存在
    - 类型必须正确（list / float / str / null）
    - 缺失就填 null 或 []
    - 顺序固定（靠 dict 构建顺序 + sort_keys=False）
    """
    root = obj.get("contrast_test_record", obj)

    meta = root.get("meta", {}) or {}
    env = (meta.get("test_environment", {}) or {})
    inst = (meta.get("instrument", {}) or {})

    meas = root.get("measurements", {}) or {}
    native = meas.get("native_contrast", {}) or {}
    eff = meas.get("effective_contrast", {}) or {}

    cm = root.get("computed_metrics", {}) or {}
    ncr = cm.get("native_contrast_ratio", {}) or {}
    ecr = cm.get("effective_contrast_ratio", {}) or {}
    gain = cm.get("dimming_gain", {}) or {}

    notes = root.get("extraction_notes", {}) or {}

    out: Dict[str, Any] = {
        "contrast_test_record": {
            "meta": {
                "test_date": _to_str_or_none(meta.get("test_date")),
                "device_id": _to_str_or_none(meta.get("device_id")),
                "inspector": _to_str_or_none(meta.get("inspector")),
                "standard_version": _to_str_or_none(meta.get("standard_version")),
                "test_environment": {
                    "ambient_light_lux": _to_str_or_none(env.get("ambient_light_lux")) or "<1 lux",
                    "room_temperature_c": int(env.get("room_temperature_c", 23)),
                },
                "instrument": {
                    "meter_model": _to_str_or_none(inst.get("meter_model")) or "CA-410",
                    "meter_distance_mm": int(inst.get("meter_distance_mm", 30)),
                },
            },
            "measurements": {
                "native_contrast": {
                    "mode": _to_str_or_none(native.get("mode")) or "Local Dimming OFF",
                    "calibration_target_nits": _to_str_or_none(native.get("calibration_target_nits")) or "100 nits",
                    "black_luminance_cd_m2": _ensure_list(native.get("black_luminance_cd_m2")),
                    "white_luminance_cd_m2": _ensure_list(native.get("white_luminance_cd_m2")),
                    "white_avg_nits": _to_float_or_none(native.get("white_avg_nits")),
                    "black_avg_nits": _to_float_or_none(native.get("black_avg_nits")),
                },
                "effective_contrast": {
                    "mode": _to_str_or_none(eff.get("mode")) or "Local Dimming High / Auto",
                    "calibration_target_nits": _to_str_or_none(eff.get("calibration_target_nits")) or "100 nits",
                    "black_luminance_cd_m2": _ensure_list(eff.get("black_luminance_cd_m2")),
                    "white_luminance_cd_m2": _ensure_list(eff.get("white_luminance_cd_m2")),
                    "white_avg_nits": _to_float_or_none(eff.get("white_avg_nits")),
                    "black_avg_nits": _to_float_or_none(eff.get("black_avg_nits")),
                    "brightness_note": _to_str_or_none(eff.get("brightness_note")),
                },
            },
            "computed_metrics": {
                "native_contrast_ratio": {
                    "value": _to_float_or_none(ncr.get("value")),
                    "formula": _to_str_or_none(ncr.get("formula")) or "white_avg_nits / black_avg_nits",
                    "source_fields": ncr.get("source_fields") or [
                        "measurements.native_contrast.white_avg_nits",
                        "measurements.native_contrast.black_avg_nits",
                    ],
                },
                "effective_contrast_ratio": {
                    "value": _to_float_or_none(ecr.get("value")),
                    "formula": _to_str_or_none(ecr.get("formula")) or "white_avg_nits / black_avg_nits",
                    "source_fields": ecr.get("source_fields") or [
                        "measurements.effective_contrast.white_avg_nits",
                        "measurements.effective_contrast.black_avg_nits",
                    ],
                },
                "dimming_gain": {
                    "value": _to_float_or_none(gain.get("value")),
                    "formula": _to_str_or_none(gain.get("formula")) or "effective_contrast_ratio / native_contrast_ratio",
                },
            },
            "extraction_notes": {
                "uncertainties": notes.get("uncertainties") or [],
            },
        }
    }
    return out


def validate_contrast_record(obj: Dict[str, Any]) -> None:
    """
    只做“结构宪法”校验：缺字段/类型错就 raise。
    """
    r = obj.get("contrast_test_record")
    if not isinstance(r, dict):
        raise ValueError("必须包含顶层字段：contrast_test_record (dict)")

    # 必要顶层
    for k in ["meta", "measurements", "computed_metrics", "extraction_notes"]:
        if k not in r:
            raise ValueError(f"contrast_test_record 缺少字段：{k}")

    # measurements
    m = r["measurements"]
    for k in ["native_contrast", "effective_contrast"]:
        if k not in m:
            raise ValueError(f"measurements 缺少字段：{k}")

    # 列表字段类型
    nc = m["native_contrast"]
    ec = m["effective_contrast"]
    for k in ["black_luminance_cd_m2", "white_luminance_cd_m2"]:
        if not isinstance(nc.get(k), list):
            raise ValueError(f"native_contrast.{k} 必须是 list")
        if not isinstance(ec.get(k), list):
            raise ValueError(f"effective_contrast.{k} 必须是 list")

    # computed_metrics 必备字段
    cm = r["computed_metrics"]
    for k in ["native_contrast_ratio", "effective_contrast_ratio", "dimming_gain"]:
        if k not in cm:
            raise ValueError(f"computed_metrics 缺少字段：{k}")


def canonize_and_validate_yaml_text(yaml_text: str) -> str:
    """
    输入可能是 LLM 输出的 yaml（可能带 ``` 围栏），输出“宪法化后的最终 YAML 文本”
    """
    cleaned = _strip_code_fence(yaml_text)
    obj = yaml.safe_load(cleaned) or {}
    canon = canonize_contrast_record(obj)
    validate_contrast_record(canon)
    return yaml.safe_dump(canon, allow_unicode=True, sort_keys=False)
