# -*- coding: utf-8 -*-
# ============================================================
# scan_display_size_inch_all.py
#
# 功能：
# - 递归扫描以下两个目录下的所有 .yml / .yaml 文件
# - 提取 display.size_inch（电视尺寸字段）
# - 统计：
#   * 一共有多少 YAML
#   * 有多少包含尺寸字段
#   * 一共有哪些尺寸（去重）
#   * 每个尺寸出现多少次
#   * 每个尺寸对应哪些文件
#   * 按品牌目录（root 下第一层）汇总
#
# 运行方式（Git Bash / CMD 都可）：
#   /c/software/Anaconda3/python.exe scan_display_size_inch_all.py
# ============================================================

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import yaml
except Exception:
    print("[ERROR] 缺少 PyYAML，请先执行：pip install pyyaml")
    raise


# ===== 扫描目录（Windows 路径一定要 raw string）=====
ROOTS = [
    r"C:\Users\admin\tvlabs_scraper\TVLabs\TV_Grab\out_step3_2025_spec",
    r"C:\Users\admin\tvlabs_scraper\TVLabs\TV_Grab\output_all_brands_2026_spec",
]

MAX_FILES_PER_SIZE_PRINT = 30


def to_int_maybe(x: Any) -> Optional[int]:
    """把 55 / '55' / '55英寸' / '55\"' / 55.0 转成 int"""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(x) if x.is_integer() else None
    if isinstance(x, str):
        m = re.search(r"(\d{2,3})", x)
        return int(m.group(1)) if m else None
    return None


def get_brand_from_path(root: Path, file_path: Path) -> str:
    """品牌 = root 下第一层目录名"""
    try:
        rel = file_path.relative_to(root)
    except Exception:
        return "(unknown)"
    return rel.parts[0] if len(rel.parts) >= 2 else "(root)"


def extract_display_size_inch(data: Any) -> Optional[int]:
    """
    只识别 display.size_inch 这一类字段
    """
    if not isinstance(data, dict):
        return None

    # display: { size_inch: 55 }
    if "display" in data and isinstance(data["display"], dict):
        if "size_inch" in data["display"]:
            return to_int_maybe(data["display"]["size_inch"])

    # 扁平 key
    for k in ("display.size_inch", "display:size_inch", "display_size_inch"):
        if k in data:
            return to_int_maybe(data[k])

    return None


def main() -> int:
    roots = [Path(r) for r in ROOTS]

    all_yaml: List[Tuple[Path, Path]] = []
    for root in roots:
        if not root.exists():
            print(f"[WARN] 目录不存在，跳过：{root}")
            continue
        for p in root.rglob("*"):
            if p.suffix.lower() in (".yml", ".yaml"):
                all_yaml.append((root, p))

    total_files = len(all_yaml)
    if total_files == 0:
        print("[INFO] 未找到任何 YAML 文件")
        return 0

    sizes: Set[int] = set()
    size_count: Dict[int, int] = {}
    size_files: Dict[int, List[str]] = {}
    with_size = 0

    brand_stats: Dict[str, Dict[str, Any]] = {}

    for root, fp in all_yaml:
        brand = get_brand_from_path(root, fp)
        brand_stats.setdefault(
            brand, {"total": 0, "with_size": 0, "sizes": set()}
        )
        brand_stats[brand]["total"] += 1

        try:
            data = yaml.safe_load(fp.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue

        size = extract_display_size_inch(data)
        if size is None:
            continue

        with_size += 1
        sizes.add(size)
        size_count[size] = size_count.get(size, 0) + 1
        size_files.setdefault(size, []).append(str(fp))

        brand_stats[brand]["with_size"] += 1
        brand_stats[brand]["sizes"].add(size)

    # ===== 输出 =====
    print("=" * 90)
    print("display.size_inch（电视尺寸）扫描结果")
    print("=" * 90)
    print(f"YAML 文件总数：{total_files}")
    print(f"包含尺寸字段：{with_size}")
    print(f"未包含尺寸字段：{total_files - with_size}")

    print("\n尺寸种类数：", len(sizes))
    print("尺寸列表：", ", ".join(str(x) for x in sorted(sizes)))

    print("\n各尺寸出现次数：")
    for s, cnt in sorted(size_count.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {s} 寸：{cnt} 个文件")

    print("\n各尺寸对应文件（最多显示前 30 条）：")
    for s in sorted(size_files):
        files = size_files[s]
        print(f"\n[{s} 寸] 共 {len(files)} 个")
        for i, f in enumerate(files[:MAX_FILES_PER_SIZE_PRINT], 1):
            print(f"  {i:02d}. {f}")
        if len(files) > MAX_FILES_PER_SIZE_PRINT:
            print(f"  ... 还有 {len(files) - MAX_FILES_PER_SIZE_PRINT} 条")

    print("\n按品牌汇总：")
    for brand in sorted(brand_stats):
        st = brand_stats[brand]
        sizes_str = ", ".join(str(x) for x in sorted(st["sizes"])) or "-"
        print(
            f"[{brand}] total={st['total']}  "
            f"with_size={st['with_size']}  sizes={sizes_str}"
        )

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())