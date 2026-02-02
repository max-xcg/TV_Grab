from typing import List, Dict, Any
from tv_buy_1_0.llm.deepseek_client import chat
from tv_buy_1_0.llm.prompt import SYSTEM_PROMPT, USER_TEMPLATE


def format_top3_facts(top3: List[Dict[str, Any]]) -> str:
    lines = []
    for i, tv in enumerate(top3, 1):
        lines.append(
            f"{i}. {tv.get('brand')} {tv.get('model')} {tv.get('size_inch')}寸 | "
            f"￥{tv.get('street_rmb')} | 首发 {tv.get('launch_date')}"
        )
        for k in [
            "input_lag_ms_60hz",
            "hdmi_2_1_ports",
            "allm",
            "vrr",
            "peak_brightness_nits",
            "local_dimming_zones",
        ]:
            if k in tv:
                lines.append(f"   - {k}: {tv.get(k)}")
    return "\n".join(lines)


def enhance_with_llm(
    top3: List[Dict[str, Any]],
    size: int,
    scene: str,
    budget: int | None,
) -> str:
    user_prompt = USER_TEMPLATE.format(
        size=size,
        scene=scene,
        budget=budget if budget is not None else "未指定",
        top3_facts=format_top3_facts(top3),
    )
    return chat(SYSTEM_PROMPT, user_prompt)
