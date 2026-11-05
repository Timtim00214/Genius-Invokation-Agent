# -*- coding: utf-8 -*-
"""【回合复盘】从模型的回合总结里抽出"意图 + 胜率"（review_parse）。

每回合结束时司机让模型写一段很短的复盘，格式约定是两行：

    意图：这一回合我想做什么、接下来怎么衔接、准备观察对手什么（≤200 字）
    胜率：0.42

模型经常不老实：会加粗、加项目符号、写"胜率约 45%"、把两行写在一起、或者干脆漏掉胜率。
本节点做的是**宽容解析**：能抽就抽，抽不到就给出``ok=False``与原因，让上游决定是否重试，
而不是抛异常把整局打断。

设计要点:
    - 意图优先按"意图/思路/打算/计划"等标签抓；抓不到就把整段当意图（去掉胜率行）。
    - 胜率接受 ``0.42`` / ``42%`` / ``42``（大于 1 自动按百分数换算），并夹到 [0, 1]。
    - 意图按 ``max_intent_chars`` 截断（需求是 200 字），超长时在末尾加省略号。

契约:
    输入 :class:`ReviewParseInput` / 输出 :class:`ReviewParseOutput`。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode, InputSchema

__all__ = ["ReviewParseInput", "ReviewParseOutput", "ReviewParseNode"]

_INTENT_LABEL = re.compile(r"(意图|思路|打算|计划|复盘)\s*[:：]\s*")
_RATE_LABEL = re.compile(r"(胜率|胜算|赢面|概率)\s*[:：]?\s*")
_RATE_VALUE = re.compile(r"([01](?:\.\d+)?|\d{1,3}(?:\.\d+)?)\s*(%|％)?")
# 没有标签时的兜底写法：只认百分比与 0.x / 1.0 这类小数
_RATE_PERCENT = re.compile(r"(?<![\d.])(\d{1,3})\s*(?:%|％)")
_RATE_DECIMAL = re.compile(r"(?<![\d.])([01]\.\d{1,3})(?![\d.])")
_MARKUP = re.compile(r"[*_`#>]+")


def _clean(text: str) -> str:
    """去掉 markdown 装饰与多余空白。

    Args:
        text (str): 原始文本。

    Returns:
        str: 清洗后的文本。
    """
    out = _MARKUP.sub("", text or "")
    out = out.replace("\r", "")
    out = re.sub(r"[ \t]+", " ", out)
    return out.strip()


def _to_ratio(raw: str, percent: bool) -> Optional[float]:
    """把 ``0.55`` / ``55`` / ``55%`` 归一化成 [0, 1] 的比例。"""
    try:
        num = float(raw)
    except ValueError:
        return None
    if percent or num > 1.0:
        num = num / 100.0
    if 0.0 <= num <= 1.0:
        return round(num, 4)
    return None


def _extract_rate(text: str) -> Optional[float]:
    """从文本里找出胜率数字。

    只认两种位置，避免把"第 4 回合"里的 4 当成 4%：

    1. ``胜率：`` / ``胜算:`` / ``赢面`` / ``概率`` 标签之后的第一个数字；
    2. 没有标签时，只认显式的 ``55%`` 或 ``0.55`` 这类写法。

    Args:
        text (str): 原始文本。

    Returns:
        Optional[float]: 落在 [0, 1] 的胜率；找不到时返回 None。
    """
    for label in _RATE_LABEL.finditer(text):
        tail = text[label.end() : label.end() + 40]
        match = _RATE_VALUE.search(tail)
        if not match:
            continue
        ratio = _to_ratio(match.group(1), bool(match.group(2)))
        if ratio is not None:
            return ratio
    for match in _RATE_PERCENT.finditer(text):
        ratio = _to_ratio(match.group(1), True)
        if ratio is not None:
            return ratio
    for match in _RATE_DECIMAL.finditer(text):
        ratio = _to_ratio(match.group(1), False)
        if ratio is not None:
            return ratio
    return None


class ReviewParseInput(InputSchema):
    """回合复盘的输入。"""

    text: str = Field(..., description="模型返回的复盘正文（两行：意图 / 胜率）", min_length=1)
    max_intent_chars: int = Field(200, ge=20, le=2000, description="意图部分最多保留多少字")
    default_winrate: Optional[float] = Field(
        None, description="解析不到胜率时使用的兜底值（不填则输出空）"
    )


class ReviewParseOutput(BaseModel):
    """回合复盘的结构化结果。"""

    model_config = {"extra": "forbid"}

    intent: str = Field("", description="这一手的意图（已截断到 max_intent_chars）")
    winrate: Optional[float] = Field(None, description="模型对当前胜率的估计（0~1）")
    ok: bool = Field(False, description="意图与胜率是否都解析成功")
    note: str = Field("", description="解析说明（缺胜率 / 缺意图 / 截断等）")
    raw_chars: int = Field(0, description="原始文本长度")


class ReviewParseNode(BaseNode):
    """把模型的两行复盘解析成结构化字段。"""

    node_type = "review_parse"
    description = "回合复盘：从模型总结里抽出「意图（≤200 字）」与「胜率（0~1）」"
    input_model = ReviewParseInput
    output_model = ReviewParseOutput

    async def execute(
        self,
        inputs: ReviewParseInput,
        context: Optional[Dict[str, Any]] = None,
    ) -> ReviewParseOutput:
        """解析复盘文本。

        Args:
            inputs (ReviewParseInput): 模型正文与截断长度。
            context (Optional[Dict[str, Any]]): 引擎注入的只读上下文（本节点不使用）。

        Returns:
            ReviewParseOutput: 意图、胜率与解析说明。
        """
        raw = inputs.text or ""
        text = _clean(raw)
        notes: list[str] = []

        winrate = _extract_rate(text)
        if winrate is None and inputs.default_winrate is not None:
            winrate = max(0.0, min(1.0, float(inputs.default_winrate)))
            notes.append("没有找到胜率，用了兜底值")
        elif winrate is None:
            notes.append("没有找到胜率数字")

        # 意图：优先取标签后面的内容；否则去掉胜率那一行，把剩下的当意图
        intent = ""
        label = _INTENT_LABEL.search(text)
        if label:
            rest = text[label.end():]
            cut = _RATE_LABEL.search(rest)
            intent = (rest[: cut.start()] if cut else rest).strip()
        else:
            lines = [
                line
                for line in text.split("\n")
                if line.strip() and not _RATE_LABEL.search(line)
            ]
            intent = "\n".join(lines).strip()
            if intent:
                notes.append("没有『意图』标签，已把剩余文字当作意图")

        intent = re.sub(r"\n{2,}", "\n", intent).strip()
        if not intent:
            notes.append("意图为空")
        elif len(intent) > inputs.max_intent_chars:
            intent = intent[: inputs.max_intent_chars - 1].rstrip() + "…"
            notes.append(f"意图已截断到 {inputs.max_intent_chars} 字")

        return ReviewParseOutput(
            intent=intent,
            winrate=winrate,
            ok=bool(intent) and winrate is not None,
            note="；".join(notes),
            raw_chars=len(raw),
        )
