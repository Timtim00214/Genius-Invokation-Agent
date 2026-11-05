# -*- coding: utf-8 -*-
"""
受限表达式求值器。

条件分支节点（``branch``）需要支持 ``count > 5``、``status == 'ok'``
这类判断。直接使用 ``eval`` 是**绝对禁止**的：蓝图来自前端画布，
属于不可信输入，``eval`` 会带来任意代码执行风险。

本模块用 ``ast`` 做白名单求值：只有明确列出的语法节点类型才会被执行，
其余一律拒绝并抛出具象异常。

安全边界:
    - 允许：常量、变量名、比较运算、布尔运算、算术运算、``not``、字面量容器。
    - 禁止：函数调用、属性访问、下标访问、推导式、赋值、导入、lambda。
    - 变量只能取自调用方显式传入的 ``variables`` 字典。

Example:
    >>> evaluate("count > 5", {"count": 8})
    True
    >>> evaluate("status == 'ok' and retries < 3", {"status": "ok", "retries": 1})
    True
"""

from __future__ import annotations

import ast
import operator
from typing import Any, Dict, Mapping

__all__ = ["ExpressionError", "evaluate", "safe_compare"]

# ----------------------------------------------------------------------
# 允许的运算符
# ----------------------------------------------------------------------
_BIN_OPS: Dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: Dict[type, Any] = {
    ast.Not: operator.not_,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_CMP_OPS: Dict[type, Any] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


class ExpressionError(ValueError):
    """表达式非法或求值失败时抛出的异常。

    Attributes:
        expression (str): 出错的原始表达式。
        reason (str): 具体原因。
    """

    def __init__(self, expression: str, reason: str) -> None:
        """初始化异常实例。

        Args:
            expression (str): 原始表达式。
            reason (str): 失败原因。
        """
        self.expression = expression
        self.reason = reason
        super().__init__(f"表达式求值失败 [{expression}]: {reason}")


def evaluate(expression: str, variables: Mapping[str, Any] | None = None) -> Any:
    """安全地求值一个受限表达式。

    Args:
        expression (str): 表达式文本，例如 ``"count > 5"``。
        variables (Mapping[str, Any] | None): 可引用的变量表。

    Returns:
        Any: 求值结果。

    Raises:
        ExpressionError: 语法错误、引用了未定义变量、或使用了被禁止的语法时。
    """
    text = (expression or "").strip()
    if not text:
        raise ExpressionError(expression, "表达式为空")

    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(expression, f"语法错误：{exc.msg}") from exc

    scope: Dict[str, Any] = dict(variables or {})
    try:
        return _eval_node(tree.body, scope)
    except ExpressionError:
        raise
    except Exception as exc:  # noqa: BLE001 - 运算期错误统一转领域异常
        raise ExpressionError(expression, f"运行期错误：{exc!r}") from exc


def safe_compare(expression: str, variables: Mapping[str, Any] | None = None) -> bool:
    """求值表达式并转为布尔值（供条件节点使用）。

    Args:
        expression (str): 表达式文本。
        variables (Mapping[str, Any] | None): 变量表。

    Returns:
        bool: 表达式的真值。

    Raises:
        ExpressionError: 同 :func:`evaluate`。
    """
    return bool(evaluate(expression, variables))


# ----------------------------------------------------------------------
# AST 求值（白名单）
# ----------------------------------------------------------------------
def _eval_node(node: ast.AST, scope: Dict[str, Any]) -> Any:
    """递归求值单个 AST 节点。

    Args:
        node (ast.AST): 语法树节点。
        scope (Dict[str, Any]): 变量表。

    Returns:
        Any: 该节点的值。

    Raises:
        ExpressionError: 遇到白名单之外的语法时抛出。
    """
    # 字面量：数字、字符串、True/False/None
    if isinstance(node, ast.Constant):
        return node.value

    # 列表 / 元组 / 集合 / 字典字面量
    if isinstance(node, ast.List):
        return [_eval_node(e, scope) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(e, scope) for e in node.elts)
    if isinstance(node, ast.Set):
        return {_eval_node(e, scope) for e in node.elts}
    if isinstance(node, ast.Dict):
        return {
            _eval_node(k, scope): _eval_node(v, scope)
            for k, v in zip(node.keys, node.values)
        }

    # 变量名：只能取自显式传入的作用域
    if isinstance(node, ast.Name):
        if node.id not in scope:
            raise ExpressionError(
                node.id, f"未定义的变量 {node.id!r}；可用变量: {sorted(scope)}"
            )
        return scope[node.id]

    # 二元运算
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ExpressionError(
                "", f"不支持的运算符 {type(node.op).__name__}"
            )
        return op(_eval_node(node.left, scope), _eval_node(node.right, scope))

    # 一元运算（not / 正负号）
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ExpressionError(
                "", f"不支持的一元运算符 {type(node.op).__name__}"
            )
        return op(_eval_node(node.operand, scope))

    # 比较运算（支持 a < b < c 链式比较）
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, scope)
        for op_node, comparator in zip(node.ops, node.comparators):
            op = _CMP_OPS.get(type(op_node))
            if op is None:
                raise ExpressionError(
                    "", f"不支持的比较符 {type(op_node).__name__}"
                )
            right = _eval_node(comparator, scope)
            if not op(left, right):
                return False
            left = right
        return True

    # 布尔运算（and / or，短路求值）
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: Any = True
            for value in node.values:
                result = _eval_node(value, scope)
                if not result:
                    return result
            return result
        result = False
        for value in node.values:
            result = _eval_node(value, scope)
            if result:
                return result
        return result

    # 三元表达式：a if cond else b
    if isinstance(node, ast.IfExp):
        return (
            _eval_node(node.body, scope)
            if _eval_node(node.test, scope)
            else _eval_node(node.orelse, scope)
        )

    # 其余一律拒绝：函数调用、属性访问、下标、推导式等都走这里。
    raise ExpressionError(
        "", f"禁止的语法：{type(node).__name__}（只允许比较、布尔与算术运算）"
    )
