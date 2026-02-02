# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple


import yaml
from PIL import Image

# ✅ 关键：Web 从 TV_Grab 根目录启动（python -m uvicorn tv_buy_1_0.web.app:app）
# 所以不能 from llm.xxx，要从 tv_buy_1_0.llm.xxx 导入
try:
    # ✅ 豆包 Vision（火山方舟 Ark）
    from tv_buy_1_0.llm.doubao_vision import chat_with_images
except ModuleNotFoundError:
    # 兜底：如果你从 tv_buy_1_0 目录里直接跑脚本（非 package 模式）
    from llm.doubao_vision import chat_with_images


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
    """
    - white_avg: 通常 3 位
    - ratio/gain: 通常 2 位
    """
    if x is None:
        return None
    return round(x, nd)


def _pick_black(nums: List[float], n: int = 8) -> List[float]:
    # 黑场：0 < x < 1，取最小的 n 个更像黑
    cand = [x for x in nums if 0 < x < 1.0]
    cand.sort()
    return cand[:n]


def _pick_white(nums: List[float], n: int = 8) -> List[float]:
    # 白场：优先 80~150（你示例都是 103~114）
    cand_all = [x for x in nums if x > 50.0]
    cand_mid = [x for x in cand_all if 80.0 <= x <= 150.0]

    use = cand_mid if len(cand_mid) >= n else cand_all

    # 按接近 110 排序（100nits目标下，白点一般在100上下）
    use.sort(key=lambda v: abs(v - 110.0))
    return use[:n]


def _find_brightness_note(text: str) -> Optional[str]:
    t = (text or "").replace("\n", " ")

    # 中文：100nits目标亮度不可满足
    m = re.search(r"(100\s*nits?.{0,18}?(不可满足|无法|达不到|失败))", t, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # 更宽松兜底
    if "100" in t and ("不可满足" in t or "达不到" in t):
        return "100nits目标亮度不可满足"

    return None


def _crop_table_box(image_path: str) -> Tuple[int, int, int, int]:
    """
    ✅ 裁剪到表格区域（避免标题/日期被混入）
    通用比例：后续你发现不同截图布局，可微调比例。
    """
    img = Image.open(image_path)
    w, h = img.size

    left = int(w * 0.05)
    right = int(w * 0.95)
    top = int(h * 0.20)
    bottom = int(h * 0.78)

    return (left, top, right, bottom)


def _ocr_image_via_doubao(
    image_path: str,
    *,
    numeric_only: bool,
    crop_box=None,
) -> str:
    """
    用 豆包 Vision 识别图片，返回文本。
    - numeric_only=True: 只输出数字（空格分隔）
    - crop_box: 先裁剪再发给模型（提高稳定性）
    """
    send_path = image_path

    # 先裁剪 ROI
    if crop_box is not None:
        img = Image.open(image_path).convert("RGB")
        img = img.crop(crop_box)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        tmp.close()
        img.save(tmp.name)
        send_path = tmp.name

    system_prompt = "你是一个严谨的图像文字识别助手，只按要求输出结果，不要解释。"

    if numeric_only:
        user_text = (
            "请读取图片中的表格数据，只输出所有数字（包含小数），"
            "按从上到下、从左到右顺序排列，用空格分隔。"
            "不要输出任何中文、单位、符号、标题或多余文字。"
        )
    else:
        user_text = (
            "请识别图片中的文字说明（尤其是表格下方的说明/备注区域）。"
            "只输出识别到的中文说明文本，不要输出数字列表，不要解释。"
        )

    try:
        out = chat_with_images(system_prompt, user_text, [send_path])
        return (out or "").strip()
    finally:
        # 清理临时文件
        if crop_box is not None and send_path != image_path:
            try:
                Path(send_path).unlink(missing_ok=True)
            except Exception:
                pass


def contrast_yaml_from_two_images(native_path: str, effective_path: str) -> str:
    # 1) 数值 OCR（裁剪 + numeric_only）
    native_num_ocr = _ocr_image_via_doubao(
        native_path, numeric_only=True, crop_box=_crop_table_box(native_path)
    )
    eff_num_ocr = _ocr_image_via_doubao(
        effective_path, numeric_only=True, crop_box=_crop_table_box(effective_path)
    )

    # 2) 全文 OCR（用来抓 brightness_note）
    native_full_ocr = _ocr_image_via_doubao(native_path, numeric_only=False, crop_box=None)
    eff_full_ocr = _ocr_image_via_doubao(effective_path, numeric_only=False, crop_box=None)

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

    # 6) 组装 YAML
    record: Dict[str, Any] = {
        "contrast_test_record": {
            "meta": {
                "test_date": None,
                "device_id": None,
                "inspector": None,
                "standard_version": None,
                "test_environment": {"ambient_light_lux": "<1 lux", "room_temperature_c": 23},
                "instrument": {"meter_model": "CA-410", "meter_distance_mm": 30},
            },
            "measurements": {
                "native_contrast": {
                    "mode": "Local Dimming OFF",
                    "calibration_target_nits": "100 nits",
                    "black_luminance_cd_m2": native_black,
                    "white_luminance_cd_m2": native_white,
                    "white_avg_nits": _round_keep(native_white_avg, 3) if native_white_avg is not None else None,
                    # black_avg 保留更多位
                    "black_avg_nits": native_black_avg,
                },
                "effective_contrast": {
                    "mode": "Local Dimming High / Auto",
                    "calibration_target_nits": "100 nits",
                    "black_luminance_cd_m2": eff_black,
                    "white_luminance_cd_m2": eff_white,
                    # ✅ 修正：这里也用 3 位，和你示例一致 107.880
                    "white_avg_nits": _round_keep(eff_white_avg, 3) if eff_white_avg is not None else None,
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
            "extraction_notes": {"uncertainties": []},
        }
    }

    # 7) 点位不足 -> uncertainties
    def _note(name: str, got: int, want: int = 8):
        if got < want:
            record["contrast_test_record"]["extraction_notes"]["uncertainties"].append(
                f"{name} 点位不足：识别到 {got}/{want}（建议调 crop_box 或换更清晰截图）"
            )

    _note("native_black", len(native_black))
    _note("native_white", len(native_white))
    _note("effective_black", len(eff_black))
    _note("effective_white", len(eff_white))

    # 8) 固定备注：平均值来源
    record["contrast_test_record"]["extraction_notes"]["uncertainties"].append(
        "平均值由逐点数据计算得出"
    )

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


# -------------------------
# 兼容旧接口命名（router 里可能 import contrast_yaml_from_images）
# -------------------------
def contrast_yaml_from_images(native_path: str, effective_path: str) -> str:
    return contrast_yaml_from_two_images(native_path, effective_path)


def save_contrast_yaml(yaml_text: str, out_dir: str = "summaries/contrast_records", prefix: str = "contrast") -> str:
    return save_contrast_yaml_text(yaml_text, out_dir=out_dir, prefix=prefix)
