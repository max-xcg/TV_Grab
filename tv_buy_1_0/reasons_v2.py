# -*- coding: utf-8 -*-
"""
reasons_v2.py
专注【表达层】：
- PS5 / Movie / Bright 三场景真人导购话术（规则引擎层）
- 明确区分：参数缺失 vs 参数偏弱
- 品牌性格（当前先做 TCL，可按同结构扩展到 24 个品牌）
- Top1 总结：每个场景 2 句真人导购版本
"""

from typing import Dict, List, Tuple, Any


# =========================
# 工具：安全取值 / 规范化
# =========================
def _norm_brand(b: Any) -> str:
    if not b:
        return ""
    return str(b).strip()


def _to_bool(x: Any) -> Any:
    """把 0/1/True/False/'有'/'无' 等转成 bool 或 None（未知）"""
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return bool(int(x))
    s = str(x).strip().lower()
    if s in ("1", "true", "yes", "y", "有", "支持", "是"):
        return True
    if s in ("0", "false", "no", "n", "无", "不支持", "否"):
        return False
    return None


def _to_float(x: Any) -> Any:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


# =========================
# 品牌性格（先只做 TCL）
# =========================
def brand_personality(brand: str) -> str:
    if not brand:
        return ""
    b = str(brand).strip().lower()

    if b == "tcl":
        return "TCL 一贯是参数取向，新款规格给得很激进，但首发阶段更适合等一轮实测确认。"

    return ""


# =========================
# PS5 场景：真人导购 reasons
# =========================
def reasons_ps5_v2(tv: Dict[str, Any]) -> Tuple[List[str], str]:
    r: List[str] = []

    # 1) 输入延迟
    lag = tv.get("input_lag_ms_60hz")
    lagv = _to_float(lag)
    if lagv is not None:
        if lagv <= 6:
            r.append(f"游戏响应非常快，约 {lagv:g}ms 输入延迟，对动作类和射击游戏都很友好。")
        elif lagv <= 12:
            r.append(f"输入延迟约 {lagv:g}ms，日常主机游戏足够用，兼顾了画质和响应。")
        else:
            r.append(f"输入延迟约 {lagv:g}ms，剧情/休闲游戏没问题，但硬核竞技玩家可能不够爽。")
    else:
        r.append("输入延迟数据暂未公开，属于【参数缺失】（不是参数差），但建议等实测再下最终结论。")

    # 2) HDMI2.1 / ALLM / VRR
    hdmi21 = tv.get("hdmi_2_1_ports") or 0
    try:
        hdmi21 = int(hdmi21)
    except Exception:
        hdmi21 = 0

    allm = _to_bool(tv.get("allm"))
    vrr = _to_bool(tv.get("vrr"))

    if hdmi21 >= 2:
        r.append(f"配有 {hdmi21} 个 HDMI 2.1 接口，PS5 + 回音壁 / 第二主机接线会比较从容。")
    elif hdmi21 == 1:
        r.append("只有 1 个 HDMI 2.1，单主机够用，但多设备党可能要做取舍。")
    else:
        r.append("HDMI 2.1 接口偏少（或未标注），多主机 / 外接设备多的玩家要注意接口规划。")

    if allm is None:
        r.append("ALLM 暂未标注，属于【参数缺失】；如果你嫌麻烦，建议确认是否有自动低延迟切换。")
    elif allm:
        r.append("支持 ALLM，开机进游戏会自动切换到低延迟模式，使用体验比较省心。")
    else:
        r.append("ALLM 标注为不支持（或关闭），可能需要你手动切换游戏模式。")

    if vrr is None:
        r.append("VRR 支持情况暂未公开，属于【参数缺失】；强依赖 VRR 的玩家建议等后续固件/实测确认。")
    elif vrr:
        r.append("支持 VRR，可有效减少帧率波动带来的画面撕裂，玩开放世界更舒服。")
    else:
        r.append("VRR 标注为不支持（属于【参数偏弱】），对帧率波动敏感的玩家可能会有遗憾。")

    # 3) HDR 观感（亮度/分区）
    brightness = _to_float(tv.get("peak_brightness_nits"))
    zones = _to_float(tv.get("local_dimming_zones"))

    if brightness is not None:
        if brightness >= 3000:
            r.append("HDR 游戏亮度很猛，爆炸/光效场景冲击力强，属于“上头型”观感。")
        elif brightness >= 1500:
            r.append("HDR 亮度属于中上，整体观感稳定，亮但不夸张，长时间玩更舒服。")
        else:
            r.append("HDR 亮度偏保守（属于【参数偏弱】），更适合不追求“炸裂高光”的玩家。")
    else:
        r.append("峰值亮度参数未公布，属于【参数缺失】；HDR 强不强要等实测或后续口径。")

    if zones is not None:
        if zones >= 2000:
            r.append("分区数量充足，暗场游戏里高光压得住，不容易整屏泛白。")
        elif zones >= 800:
            r.append("分区数量中等，暗场对比有提升，但不属于“控光怪兽”。")
        else:
            r.append("分区数量偏少（属于【参数偏弱】），暗场高光压制可能一般。")
    else:
        r.append("控光分区数据未公布，属于【参数缺失】；暗场表现需等待实测验证。")

    bp = brand_personality(_norm_brand(tv.get("brand", "")))
    if bp:
        r.append(bp)

    # 不适合谁
    not_fit: List[str] = []
    if lagv is None:
        not_fit.append("对输入延迟非常敏感、追求极限电竞体验的玩家（建议等实测）")
    elif lagv > 12:
        not_fit.append("重度竞技玩家（更适合低延迟更明确的型号）")

    if vrr is None:
        not_fit.append("强依赖 VRR 的玩家（建议确认固件/实测）")
    elif vrr is False:
        not_fit.append("经常玩帧率波动大的开放世界/性能模式的玩家（VRR 缺失会更难受）")

    if hdmi21 < 2:
        not_fit.append("多主机 / 多设备同时接入用户（接口可能不够用）")

    if not not_fit:
        not_fit.append("大多数 PS5 玩家（体验会比较完整）")

    return r, "；".join(not_fit)


