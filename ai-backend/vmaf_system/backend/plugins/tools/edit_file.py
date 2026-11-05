# -*- coding: utf-8 -*-
"""
【可扩展】改文件节点（动作 / 副作用节点）。

架构图下方的动作能力之一。与"读文件"相反，本节点是**唯一允许产生
外部副作用**的一类节点。为此它内建三重安全围栏：

    1. **路径围栏**：``allow_root`` 之外的路径一律拒绝写入。
    2. **模式围栏**：四种写入模式（append / overwrite / replace / insert_line），
       其中破坏性最强的 ``overwrite`` 必须显式把 ``allow_overwrite`` 置为 True。
    3. **痕迹可追**：默认自动创建父目录并输出操作前后的字节数变化。

设计红线:
    - 状态隔离：每次调用独立完成"读-改-写"，不缓存文件句柄。
    - 强类型防御：目标文件缺失、命中次数不符预期等一律抛出具象异常，
       绝不静默吞错。

契约:
    输入 :class:`EditFileInput` / 输出 :class:`EditFileOutput`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from ..base import BaseNode

__all__ = ["EditFileInput", "EditFileOutput", "EditFileNode"]


class EditFileInput(BaseModel):
    """改文件节点的输入契约。

    Attributes:
        path (str): 目标文件路径（相对路径按当前工作目录解析）。
        mode (Literal["append", "overwrite", "replace", "insert_line"]): 写入模式。
        content (str): 待写入的文本内容（四种模式均使用）。
        old_text (Optional[str]): ``replace`` 模式下被替换的原文。
        line_number (Optional[int]): ``insert_line`` 模式下的插入行号（1 为基准）。
        allow_root (Optional[str]): 允许写入的根目录；留空则不启用路径围栏。
        allow_overwrite (bool): 是否允许破坏性的 ``overwrite`` 模式。
        create (bool): 目标文件不存在时是否自动创建。
        encoding (str): 文件编码。
        dry_run (bool): 演练模式，仅计算改动不落盘。
    """

    path: str = Field(
        ...,
        min_length=1,
        description=(
            "要写入的文件路径。建议填绝对路径，例如 D:/out/结果.md；"
            "若配置了 allow_root，则路径必须位于该目录之内"
        ),
    )
    mode: Literal["append", "overwrite", "replace", "insert_line"] = Field(
        "append",
        description=(
            "写入方式：append 追加到末尾；overwrite 覆盖全文（破坏性，需打开 "
            "allow_overwrite）；replace 把 old_text 替换为 content；"
            "insert_line 在第 line_number 行前插入一行"
        ),
    )
    content: str = Field("", description="待写入的文本内容")
    old_text: Optional[str] = Field(None, description="replace 模式的被替换原文")
    line_number: Optional[int] = Field(
        None, ge=1, description="insert_line 模式的插入行号"
    )
    allow_root: Optional[str] = Field(
        None,
        description=(
            "允许写入的根目录（安全围栏）。填写后，任何越出该目录的路径都会被拒绝，"
            "用于防止误改系统文件；留空表示不限制"
        ),
    )
    allow_overwrite: bool = Field(
        False,
        description="是否允许 overwrite 覆盖全文。默认关闭，需要时必须显式打开",
    )
    create: bool = Field(True, description="目标文件不存在时是否自动创建")
    encoding: str = Field("utf-8", description="写入使用的文本编码")
    dry_run: bool = Field(
        False, description="演练模式：只计算会改成什么，不真正写盘，用于确认改动"
    )

    @model_validator(mode="after")
    def _check_mode_args(self) -> "EditFileInput":
        """校验各模式所需的附加参数是否齐备。

        Returns:
            EditFileInput: 通过校验的实例自身。

        Raises:
            ValueError: 模式与参数不匹配时抛出。
        """
        if self.mode == "replace" and not self.old_text:
            raise ValueError("replace 模式必须提供 old_text")
        if self.mode == "insert_line" and self.line_number is None:
            raise ValueError("insert_line 模式必须提供 line_number")
        if self.mode == "overwrite" and not self.allow_overwrite:
            raise ValueError(
                "overwrite 为破坏性操作，必须显式将 allow_overwrite 置为 true"
            )
        return self


class EditFileOutput(BaseModel):
    """改文件节点的输出契约。

    Attributes:
        path (str): 规范化后的绝对路径。
        changed (bool): 内容是否实际发生变化。
        size_before (int): 操作前文件字节数。
        size_after (int): 操作后文件字节数。
        replacements (int): ``replace`` 模式下命中的替换次数。
        created (bool): 本次是否新建了文件。
        dry_run (bool): 本次是否为演练模式。
    """

    path: str = Field(..., description="规范化后的绝对路径")
    changed: bool = Field(False, description="内容是否发生变化")
    size_before: int = Field(0, ge=0, description="操作前字节数")
    size_after: int = Field(0, ge=0, description="操作后字节数")
    replacements: int = Field(0, ge=0, description="replace 模式命中次数")
    created: bool = Field(False, description="是否新建文件")
    dry_run: bool = Field(False, description="是否为演练模式")


class EditFileNode(BaseNode):
    """改文件节点：在路径与模式双重围栏下写入本地文件。

    node_type: ``edit_file``
    """

    node_type: str = "edit_file"
    description: str = "写入/修改本地文件，受路径围栏与破坏性模式开关双重保护"
    version: str = "0.1.0"
    input_model: type = EditFileInput
    output_model: type = EditFileOutput

    async def execute(
        self, inputs: EditFileInput, context: Optional[Dict[str, Any]] = None
    ) -> EditFileOutput:
        """执行一次文件写入。

        Args:
            inputs (EditFileInput): 已校验的输入实例。
            context (Optional[Dict[str, Any]]): 运行时上下文（本节点未使用）。

        Returns:
            EditFileOutput: 写入结果，含字节数变化与替换命中次数。

        Raises:
            PermissionError: 目标路径越出 ``allow_root`` 围栏时抛出。
            FileNotFoundError: 文件不存在且 ``create`` 为 False 时抛出。
            ValueError: 目标路径是目录，或 ``replace`` 模式未命中时抛出。
        """
        target = Path(inputs.path).expanduser().resolve()

        if inputs.allow_root:
            root = Path(inputs.allow_root).expanduser().resolve()
            if not _is_within(target, root):
                raise PermissionError(
                    f"目标路径 {target} 越出允许写入的根目录 {root}"
                )

        if target.exists() and target.is_dir():
            raise ValueError(f"目标路径是目录，无法作为文件写入: {target}")

        existed = target.is_file()
        if not existed and not inputs.create:
            raise FileNotFoundError(f"文件不存在且 create=False: {target}")

        original = target.read_text(encoding=inputs.encoding) if existed else ""
        size_before = target.stat().st_size if existed else 0

        replacements = 0
        if inputs.mode == "append":
            new_text = f"{original}{inputs.content}"
        elif inputs.mode == "overwrite":
            new_text = inputs.content
        elif inputs.mode == "replace":
            replacements = original.count(inputs.old_text or "")
            if replacements == 0:
                raise ValueError(
                    f"replace 模式未在文件中找到目标片段: {inputs.old_text!r}"
                )
            new_text = original.replace(inputs.old_text or "", inputs.content)
        else:  # insert_line
            lines = original.splitlines()
            index = min(inputs.line_number or 1, len(lines) + 1) - 1
            lines.insert(index, inputs.content)
            new_text = "\n".join(lines) + ("\n" if original.endswith("\n") else "")

        changed = new_text != original

        if not inputs.dry_run and changed:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_text, encoding=inputs.encoding)

        return EditFileOutput(
            path=str(target),
            changed=changed,
            size_before=size_before,
            size_after=len(new_text.encode(inputs.encoding)),
            replacements=replacements,
            created=not existed and not inputs.dry_run,
            dry_run=inputs.dry_run,
        )


def _is_within(target: Path, root: Path) -> bool:
    """判断目标路径是否位于允许根目录之内。

    Args:
        target (Path): 目标文件绝对路径。
        root (Path): 允许写入的根目录绝对路径。

    Returns:
        bool: 位于根目录之内（含根目录自身）时返回 True。
    """
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True
