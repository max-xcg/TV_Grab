# -*- coding: utf-8 -*-
"""
tv_buy_1_0/agent/dialogue_3p2.py

固定 4 问 + 固定 3+2 输出（无 score 版）
- 4问：尺寸 / 预算 / 用途(scene) / 品牌态度
- Top3：预算内，按“新机型优先（launch_date 新->旧）”排序；同月再按价格高->低（佣金友好）
- +2：预算内且不重复 Top3
    备选1：低价款（预算内最便宜）
    备选2：中价款（预算内最接近预算*0.7）

规则：
- 任何 price 缺失（None/<=0）直接过滤（不出现在 Top3 / 备选）
- Top3 / +2 都必须 <= budget_max（严格预算内）
- 品牌逻辑：
    * only：只取该品牌
    * exclude：排除这些品牌
    * any：不限

关键修复：
- 不再走 ToolClient(HTTP server) 去调 tv_rank（避免 server 端旧排序导致对话结果不一致）
- 直接调用本地 tv_buy_1_0.tools_cli.tv_rank.tool_call()（与你 CLI tv_rank 输出同源同排序）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

# ✅ 直接复用你已经验证正确的 CLI tv_rank（newest-first）
from tv_buy_1_0.tools_cli import tv_rank as tv_rank_mod


# =========================================================
# State
# =========================================================
@dataclass
class DialogState:
    size: Optional[int] = None
    budget_max: Optional[int] = None
    scene: Optional[str] = None  # movie/ps5/bright/sport

    brand_mode: str = "any"      # any / only / exclude
    brand_list: List[str] = None
    brand_asked: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d["brand_list"] is None:
            d["brand_list"] = []
        return d


# =========================================================
# Helpers
# =========================================================
def _norm_brand_token(x: str) -> str:
    return (x or "").strip().lower()


def _brand_to_db(brand: str) -> str:
    """
    把用户输入品牌规范化到 DB 里常见写法：
    tcl -> TCL
    hisense -> hisense / Hisense? 你 DB 里是啥就保持啥（这里保守：首字母大写，其余不动）
    """
    b = (brand or "").strip()
    if not b:
        return b
    low = b.lower()
    if low == "tcl":
        return "TCL"
    if low == "sony":
        return "SONY"
    if low == "samsung":
        return "SAMSUNG"
    if low == "hisense":
        # 你的 DB 里既出现过 "hisense 海信E8S..."，也可能是 "Hisense"
        # 这里不强行改，返回原输入（但把全小写 hisense 变成 hisense 也行）
        return "hisense"
    return b


def _safe_int_from_text(x: str) -> Optional[int]:
    """
    支持：6000 / 1.2万 / 8k / 13k / 2w / 20000
    """
    s = (x or "").strip().lower()
    if not s:
        return None

    m = re.search(r"(\d+(?:\.\d+)?)\s*万", s)
    if m:
        try:
            return int(float(m.group(1)) * 10000)
        except Exception:
            return None

    m = re.search(r"(\d+(?:\.\d+)?)\s*k", s)
    if m:
        try:
            return int(float(m.group(1)) * 1000)
        except Exception:
            return None

    m = re.search(r"(\d+(?:\.\d+)?)\s*w", s)
    if m:
        try:
            return int(float(m.group(1)) * 10000)
        except Exception:
            return None

    digits = re.sub(r"[^\d]", "", s)
    if digits:
        try:
            return int(digits)
        except Exception:
            return None
    return None


def _safe_size(x: str) -> Optional[int]:
    s = (x or "").strip().lower()
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        return None
    try:
        v = int(digits)
        if 20 <= v <= 120:
            return v
    except Exception:
        return None
    return None


def _norm_scene(x: str) -> Optional[str]:
    s = (x or "").strip().lower()
    if s in ("movie", "ps5", "bright", "sport"):
        return s
    return None


def _parse_brand_attitude(x: str) -> Tuple[Optional[str], List[str]]:
    """
    输入示例：
      - 无所谓
      - 只要 tcl
      - 排除 tcl
      - tcl（直接输入品牌，按 only 处理）
    返回：
      (mode, brand_list)
      mode: any/only/exclude 或 None(无法判断)
    """
    s = (x or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    if not s:
        return None, []

    if s in ("无所谓", "随便", "都行", "不限", "any"):
        return "any", []

    m = re.match(r"^(只要|只选|必须|就要)\s*(.+)$", s)
    if m:
        brands = [_norm_brand_token(b) for b in re.split(r"[,/，\s]+", m.group(2)) if b.strip()]
        brands = [b for b in brands if b]
        return "only", brands

    m = re.match(r"^(排除|不要|不考虑|别给)\s*(.+)$", s)
    if m:
        brands = [_norm_brand_token(b) for b in re.split(r"[,/，\s]+", m.group(2)) if b.strip()]
        brands = [b for b in brands if b]
        return "exclude", brands

    # 用户直接输入 tcl / hisense 等，按 only
    if re.fullmatch(r"[a-z0-9\u4e00-\u9fff\-_]+", s):
        return "only", [s]

    return None, []


def _launch_key(launch_date: Any) -> int:
    """
    把 '2026-01' => 202601；缺失=>0
    """
    if not launch_date:
        return 0
    s = str(launch_date).strip()
    m = re.match(r"^(\d{4})[-/](\d{1,2})", s)
    if not m:
        return 0
    y = int(m.group(1))
    mo = int(m.group(2))
    return y * 100 + mo


def _get_price(tv: Dict[str, Any]) -> Optional[int]:
    """
    CLI tv_rank 的输出字段是 price_cny
    DB 原字段是 street_rmb
    这里兼容三种
    """
    for k in ("price_cny", "street_rmb", "meta_price_cny"):
        v = tv.get(k)
        if v is None:
            continue
        try:
            iv = int(v)
            if iv > 0:
                return iv
        except Exception:
            continue
    return None


def _model_of(tv: Dict[str, Any]) -> str:
    return str(tv.get("model") or "").strip()


# =========================================================
# Dialogue Engine
# =========================================================
class Dialogue3p2:
    def __init__(self):
        pass

    def reset_state(self) -> DialogState:
        return DialogState(
            size=None,
            budget_max=None,
            scene=None,
            brand_mode="any",
            brand_list=[],
            brand_asked=False,
        )

    def _need_q1(self, st: DialogState) -> bool:
        return st.size is None

    def _need_q2(self, st: DialogState) -> bool:
        return st.budget_max is None

    def _need_q3(self, st: DialogState) -> bool:
        return st.scene is None

    def _need_q4(self, st: DialogState) -> bool:
        return not bool(st.brand_asked)

    def chat(self, user_text: str, state_dict: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        st = self._load_state(state_dict)
        text = (user_text or "").strip()

        if not text:
            return {"reply": "你可以按顺序输入：75 / 13k / ps5 / 只要 tcl（或 排除 tcl）", "state": st.to_dict(), "done": False}

        if text.lower() == "reset":
            st = self.reset_state()
            return {"reply": "✅ 已重置。Q1/4：你要多大尺寸？（例：75 / 65 / 85）", "state": st.to_dict(), "done": False}

        if text.lower() == "exit":
            return {"reply": "bye", "state": st.to_dict(), "done": True}

        # Q1
        if self._need_q1(st):
            v = _safe_size(text)
            if v is None:
                return {"reply": "Q1/4：你要多大尺寸？（例：75 / 65 / 85）", "state": st.to_dict(), "done": False}
            st.size = v
            return {"reply": "Q2/4：预算上限多少？（例：6000 / 1.2万 / 8k / 13k）", "state": st.to_dict(), "done": False}

        # Q2
        if self._need_q2(st):
            v = _safe_int_from_text(text)
            if v is None or v <= 0:
                return {"reply": "Q2/4：预算上限多少？（例：6000 / 1.2万 / 8k / 13k）", "state": st.to_dict(), "done": False}
            st.budget_max = v
            return {"reply": "Q3/4：主要场景是什么？（movie / ps5 / bright / sport）", "state": st.to_dict(), "done": False}

        # Q3
        if self._need_q3(st):
            v = _norm_scene(text)
            if v is None:
                return {"reply": "Q3/4：主要场景是什么？（movie / ps5 / bright / sport）", "state": st.to_dict(), "done": False}
            st.scene = v
            return {"reply": "Q4/4：品牌态度？（无所谓 / 只要 某品牌 / 排除 某品牌）", "state": st.to_dict(), "done": False}

        # Q4
        if self._need_q4(st):
            mode, brands = _parse_brand_attitude(text)
            if mode is None:
                return {"reply": "Q4/4：品牌态度？（无所谓 / 只要 某品牌 / 排除 某品牌）", "state": st.to_dict(), "done": False}
            st.brand_mode = mode
            st.brand_list = brands
            st.brand_asked = True
            reply = self._run_3p2(st)
            return {"reply": reply, "state": st.to_dict(), "done": True}

        # 都齐了 -> 继续输入也重复给结果（方便测试）
        reply = self._run_3p2(st)
        return {"reply": reply, "state": st.to_dict(), "done": True}

    def _load_state(self, d: Optional[Dict[str, Any]]) -> DialogState:
        if not d:
            return self.reset_state()
        st = DialogState(
            size=d.get("size"),
            budget_max=d.get("budget_max"),
            scene=d.get("scene"),
            brand_mode=d.get("brand_mode") or "any",
            brand_list=d.get("brand_list") if d.get("brand_list") is not None else [],
            brand_asked=bool(d.get("brand_asked", False)),
        )
        st.brand_list = [str(x).strip() for x in (st.brand_list or []) if str(x).strip()]
        return st

    def _brand_for_tool(self, st: DialogState) -> Optional[str]:
        if st.brand_mode == "only" and st.brand_list:
            return _brand_to_db(st.brand_list[0])
        return None

    def _apply_brand_exclude(self, items: List[Dict[str, Any]], st: DialogState) -> List[Dict[str, Any]]:
        if st.brand_mode != "exclude":
            return items
        ex = set([_brand_to_db(x) for x in (st.brand_list or []) if x])
        if not ex:
            return items
        out = []
        for t in items:
            b = _brand_to_db(str(t.get("brand") or ""))
            if b and b in ex:
                continue
            out.append(t)
        return out

    def _apply_budget_and_price_filter(self, items: List[Dict[str, Any]], budget_max: int) -> List[Dict[str, Any]]:
        out = []
        for t in items:
            p = _get_price(t)
            if p is None or p <= 0:
                continue
            if p > budget_max:
                continue
            out.append(t)
        return out

    def _sort_recent_then_price(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        新机型优先；同月则价格高优先（佣金友好）
        """
        return sorted(
            items,
            key=lambda t: (
                _launch_key(t.get("launch_date")),
                _get_price(t) or 0,
            ),
            reverse=True,
        )

    def _fmt_tv_line(self, tv: Dict[str, Any]) -> str:
        brand = tv.get("brand")
        model = tv.get("model")
        size = tv.get("size_inch")
        price = _get_price(tv)
        launch = tv.get("launch_date")
        return f"{brand} {model} | {size}寸 | ￥{price} | 首发 {launch}"

    def _pick_low_and_mid(
        self,
        pool: List[Dict[str, Any]],
        used_models: set,
        budget_max: int,
        target_ratio: float = 0.70,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        备选1：预算内最便宜（低价）
        备选2：预算内最接近预算*0.7（中价）
        """
        cand = []
        for t in pool:
            m = _model_of(t)
            if not m or m in used_models:
                continue
            p = _get_price(t)
            if p is None or p <= 0 or p > budget_max:
                continue
            cand.append(t)

        if not cand:
            return None, None

        # 低价：按价格升序；同价选更新的
        low_sorted = sorted(cand, key=lambda t: (_get_price(t) or 10**18, -_launch_key(t.get("launch_date"))))
        low_best = low_sorted[0] if low_sorted else None

        # 中价：按 |price - budget*0.7|；同等偏向更新/更高价
        target = int(budget_max * target_ratio)
        mid_sorted = sorted(
            cand,
            key=lambda t: (
                abs((_get_price(t) or 0) - target),
                -_launch_key(t.get("launch_date")),
                -(_get_price(t) or 0),
            ),
        )
        mid_best = mid_sorted[0] if mid_sorted else None

        if low_best and mid_best and _model_of(low_best) == _model_of(mid_best):
            for t in mid_sorted[1:]:
                if _model_of(t) != _model_of(low_best):
                    mid_best = t
                    break

        return low_best, mid_best

    def _run_3p2(self, st: DialogState) -> str:
        brand_for_tool = self._brand_for_tool(st)

        # ✅ 直接调用本地 tv_rank（与你 CLI tv_rank.py 一致）
        ranked = tv_rank_mod.tool_call(
            {
                "size": int(st.size),
                "scene": str(st.scene),
                "brand": brand_for_tool,                 # only 才传
                "budget_max": int(st.budget_max),
                "prefer_year": 2026,
                "top": 300,                              # 需要足够大的池，+2 才稳定
            }
        )

        items = (ranked or {}).get("top", []) if isinstance(ranked, dict) else []
        items = list(items)

        # exclude 过滤（any/only 已由 tool_brand/不传 brand 处理）
        items = self._apply_brand_exclude(items, st)

        # 严格预算内 + 缺价过滤
        items = self._apply_budget_and_price_filter(items, int(st.budget_max))

        # 再按“新机型优先；同月价格高->低”排序（佣金友好）
        items = self._sort_recent_then_price(items)

        top3 = items[:3]
        used_models = set([_model_of(t) for t in top3 if _model_of(t)])

        low_best, mid_best = self._pick_low_and_mid(
            pool=items,
            used_models=used_models,
            budget_max=int(st.budget_max),
            target_ratio=0.70,
        )

        brand_desc = "无所谓"
        if st.brand_mode == "only":
            brand_desc = f"只要 {str(st.brand_list[0]).upper()}" if st.brand_list else "只要（未识别）"
        elif st.brand_mode == "exclude":
            brand_desc = "排除 " + " / ".join([str(x).upper() for x in (st.brand_list or [])]) if st.brand_list else "排除（未识别）"

        lines: List[str] = []
        lines.append("✅ 已收集需求（固定4问完成）")
        lines.append(f"- 尺寸：{st.size} 寸")
        lines.append(f"- 预算上限：{st.budget_max} 元")
        lines.append(f"- 场景：{st.scene}")
        lines.append(f"- 品牌：{brand_desc}")
        lines.append("")
        lines.append("Top3：当前条件下综合最优（预算内，按“新机型优先”排序）")
        lines.append("--------------------------------------------------------------------")

        if not top3:
            lines.append("暂无可推荐机型（常见原因：预算内可用候选不足 或 价格缺失被过滤）")
        else:
            for i, tv in enumerate(top3, 1):
                lines.append(f"{i}. {self._fmt_tv_line(tv)}")

        lines.append("")
        lines.append("+2 备选（不重复 Top3）")
        lines.append("--------------------------------------------------------------------")

        if low_best:
            lines.append(f"备选1｜低价款：{self._fmt_tv_line(low_best)}")
        else:
            lines.append("备选1｜低价款：暂无（预算内找不到不重复且有价格的候选）")

        if mid_best:
            lines.append(f"备选2｜中价款：{self._fmt_tv_line(mid_best)}")
        else:
            lines.append("备选2｜中价款：暂无（预算内找不到不重复且有价格的候选）")

        lines.append("")
        lines.append("说明：排序规则=首发时间新->旧（权重最高）；同月再按价格高->低（更偏向高价机型）。")
        if st.brand_mode == "only":
            lines.append("说明：你选择了“只要某品牌”，因此 Top3/+2 都在该品牌内找。")
        elif st.brand_mode == "exclude":
            lines.append("说明：你选择了“排除某品牌”，因此结果已过滤这些品牌。")
        else:
            lines.append("说明：你未限制品牌，因此 Top3/+2 允许跨品牌。")

        return "\n".join(lines)
