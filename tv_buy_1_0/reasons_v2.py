# -*- coding: utf-8 -*-
"""
reasons_v2.py  （完整版｜可一键复制粘贴替换）

专注【表达层】：
- PS5 / Movie / Bright 三场景真人导购话术（规则引擎层）
- 明确区分：参数缺失 vs 参数偏弱
- 品牌性格（当前先做 TCL，可按同结构扩展）
- Top1 总结：每个场景 2 句真人导购版本

注意：
- 这里不做“计算/推荐排序”，只负责“解释怎么说”
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple, Optional


# =========================
# 工具：安全取值 / 规范化
# =========================
def _norm_brand(b: Any) -> str:
    if not b:
        return ""
    return str(b).strip()


def _to_bool(x: Any) -> Optional[bool]:
    """把 0/1/True/False/'有'/'无' 等转成 bool 或 None（未知/缺失）"""
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        # 兼容 1/0/1.0/0.0
        try:
            return bool(int(x))
        except Exception:
            return None
    s = str(x).strip().lower()
    if s in ("1", "true", "yes", "y", "有", "支持", "是", "ok"):
        return True
    if s in ("0", "false", "no", "n", "无", "不支持", "否"):
        return False
    return None


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        # 兼容 "8500 nits" / "＜0.99" 这种，尽量抽数字
        s = str(x)
        m = re.search(r"([0-9]+(\.[0-9]+)?)", s)
        if not m:
            return None
        try:
            return float(m.group(1))
        except Exception:
            return None


def _to_int(x: Any) -> Optional[int]:
    v = _to_float(x)
    if v is None:
        return None
    try:
        return int(round(v))
    except Exception:
        return None


def _fmt_money(x: Any) -> str:
    v = _to_int(x)
    return "?" if v is None else str(v)


def _yn_cn(x: Optional[bool]) -> str:
    if x is None:
        return "【参数缺失】需确认"
    return "支持" if x else "【参数偏弱】不支持"


# =========================
# 品牌性格（先只做 TCL）
# =========================
def brand_personality(brand: str) -> str:
    if not brand:
        return ""
    b = str(brand).strip().lower()
    if b == "tcl":
        return "TCL 一贯偏参数取向，新款规格给得激进；首发期更建议结合一轮实测/口碑再拍板。"
    return ""


# =========================
# PS5 场景：真人导购 reasons（短版）
# =========================
def reasons_ps5_v2(tv: Dict[str, Any]) -> Tuple[List[str], str]:
    r: List[str] = []

    # 1) 输入延迟（明确：缺失 vs 偏弱）
    lagv = _to_float(tv.get("input_lag_ms_60hz"))
    if lagv is None:
        r.append("输入延迟：【参数缺失】暂未公开（不是差），但硬核竞技玩家建议等实测再下单。")
    else:
        if lagv <= 6:
            r.append(f"输入延迟：约 {lagv:g}ms（很快），动作/射击更跟手。")
        elif lagv <= 12:
            r.append(f"输入延迟：约 {lagv:g}ms（够用偏上），画质与响应更均衡。")
        else:
            r.append(f"输入延迟：约 {lagv:g}ms（【参数偏弱】偏慢），更适合剧情/休闲。")

    # 2) HDMI2.1 / ALLM / VRR（缺失就写缺失；明确不支持写偏弱）
    hdmi21 = tv.get("hdmi_2_1_ports")
    try:
        hdmi21i = int(hdmi21) if hdmi21 is not None else 0
    except Exception:
        hdmi21i = 0

    allm = _to_bool(tv.get("allm"))
    vrr = _to_bool(tv.get("vrr"))

    if hdmi21i >= 2:
        r.append(f"接口：HDMI 2.1 ×{hdmi21i}，多主机/回音壁接线更从容。")
    elif hdmi21i == 1:
        r.append("接口：HDMI 2.1 ×1（够单主机），多设备需要取舍。")
    else:
        r.append("接口：HDMI 2.1 口数【参数缺失/偏少】（多设备玩家注意）。")

    r.append(f"游戏功能：ALLM {_yn_cn(allm)}；VRR {_yn_cn(vrr)}。")

    # 3) HDR 观感（亮度/分区，避免“乱夸”，>6000 加口径提醒）
    brightness = _to_int(tv.get("peak_brightness_nits"))
    zones = _to_int(tv.get("local_dimming_zones"))

    if brightness is None:
        r.append("HDR 亮度：【参数缺失】未公开，冲击力要等实测/后续口径。")
    else:
        if brightness >= 3000:
            tip = "（⚠️口径可能偏激进，建议看实测）" if brightness >= 6000 else ""
            r.append(f"HDR 亮度：{brightness} nits（高光很猛）{tip}")
        elif brightness >= 1500:
            r.append(f"HDR 亮度：{brightness} nits（中上，稳定耐看）")
        else:
            r.append(f"HDR 亮度：{brightness} nits（【参数偏弱】偏保守）")

    if zones is None:
        r.append("控光分区：【参数缺失】未公开，暗场压光晕要等实测。")
    else:
        if zones >= 2000:
            r.append(f"控光分区：{zones}（暗场高光压得住，更稳）")
        elif zones >= 800:
            r.append(f"控光分区：{zones}（中等偏上，不是控光怪兽）")
        else:
            r.append(f"控光分区：{zones}（【参数偏弱】偏少，暗场可能一般）")

    # 品牌性格（可选补一句）
    bp = brand_personality(_norm_brand(tv.get("brand", "")))
    if bp:
        r.append(bp)

    # 不适合谁（一句话）
    not_fit: List[str] = []
    if lagv is None:
        not_fit.append("极度看重输入延迟的竞技党（先等实测）")
    elif lagv > 12:
        not_fit.append("重度竞技玩家（更建议低延迟更明确的机型）")

    if vrr is None:
        not_fit.append("强依赖 VRR 的玩家（先确认固件/实测）")
    elif vrr is False:
        not_fit.append("经常玩帧率波动大的开放世界（无 VRR 更难受）")

    if hdmi21i < 2:
        not_fit.append("多主机/多设备党（接口可能不够用）")

    if not not_fit:
        not_fit.append("大多数 PS5 玩家（整体体验更完整）")

    return r, "；".join(not_fit)


# =========================
# Movie 场景：真人导购 reasons（暗场优先）
# =========================
def reasons_movie_v2(tv: Dict[str, Any]) -> Tuple[List[str], str]:
    r: List[str] = []

    zones = _to_int(tv.get("local_dimming_zones"))
    brightness = _to_int(tv.get("peak_brightness_nits"))
    uniform = _to_float(tv.get("uniformity_gray50_max_dev"))
    refl = _to_float(tv.get("reflection_specular"))

    # 1) 暗场控光（第一优先）
    if zones is None:
        r.append("暗场控光：分区【参数缺失】未公开，字幕泛白/光晕能力要等实测。")
    else:
        if zones >= 2000:
            r.append("暗场控光：分区很强，压光晕更有把握（电影党最在意）。")
        elif zones >= 800:
            r.append("暗场控光：分区中上，暗场对比有提升，但不算控光怪兽。")
        else:
            r.append("暗场控光：分区偏少（【参数偏弱】），暗场高光压制可能一般。")

    # 2) HDR 亮度（第二优先）
    if brightness is None:
        r.append("HDR 亮度：【参数缺失】未公开，冲击力要等实测/后续口径。")
    else:
        if brightness >= 2500:
            r.append("HDR 亮度：很充足，大片高光更有冲击力。")
        elif brightness >= 1200:
            r.append("HDR 亮度：够用偏上，观感稳、不刺眼，适合长时间追剧。")
        else:
            r.append("HDR 亮度：偏保守（【参数偏弱】），不走“炸裂高光”路线。")

    # 3) 均匀性/反射（第三优先）
    if uniform is None:
        r.append("均匀性：【参数缺失】建议线下看灰底/暗场（脏屏/漏光敏感必看）。")
    else:
        if uniform <= 0.06:
            r.append("均匀性：预计较好，纯色/暗场更干净。")
        elif uniform <= 0.12:
            r.append("均匀性：中等水平，极端灰底/球赛可能略看得出来。")
        else:
            r.append("均匀性：偏弱（【参数偏弱】），暗场纯色敏感建议先线下确认。")

    if refl is None:
        r.append("反射：【参数缺失】客厅灯多/大窗建议线下看倒影控制。")
    else:
        if refl <= 1.5:
            r.append("反射：控制较好，夜晚开灯干扰更小。")
        elif refl <= 3.0:
            r.append("反射：中等，强光下可能有倒影，注意灯位/窗帘。")
        else:
            r.append("反射：偏弱（【参数偏弱】），强环境光更影响沉浸。")

    bp = brand_personality(_norm_brand(tv.get("brand", "")))
    if bp:
        r.append(bp)

    not_fit: List[str] = []
    if zones is None:
        not_fit.append("极度在意暗场光晕的电影党（建议等实测）")
    elif zones < 800:
        not_fit.append("追求极致暗场对比的电影党（更适合更强控光型号）")

    if uniform is None:
        not_fit.append("对脏屏/漏光特别敏感的人（建议线下看灰底）")
    elif uniform > 0.12:
        not_fit.append("对纯色画面非常敏感的人（可能会在灰底看到不均）")

    if not_fit:
        return r, "；".join(not_fit)
    return r, "大多数电影/追剧用户（取向更偏观影沉浸）"


# =========================
# Bright 场景：真人导购 reasons（白天客厅优先）
# =========================
def reasons_bright_v2(tv: Dict[str, Any]) -> Tuple[List[str], str]:
    r: List[str] = []

    brightness = _to_int(tv.get("peak_brightness_nits"))
    refl = _to_float(tv.get("reflection_specular"))
    zones = _to_int(tv.get("local_dimming_zones"))

    # 1) 白天抗环境光（第一优先）
    if brightness is None:
        r.append("白天抗光：亮度【参数缺失】未公开，建议看实测/线下真机。")
    else:
        if brightness >= 2000:
            r.append("白天抗光：很强，采光强/大窗客厅更稳。")
        elif brightness >= 900:
            r.append("白天抗光：够用，强日照直射建议配合窗帘。")
        else:
            r.append("白天抗光：偏保守（【参数偏弱】），强光下可能显得发灰。")

    # 2) 反射控制（第二优先）
    if refl is None:
        r.append("反射控制：【参数缺失】灯多/窗大建议线下重点看倒影。")
    else:
        if refl <= 1.5:
            r.append("反射控制：较好，开灯/白天倒影更少。")
        elif refl <= 3.0:
            r.append("反射控制：中等，强光下可能有倒影，注意摆位/灯位。")
        else:
            r.append("反射控制：偏弱（【参数偏弱】），倒影干扰会更明显。")

    # 3) 分区是加分项
    if zones is None:
        r.append("对比层次：分区【参数缺失】未公开，层次感要等实测。")
    else:
        if zones >= 1000:
            r.append("对比层次：分区不错，白天也更有立体感。")
        elif zones >= 400:
            r.append("对比层次：分区中等，提升有限但有加分。")
        else:
            r.append("对比层次：分区偏少（【参数偏弱】），整体可能更“平”。")

    bp = brand_personality(_norm_brand(tv.get("brand", "")))
    if bp:
        r.append(bp)

    not_fit: List[str] = []
    if brightness is None:
        not_fit.append("白天强光且特别看重抗光的人（建议等实测/看真机）")
    elif brightness < 900:
        not_fit.append("采光特别强又不想拉窗帘的人（亮度可能不够顶）")

    if refl is None:
        not_fit.append("非常在意倒影的人（建议线下看反光）")
    elif refl > 3.0:
        not_fit.append("电视正对大窗/顶灯的家庭（倒影更明显）")

    if not_fit:
        return r, "；".join(not_fit)
    return r, "大多数白天客厅使用为主的用户（抗光/反射取向更明确）"


# =========================
# Top1：每个场景 2 句总结（更稳）
# =========================
def top1_summary_ps5(tv: Dict[str, Any]) -> str:
    brand = _norm_brand(tv.get("brand", ""))
    model = str(tv.get("model", "") or "").strip()
    year = str(tv.get("launch_date", "") or "")[:4] if tv.get("launch_date") else ""

    allm = _to_bool(tv.get("allm"))
    vrr = _to_bool(tv.get("vrr"))

    persona = brand_personality(brand) or "整体取向偏均衡，主打稳定与可用性。"
    line1 = f"{brand} {model} 属于 {year} 年的新款取向，走『PS5 规格兼顾』路线：ALLM {_yn_cn(allm)}，VRR {_yn_cn(vrr)}。"
    line2 = f"{persona} 如果你对 VRR/输入延迟很敏感，建议把“是否支持/具体数值”问清或等实测再拍板。"
    return f"{line1}\n{line2}"


def top1_summary_movie(tv: Dict[str, Any]) -> str:
    brand = _norm_brand(tv.get("brand", ""))
    model = str(tv.get("model", "") or "").strip()
    year = str(tv.get("launch_date", "") or "")[:4] if tv.get("launch_date") else ""

    persona = brand_personality(brand) or "整体取向偏观影沉浸，重视暗场和层次。"
    line1 = f"{brand} {model} 属于 {year} 年的观影向取向，更偏『暗场控光 + 电影氛围』。"
    line2 = f"{persona} 暗场党最建议看一轮实测（光晕/字幕泛白/均匀性），确认后更稳。"
    return f"{line1}\n{line2}"


def top1_summary_bright(tv: Dict[str, Any]) -> str:
    brand = _norm_brand(tv.get("brand", ""))
    model = str(tv.get("model", "") or "").strip()
    year = str(tv.get("launch_date", "") or "")[:4] if tv.get("launch_date") else ""

    persona = brand_personality(brand) or "整体取向偏白天客厅，强调亮度与抗环境光。"
    line1 = f"{brand} {model} 属于 {year} 年的客厅向机型，更偏『白天抗光 + 反射控制』路线。"
    line2 = f"{persona} 如果你对倒影特别敏感，建议线下重点看反光控制再决定。"
    return f"{line1}\n{line2}"