# =========================
# Movie 场景：真人导购 reasons（暗场优先）
# =========================
def reasons_movie_v2(tv: Dict[str, Any]) -> Tuple[List[str], str]:
    r: List[str] = []

    zones = _to_float(tv.get("local_dimming_zones"))
    brightness = _to_float(tv.get("peak_brightness_nits"))
    uniform = _to_float(tv.get("uniformity_gray50_max_dev"))
    refl = _to_float(tv.get("reflection_specular"))

    # 1) 暗场控光（第一优先）
    if zones is None:
        r.append("分区控光数据未公布，属于【参数缺失】；暗场压光晕能力需要等实测验证。")
    else:
        if zones >= 2000:
            r.append("分区控光很强，暗场字幕/高光更稳，电影党最在意的“压得住”这点更有把握。")
        elif zones >= 800:
            r.append("分区控光中上，暗场对比会明显优于普通直下式，但不算“控光怪兽”。")
        else:
            r.append("分区控光偏少（属于【参数偏弱】），暗场高光压制可能一般，容易看到光晕/泛白。")

    # 2) 电影 HDR 亮度（第二优先）
    if brightness is None:
        r.append("峰值亮度未标注，属于【参数缺失】；HDR 冲击力要等实测或后续口径。")
    else:
        if brightness >= 2500:
            r.append("HDR 亮度很充足，大片高光（爆炸、霓虹、火焰）更有冲击力。")
        elif brightness >= 1200:
            r.append("HDR 亮度属于够用偏上，观感稳、不刺眼，适合长时间追剧观影。")
        else:
            r.append("HDR 亮度偏保守（属于【参数偏弱】），更适合不追求“炸裂高光”的电影党。")

    # 3) 均匀性/反射：影响沉浸感（第三优先）
    if uniform is None:
        r.append("均匀性数据缺失（属于【参数缺失】）；建议看实机，重点观察灰底/暗场是否有脏屏、漏光。")
    else:
        # 这里越小越好：给一个大致“人话档位”
        if uniform <= 0.06:
            r.append("均匀性表现预计较好，暗场纯色画面更干净，观影沉浸感更稳。")
        elif uniform <= 0.12:
            r.append("均匀性属于中等水平，日常观影没问题，极端灰底/足球场景可能略看得出来。")
        else:
            r.append("均匀性偏弱（属于【参数偏弱】），对暗场纯色敏感的用户建议优先线下确认。")

    if refl is None:
        r.append("镜面反射数据缺失（属于【参数缺失】）；如果客厅有灯带/大窗，建议优先线下看反光控制。")
    else:
        if refl <= 1.5:
            r.append("反射控制较好，夜晚开灯/侧灯对画面的干扰会更小。")
        elif refl <= 3.0:
            r.append("反射控制中等，环境光强时会有一定倒影，建议控制灯位/窗帘。")
        else:
            r.append("反射控制偏弱（属于【参数偏弱】），有强环境光会更影响电影观感。")

    bp = brand_personality(_norm_brand(tv.get("brand", "")))
    if bp:
        r.append(bp)

    # 不适合谁
    not_fit: List[str] = []
    if zones is None:
        not_fit.append("极度在意暗场光晕/字幕泛白的电影党（建议等实测）")
    elif zones < 800:
        not_fit.append("追求极致暗场对比的电影党（更适合高分区/更强控光的型号）")

    if uniform is None:
        not_fit.append("对脏屏/漏光特别敏感的人（建议线下看灰底/暗场）")
    elif uniform > 0.12:
        not_fit.append("对纯色画面非常敏感的人（可能会在灰底/暗场看到不均匀）")

    if not_fit:
        return r, "；".join(not_fit)
    return r, "大多数以电影/追剧为主的用户（整体取向更偏观影沉浸）"


