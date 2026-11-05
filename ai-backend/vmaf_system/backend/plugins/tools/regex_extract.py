# -*- coding: utf-8 -*-
"""
【可扩展】正则抽取节点。

架构图中的左侧增强能力：挂在对话节点旁，用于从任意文本（模型回复、
文件内容、网页正文）中按正则表达式抽取结构化片段，常作为
"联网 -> 正则 -> 对话" 或 "读文件 -> 正则 -> 对话" 链路的中间环节。

设计红线:
    - 纯函数：不缓存编译结果到全局，每次执行独立编译（``re`` 模块自身
      有内部缓存，插件层不引入额外可变状态）。
    - 强类型防御：非法正则当场抛出 ``ValueError``，不做静默吞异常。

契约:
    输入 :class:`RegexExtractInput` / 输出 :class:`RegexExtractOutput`。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode

__all__ = [
    "RegexExtractInput",
    "RegexExtractOutput",
    "RegexMatchItem",
    "RegexExtractNode",
]


class RegexMatchItem(BaseModel):
    """单条正则匹配结果。

    Attributes:
        match (str): 完整匹配到的文本。
        groups (List[str]): 捕获组列表（未参与匹配的组为空字符串）。
        named_groups (Dict[str, str]): 命名捕获组映射。
        start (int): 匹配起始下标（含）。
        end (int): 匹配结束下标（不含）。
    """

    match: str = Field(..., description="完整匹配文本")
    groups: List[str] = Field(default_factory=list, description="捕获组列表")
    named_groups: Dict[str, str] = Field(
        default_factory=dict, description="命名捕获组映射"
    )
    start: int = Field(0, ge=0, description="匹配起始下标")
    end: int = Field(0, ge=0, description="匹配结束下标")


class RegexExtractInput(BaseModel):
    """正则抽取节点的输入契约。

    Attributes:
        text (str): 待抽取的源文本。
        pattern (str): 正则表达式（Python ``re`` 语法）。
        mode (Literal["findall", "search"]): ``findall`` 返回全部匹配，
            ``search`` 仅返回首个匹配。
        limit (int): 最多返回的匹配条数，取值 1 ~ 1000。
        flags (List[str]): 修饰符列表，支持 ``IGNORECASE`` / ``MULTILINE`` /
            ``DOTALL``。
    """

    text: str = Field(
        ..., description="待匹配的源文本，通常由上游节点（读文件 / 联网）连线注入"
    )
    pattern: str = Field(
        ...,
        min_length=1,
        description=(
            "正则表达式，采用 Python re 语法。例：^class (\\w+) 表示匹配行首的 class "
            "声明并捕获类名；\\d+ 表示连续数字"
        ),
    )
    mode: Literal["findall", "search"] = Field(
        "findall",
        description="findall 返回全部匹配；search 只返回第一个匹配",
    )
    limit: int = Field(
        100, ge=1, le=1000, description="最多返回的匹配条数，超出部分会被截断"
    )
    flags: List[str] = Field(
        default_factory=list,
        description=(
            "修饰符，可多选：IGNORECASE 忽略大小写；MULTILINE 让 ^ 和 $ 匹配每一行的"
            "首尾；DOTALL 让 . 也能匹配换行符"
        ),
    )


class RegexExtractOutput(BaseModel):
    """正则抽取节点的输出契约。

    Attributes:
        pattern (str): 实际生效的正则表达式。
        matches (List[RegexMatchItem]): 匹配结果列表。
        count (int): 匹配条数（与 ``matches`` 长度一致）。
        truncated (bool): 是否因 ``limit`` 限制而截断了结果。
    """

    pattern: str = Field(..., description="实际生效的正则表达式")
    matches: List[RegexMatchItem] = Field(
        default_factory=list, description="匹配结果列表"
    )
    count: int = Field(0, ge=0, description="匹配条数")
    truncated: bool = Field(False, description="结果是否因 limit 被截断")


_FLAG_MAP: Dict[str, int] = {
    "IGNORECASE": re.IGNORECASE,
    "MULTILINE": re.MULTILINE,
    "DOTALL": re.DOTALL,
}


def _build_flags(flag_names: List[str]) -> int:
    """把修饰符名称列表转换为 ``re`` 模块标志位。

    Args:
        flag_names (List[str]): 修饰符名称列表。

    Returns:
        int: 按位或后的 ``re`` 标志位；无有效修饰符时为 0。
    """
    result = 0
    for name in flag_names:
        result |= _FLAG_MAP.get(name.strip().upper(), 0)
    return result


class RegexExtractNode(BaseNode):
    """正则抽取节点：从文本中按正则提取结构化片段。

    node_type: ``regex_extract``
    """

    node_type: str = "regex_extract"
    description: str = "按正则表达式从文本中抽取结构化片段，支持捕获组与命名组"
    version: str = "0.1.0"
    input_model: type = RegexExtractInput
    output_model: type = RegexExtractOutput

    async def execute(
        self, inputs: RegexExtractInput, context: Optional[Dict[str, Any]] = None
    ) -> RegexExtractOutput:
        """执行正则抽取。

        Args:
            inputs (RegexExtractInput): 已校验的输入实例。
            context (Optional[Dict[str, Any]]): 运行时上下文（本节点未使用）。

        Returns:
            RegexExtractOutput: 抽取结果。

        Raises:
            ValueError: 正则表达式非法时抛出。
        """
        try:
            compiled = re.compile(inputs.pattern, _build_flags(inputs.flags))
        except re.error as exc:
            raise ValueError(f"非法的正则表达式 {inputs.pattern!r}: {exc}") from exc

        raw_matches: List[re.Match] = []
        if inputs.mode == "search":
            found = compiled.search(inputs.text)
            if found is not None:
                raw_matches.append(found)
        else:
            raw_matches = list(compiled.finditer(inputs.text))

        truncated = len(raw_matches) > inputs.limit
        items: List[RegexMatchItem] = [
            RegexMatchItem(
                match=m.group(0),
                groups=[g if g is not None else "" for g in m.groups()],
                named_groups={
                    k: (v if v is not None else "")
                    for k, v in (m.groupdict() or {}).items()
                },
                start=m.start(),
                end=m.end(),
            )
            for m in raw_matches[: inputs.limit]
        ]

        return RegexExtractOutput(
            pattern=inputs.pattern,
            matches=items,
            count=len(items),
            truncated=truncated,
        )
