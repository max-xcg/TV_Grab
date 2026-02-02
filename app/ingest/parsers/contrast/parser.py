# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from PIL import Image

from .ocr import OcrEngine, TesseractOcrEngine
from .models import (
    ContrastMeta, ContrastMeasurement, ContrastRecord,
    ComputedMetric, SourceNote
)

# 仅抓 “带小数点的数”（与你的黑/白点位一致：0.0149 / 105.22 这种）
_FLOAT_RE = re.compile(r"(?<!\d)(\d+\.\d+)(?!\d)")
_DATE_RE = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})")


def _fmt_avg(x: Optional[float]) -> Optional[float]:
    """
    平均值格式化：
    - 白场：通常保留 3 位小数（104.885）
    - 黑场：保留 7 位（0.0156625），并去掉尾随 0
    - 禁止科学计数法：用字符串格式化再转 float
    """
    if x is None:
        return None
    if x > 1:
        return float(f"{x:.3f}")
    return float(f"{x:.7f}".rstrip("0").rstrip("."))


def _fmt_ratio2(x: Optional[float]) -> Optional[float]:
    """
    computed_metrics.value：
    - 对比度比值 / dimming_gain：保留 2 位小数（6696.57 / 6.64）
    """
    if x is None:
        return None
    return float(f"{x:.2f}")


def _extract_floats(text: str) -> List[float]:
    vals: List[float] = []
    for m in _FLOAT_RE.finditer(text):
        try:
            vals.append(float(m.group(1)))
        except Exception:
            continue
    return vals


def _classify_bw(values: List[float]) -> Tuple[List[float], List[float]]:
    """
    G2 规则：
    - < 1.0 视为黑场亮度
    - > 50.0 视为白场亮度
    """
    black = [v for v in values if v < 1.0]
    white = [v for v in values if v > 50.0]
    return black, white