# =========================
# Bright 场景：真人导购 reasons（白天客厅优先）
# =========================
def reasons_bright_v2(tv: Dict[str, Any]) -> Tuple[List[str], str]:
    r: List[str] = []

    brightness = _to_float(tv.get("peak_brightness_nits"))
    refl = _to_float(tv.get("reflection_specular"))
    zones = _to_float(tv.get("local_dimming_zones"))

    # 1) 白天抗环境光（第一优先）
    if brightness is None:
        r.append("峰值亮度未标注，属于【参数缺失】；白天抗光能力需要等实测或后续口径。")
    else:
        if brightness >= 2000:
            r.append("白天抗环境光能力很强，画面不容易“灰”，适合采光强/大窗客厅。")
        elif brightness >= 900:
            r.append("白天亮度属于够用，正常客厅没问题；强日照直射建议配合窗帘。")
        else:
            r.append("亮度偏保守（属于【参数偏弱】），白天强光环境可能会显得发灰。")

    # 2) 反射控制（第二优先）
    if refl is None:
        r.append("镜面反射数据缺失（属于【参数缺失】）；如果你家灯多/窗大，建议线下重点看倒影。")
    else:
        if refl <= 1.5:
            r.append("反射控制较好，白天/开灯时倒影更少，观感更清爽。")
        elif refl <= 3.0:
            r.append("反射控制中等，强光下可能会看到倒影，建议调整摆位/灯位。")
        else:
            r.append("反射控制偏弱（属于【参数偏弱】），对面窗或顶灯倒影会更明显。")

    # 3) 分区是加分项：白天也影响立体感
    if zones is None:
        r.append("分区控光数据未公布，属于【参数缺失】；白天对比层次要等实测。")
    else:
        if zones >= 1000:
            r.append("分区控光不错，白天看也更有层次，不容易整屏发灰。")
        elif zones >= 400:
            r.append("分区控光中等，白天观影能提升一点立体感，但不是强控光路线。")
        else:
            r.append("分区控光偏少（属于【参数偏弱】），白天整体对比可能更“平”。")

    bp = brand_personality(_norm_brand(tv.get("brand", "")))
    if bp:
        r.append(bp)

    # 不适合谁
    not_fit: List[str] = []
    if brightness is None:
        not_fit.append("白天强光环境且非常看重抗光的人（建议等实测/看真机）")
    elif brightness < 900:
        not_fit.append("采光特别强、又不想拉窗帘的人（亮度可能不够顶）")

    if refl is None:
        not_fit.append("非常在意倒影的人（建议线下看反光）")
    elif refl > 3.0:
        not_fit.append("电视正对大窗/顶灯的家庭（倒影干扰会更明显）")

    if not_fit:
        return r, "；".join(not_fit)
    return r, "大多数白天客厅使用为主的用户（抗光/反射取向更明确）"


# =========================
# Top1：每个场景 2 句总结
# =========================
def top1_summary_ps5(tv: Dict[str, Any]) -> str:
    brand = _norm_brand(tv.get("brand", ""))
    model = str(tv.get("model", "") or "").strip()
    year = str(tv.get("launch_date", "") or "")[:4] if tv.get("launch_date") else ""
    persona = brand_personality(brand) or "这牌子整体取向偏均衡，主打稳定和省心。"

    line1 = f"{brand} {model} 属于 {year} 年的新款定位，整体是『PS5 友好 + 规格/体验兼顾』的路线。"
    line2 = f"{persona} 如果你能接受部分信息还要等实测确认，就可以先把它当作当前条件下的首选。"
    return f"{line1}\n{line2}"


def top1_summary_movie(tv: Dict[str, Any]) -> str:
    brand = _norm_brand(tv.get("brand", ""))
    model = str(tv.get("model", "") or "").strip()
    year = str(tv.get("launch_date", "") or "")[:4] if tv.get("launch_date") else ""
    persona = brand_personality(brand) or "整体取向偏观影沉浸，重视暗场和层次。"

    line1 = f"{brand} {model} 属于 {year} 年的取向机型，更偏『暗场控光 + 观影沉浸』路线。"
    line2 = f"{persona} 如果你是晚上/暗光环境看得多，它会更贴合你的使用场景；但暗场党仍建议优先看一轮实测。"
    return f"{line1}\n{line2}"


def top1_summary_bright(tv: Dict[str, Any]) -> str:
    brand = _norm_brand(tv.get("brand", ""))
    model = str(tv.get("model", "") or "").strip()
    year = str(tv.get("launch_date", "") or "")[:4] if tv.get("launch_date") else ""
    persona = brand_personality(brand) or "整体取向偏白天客厅，强调亮度与抗环境光。"

    line1 = f"{brand} {model} 属于 {year} 年的客厅向机型，更偏『白天抗光 + 反射控制』路线。"
    line2 = f"{persona} 如果你家采光强、白天看得多，它会更省心；但反光敏感的话建议线下看倒影控制。"
    return f"{line1}\n{line2}"
