# -*- coding: utf-8 -*-
"""
【控制流】条件分支节点（If / Else）。

架构图中"对话"周围的决策点：根据条件把数据导向两条路径之一，
类似代码里的 ``if / else``。

**工作方式**

    输入 value ──┐
                ├─> 求值 expression ─> route = "on_true" | "on_false"
    表达式 ─────┘
                        │
          ┌─────────────┴─────────────┐
       on_true                     on_false
          │                           │
       （下游 A）                  （下游 B）

引擎读取输出中的 ``route`` 字段，**只激活被选中那一条边**，
另一条分支上的节点会被跳过（如果它们没有其它活跃输入）。

**表达式写法**

    ``value`` 始终可用，代表连进来的输入值。例如::

        value > 5
        value == 'ok'
        value in ['a', 'b']
        value >= 0 and value < 100

    也可以填 ``variables`` 里的自定义变量。表达式在**受限求值器**
    （``utils/expr.py``）中执行，只允许比较、布尔与算术运算，
    禁止函数调用、属性访问等一切可能执行任意代码的语法。

设计红线:
    - 纯函数：只做判定与透传，不产生副作用。
    - 强类型：表达式非法当场抛异常，绝不静默走错分支。

契约:
    输入 :class:`BranchInput` / 输出 :class:`BranchOutput`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode
from ...utils.expr import ExpressionError, evaluate

__all__ = ["BranchInput", "BranchOutput", "BranchNode"]


class BranchInput(BaseModel):
    """条件分支节点的输入契约。

    Attributes:
        value (Any): 待判定的值，同时会以变量名 ``value`` 进入表达式作用域。
            通常由上游连线注入（例如 ``regex_extract.count``）。
        expression (str): 布尔表达式，如 ``"value > 5"``。留空时按
            ``bool(value)`` 判定。
        variables (Dict[str, Any]): 表达式中可额外引用的变量。
        condition (Optional[bool]): 直接指定结果，优先级高于 expression。
            适合由上游明确给出布尔值的场景。
    """

    value: Any = Field(None, description="待判定的值，表达式中可用变量名 value 引用")
    expression: str = Field(
        "", description="布尔表达式，例如 value > 5；留空则按 bool(value) 判定"
    )
    variables: Dict[str, Any] = Field(
        default_factory=dict, description="表达式中可额外引用的变量"
    )
    condition: Optional[bool] = Field(
        None, description="直接指定判定结果，优先级高于 expression"
    )


class BranchOutput(BaseModel):
    """条件分支节点的输出契约。

    Attributes:
        route (Literal["on_true", "on_false"]): 命中的分支名。**引擎据此
            决定激活哪条出边**，用户无需手动连线该字段。
        on_true (Any): 走 true 分支时向下游传递的值（原样透传 ``value``）。
        on_false (Any): 走 false 分支时向下游传递的值（原样透传 ``value``）。
        matched (bool): 条件是否成立，便于日志排查。
    """

    route: Literal["on_true", "on_false"] = Field(
        ..., description="命中的分支名，引擎据此激活对应出边"
    )
    on_true: Any = Field(None, description="true 分支向下游传递的值")
    on_false: Any = Field(None, description="false 分支向下游传递的值")
    matched: bool = Field(False, description="条件是否成立")


class BranchNode(BaseNode):
    """条件分支节点：按条件把数据导向两条路径之一。

    node_type: ``branch``
    """

    node_type: str = "branch"
    description: str = "【控制流】条件分支（If/Else）：按表达式把数据导向 true 或 false 路径"
    version: str = "0.1.0"
    input_model: type = BranchInput
    output_model: type = BranchOutput

    #: 引擎据此识别控制流节点并做特殊调度。
    control_flow: str = "branch"

    async def execute(
        self, inputs: BranchInput, context: Optional[Dict[str, Any]] = None
    ) -> BranchOutput:
        """执行条件判定。

        Args:
            inputs (BranchInput): 已校验的输入实例。
            context (Optional[Dict[str, Any]]): 运行时上下文（本节点未使用）。

        Returns:
            BranchOutput: 含命中分支与透传值的结果。

        Raises:
            ValueError: 表达式非法时抛出（携带具体原因）。
        """
        matched = self._decide(inputs)

        return BranchOutput(
            route="on_true" if matched else "on_false",
            on_true=inputs.value if matched else None,
            on_false=None if matched else inputs.value,
            matched=matched,
        )

    @staticmethod
    def _decide(inputs: BranchInput) -> bool:
        """计算条件结果。

        优先级：``condition`` > ``expression`` > ``bool(value)``。

        Args:
            inputs (BranchInput): 输入实例。

        Returns:
            bool: 条件是否成立。

        Raises:
            ValueError: 表达式非法时抛出。
        """
        if inputs.condition is not None:
            return bool(inputs.condition)

        if inputs.expression.strip():
            scope: Dict[str, Any] = {"value": inputs.value}
            scope.update(inputs.variables)
            try:
                return bool(evaluate(inputs.expression, scope))
            except ExpressionError as exc:
                raise ValueError(
                    f"分支条件表达式非法：{exc.reason}（表达式：{inputs.expression}）"
                ) from exc

        return bool(inputs.value)
