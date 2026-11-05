# -*- coding: utf-8 -*-
"""【往期判断】把历史胜率与意图渲染成一小段可读表格（winrate_table）。

对局里最有用的"参照物"不是当前局面，而是**自己在过去几回合怎么想、当时觉得胜算多少**。
司机每回合末让模型估一次胜率（0~1）并留一句意图，本节点负责把这份历史整理成
提示词里的一段文字，让模型在估新的胜率时能看到自己的旧判断、发现形势是变好还是变坏。

输入是"列表套字典"（``[{round, winrate, intent}]``），输出是一段纯文本：

    第 1 回合末：45%｜先手铺场，观察对手是否抢攻
    第 2 回合末：38%｜被压血，改用护盾拖节奏
    …（更早的 3 条省略）
    趋势：从 45% 降到 38%（-7），最近三回合连续下降。

设计要点:
    - 只做渲染与统计，不做判断：胜率是模型自己给的，节点不替它改数。
    - 脏数据不炸：缺字段、胜负率越界、round 非数字都按"缺"处理并跳过。
    - 默认只保留最近 ``keep`` 条，避免提示词被历史淹没；超出部分在末尾提示省略了几条。

契约:
    输入 :class:`WinrateTableInput` / 输出 :class:`WinrateTableOutput`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode, InputSchema

__all__ = ["WinrateTableInput", "WinrateTableOutput", "WinrateTableNode"]


class WinrateTableInput(InputSchema):
    """往期判断的输入。"""

    history: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="历史记录列表，每项形如 {round, winrate, intent}；缺字段的项会被跳过",
    )
    keep: int = Field(6, ge=1, le=40, description="最多保留最近多少条（更早的只在末尾提示条数）")
    with_intent: bool = Field(True, description="是否带上每回合的意图文字（关掉只留胜率数字）")
    intent_chars: int = Field(60, ge=0, le=500, description="每条意图最多显示多少字，0 表示不截断")


class WinrateTableOutput(BaseModel):
    """渲染结果。"""

    model_config = {"extra": "forbid"}

    text: str = Field("", description="可直接塞进提示词的多行文本")
    count: int = Field(0, description="实际渲染了多少条")
    latest: Optional[float] = Field(None, description="最近一次胜率估计（0~1）")
    first: Optional[float] = Field(None, description="最早一次胜率估计（0~1）")
    trend: str = Field("", description="一句话趋势：上升 / 下降 / 持平 / 波动")
    chars: int = Field(0, description="文本长度")


def _as_float(value: Any) -> Optional[float]:
    """把任意值尽量解释成 0~1 的胜率。

    Args:
        value (Any): 原始值，可能是数字、字符串（含百分号）或百分比整数。

    Returns:
        Optional[float]: 落在 [0, 1] 的浮点数；无法解释时返回 None。
    """
    if isinstance(value, bool) or value is None:
        return None
    num: Optional[float] = None
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str):
        text = value.strip().replace("%", "").replace("％", "")
        try:
            num = float(text)
        except ValueError:
            return None
    if num is None:
        return None
    if num > 1.0 and num <= 100.0:
        num = num / 100.0
    if num < 0.0:
        num = 0.0
    if num > 1.0:
        num = 1.0
    return num


def _as_round(value: Any) -> Optional[int]:
    """把任意值尽量解释成回合号。

    Args:
        value (Any): 原始值。

    Returns:
        Optional[int]: 回合号；无法解释时返回 None。
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


class WinrateTableNode(BaseNode):
    """把历史胜率渲染成提示词里的一小段。"""

    node_type = "winrate_table"
    description = "往期判断：把历史胜率与意图渲染成提示词段落（供模型估新胜率时参照）"
    input_model = WinrateTableInput
    output_model = WinrateTableOutput

    async def execute(
        self,
        inputs: WinrateTableInput,
        context: Optional[Dict[str, Any]] = None,
    ) -> WinrateTableOutput:
        """渲染历史胜率。

        Args:
            inputs (WinrateTableInput): 历史列表与显示选项。
            context (Optional[Dict[str, Any]]): 引擎注入的只读上下文（本节点不使用）。

        Returns:
            WinrateTableOutput: 文本与统计信息。
        """
        rows: List[Dict[str, Any]] = []
        for item in inputs.history or []:
            if not isinstance(item, dict):
                continue
            winrate = _as_float(item.get("winrate", item.get("rate")))
            if winrate is None:
                continue
            rows.append(
                {
                    "round": _as_round(item.get("round")),
                    "winrate": winrate,
                    "intent": str(item.get("intent") or "").strip(),
                }
            )

        if not rows:
            return WinrateTableOutput(
                text="（还没有往期估计：这是本局第一次估计胜率）",
                count=0,
                latest=None,
                first=None,
                trend="",
                chars=0,
            )

        hidden = max(0, len(rows) - inputs.keep)
        shown = rows[hidden:]

        lines: List[str] = []
        if hidden:
            lines.append(f"（更早的 {hidden} 条已省略）")
        for row in shown:
            label = f"第 {row['round']} 回合末" if row["round"] is not None else "某回合末"
            percent = int(round(row["winrate"] * 100))
            if inputs.with_intent and row["intent"]:
                intent = row["intent"]
                if inputs.intent_chars and len(intent) > inputs.intent_chars:
                    intent = intent[: inputs.intent_chars] + "…"
                lines.append(f"{label}：{percent}%｜{intent}")
            else:
                lines.append(f"{label}：{percent}%")

        first = rows[0]["winrate"]
        latest = rows[-1]["winrate"]
        delta = latest - first
        if abs(delta) < 0.03:
            trend = "基本持平"
        elif delta > 0:
            trend = f"整体上升（+{int(round(delta * 100))}）"
        else:
            trend = f"整体下降（{int(round(delta * 100))}）"
        if len(rows) >= 3:
            recent = [r["winrate"] for r in rows[-3:]]
            if recent[0] > recent[1] > recent[2]:
                trend += "；最近三回合连续下降"
            elif recent[0] < recent[1] < recent[2]:
                trend += "；最近三回合连续上升"
        lines.append(f"趋势：{trend}")

        text = "\n".join(lines)
        return WinrateTableOutput(
            text=text,
            count=len(shown),
            latest=latest,
            first=first,
            trend=trend,
            chars=len(text),
        )
