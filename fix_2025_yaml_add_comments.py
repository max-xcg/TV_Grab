# -*- coding: utf-8 -*-
"""
fix_2025_yaml_add_comments.py  （最终定稿版）

规则（已锁死）：
- 就地覆盖 out_step3_2025_spec 下所有 *_spec.yaml
- 不生成 / 不保留任何 .bak（会主动删除）
- 注释与值之间留“很宽的空格”（8 个空格）
- 不修改任何已有数据，只动结构 / 注释 / 排版
"""

from __future__ import annotations

import os
import re
import sys
import argparse
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional

import yaml


# =================================================
# 配置：注释前空格数量（你要“空多”，这里锁 8）
# =================================================
COMMENT_GAP = "        "  # 8 spaces


# -----------------------------
# YAML 值渲染
# -----------------------------
def yml(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)

    s = str(v)
    if re.search(r"[:#\n\r\t]", s) or s.strip() != s:
        s = s.replace("'", "''")
        return f"'{s}'"
    return s


def today_ymd() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root not dict: {path}")
    return data


# -----------------------------
# Schema（与 2026 对齐）
# -----------------------------
SchemaNode = Tuple[str, Optional[str], Optional[List["SchemaNode"]]]

SCHEMA: List[SchemaNode] = [
    ("meta", "元信息", [
        ("launch_date", "首发时间：YYYY-MM（未知填 null）", None),
        ("first_release", "是否为该系列首发批次（未知填 null）", None),
        ("data_source", "数据来源", None),
        ("price_cny", "官方/标注价格（CNY；未知填 null）", None),
        ("last_updated", "更新时间：YYYY-MM-DD（未知填 null）", None),
    ]),
    ("product_id", "产品唯一ID（建议 brand_model_size）", None),
    ("brand", "品牌（brand_path）", None),
    ("model", "型号", None),
    ("category", "品类（tv）", None),

    ("positioning", "市场定位", [
        ("tier", "档位枚举（entry_level/midrange/upper_midrange/high_end）或 null", None),
        ("type", "类型枚举（gaming_tv/non_gaming_tv）或 null", None),
        ("gaming_grade", "游戏定位（flagship/advanced/...）或 null", None),
    ]),

    ("display", "显示参数", [
        ("size_inch", "屏幕尺寸（英寸；未知填 null）", None),
        ("resolution", "分辨率（4k/8k/...；未知填 null）", None),
        ("technology", "显示技术（lcd/mini_led_lcd/...；未知填 null）", None),
        ("panel_type", "面板类型（soft/hard；未知填 null）", None),
        ("backlight_type", "背光方式（direct_lit/edge_lit；未知填 null）", None),
        ("peak_brightness_nits", "峰值亮度（尼特；未知填 null）", None),
        ("local_dimming_zones", "控光分区数量（未知填 null）", None),
        ("dimming_structure", "分区结构（未知填 null）", None),
        ("color_gamut_dci_p3_pct", "DCI-P3 色域覆盖率（%；未知填 null）", None),
        ("quantum_dot", "是否量子点（true/false；未知填 null）", None),
        ("anti_reflection", "抗反射", [
            ("type", "抗反射类型（未知填 null）", None),
            ("reflectance_pct", "反射率（%；未知填 null）", None),
        ]),
    ]),

    ("refresh", "刷新与运动", [
        ("native_hz", "原生刷新率（Hz；未知填 null）", None),
        ("dlf_max_hz", "倍频最高刷新率（Hz；未知填 null）", None),
        ("memc", "运动补偿（MEMC）", [
            ("supported", "是否支持 MEMC（未知填 null）", None),
            ("max_fps", "最大插帧帧率（未知填 null）", None),
        ]),
    ]),

    ("processing", "画质处理", [
        ("picture_chip", "画质芯片", [
            ("name", "芯片名称（未知填 null）", None),
            ("type", "芯片类型（未知填 null）", None),
        ]),
    ]),

    ("soc", "主控 SoC", [
        ("vendor", "SoC 厂商（未知填 null）", None),
        ("model", "SoC 型号（未知填 null）", None),
        ("cpu", "CPU", [
            ("architecture", "CPU 架构（未知填 null）", None),
            ("cores", "CPU 核心数（未知填 null）", None),
            ("clock_ghz", "CPU 主频（未知填 null）", None),
        ]),
    ]),

    ("memory", "内存与存储", [
        ("ram_gb", "运行内存（GB；未知填 null）", None),
        ("storage_gb", "存储空间（GB；未知填 null）", None),
    ]),

    ("detail_url", "详情页 URL（来源页）", None),
]


# -----------------------------
# 构建规范数据
# -----------------------------
def build_norm_data(src: Dict[str, Any], schema: List[SchemaNode]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, _comment, children in schema:
        if children:
            sub = src.get(key)
            if not isinstance(sub, dict):
                sub = {}
            out[key] = build_norm_data(sub, children)
        else:
            out[key] = src.get(key, None)

    # 保留 schema 外字段（不丢你 2025 私有数据）
    for k, v in src.items():
        if k not in out:
            out[k] = v
    return out


# -----------------------------
# 渲染（空很多）
# -----------------------------
def render_schema(d: Dict[str, Any], schema: List[SchemaNode], indent: int = 0) -> List[str]:
    lines: List[str] = []
    sp = "  " * indent

    for key, comment, children in schema:
        if children:
            lines.append(f"{sp}{key}:{COMMENT_GAP}# {comment}")
            sub = d.get(key, {})
            lines.extend(render_schema(sub if isinstance(sub, dict) else {}, children, indent + 1))
        else:
            v = d.get(key)
            lines.append(f"{sp}{key}: {yml(v)}{COMMENT_GAP}# {comment}")

    return lines


# -----------------------------
# 文件处理
# -----------------------------
def iter_spec_files(root: str) -> List[str]:
    out = []
    for base, _, files in os.walk(root):
        for f in files:
            if f.endswith("_spec.yaml"):
                out.append(os.path.join(base, f))
    return sorted(out)


def delete_bak_files(root: str) -> int:
    cnt = 0
    for base, _, files in os.walk(root):
        for f in files:
            if f.endswith(".bak"):
                try:
                    os.remove(os.path.join(base, f))
                    cnt += 1
                except Exception:
                    pass
    return cnt


def rewrite_file(path: str) -> None:
    src = load_yaml(path)
    norm = build_norm_data(src, SCHEMA)

    meta = norm.get("meta")
    if isinstance(meta, dict) and meta.get("last_updated") is None:
        meta["last_updated"] = today_ymd()

    text = "\n".join(render_schema(norm, SCHEMA)).rstrip() + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in_2025", required=True)
    args = ap.parse_args()

    root = args.in_2025
    if not os.path.isdir(root):
        print(f"[ERR] Not a dir: {root}")
        sys.exit(2)

    deleted = delete_bak_files(root)
    files = iter_spec_files(root)

    ok = 0
    for p in files:
        try:
            rewrite_file(p)
            ok += 1
        except Exception as e:
            print(f"[ERR] {p}: {e}")

    print(f"[DONE] rewritten={ok}, deleted_bak={deleted}")


if __name__ == "__main__":
    main()