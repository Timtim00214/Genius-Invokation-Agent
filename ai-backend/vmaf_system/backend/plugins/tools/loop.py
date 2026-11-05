# -*- coding: utf-8 -*-
"""
【控制流】循环节点（Loop）。

用于把"一组节点"重复执行多次，例如：

    第一次对话（拼几个模块）→ 第二次对话（再拼几个）→ 整体循环 N 次

**使用方式**

在画布上放一对 ``loop_start`` 与 ``loop_end``，把需要重复的节点放在
两者"之间"（拓扑上位于它们中间），然后把循环体最后一个节点的输出
连到 ``loop_end``。引擎会自动识别这个区间并重复执行。

::

    loop_start ──> [节点A] ──> [节点B] ──> loop_end ──> 后续节点
        ^                 循环体（重复 N 次）              ^
        └──────────── 循环结束后继续 ────────────────────┘

**配对规则**（由引擎解析器静态完成，不依赖用户手动指定 ID）::

    1. 每个 loop_start 必须与一个 loop_end 配对（孤立的 loop_end 会报错）
    2. **支持嵌套**：按拓扑序用栈配对，内层 loop_end 先关闭；
       交叉（A 开、B 开、A 关、B 关）会被明确拒绝
    3. 循环体内部的节点不得绕过本循环的 loop_end 直接连到循环外的节点

嵌套时可通过 ``loop.indexes``（由外到内的下标列表）与 ``loop.depth``
（当前深度）区分层级；``loop.index`` / ``loop.item`` 始终指向最内层。

设计红线:
    - 纯函数：循环节点自身不含任何状态，迭代由引擎调度。
    - 状态隔离：每次迭代通过 Context 的 ``loop`` 命名空间暴露
      ``index`` / ``item`` / ``indexes`` / ``depth``，不写入任何全局变量。

契约:
    :class:`LoopStartInput` / :class:`LoopStartOutput` /
    :class:`LoopEndInput` / :class:`LoopEndOutput`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode

__all__ = [
    "LoopStartInput",
    "LoopStartOutput",
    "LoopStartNode",
    "LoopEndInput",
    "LoopEndOutput",
    "LoopEndNode",
]


# ======================================================================
# 循环起点
# ======================================================================
class LoopStartInput(BaseModel):
    """循环起点的输入契约。

    Attributes:
        iterations (int): 重复次数，当未提供 ``items`` 时生效。
        items (Optional[List[Any]]): 待遍历的列表。提供时迭代次数取其长度，
            每次迭代通过 ``loop.item`` 暴露当前元素。
        value (Any): 需要传入循环体的初始值（可选）。
    """

    iterations: int = Field(
        3, ge=1, le=1000, description="重复次数（未提供 items 时生效）"
    )
    items: Optional[List[Any]] = Field(
        None, description="待遍历列表；提供时迭代次数取其长度"
    )
    value: Any = Field(None, description="传入循环体的初始值（可选）")


class LoopStartOutput(BaseModel):
    """循环起点的输出契约。

    Attributes:
        iterations_planned (int): 计划执行的迭代次数。
        item_count (int): 提供的 items 元素个数（无 items 时为 0）。
        value (Any): 原样透传的初始值（供循环体内首个节点使用）。
    """

    iterations_planned: int = Field(0, ge=0, description="计划迭代次数")
    item_count: int = Field(0, ge=0, description="items 元素个数")
    value: Any = Field(None, description="原样透传的初始值")


class LoopStartNode(BaseNode):
    """循环起点：声明一段循环体的开始与迭代次数。

    node_type: ``loop_start``
    """

    node_type: str = "loop_start"
    description: str = (
        "【控制流】循环起点：与 loop_end 配对，把两者之间的节点重复执行 N 次"
    )
    version: str = "0.1.0"
    input_model: type = LoopStartInput
    output_model: type = LoopStartOutput

    #: 引擎据此识别控制流节点并做特殊调度。
    control_flow: str = "loop_start"

    async def execute(
        self, inputs: LoopStartInput, context: Optional[Dict[str, Any]] = None
    ) -> LoopStartOutput:
        """声明迭代计划（真正的循环由引擎执行）。

        Args:
            inputs (LoopStartInput): 已校验的输入实例。
            context (Optional[Dict[str, Any]]): 运行时上下文（本节点未使用）。

        Returns:
            LoopStartOutput: 迭代计划。
        """
        planned = len(inputs.items) if inputs.items is not None else inputs.iterations

        return LoopStartOutput(
            iterations_planned=planned,
            item_count=len(inputs.items) if inputs.items is not None else 0,
            value=inputs.value,
        )


# ======================================================================
# 循环终点
# ======================================================================
class LoopEndInput(BaseModel):
    """循环终点的输入契约。

    Attributes:
        value (Any): 来自循环体最后一次迭代的结果，作为聚合输入。
            引擎会把每次迭代末尾的输出自动汇总进 ``results``。
        label (str): 可在画布上显示的自定义名称，仅用于展示。
    """

    value: Any = Field(None, description="循环体最后一次迭代传出的结果")
    label: str = Field("", description="循环标签，仅用于展示")


class LoopEndOutput(BaseModel):
    """循环终点的输出契约。

    Attributes:
        iterations_done (int): 实际完成的迭代次数。
        results (List[Dict[str, Any]]): 每次迭代的聚合结果，
            每项形如 ``{"index": 0, "outputs": {节点id: 输出字典}}``。
        last_value (Any): 最后一次迭代传出的值。
        stopped_early (bool): 是否因迭代中出错而提前结束。
    """

    iterations_done: int = Field(0, ge=0, description="实际完成的迭代次数")
    results: List[Dict[str, Any]] = Field(
        default_factory=list, description="每次迭代的聚合结果"
    )
    last_value: Any = Field(None, description="最后一次迭代传出的值")
    stopped_early: bool = Field(False, description="是否因错误提前结束")


class LoopEndNode(BaseNode):
    """循环终点：汇总各次迭代的结果。

    node_type: ``loop_end``
    """

    node_type: str = "loop_end"
    description: str = "【控制流】循环终点：与 loop_start 配对，汇总每次迭代的结果"
    version: str = "0.1.0"
    input_model: type = LoopEndInput
    output_model: type = LoopEndOutput

    #: 引擎据此识别控制流节点并做特殊调度。
    control_flow: str = "loop_end"

    async def execute(
        self, inputs: LoopEndInput, context: Optional[Dict[str, Any]] = None
    ) -> LoopEndOutput:
        """汇总循环结果。

        真正的聚合由引擎在每轮迭代后写入 Context，本节点只读取并
        组装为强类型输出。直接单独调用时（不在循环中）返回空聚合。

        Args:
            inputs (LoopEndInput): 已校验的输入实例。
            context (Optional[Dict[str, Any]]): 运行时上下文（只读视图）。

        Returns:
            LoopEndOutput: 聚合结果。
        """
        shared = (context or {}).get("shared", {}) or {}
        results: List[Dict[str, Any]] = shared.get("loop.results", []) or []

        return LoopEndOutput(
            iterations_done=len(results),
            results=results,
            last_value=inputs.value,
            stopped_early=bool(shared.get("loop.stopped_early", False)),
        )
