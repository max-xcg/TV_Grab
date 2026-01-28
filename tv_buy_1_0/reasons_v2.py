# -*- coding: utf-8 -*-
"""
reasons_v2.py
专注【表达层】：
- PS5 场景真人导购话术
- 明确区分：参数缺失 vs 参数偏弱
- 品牌性格（当前先做 TCL，可按同结构扩展到 24 个品牌）
- Top1 总结：2 句真人导购版本
"""

from typing import Dict, List, Tuple, Any


# =========================
# 工具：安全取值 / 规范化
# =========================
def _norm_brand(b: Any) -> str:
    if not b:
        return ""
    s = str(b).strip()
    return s


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


# =========================
# 品牌性格（先只做 TCL）
# 后续你可以照这个结构继续扩 24 个品牌
# =========================
def brand_personality(brand: str) -> str:
    if not brand:
        return ""
    b = str(brand).strip().lower()

    if b == "tcl":
        return "TCL 一贯是参数取向，新款规格给得很激进，但首发阶段更适合等一轮实测确认。"

    # 预留扩展（示例）：
    # if b in ("mi", "小米", "xiaomi"):
    #     return "小米更偏均衡和系统体验，适合买回来就直接用、少折腾的用户。"
    # if b in ("sony", "索尼"):
    #     return "索尼更偏画面调校和稳定观感，纸面参数不一定夸张，但整体观感更耐看。"

    return ""


# =========================
# PS5 场景：真人导购 reasons
# =========================
def reasons_ps5_v2(tv: Dict[str, Any]) -> Tuple[List[str], str]:
    """
    返回：
      - reasons: List[str]   # 为什么推荐（人话）
      - not_fit: str         # 不太适合谁（明确人群）
    """

    r: List[str] = []

    # ---------- 1️⃣ 游戏适配性（第一句话必须是这个） ----------
    lag = tv.get("input_lag_ms_60hz")
    if lag is not None:
        try:
            lagv = float(lag)
            if lagv <= 6:
                r.append(f"游戏响应非常快，约 {lagv:g}ms 输入延迟，对动作类和射击游戏都很友好。")
            elif lagv <= 12:
                r.append(f"输入延迟约 {lagv:g}ms，日常主机游戏足够用，兼顾了画质和响应。")
            else:
                r.append(f"输入延迟约 {lagv:g}ms，剧情/休闲游戏没问题，但硬核竞技玩家可能不够爽。")
        except Exception:
            r.append("输入延迟数据有记录但格式不规范，建议以实测为准。")
    else:
        r.append("输入延迟数据暂未公开，属于【参数缺失】（不是参数差），但建议等实测再下最终结论。")

    # ---------- 2️⃣ 主机连接便利性 ----------
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

    # ---------- 3️⃣ VRR：明确区分“缺失 vs 弱” ----------
    if vrr is None:
        r.append("VRR 支持情况暂未公开，属于【参数缺失】；强依赖 VRR 的玩家建议等后续固件/实测确认。")
    elif vrr:
        r.append("支持 VRR，可有效减少帧率波动带来的画面撕裂，玩开放世界更舒服。")
    else:
        r.append("VRR 标注为不支持（属于【参数偏弱】），对帧率波动敏感的玩家可能会有遗憾。")

    # ---------- 4️⃣ HDR 游戏观感（说感受，不报表） ----------
    brightness = tv.get("peak_brightness_nits")
    zones = tv.get("local_dimming_zones")

    if brightness is not None:
        try:
            bv = float(brightness)
            if bv >= 3000:
                r.append("HDR 游戏亮度很猛，爆炸/光效场景冲击力强，属于“上头型”观感。")
            elif bv >= 1500:
                r.append("HDR 亮度属于中上，整体观感稳定，亮但不夸张，长时间玩更舒服。")
            else:
                r.append("HDR 亮度偏保守（属于【参数偏弱】），更适合不追求“炸裂高光”的玩家。")
        except Exception:
            r.append("峰值亮度有记录但格式不规范，HDR 表现建议结合实测。")
    else:
        r.append("峰值亮度参数未公布，属于【参数缺失】；HDR 强不强要等实测或后续口径。")

    if zones is not None:
        try:
            zv = float(zones)
            if zv >= 2000:
                r.append("分区数量充足，暗场游戏里高光压得住，不容易整屏泛白。")
            elif zv >= 800:
                r.append("分区数量中等，暗场对比有提升，但不属于“控光怪兽”。")
            else:
                r.append("分区数量偏少（属于【参数偏弱】），暗场高光压制可能一般。")
        except Exception:
            r.append("控光分区有记录但格式不规范，暗场表现建议结合实测。")
    else:
        r.append("控光分区数据未公布，属于【参数缺失】；暗场表现需等待实测验证。")

    # ---------- 5️⃣ 品牌性格补一句 ----------
    bp = brand_personality(_norm_brand(tv.get("brand", "")))
    if bp:
        r.append(bp)

    # ---------- 不适合谁（一定要给） ----------
    not_fit: List[str] = []

    # 强硬的“劝退”逻辑：缺失/偏弱都要指向人群
    if lag is None:
        not_fit.append("对输入延迟非常敏感、追求极限电竞体验的玩家（建议等实测）")
    else:
        try:
            if float(lag) > 12:
                not_fit.append("重度竞技玩家（更适合低延迟更明确的型号）")
        except Exception:
            pass

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
# Top1：真人导购 2 句总结
# =========================
def top1_summary_ps5(tv: Dict[str, Any]) -> str:
    """
    给 Top1 用的 2 句导购总结：
    - 第一句：告诉你“它是什么类型”
    - 第二句：给一个“买/等/换”的行动建议
    """
    brand = _norm_brand(tv.get("brand", ""))
    model = str(tv.get("model", "") or "").strip()
    year = str(tv.get("launch_date", "") or "")[:4] if tv.get("launch_date") else ""

    persona = brand_personality(brand)
    if not persona:
        persona = "这牌子整体取向偏均衡，主打稳定和省心。"

    line1 = f"{brand} {model} 属于 {year} 年的新款定位，整体是『PS5 友好 + 规格/体验兼顾』的路线。"
    line2 = f"{persona} 如果你能接受部分信息还要等实测确认，就可以先把它当作当前条件下的首选。"

    return f"{line1}\n{line2}"
