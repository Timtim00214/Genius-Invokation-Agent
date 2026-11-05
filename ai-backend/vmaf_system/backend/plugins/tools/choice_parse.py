# -*- coding: utf-8 -*-
"""
【解析】把模型的自由文本回答解析成"编号"（choice_parse）。

模型不会永远只说一个数字：它可能说"3"、"我选 3"、`{"index": 3}`、
"应该用 5 号技能"，也可能说了个不在菜单里的编号。本节点负责把这些都收敛成
一个**白名单内的整数**，并明确告诉下游"解析成功还是失败、失败原因是什么"。

这样做的好处是把"格式不稳定"关在这一个节点里：
下游 ``rpc_build`` 只面对 ``index: int | None``，不必再考虑文本。
解析失败不等于回合报销——``rpc_build`` 会用默认动作兜底，
司机也会把 ``reason`` 作为反馈带着重跑一次。

除了单个 ``index``，还输出 ``numbers``（回复里出现过的全部白名单编号）：
    掷骰重投、换手牌这类"多选"请求需要一次回答好几个位置，单选请求只用 ``index`` 即可，
    两个字段互不影响。

    契约:
        输入 :class:`ChoiceParseInput` / 输出 :class:`ChoiceParseOutput`。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode

__all__ = ["ChoiceParseInput", "ChoiceParseOutput", "ChoiceParseNode"]

#: 从 JSON 里尝试读取编号的键名（按优先级）。
_JSON_KEYS: tuple = ("index", "action", "choice", "number", "id", "option")

#: 多选时模型可能用的 JSON 键名（值是数组）：重投骰子/换手牌一次报好几个位置。
_JSON_LIST_KEYS: tuple = ("numbers", "indexes", "indices", "choices", "dice", "positions", "handIds")

#: 一次最多接受多少个编号：防止模型把整段菜单编号全抄回来。
_MAX_NUMBERS = 16

#: 匹配"方括号里的编号"，例如 ``[3]``、``【选项 3】``。
_BRACKET_RE = re.compile(r"[\[【（(]\s*(\d{1,3})\s*[\]】）)]")

#: 兜底匹配任意独立整数（避免匹配到 "3.5" 的小数部分或 "15101" 这种技能 id）。
_INT_RE = re.compile(r"(?<![\d.])(\d{1,3})(?![\d.])")


class ChoiceParseInput(BaseModel):
    """编号解析的输入契约。

    Attributes:
        text (str): 模型原始回复，连线接 ``llm_chat.reply``。
        valid_indexes (List[int]): 合法编号白名单，连线接 ``action_menu.valid_indexes``；
            留空表示不校验（仅在调试时这样做）。
        mode (Literal["auto", "index", "json"]): 解析策略。auto 先试 JSON 再试整数。
        default_index (Optional[int]): 解析失败时使用的编号；留空则返回空，让下游兜底。
        max_chars (int): 只解析回复的前若干字符，防止模型写长篇大论。
    """

    model_config = {"extra": "forbid"}

    text: str = Field("", description="模型原始回复，连线接 llm_chat.reply")
    valid_indexes: List[int] = Field(
        default_factory=list,
        description="合法编号白名单，连线接 action_menu.valid_indexes；留空表示不校验",
    )
    mode: Literal["auto", "index", "json"] = Field(
        "auto", description="解析策略：auto 先试 JSON 再试整数；index 只试整数；json 只试 JSON"
    )
    default_index: Optional[int] = Field(
        None, description="解析失败时的默认编号；留空则由下游（rpc_build）兜底"
    )
    max_chars: int = Field(2000, ge=10, le=20000, description="只解析回复的前若干字符")


class ChoiceParseOutput(BaseModel):
    """编号解析的输出契约。

    Attributes:
        index (Optional[int]): 解析出的第一个合法编号；失败时为 None 或 ``default_index``。
        numbers (List[int]): 回复里出现过的全部合法编号（多选请求用；单选时通常只有一个）。
        ok (bool): 是否解析出一个合法编号。
        reason (str): 失败原因（可直接作为重试反馈喂回模型）。
        matched (str): 实际命中的原始片段，便于排查"模型到底说了什么"。
    """

    model_config = {"extra": "forbid"}

    index: Optional[int] = Field(None, description="解析出的第一个合法编号")
    numbers: List[int] = Field(default_factory=list, description="全部合法编号（多选请求用）")
    ok: bool = Field(False, description="是否解析出合法编号")
    reason: str = Field("", description="失败原因（可回灌给模型）")
    matched: str = Field("", description="命中的原始片段")


class ChoiceParseNode(BaseNode):
    """把自由文本收敛成白名单内的编号。"""

    node_type = "choice_parse"
    description = "把模型回复解析成编号，并按合法编号白名单校验"
    input_model = ChoiceParseInput
    output_model = ChoiceParseOutput

    async def execute(
        self,
        inputs: ChoiceParseInput,
        context: Optional[Dict[str, Any]] = None,
    ) -> ChoiceParseOutput:
        """解析编号并校验白名单。

        Args:
            inputs (ChoiceParseInput): 原始文本、白名单与策略。
            context (Optional[Dict[str, Any]]): 未使用。

        Returns:
            ChoiceParseOutput: 编号、是否成功、失败原因。
        """
        text = (inputs.text or "")[: inputs.max_chars]
        allowed = set(inputs.valid_indexes or [])
        candidates: List[tuple] = []  # (number, matched_text, source)

        if inputs.mode in ("auto", "json"):
            for number, matched in self._from_json_multi(text):
                candidates.append((number, matched, "json"))
            parsed = self._from_json(text)
            if parsed is not None:
                candidates.append((parsed[0], parsed[1], "json"))

        if inputs.mode in ("auto", "index"):
            for number, matched in self._from_text(text):
                candidates.append((number, matched, "text"))

        picked: List[int] = []
        matched_first = ""
        for number, matched, source in candidates:
            if allowed and number not in allowed:
                continue
            if number in picked:
                continue
            if not picked:
                matched_first = f"{source}:{matched}"
            picked.append(number)
            if len(picked) >= _MAX_NUMBERS:
                break
        if picked:
            return ChoiceParseOutput(
                index=picked[0],
                numbers=picked,
                ok=True,
                reason="",
                matched=matched_first,
            )

        # 走到这里：要么没解析出任何数字，要么解析出的都不在白名单里。
        if inputs.default_index is not None and (
            not allowed or inputs.default_index in allowed
        ):
            return ChoiceParseOutput(
                index=inputs.default_index,
                numbers=[],
                ok=False,
                reason=(
                    "没能从回复里解析出合法编号，已改用默认编号 "
                    f"{inputs.default_index}"
                ),
                matched=candidates[0][1] if candidates else "",
            )

        if candidates:
            reason = (
                f"回复里的编号 {candidates[0][0]} 不在可选范围内；"
                f"只能从 {sorted(allowed)} 里选一个数字"
                if allowed
                else f"回复里的编号 {candidates[0][0]} 无法确认，请只回复一个数字"
            )
            return ChoiceParseOutput(
                numbers=[], index=None, ok=False, reason=reason, matched=candidates[0][1]
            )

        return ChoiceParseOutput(
            numbers=[],
            index=None,
            ok=False,
            reason="回复里没有数字，也没有可识别的 JSON 字段；请只回复一个编号数字",
            matched="",
        )

    # ------------------------------------------------------------------
    # 解析辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _from_json(text: str) -> Optional[tuple]:
        """尝试把回复中的第一段 JSON 对象解析出编号。

        Args:
            text (str): 回复文本。

        Returns:
            Optional[tuple]: ``(编号, 原始片段)``；无法解析时返回 None。
        """
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        snippet = text[start : end + 1]
        try:
            payload = json.loads(snippet)
        except (ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        for key in _JSON_KEYS:
            if key in payload:
                value = payload[key]
                if isinstance(value, bool):
                    continue
                if isinstance(value, int):
                    return value, f'{{"{key}":{value}}}'
                if isinstance(value, str) and value.strip().isdigit():
                    return int(value.strip()), f'{{"{key}":"{value}"}}'
        return None

    @staticmethod
    def _from_json_multi(text: str) -> List[tuple]:
        """从回复的 JSON 对象里读出"数组形式的多个编号"（重投骰子/换手牌用）。

        Args:
            text (str): 回复文本。

        Returns:
            List[tuple]: ``[(编号, 原始片段), ...]``；没有数组字段时返回空列表。
        """
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            payload = json.loads(text[start : end + 1])
        except (ValueError, TypeError):
            return []
        if not isinstance(payload, dict):
            return []
        for key in _JSON_LIST_KEYS:
            value = payload.get(key)
            if not isinstance(value, list):
                continue
            found: List[tuple] = []
            for item in value:
                if isinstance(item, bool):
                    continue
                if isinstance(item, int):
                    found.append((item, f'{{"{key}":[{item}]}}'))
                elif isinstance(item, str) and item.strip().lstrip("-").isdigit():
                    number = int(item.strip())
                    if number >= 0:
                        found.append((number, f'{{"{key}":"{item}"}}'))
            if found:
                return found
        return []

    @staticmethod
    def _from_text(text: str) -> List[tuple]:
        """按"方括号优先、独立整数其次"的顺序收集编号。

        Args:
            text (str): 回复文本。

        Returns:
            List[tuple]: ``[(编号, 原始片段), ...]``，按置信度从高到低。
        """
        found: List[tuple] = []
        for match in _BRACKET_RE.finditer(text):
            found.append((int(match.group(1)), match.group(0)))
        if not found:
            for match in _INT_RE.finditer(text):
                found.append((int(match.group(1)), match.group(1)))
        # 去重但保持顺序
        unique: List[tuple] = []
        seen = set()
        for number, matched in found:
            if (number, matched) in seen:
                continue
            seen.add((number, matched))
            unique.append((number, matched))
        return unique
