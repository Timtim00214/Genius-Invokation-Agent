# -*- coding: utf-8 -*-
"""
【可扩展】tool-use 节点（工具调用意图解析与分发）。

架构图下方的工具调用 / 动作能力：把模型输出的"工具调用意图"解析为
结构化指令，并按注册表分发给对应的下游动作节点（如 ``edit_file``）。

本节点本身**不执行**任何副作用动作，它只产出"应当调用什么工具、参数是什么"
的结构化决策——真正的写操作由下游动作节点完成，以此守住
"对话链路无副作用"的纯函数红线。

设计红线:
    - 纯函数：仅做解析与意图结构化，不触碰文件系统与网络。
    - 强类型防御：未知工具名、非法 JSON 参数都当场抛出具象异常。

契约:
    输入 :class:`ToolUseInput` / 输出 :class:`ToolUseOutput`。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode

__all__ = ["ToolCall", "ToolUseInput", "ToolUseOutput", "ToolUseNode"]


class ToolCall(BaseModel):
    """单条工具调用意图。

    Attributes:
        name (str): 工具名称，需存在于本节点注册的工具表中。
        arguments (Dict[str, Any]): 解析后的工具参数字典。
        raw_arguments (str): 原始参数文本（JSON 字符串或自由文本）。
        parsed (bool): 参数是否成功解析为 JSON 对象。
    """

    name: str = Field(..., description="工具名称")
    arguments: Dict[str, Any] = Field(
        default_factory=dict, description="解析后的工具参数"
    )
    raw_arguments: str = Field("", description="原始参数文本")
    parsed: bool = Field(False, description="参数是否成功解析为 JSON 对象")


class ToolUseInput(BaseModel):
    """tool-use 节点的输入契约。

    Attributes:
        model_output (str): 模型的原始输出文本，其中可能包含工具调用段。
        available_tools (List[str]): 允许调用的工具名白名单。
        call_format (str): 工具调用的解析格式，支持 ``json_block``
            （```json ... ``` 包裹）与 ``function_call``（OpenAI 风格）。
        allow_unknown (bool): 为 True 时，未在白名单中的工具名会被记录但
            不抛异常；为 False 时直接抛 ``ValueError``。
    """

    model_output: str = Field(..., description="模型原始输出文本")
    available_tools: List[str] = Field(
        default_factory=list, description="允许调用的工具名白名单"
    )
    call_format: str = Field(
        "json_block",
        pattern="^(json_block|function_call)$",
        description="解析格式：json_block / function_call",
    )
    allow_unknown: bool = Field(
        False, description="是否容忍白名单之外的工具名（记录但不抛异常）"
    )


class ToolUseOutput(BaseModel):
    """tool-use 节点的输出契约。

    Attributes:
        calls (List[ToolCall]): 解析出的工具调用意图列表。
        count (int): 调用意图条数。
        has_calls (bool): 是否解析到了至少一条调用。
        rejected (List[str]): 因不在白名单而被拒绝的工具名列表。
        plain_text (str): 剔除工具调用段后的纯文本回复。
    """

    calls: List[ToolCall] = Field(
        default_factory=list, description="解析出的工具调用意图列表"
    )
    count: int = Field(0, ge=0, description="调用意图条数")
    has_calls: bool = Field(False, description="是否解析到调用意图")
    rejected: List[str] = Field(
        default_factory=list, description="被拒绝的工具名列表"
    )
    plain_text: str = Field("", description="剔除调用段后的纯文本回复")


def _extract_json_blocks(text: str) -> List[str]:
    """抽取文本中被 ```json ... ``` 包裹的片段。

    Args:
        text (str): 原始文本。

    Returns:
        List[str]: 抽取到的 JSON 片段列表。
    """
    import re

    return re.findall(r"```json\s*(.*?)```", text, re.S)


def _strip_json_blocks(text: str) -> str:
    """剔除文本中的 ```json ... ``` 片段。

    Args:
        text (str): 原始文本。

    Returns:
        str: 剔除后的纯文本。
    """
    import re

    return re.sub(r"```json\s*.*?```", "", text, flags=re.S).strip()


class ToolUseNode(BaseNode):
    """tool-use 节点：解析模型输出中的工具调用意图（不执行副作用）。

    node_type: ``tool_use``
    """

    node_type: str = "tool_use"
    description: str = "解析模型输出中的工具调用意图并分发为结构化指令（自身不执行副作用）"
    version: str = "0.1.0"
    input_model: type = ToolUseInput
    output_model: type = ToolUseOutput

    async def execute(
        self, inputs: ToolUseInput, context: Optional[Dict[str, Any]] = None
    ) -> ToolUseOutput:
        """解析工具调用意图。

        Args:
            inputs (ToolUseInput): 已校验的输入实例。
            context (Optional[Dict[str, Any]]): 运行时上下文（本节点未使用）。

        Returns:
            ToolUseOutput: 解析结果，含意图列表与被拒绝项。

        Raises:
            ValueError: 遇到白名单外的工具名且 ``allow_unknown`` 为 False 时抛出。
        """
        allowed = set(inputs.available_tools)
        calls: List[ToolCall] = []
        rejected: List[str] = []

        blocks = _extract_json_blocks(inputs.model_output)
        for block in blocks:
            try:
                payload = json.loads(block.strip())
            except json.JSONDecodeError:
                continue

            candidates: List[Dict[str, Any]] = []
            if isinstance(payload, list):
                candidates = [c for c in payload if isinstance(c, dict)]
            elif isinstance(payload, dict):
                if "name" in payload:
                    candidates = [payload]
                elif "function_calls" in payload:
                    candidates = list(payload["function_calls"])

            for candidate in candidates:
                name = str(candidate.get("name", "")).strip()
                if not name:
                    continue
                arguments = candidate.get("arguments") or candidate.get("parameters") or {}
                if not isinstance(arguments, dict):
                    arguments = {}

                if allowed and name not in allowed:
                    rejected.append(name)
                    if not inputs.allow_unknown:
                        raise ValueError(
                            f"模型请求调用未注册工具 {name!r}；"
                            f"可用工具: {sorted(allowed)}"
                        )
                    continue

                calls.append(
                    ToolCall(
                        name=name,
                        arguments=arguments,
                        raw_arguments=block.strip(),
                        parsed=True,
                    )
                )

        return ToolUseOutput(
            calls=calls,
            count=len(calls),
            has_calls=bool(calls),
            rejected=rejected,
            plain_text=_strip_json_blocks(inputs.model_output),
        )