def _avg(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / float(len(values))


def _pick_first_date(text: str) -> Optional[str]:
    m = _DATE_RE.search(text)
    if not m:
        return None
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    return f"{y}-{mo:02d}-{d:02d}"


def _extract_meta_fields(text: str) -> ContrastMeta:
    """
    针对“表格型 meta 图”的 OCR 文本做关键词提取。
    严格：抓不到就 None（不推断）
    """
    meta = ContrastMeta()

    # 日期
    meta.test_date = _pick_first_date(text)

    # 设备ID：尽量抓含“设备/Device/ID”或品牌关键词的行（抓不到就是 None）
    dev_candidates: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if ("设备" in s) or ("Device" in s) or ("ID" in s):
            dev_candidates.append(s)
        if re.search(r"(海信|Hisense|TCL|Sony|SAMSUNG|LG|华为|HUAWEI)", s, re.I):
            dev_candidates.append(s)

    if dev_candidates:
        meta.device_id = max(dev_candidates, key=len)

    # 测试工程师/Inspector：同样只抓文本中可见内容
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if re.search(r"(测试工程师|Inspector)", s, re.I):
            meta.inspector = s
            break
        if re.fullmatch(r"[A-Z]{2,6}", s):
            if meta.inspector is None:
                meta.inspector = s

    # 标准版本
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if ("SOP" in s) or ("Standard" in s) or ("G2_" in s):
            meta.standard_version = s
            break

    return meta


class ContrastImageParser:
    """
    输入：
      - native_img_path: 原生对比度（Local Dimming OFF）
      - effective_img_path: 有效对比度（Local Dimming ON/High）
      - meta_img_path: meta 表格（可选）

    输出：
      - ContrastRecord（最终可转成 contrast_test_record YAML）
    """

    def __init__(self, ocr: Optional[OcrEngine] = None):
        self.ocr = ocr or TesseractOcrEngine()

    def parse(
        self,
        native_img_path: str,
        effective_img_path: str,
        meta_img_path: Optional[str] = None,
    ) -> ContrastRecord:

        # --- OCR ---
        native_img = Image.open(native_img_path).convert("RGB")
        effective_img = Image.open(effective_img_path).convert("RGB")

        native_txt = self.ocr.image_to_text(native_img).text
        effective_txt = self.ocr.image_to_text(effective_img).text

        meta_txt = ""
        if meta_img_path:
            meta_img = Image.open(meta_img_path).convert("RGB")
            meta_txt = self.ocr.image_to_text(meta_img).text

        # --- Extract numeric values ---
        native_vals = _extract_floats(native_txt)
        effective_vals = _extract_floats(effective_txt)

        native_black, native_white = _classify_bw(native_vals)
        eff_black, eff_white = _classify_bw(effective_vals)

        # --- Build measurements (avg 用 _fmt_avg) ---
        native = ContrastMeasurement(
            mode="Local Dimming OFF",
            calibration_target_nits="100 nits",
            black_luminance_cd_m2=native_black,
            white_luminance_cd_m2=native_white,
            white_avg_nits=_fmt_avg(_avg(native_white)),
            black_avg_nits=_fmt_avg(_avg(native_black)),
        )

        effective = ContrastMeasurement(
            mode="Local Dimming High / Auto",
            calibration_target_nits="100 nits",
            black_luminance_cd_m2=eff_black,
            white_luminance_cd_m2=eff_white,
            white_avg_nits=_fmt_avg(_avg(eff_white)),
            black_avg_nits=_fmt_avg(_avg(eff_black)),
            brightness_note=self._extract_brightness_note(effective_txt),
        )

        # --- Meta ---
        meta = _extract_meta_fields(meta_txt) if meta_txt else ContrastMeta()

        # --- Compute metrics (计算用原始 float；写入时用 _fmt_ratio2) ---
        native_ratio: Optional[float] = None
        if (
            native.white_avg_nits is not None
            and native.black_avg_nits is not None
            and native.black_avg_nits != 0
        ):
            native_ratio = native.white_avg_nits / native.black_avg_nits

        effective_ratio: Optional[float] = None
        if (
            effective.white_avg_nits is not None
            and effective.black_avg_nits is not None
            and effective.black_avg_nits != 0
        ):
            effective_ratio = effective.white_avg_nits / effective.black_avg_nits

        dimming_gain: Optional[float] = None
        if (
            native_ratio is not None
            and effective_ratio is not None
            and native_ratio != 0
        ):
            dimming_gain = effective_ratio / native_ratio

        # --- Assemble record ---
        rec = ContrastRecord(
            meta=meta,
            native_contrast=native,
            effective_contrast=effective,

            native_contrast_ratio=ComputedMetric(
                value=_fmt_ratio2(native_ratio) if native_ratio is not None else None,
                formula="white_avg_nits / black_avg_nits",
                source_fields=[
                    "measurements.native_contrast.white_avg_nits",
                    "measurements.native_contrast.black_avg_nits",
                ],
            ),
            effective_contrast_ratio=ComputedMetric(
                value=_fmt_ratio2(effective_ratio) if effective_ratio is not None else None,
                formula="white_avg_nits / black_avg_nits",
                source_fields=[
                    "measurements.effective_contrast.white_avg_nits",
                    "measurements.effective_contrast.black_avg_nits",
                ],
            ),
            dimming_gain=ComputedMetric(
                value=_fmt_ratio2(dimming_gain) if dimming_gain is not None else None,
                formula="effective_contrast_ratio / native_contrast_ratio",
                source_fields=[],
            ),
        )

        # --- Notes / uncertainties ---
        # 若图片未标注 AVG/Mean 且我们计算了 avg，按规则写说明
        if not re.search(r"(AVG|Mean)", native_txt, re.I):
            if native.white_avg_nits is not None or native.black_avg_nits is not None:
                rec.extraction_uncertainties.append("图片中未显示'计算值'一栏的具体数值，平均值由逐点数据计算得出（原生对比度）")
        if not re.search(r"(AVG|Mean)", effective_txt, re.I):
            if effective.white_avg_nits is not None or effective.black_avg_nits is not None:
                rec.extraction_uncertainties.append("图片中未显示'计算值'一栏的具体数值，平均值由逐点数据计算得出（有效对比度）")

        # 保存 OCR 片段（后续入库 sources 表时可用）
        rec.sources.append(SourceNote(kind="ocr_text_native", snippet=native_txt[:800]))
        rec.sources.append(SourceNote(kind="ocr_text_effective", snippet=effective_txt[:800]))
        if meta_txt:
            rec.sources.append(SourceNote(kind="ocr_text_meta", snippet=meta_txt[:800]))

        return rec

    @staticmethod
    def _extract_brightness_note(text: str) -> Optional[str]:
        """
        抓取图片底部说明，例如：
          - “目标亮度100nits，状态为：原生”
          - “100nits目标亮度不可满足”
        """
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for ln in lines[::-1]:
            if ("100" in ln and "nits" in ln.lower()) or ("目标亮度" in ln) or ("不可满足" in ln):
                return ln
        return None
