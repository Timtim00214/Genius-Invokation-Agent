# -*- coding: utf-8 -*-
"""
【可扩展】上下文压缩/处理节点。

架构图右侧的处理回路：``Core --> Compress --> Core``，是一个**双向**环节。
本节点负责在对话历史过长时对其进行有损压缩：保留系统提示与最近若干轮
对话，把更早的轮次折叠为一条摘要消息，从而把 token 消耗压回预算内。

设计红线:
    - 纯函数：输入完整的消息列表 + 预算，输出压缩后的**新**列表，
      绝不原地修改入参（Pydantic 模型保持不可变语义）。
    - 确定性优先：默认采用"保留尾部 + 头部摘要占位"的确定性策略，
      不依赖外部模型即可工作；``summary`` 可由上游的对话节点生成后注入。

契约:
    输入 :class:`ContextCompressInput` / 输出 :class:`ContextCompressOutput`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode, Message

__all__ = [
    "HistoryMessage",
    "ContextCompressInput",
    "ContextCompressOutput",
    "ContextCompressNode",
]

#: 复用契约层共享消息类型，保证与 llm_chat 节点可直连。
#: 保留 HistoryMessage 名称仅为阅读便利，它与 Message 是同一个类对象。
HistoryMessage = Message


class ContextCompressInput(BaseModel):
    """上下文压缩节点的输入契约。

    Attributes:
        messages (List[HistoryMessage]): 待压缩的完整消息列表（含 system）。
        max_messages (int): 压缩后消息总数的上限（含 system 与摘要位）。
        keep_last_turns (int): 必须原样保留的最近对话轮数（一轮 = user + assistant）。
        summary (Optional[str]): 由上游生成的历史摘要；留空时自动生成占位摘要。
        summary_prefix (str): 摘要消息的前缀标记文本。
    """

    messages: List[HistoryMessage] = Field(
        ..., min_length=1, description="待压缩的完整消息列表"
    )
    max_messages: int = Field(20, ge=2, le=1000, description="压缩后消息总数上限")
    keep_last_turns: int = Field(3, ge=1, le=100, description="原样保留的最近轮数")
    summary: Optional[str] = Field(
        None, description="历史摘要；留空时自动生成占位摘要"
    )
    summary_prefix: str = Field(
        "[历史摘要]", description="摘要消息的前缀标记"
    )


class ContextCompressOutput(BaseModel):
    """上下文压缩节点的输出契约。

    Attributes:
        messages (List[HistoryMessage]): 压缩后的消息列表（新对象，入参未被修改）。
        original_count (int): 压缩前消息条数。
        compressed_count (int): 压缩后消息条数。
        dropped_count (int): 被折叠掉的消息条数。
        compressed (bool): 是否实际发生了压缩。
        estimated_tokens (int): 压缩后内容的 token 估算值。
    """

    messages: List[HistoryMessage] = Field(
        ..., description="压缩后的消息列表"
    )
    original_count: int = Field(0, ge=0, description="压缩前消息条数")
    compressed_count: int = Field(0, ge=0, description="压缩后消息条数")
    dropped_count: int = Field(0, ge=0, description="被折叠的消息条数")
    compressed: bool = Field(False, description="是否实际发生压缩")
    estimated_tokens: int = Field(0, ge=0, description="压缩后 token 估算值")


def _estimate_tokens(text: str) -> int:
    """粗略估算文本 token 数。

    Args:
        text (str): 待估算文本。

    Returns:
        int: 估算的 token 数。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    rest = max(len(text) - cjk, 0)
    return cjk + rest // 4


class ContextCompressNode(BaseNode):
    """上下文压缩节点：把过长的对话历史折叠为"摘要 + 最近若干轮"。

    node_type: ``context_compress``
    """

    node_type: str = "context_compress"
    description: str = "【回路】上下文压缩：把过长历史折叠为摘要 + 最近若干轮，控制 token 预算"
    version: str = "0.1.0"
    input_model: type = ContextCompressInput
    output_model: type = ContextCompressOutput

    async def execute(
        self,
        inputs: ContextCompressInput,
        context: Optional[Dict[str, Any]] = None,
    ) -> ContextCompressOutput:
        """执行上下文压缩。

        策略（确定性）:
            1. 所有 ``system`` 消息永远保留在最前面；
            2. 其余消息按 ``keep_last_turns`` 保留最近若干轮；
            3. 更早的轮次折叠为一条 system 摘要消息；
            4. 若总条数仍超过 ``max_messages``，从最早的**非 system** 消息开始丢弃。

        Args:
            inputs (ContextCompressInput): 已校验的输入实例。
            context (Optional[Dict[str, Any]]): 运行时上下文（本节点未使用）。

        Returns:
            ContextCompressOutput: 压缩结果，含前后条数对比与 token 估算。
        """
        original_count = len(inputs.messages)

        system_messages = [m for m in inputs.messages if m.role == "system"]
        non_system = [m for m in inputs.messages if m.role != "system"]

        # 从尾部回溯，按 (user, assistant) 成对切分出最近 N 轮。
        keep_needed = inputs.keep_last_turns * 2
        recent = (
            non_system[-keep_needed:] if len(non_system) > keep_needed else list(non_system)
        )
        older = (
            non_system[: len(non_system) - len(recent)]
            if len(non_system) > len(recent)
            else []
        )

        result: List[HistoryMessage] = list(system_messages)
        dropped = 0

        if older:
            dropped = len(older)
            digest = inputs.summary or (
                f"（已折叠 {dropped} 条较早消息："
                + " | ".join(m.content[:40] for m in older[:5])
                + (" ..." if dropped > 5 else "")
                + "）"
            )
            result.append(
                HistoryMessage(
                    role="system", content=f"{inputs.summary_prefix} {digest}"
                )
            )

        result.extend(recent)

        # 超出总预算时，从最早的非 system / 非摘要消息开始丢弃。
        if len(result) > inputs.max_messages:
            overflow = len(result) - inputs.max_messages
            head_count = len(system_messages) + (1 if older else 0)
            result = result[:head_count] + result[head_count + overflow :]
            dropped += overflow

        estimated = _estimate_tokens("".join(m.content for m in result))

        return ContextCompressOutput(
            messages=result,
            original_count=original_count,
            compressed_count=len(result),
            dropped_count=dropped,
            compressed=dropped > 0,
            estimated_tokens=estimated,
        )
