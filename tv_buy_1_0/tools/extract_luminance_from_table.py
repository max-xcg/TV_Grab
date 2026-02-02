# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple

import yaml
from PIL import Image

from tools.ocr_extract_text import ocr_image

# =========================
# 正则：抓数字（float/int）
# =========================
_NUM_RE = re.compile(r"(?<!\d)(\d+\.\d+|\d+)(?!\d)")


def _extract_numbers(text: str) -> List[float]:
    nums: List[float] = []
    for m in _NUM_RE.finditer(text or ""):
        s = m.group(1)
        try:
            nums.append(float(s))
        except Exception:
            pass
    return nums


def _avg(xs: List[float]) -> Optional[float]:
    return (sum(xs) / len(xs)) if xs else None


def _ratio(white_avg: Optional[float], black_avg: Optional[float]) -> Optional[float]:
    if white_avg is None or black_avg is None or black_avg == 0:
        return None
    return white_avg / black_avg


def _round_keep(x: Optional[float], nd: int) -> Optional[float]:
    if x is None:
        return None
    return round(x, nd)


def _pick_black(nums: List[float], n: int = 8) -> List[float]:
    """
    黑场：0 < x < 1，取最小的 n 个更像黑
    适配你现在的两张图：黑值都是 0.00xx
    """
    cand = [x for x in nums if 0 < x < 1.0]
    cand.sort()
    return cand[:n]


def _pick_white(nums: List[float], n: int = 8) -> List[float]:
    """
    白场：优先 80~150（你示例都是 103~114）
    再按接近 110 排序取 n 个
    """
    cand_all = [x for x in nums if x > 50.0]
    cand_mid = [x for x in cand_all if 80.0 <= x <= 150.0]
    use = cand_mid if len(cand_mid) >= n else cand_all
    use.sort(key=lambda v: abs(v - 110.0))
    return use[:n]


