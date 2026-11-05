# -*- coding: utf-8 -*-
"""赛前计划的收尾节点：去掉客套话、压到字数以内。

为什么需要它:
    "战术计划不超过 500 字，并且在之后每一手都带上"是这个项目的硬要求。
    模型很自然会多写（"好的，下面是我的战术："、分段编号、结尾总结），而计划
    要每回合重复注入，超一点点都会持续放大 token 成本。这里做三件事：

    1. 去掉开头客套（"好的"、"以下是……"）与包裹的引号；
    2. 合并多余空行，压成紧凑段落；
    3. 超长时按句子边界截断（不是硬切在半句），并标注已截断。

    它不评价计划好坏——那是提示词与人的事。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..base import BaseNode

#: 常见的"开场废话"前缀，逐条剥掉（大小写不敏感，只剥开头）。
_PREAMBLE = [
    r"^\s*(好的|好|明白|收到|没问题)[，,。！!\s]*",
    r"^\s*(以下|下面)(是|为)?(我|我们)?(的)?(战术|计划|策略|思路)[：:\s]*",
    r"^\s*(战术|计划|策略|思路)[：:\s]+",
]

#: 句末标点，用于"尽量切在句子结尾"。
_SENTENCE_END = re.compile(r"[。！？!?；;\n]")


class PlanGuardInput(BaseModel):
    """``plan_guard`` 的输入。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field("", description="模型写的计划原文")
    max_chars: int = Field(500, ge=50, le=5000, description="计划字数上限（含标点，按字符计）")


class PlanGuardOutput(BaseModel):
    """``plan_guard`` 的输出。"""

    model_config = ConfigDict(extra="forbid")

    plan: str = Field("", description="清洗后的计划，可直接注入后续每一手")
    chars: int = Field(0, description="清洗后字符数")
    truncated: bool = Field(False, description="是否发生了截断")
    dropped: List[str] = Field(default_factory=list, description="被去掉的部分：preamble / quotes / blank_lines / over_length")


def _strip_preamble(text: str) -> tuple:
    """剥掉开场客套话，返回 (新文本, 是否改过)。"""
    changed = False
    for _ in range(4):
        before = text
        for pattern in _PREAMBLE:
            text = re.sub(pattern, "", text, count=1, flags=re.IGNORECASE)
        if text == before:
            break
        changed = True
    return text, changed


def _cut_at_sentence(text: str, limit: int) -> str:
    """尽量在 limit 以内切在句末标点上，切不到就硬切。"""
    window = text[:limit]
    best = -1
    for match in _SENTENCE_END.finditer(window):
        best = match.end()
    if best >= limit * 0.5:
        return window[:best].rstrip()
    return window.rstrip()


class PlanGuardNode(BaseNode):
    """把计划洗成"可直接每回合注入"的短文本。"""

    node_type = "plan_guard"
    description = "清洗赛前计划：去客套、压空行、按句截断到字数上限"
    version = "0.1.0"
    input_model = PlanGuardInput
    output_model = PlanGuardOutput

    async def execute(self, inputs: PlanGuardInput, context: Optional[Dict[str, Any]] = None) -> PlanGuardOutput:
        """执行清洗。"""
        dropped: List[str] = []
        text = (inputs.text or "").strip()

        # 去掉模型习惯加的包裹引号 / 代码围栏
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
            dropped.append("quotes")
        stripped = text.strip().strip('"“”')
        if stripped != text:
            dropped.append("quotes")
        text = stripped

        text, changed = _strip_preamble(text)
        if changed:
            dropped.append("preamble")

        # 客套话后面常常又跟一个引号（"好的，以下是战术：\n“……”"），再剥一次
        restripped = text.strip().strip('"“”').strip()
        if restripped != text and "quotes" not in dropped:
            dropped.append("quotes")
        text = restripped

        compact = re.sub(r"\n{3,}", "\n\n", text).strip()
        if compact != text:
            dropped.append("blank_lines")
        text = compact

        truncated = False
        if len(text) > inputs.max_chars:
            text = _cut_at_sentence(text, inputs.max_chars)
            truncated = True
            dropped.append("over_length")

        return PlanGuardOutput(plan=text, chars=len(text), truncated=truncated, dropped=dropped)
