# -*- coding: utf-8 -*-
from pathlib import Path
import yaml
from typing import Dict, Any

# 当前文件：tv_buy_1_0/g2_lab/services/_prompt_extract.py
# parents[1] => tv_buy_1_0/g2_lab
PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "contrast_extract_system.yaml"

def load_extract_prompt() -> Dict[str, Any]:
    if not PROMPT_FILE.exists():
        raise FileNotFoundError(f"[EXTRACT] 找不到 prompt 文件: {PROMPT_FILE}")

    cfg = yaml.safe_load(PROMPT_FILE.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError("[EXTRACT] prompt YAML 不是 dict")

    return cfg
