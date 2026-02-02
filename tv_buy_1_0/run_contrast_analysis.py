# -*- coding: utf-8 -*-
"""
run_contrast_analysis.py

读取：
  1) 最新的 summaries/contrast_records/contrast_*.yaml（对比度数据）
  2) g2_lab/prompts/contrast_analysis.yaml（写作规范/提示词）

然后调用 豆包/火山方舟(Ark) 的文本模型/Endpoint，生成：
  - 一段中文分析结论
  - editorial_verdict（YAML段）

输出：
  - 终端打印
  - 保存到 summaries/contrast_records/contrast_xxx_analysis.txt

依赖：
  pip install openai pyyaml
环境变量：
  ARK_API_KEY
  ARK_BASE_URL  默认 https://ark.cn-beijing.volces.com/api/v3
  ARK_TEXT_MODEL  文本模型或 EndpointID（推荐 ep-xxxx；没有就先复用视觉 ep）
"""

from __future__ import annotations

import os
from pathlib import Path
import yaml
from openai import OpenAI


def _load_latest_contrast_yaml() -> tuple[Path, str]:
    folder = Path("summaries/contrast_records")
    files = sorted(folder.glob("contrast_*.yaml"))
    if not files:
        raise FileNotFoundError(f"未找到 {folder} 下的 contrast_*.yaml，请先运行 test_contrast_yaml.py 生成。")
    yaml_path = files[-1]
    text = yaml_path.read_text(encoding="utf-8")
    return yaml_path, text


def _load_analysis_prompt() -> str:
    prompt_path = Path("g2_lab/prompts/contrast_analysis.yaml")
    if not prompt_path.exists():
        raise FileNotFoundError(f"找不到提示词文件：{prompt_path}")

    cfg = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))

    # 兼容不同写法：有的文件是 {"prompt": "..."}，也可能是 {"system_prompt": "..."} 或 {"system": "..."}
    for key in ("prompt", "system_prompt", "system", "analysis_prompt"):
        if isinstance(cfg, dict) and key in cfg and isinstance(cfg[key], str) and cfg[key].strip():
            return cfg[key].strip()

    # 如果是更复杂的结构（例如 messages），给一个兜底
    if isinstance(cfg, dict) and "messages" in cfg:
        # 尝试取第一条 system content
        msgs = cfg.get("messages")
        if isinstance(msgs, list) and msgs:
            m0 = msgs[0]
            if isinstance(m0, dict) and m0.get("role") == "system":
                content = m0.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()

    raise ValueError(f"{prompt_path} 里未找到可用的 prompt 字段（支持 prompt/system_prompt/system/analysis_prompt/messages）。")


def _call_ark_text(system_prompt: str, user_prompt: str) -> str:
    base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    api_key = os.getenv("ARK_API_KEY") or os.getenv("OPENAI_API_KEY")
    model = os.getenv("ARK_TEXT_MODEL") or os.getenv("ARK_VISION_MODEL")

    if not api_key:
        raise RuntimeError("缺少 ARK_API_KEY（或 OPENAI_API_KEY），请先在终端 export。")
    if not model:
        raise RuntimeError("缺少 ARK_TEXT_MODEL（或 ARK_VISION_MODEL），请先在终端 export。")

    client = OpenAI(api_key=api_key, base_url=base_url)

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()


def main() -> None:
    yaml_path, data_yaml_text = _load_latest_contrast_yaml()
    analysis_prompt = _load_analysis_prompt()

    user_input = f"""以下是一次电视 ANSI 对比度测试的结构化结果（YAML）：

{data_yaml_text}

请基于以上数据，按照既定评测标准，生成：
1) 一段完整的中文对比度分析结论（自然段）
2) 紧接着输出 editorial_verdict（YAML结构），包含 overall_positioning / strengths / tradeoffs。
要求：数据引用要准确（白场/黑场平均值、原生/有效对比度、调光增益等）。
"""

    result_text = _call_ark_text(analysis_prompt, user_input)

    print("===== 生成的评测结论 =====")
    print(result_text)

    out_path = yaml_path.with_name(yaml_path.stem + "_analysis.txt")
    out_path.write_text(result_text, encoding="utf-8")
    print(f"\n已保存到： {out_path.as_posix()}")


if __name__ == "__main__":
    main()