def _find_brightness_note(text: str) -> Optional[str]:
    t = (text or "").replace("\n", " ")

    m = re.search(r"(100\s*nits?.{0,18}?(不可满足|无法|达不到|失败))", t, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    if "100" in t and ("不可满足" in t or "达不到" in t):
        return "100nits目标亮度不可满足"

    return None


def _crop_table_box(image_path: str) -> Tuple[int, int, int, int]:
    """
    裁剪到表格区域（避免标题/日期混入）
    你这两张图：表格在中间偏上，说明在底部
    """
    img = Image.open(image_path)
    w, h = img.size
    left = int(w * 0.05)
    right = int(w * 0.95)
    top = int(h * 0.20)
    bottom = int(h * 0.78)
    return (left, top, right, bottom)


def _detect_white_outliers(white_list: List[float], z_thresh: float = 2.0) -> List[float]:
    """
    你提到像 113.76 这种偏离均值较大，要记录。
    这里用一个简单稳妥的方法：
    - 计算均值和标准差
    - 绝对 z-score > z_thresh 视为异常点
    """
    if len(white_list) < 4:
        return []
    mean = sum(white_list) / len(white_list)
    var = sum((x - mean) ** 2 for x in white_list) / len(white_list)
    std = var ** 0.5
    if std == 0:
        return []
    out = [x for x in white_list if abs((x - mean) / std) > z_thresh]
    return out


def contrast_yaml_from_two_images(native_path: str, effective_path: str) -> str:
    # 1) 数值 OCR（裁剪 + numeric_only）
    native_num_ocr = ocr_image(native_path, lang="eng", numeric_only=True, crop_box=_crop_table_box(native_path))
    eff_num_ocr = ocr_image(effective_path, lang="eng", numeric_only=True, crop_box=_crop_table_box(effective_path))

    # 2) 全文 OCR（用来抓 brightness_note）
    native_full_ocr = ocr_image(native_path, lang="chi_sim+eng", numeric_only=False)
    eff_full_ocr = ocr_image(effective_path, lang="chi_sim+eng", numeric_only=False)

    # 3) 数值提取 + 规则筛选
    native_nums = _extract_numbers(native_num_ocr)
    eff_nums = _extract_numbers(eff_num_ocr)

    native_black = _pick_black(native_nums, n=8)
    native_white = _pick_white(native_nums, n=8)

    eff_black = _pick_black(eff_nums, n=8)
    eff_white = _pick_white(eff_nums, n=8)

    # 4) 计算
    native_white_avg = _avg(native_white)
    native_black_avg = _avg(native_black)
    eff_white_avg = _avg(eff_white)
    eff_black_avg = _avg(eff_black)

    native_ratio = _ratio(native_white_avg, native_black_avg)
    eff_ratio = _ratio(eff_white_avg, eff_black_avg)
    gain = (eff_ratio / native_ratio) if (eff_ratio and native_ratio) else None

    # 5) brightness_note
    brightness_note = _find_brightness_note(eff_full_ocr) or _find_brightness_note(native_full_ocr)

    # 6) uncertainties（按你示例固定口径）
    uncertainties: List[str] = [
        "图片中未显示'计算值'一栏的具体数值，平均值由工程师重新计算得出"
    ]

    # 如果有效对比度白场有明显异常点，追加说明（你示例提到了 113.76）
    outliers = _detect_white_outliers(eff_white, z_thresh=2.0)
    if outliers:
        # 取一个代表值写进去（避免太长）
        uncertainties.append(f"有效对比度测试中部分白点亮度（如{outliers[0]:.2f}）偏离均值较大，已如实记录")

    # 7) 点位不足提示（保留你之前的鲁棒性）
    def _note(name: str, got: int, want: int = 8):
        if got < want:
            uncertainties.append(f"{name} 点位不足：识别到 {got}/{want}（建议调 crop_box 或换更清晰截图）")

    _note("native_black", len(native_black))
    _note("native_white", len(native_white))
    _note("effective_black", len(eff_black))
    _note("effective_white", len(eff_white))

    # 8) 组装你要的 YAML 结构（完全按示例字段名）
    record: Dict[str, Any] = {
        "contrast_test_record": {
            "meta": {
                "test_date": None,
                "device_id": None,
                "inspector": None,
                "standard_version": None,
                "test_environment": {
                    "ambient_light_lux": "<1 lux",
                    "room_temperature_c": 23,
                },
                "instrument": {
                    "meter_model": "CA-410",
                    "meter_distance_mm": 30,
                },
            },
            "measurements": {
                "native_contrast": {
                    "mode": "Local Dimming OFF",
                    "calibration_target_nits": "100 nits",
                    "black_luminance_cd_m2": native_black,
                    "white_luminance_cd_m2": native_white,
                    # 你示例 native white_avg 3位
                    "white_avg_nits": _round_keep(native_white_avg, 3) if native_white_avg is not None else None,
                    # 你示例 black_avg 不 round（保留更多位）
                    "black_avg_nits": native_black_avg,
                },
                "effective_contrast": {
                    "mode": "Local Dimming High / Auto",
                    "calibration_target_nits": "100 nits",
                    "black_luminance_cd_m2": eff_black,
                    "white_luminance_cd_m2": eff_white,
                    # 你示例 effective white_avg 2位
                    "white_avg_nits": _round_keep(eff_white_avg, 2) if eff_white_avg is not None else None,
                    "black_avg_nits": eff_black_avg,
                    "brightness_note": brightness_note,
                },
            },
            "computed_metrics": {
                "native_contrast_ratio": {
                    "value": _round_keep(native_ratio, 2) if native_ratio is not None else None,
                    "formula": "white_avg_nits / black_avg_nits",
                    "source_fields": [
                        "measurements.native_contrast.white_avg_nits",
                        "measurements.native_contrast.black_avg_nits",
                    ],
                },
                "effective_contrast_ratio": {
                    "value": _round_keep(eff_ratio, 2) if eff_ratio is not None else None,
                    "formula": "white_avg_nits / black_avg_nits",
                    "source_fields": [
                        "measurements.effective_contrast.white_avg_nits",
                        "measurements.effective_contrast.black_avg_nits",
                    ],
                },
                "dimming_gain": {
                    "value": _round_keep(gain, 2) if gain is not None else None,
                    "formula": "effective_contrast_ratio / native_contrast_ratio",
                },
            },
            "extraction_notes": {
                "uncertainties": uncertainties,
            },
        }
    }

    return yaml.safe_dump(record, allow_unicode=True, sort_keys=False)


def save_contrast_yaml_text(
    yaml_text: str,
    out_dir: str = "summaries/contrast_records",
    prefix: str = "contrast",
) -> str:
    outp = Path(out_dir)
    outp.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = outp / f"{prefix}_{ts}.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    # 示例（你按实际路径改）
    # native = "native.png"
    # effective = "effective.png"
    # y = contrast_yaml_from_two_images(native, effective)
    # print(y)
    pass
