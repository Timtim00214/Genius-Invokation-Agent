# -*- coding: utf-8 -*-
"""受限表达式求值器测试（分支条件的安全性保障）。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.types


@pytest.fixture()
def expr_module(backend_utils):
    """加载 ``utils/expr.py``。"""
    return backend_utils("expr")


# ----------------------------------------------------------------------
# 正常求值
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "expression,variables,expected",
    [
        ("value > 5", {"value": 8}, True),
        ("value > 5", {"value": 2}, False),
        ("value == 'ok'", {"value": "ok"}, True),
        ("value in ['a', 'b']", {"value": "a"}, True),
        ("value not in ['a', 'b']", {"value": "c"}, True),
        ("a > 1 and b < 5", {"a": 2, "b": 3}, True),
        ("a > 1 or b < 0", {"a": 2, "b": 9}, True),
        ("not value", {"value": False}, True),
        ("1 < value < 10", {"value": 5}, True),
        ("value * 2 == 10", {"value": 5}, True),
        ("value + 1 >= 3", {"value": 2}, True),
    ],
)
def test_evaluate_valid(expr_module, expression, variables, expected):
    """合法表达式应按预期求值。"""
    assert expr_module.evaluate(expression, variables) == expected


def test_evaluate_literal_only(expr_module):
    """纯字面量表达式也应可求值。"""
    assert expr_module.evaluate("1 + 1") == 2


def test_safe_compare_returns_bool(expr_module):
    """``safe_compare`` 必须返回布尔值。"""
    assert expr_module.safe_compare("1", {}) is True
    assert expr_module.safe_compare("0", {}) is False


# ----------------------------------------------------------------------
# 安全边界：必须拒绝的语法
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo pwned')",
        "open('/etc/passwd').read()",
        "value.__class__",
        "value[0]",
        "[x for x in range(3)]",
        "lambda: 1",
        "(1).bit_length()",
    ],
)
def test_evaluate_rejects_dangerous_syntax(expr_module, expression):
    """任何可能执行任意代码的语法都必须被拒绝。

    这是分支节点的核心安全边界：蓝图来自前端画布，属不可信输入，
    绝不能把用户输入交给 ``eval``。
    """
    with pytest.raises(expr_module.ExpressionError):
        expr_module.evaluate(expression, {"value": 1})


def test_evaluate_undefined_variable(expr_module):
    """引用未定义变量时应给出可用变量提示。"""
    with pytest.raises(expr_module.ExpressionError) as exc:
        expr_module.evaluate("missing > 1", {"value": 1})

    assert "未定义的变量" in str(exc.value)
    assert "value" in str(exc.value)


def test_evaluate_empty_expression(expr_module):
    """空表达式应被拒绝。"""
    with pytest.raises(expr_module.ExpressionError):
        expr_module.evaluate("   ", {})


def test_evaluate_syntax_error(expr_module):
    """语法错误应转为领域异常而非原生 SyntaxError。"""
    with pytest.raises(expr_module.ExpressionError):
        expr_module.evaluate("value >", {"value": 1})
