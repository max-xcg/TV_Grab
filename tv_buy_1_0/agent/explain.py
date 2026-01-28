# -*- coding: utf-8 -*-
from typing import Dict, Any, List

def top_reasons(tv: Dict[str, Any], max_n=3) -> List[str]:
    rs = []
    if tv.get("peak_brightness_nits") is not None:
        rs.append(f"峰值亮度 {tv['peak_brightness_nits']} nits")
    if tv.get("local_dimming_zones") is not None:
        rs.append(f"控光分区 {tv['local_dimming_zones']}")
    if tv.get("reflection_specular") is not None:
        rs.append(f"反射指标 {tv['reflection_specular']}（越低越好）")
    if tv.get("hdmi_2_1_ports") is not None:
        rs.append(f"HDMI 2.1 口数 {tv['hdmi_2_1_ports']}")
    if tv.get("input_lag_ms_60hz") is not None:
        rs.append(f"60Hz 延迟 {tv['input_lag_ms_60hz']} ms（越低越好）")
    if tv.get("street_rmb") is not None:
        rs.append(f"价格 ¥{tv['street_rmb']}")
    return rs[:max_n]

def not_for(tv: Dict[str, Any], p: Dict[str, Any]) -> str:
    if p.get("bright_room") and tv.get("reflection_specular") is not None and tv["reflection_specular"] > 0.03:
        return "白天反光敏感用户慎选（建议配遮光或选反射更低的）。"
    if p.get("use_gaming") and (tv.get("hdmi_2_1_ports") or 0) < 2:
        return "多设备/次世代主机用户可能 HDMI2.1 口不够。"
    return "对系统纯净/广告极敏感的人，建议线下体验系统再决定。"
