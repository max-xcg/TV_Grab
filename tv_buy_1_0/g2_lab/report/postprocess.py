# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Optional, Tuple

_EDITORIAL_RE = re.compile(r"(editorial_verdict:\s*\n(?:[ \t].*\n?)*)", re.M)

def split_output(raw: str) -> Tuple[str, Optional[str]]:
    # 1) 优先抓 YAML 代码块
    m = re.search(r"```(?:yaml|yml)\s*(.*?)```", raw, flags=re.S | re.I)
    if m and "editorial_verdict" in m.group(1):
        yaml_block = m.group(1).strip()
        analysis_text = re.sub(r"```(?:yaml|yml)\s*.*?```", "", raw, flags=re.S | re.I).strip()
        return analysis_text, yaml_block

    # 2) 抓 editorial_verdict: 段落
    m2 = _EDITORIAL_RE.search(raw)
    if m2:
        yaml_part = m2.group(1).strip()
        analysis_text = (raw[: m2.start()].strip() + "\n" + raw[m2.end():].strip()).strip()
        return analysis_text, yaml_part

    # 3) 没抓到就全部当阶段一
    return raw.strip(), None
