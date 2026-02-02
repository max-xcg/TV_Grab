# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class SourceNote:
    """用于记录抽取证据来源（简化版：先不做 bbox/page 定位，后续可升级）"""
    kind: str
    snippet: str


@dataclass
class ContrastMeta:
    test_date: Optional[str] = None
    device_id: Optional[str] = None
    inspector: Optional[str] = None
    standard_version: Optional[str] = None

    # 默认预设（按你模板）
    ambient_light_lux: str = "<1 lux"
    room_temperature_c: int = 23
    meter_model: str = "CA-410"
    meter_distance_mm: int = 30


@dataclass
class ContrastMeasurement:
    mode: str
    calibration_target_nits: str = "100 nits"
    black_luminance_cd_m2: List[float] = field(default_factory=list)
    white_luminance_cd_m2: List[float] = field(default_factory=list)
    white_avg_nits: Optional[float] = None
    black_avg_nits: Optional[float] = None
    brightness_note: Optional[str] = None


@dataclass
class ComputedMetric:
    value: Optional[float]
    formula: str
    source_fields: List[str] = field(default_factory=list)


@dataclass
class ContrastRecord:
    meta: ContrastMeta
    native_contrast: ContrastMeasurement
    effective_contrast: ContrastMeasurement

    native_contrast_ratio: ComputedMetric
    effective_contrast_ratio: ComputedMetric
    dimming_gain: ComputedMetric

    extraction_uncertainties: List[str] = field(default_factory=list)
    sources: List[SourceNote] = field(default_factory=list)

    def to_yaml_dict(self) -> Dict[str, Any]:
        d = asdict(self)

        return {
            "contrast_test_record": {
                "meta": {
                    "test_date": d["meta"]["test_date"],
                    "device_id": d["meta"]["device_id"],
                    "inspector": d["meta"]["inspector"],
                    "standard_version": d["meta"]["standard_version"],
                    "test_environment": {
                        "ambient_light_lux": d["meta"]["ambient_light_lux"],
                        "room_temperature_c": d["meta"]["room_temperature_c"],
                    },
                    "instrument": {
                        "meter_model": d["meta"]["meter_model"],
                        "meter_distance_mm": d["meta"]["meter_distance_mm"],
                    },
                },
                "measurements": {
                    "native_contrast": {
                        "mode": d["native_contrast"]["mode"],
                        "calibration_target_nits": d["native_contrast"]["calibration_target_nits"],
                        "black_luminance_cd_m2": d["native_contrast"]["black_luminance_cd_m2"],
                        "white_luminance_cd_m2": d["native_contrast"]["white_luminance_cd_m2"],
                        "white_avg_nits": d["native_contrast"]["white_avg_nits"],
                        "black_avg_nits": d["native_contrast"]["black_avg_nits"],
                    },
                    "effective_contrast": {
                        "mode": d["effective_contrast"]["mode"],
                        "calibration_target_nits": d["effective_contrast"]["calibration_target_nits"],
                        "black_luminance_cd_m2": d["effective_contrast"]["black_luminance_cd_m2"],
                        "white_luminance_cd_m2": d["effective_contrast"]["white_luminance_cd_m2"],
                        "white_avg_nits": d["effective_contrast"]["white_avg_nits"],
                        "black_avg_nits": d["effective_contrast"]["black_avg_nits"],
                        "brightness_note": d["effective_contrast"]["brightness_note"],
                    },
                },
                "computed_metrics": {
                    "native_contrast_ratio": asdict(self.native_contrast_ratio),
                    "effective_contrast_ratio": asdict(self.effective_contrast_ratio),
                    "dimming_gain": asdict(self.dimming_gain),
                },
                "extraction_notes": {
                    "uncertainties": self.extraction_uncertainties
                }
            }
        }

