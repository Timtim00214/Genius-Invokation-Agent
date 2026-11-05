# -*- coding: utf-8 -*-
"""
【可扩展】读文件节点。

架构图中的左下输入能力：把本地文本文件读入流水线，供对话节点消费
（例如注入提示词、喂给 RAG 或正则抽取节点）。

设计红线:
    - 纯函数：只读不写，任何读取异常都转换为具象异常抛出。
    - 安全围栏：拒绝目录路径，并对单次读取体积设置上限，防止误读大文件
      撑爆内存总线。

契约:
    输入 :class:`ReadFileInput` / 输出 :class:`ReadFileOutput`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator

from ..base import BaseNode

__all__ = ["ReadFileInput", "ReadFileOutput", "ReadFileNode"]


class ReadFileInput(BaseModel):
    """读文件节点的输入契约。

    Attributes:
        path (str): 目标文件路径（相对路径按当前工作目录解析）。
        encoding (str): 文本编码，默认 ``utf-8``。
        start_line (Optional[int]): 起始行号（含），1 为基准；留空则从头读。
        end_line (Optional[int]): 结束行号（含）；留空则读到文件末尾。
        max_bytes (int): 单文件读取体积上限（字节），超出即拒绝。
    """

    path: str = Field(
        ...,
        min_length=1,
        description=(
            "要读取的文件路径。推荐填绝对路径，例如 D:/data/报告.txt 或 /home/u/a.md；"
            "相对路径按后端进程的工作目录解析"
        ),
    )
    encoding: str = Field(
        "utf-8",
        description="文本编码。一般用 utf-8；Windows 下旧版记事本保存的中文文件可能是 gbk",
    )
    start_line: Optional[int] = Field(
        None, ge=1, description="只读取从第几行开始（含该行，行号从 1 起）。留空表示从头读"
    )
    end_line: Optional[int] = Field(
        None, ge=1, description="只读取到第几行结束（含该行）。留空表示读到末尾"
    )
    max_bytes: int = Field(
        5 * 1024 * 1024,
        ge=1,
        le=200 * 1024 * 1024,
        description=(
            "允许读取的最大字节数，默认 5MB。文件大于该值时直接报错拒绝，"
            "用于防止误读超大日志把内存撑爆"
        ),
    )

    @field_validator("end_line")
    @classmethod
    def _check_range(cls, value: Optional[int], info: Any) -> Optional[int]:
        """校验行号区间的合法性。

        Args:
            value (Optional[int]): 结束行号。
            info (Any): Pydantic 校验上下文，用于取同模型的 ``start_line``。

        Returns:
            Optional[int]: 原样返回通过校验的结束行号。

        Raises:
            ValueError: 结束行号早于起始行号时抛出。
        """
        start = info.data.get("start_line")
        if value is not None and start is not None and value < start:
            raise ValueError("end_line 不能早于 start_line")
        return value


class ReadFileOutput(BaseModel):
    """读文件节点的输出契约。

    Attributes:
        path (str): 规范化后的绝对路径字符串。
        content (str): 文件内容（若指定了行区间则为切片内容）。
        line_count (int): 实际返回内容的行数。
        total_lines (int): 文件总行数。
        size_bytes (int): 文件字节大小。
        truncated (bool): 是否因行区间设置而返回了截断内容。
    """

    path: str = Field(..., description="规范化后的绝对路径")
    content: str = Field(..., description="文件内容")
    line_count: int = Field(0, ge=0, description="返回内容的行数")
    total_lines: int = Field(0, ge=0, description="文件总行数")
    size_bytes: int = Field(0, ge=0, description="文件字节大小")
    truncated: bool = Field(False, description="是否返回了截断内容")


class ReadFileNode(BaseNode):
    """读文件节点：把本地文本文件内容载入流水线。

    node_type: ``read_file``
    """

    node_type: str = "read_file"
    description: str = "读取本地文本文件内容，支持按行区间切片与体积上限保护"
    version: str = "0.1.0"
    input_model: type = ReadFileInput
    output_model: type = ReadFileOutput

    async def execute(
        self, inputs: ReadFileInput, context: Optional[Dict[str, Any]] = None
    ) -> ReadFileOutput:
        """读取目标文本文件。

        Args:
            inputs (ReadFileInput): 已校验的输入实例。
            context (Optional[Dict[str, Any]]): 运行时上下文（本节点未使用）。

        Returns:
            ReadFileOutput: 文件内容的强类型结果。

        Raises:
            FileNotFoundError: 路径不存在时抛出。
            IsADirectoryError: 路径指向目录时抛出。
            ValueError: 文件体积超过 ``max_bytes`` 时抛出。
            UnicodeDecodeError: 编码不匹配时抛出。
        """
        target = Path(inputs.path).expanduser().resolve()

        if not target.exists():
            raise FileNotFoundError(f"文件不存在: {target}")
        if target.is_dir():
            raise IsADirectoryError(f"目标路径是目录，无法按文件读取: {target}")

        size_bytes = target.stat().st_size
        if size_bytes > inputs.max_bytes:
            raise ValueError(
                f"文件体积 {size_bytes} 字节超出上限 {inputs.max_bytes} 字节"
            )

        raw = target.read_text(encoding=inputs.encoding)
        all_lines = raw.splitlines()
        total_lines = len(all_lines)

        if inputs.start_line is None and inputs.end_line is None:
            content = raw
            selected = all_lines
            truncated = False
        else:
            start = (inputs.start_line or 1) - 1
            end = inputs.end_line or total_lines
            selected = all_lines[start:end]
            content = "\n".join(selected)
            truncated = True

        return ReadFileOutput(
            path=str(target),
            content=content,
            line_count=len(selected),
            total_lines=total_lines,
            size_bytes=size_bytes,
            truncated=truncated,
        )
