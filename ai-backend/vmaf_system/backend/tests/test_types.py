# -*- coding: utf-8 -*-
"""
类型兼容判定测试。

本文件是全套测试中最有价值的一批：它曾抓出"类型预检形同虚设"的
重大缺陷（早期实现末尾有一句 ``return True`` 兜底，导致
``List[RegexMatchItem] -> str`` 被判为兼容）。任何改动
``core/parser.py`` 中 ``_type_verdict`` 的行为都必须先跑这批用例。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel

pytestmark = pytest.mark.types

# 判定结论常量
COMPATIBLE = "compatible"
NULLABLE = "nullable"
INCOMPATIBLE = "incompatible"


class Foo(BaseModel):
    """用于测试的两个结构相同、身份不同的模型之一。"""

    a: int


class Bar(BaseModel):
    """用于测试的两个结构相同、身份不同的模型之一。"""

    a: int


# (源类型, 目标类型, 期望结论, 用例说明)
CASES: List[tuple] = [
    # ---- 标量 ----
    (str, str, COMPATIBLE, "相同标量"),
    (int, int, COMPATIBLE, "int -> int"),
    (int, float, COMPATIBLE, "int -> float 数值放宽"),
    (float, int, INCOMPATIBLE, "float -> int 收窄必须拒绝"),
    (str, int, INCOMPATIBLE, "str -> int"),
    (bool, str, INCOMPATIBLE, "bool -> str"),
    # ---- 容器 ----
    (List[str], List[str], COMPATIBLE, "相同容器"),
    (List[int], List[float], COMPATIBLE, "容器元素 int -> float"),
    (List[str], List[int], INCOMPATIBLE, "容器元素 str -> int 必须拒绝"),
    (List[Foo], List[Foo], COMPATIBLE, "相同 Pydantic 模型"),
    (List[Foo], List[Bar], INCOMPATIBLE, "结构相同但身份不同的模型必须拒绝"),
    (Dict[str, int], Dict[str, int], COMPATIBLE, "相同 Dict"),
    (Dict[str, int], Dict[str, str], INCOMPATIBLE, "Dict 值类型不同"),
    (Dict[str, int], List[int], INCOMPATIBLE, "不同容器族（Dict -> List）"),
    # ---- 容器与标量互连 ----
    (List[Foo], str, INCOMPATIBLE, "容器 -> 标量 必须拒绝"),
    (str, List[str], INCOMPATIBLE, "标量 -> 容器 必须拒绝"),
    # ---- Optional / Union ----
    (Optional[str], str, NULLABLE, "Optional[str] -> str 降级为可空警告"),
    (Optional[str], Optional[str], COMPATIBLE, "Optional 相同"),
    (Optional[str], int, INCOMPATIBLE, "Optional[str] -> int"),
    (str, Optional[str], COMPATIBLE, "str -> Optional[str]"),
    (Optional[int], float, NULLABLE, "Optional[int] -> float 可空"),
    # ---- 通配 ----
    (Any, str, COMPATIBLE, "Any 作为源，通配"),
    (str, Any, COMPATIBLE, "Any 作为目标，通配"),
]

CASE_IDS = [f"{c[3]}" for c in CASES]


@pytest.mark.parametrize("source,target,expected,desc", CASES, ids=CASE_IDS)
def test_type_verdict_matrix(parser_module, source, target, expected, desc):
    """类型判定矩阵：每条连线场景都必须得出预期结论。

    Args:
        parser_module: 解析器模块夹具。
        source: 源类型注解。
        target: 目标类型注解。
        expected (str): 期望结论。
        desc (str): 用例说明。
    """
    verdict, reason = parser_module._type_verdict(source, target)
    assert verdict == expected, (
        f"用例「{desc}」判定错误：{source} -> {target} "
        f"期望 {expected}，实际 {verdict}（原因：{reason}）"
    )


def test_types_compatible_matches_verdict(parser_module):
    """``_types_compatible`` 必须与 ``_type_verdict`` 的结论一致。

    ``_types_compatible`` 是布尔快捷版本，其语义应为
    "非 incompatible 即兼容"（含 nullable 警告级）。

    Args:
        parser_module: 解析器模块夹具。
    """
    for source, target, expected, desc in CASES:
        boolean_result = parser_module._types_compatible(source, target)
        assert boolean_result == (expected != INCOMPATIBLE), (
            f"用例「{desc}」布尔结论与详细结论不一致"
        )


def test_type_name_expands_generics(parser_module):
    """类型名必须展开泛型参数，否则错误信息无法定位问题。

    若只显示 ``List``，用户看到"List 不能流入 str"时无法判断
    是哪个元素类型出了问题。

    Args:
        parser_module: 解析器模块夹具。
    """
    assert parser_module._type_name(List[str]) == "List[str]"
    assert parser_module._type_name(Dict[str, int]) == "Dict[str, int]"
    assert parser_module._type_name(Optional[int]) == "int | None"
    assert parser_module._type_name(List[Foo]) == "List[Foo]"
    assert parser_module._type_name(str) == "str"
    assert parser_module._type_name(Any) == "Any"


def test_optional_not_treated_as_container(parser_module):
    """回归测试：Optional 不得被误判为容器。

    缺陷背景：``get_origin(Optional[str])`` 返回 ``Union``，若让容器分支
    先执行，``Optional[str]`` 会被误当成容器，导致
    ``Optional[str] -> str`` 被错判为"容器不能作为标量"，
    且 Union 分支永远不可达。

    Args:
        parser_module: 解析器模块夹具。
    """
    verdict, _ = parser_module._type_verdict(Optional[str], str)
    assert verdict == NULLABLE, "Optional 判定被容器分支抢先拦截了"

    verdict2, _ = parser_module._type_verdict(str, Optional[str])
    assert verdict2 == COMPATIBLE, "str -> Optional[str] 应兼容"


def test_different_container_families_rejected(parser_module):
    """回归测试：不同容器族（Dict -> List）不得因兜底而被放行。

    Args:
        parser_module: 解析器模块夹具。
    """
    verdict, reason = parser_module._type_verdict(Dict[str, int], List[int])
    assert verdict == INCOMPATIBLE
    assert reason is not None


def test_numeric_widening_only_one_direction(parser_module):
    """数值放宽只允许 int -> float，反向收窄必须拒绝。

    Args:
        parser_module: 解析器模块夹具。
    """
    assert parser_module._types_compatible(int, float) is True
    assert parser_module._types_compatible(float, int) is False


def test_structurally_identical_but_distinct_models_rejected(parser_module):
    """结构相同但类身份不同的模型不得互连。

    这是"共享类型上提至契约层"这一架构决定的直接保障：
    Pydantic 按类身份校验，若放任结构相同的不同类型互连，
    运行时必然失败，因此必须在解析期拒绝。

    Args:
        parser_module: 解析器模块夹具。
    """
    assert parser_module._types_compatible(Foo, Bar) is False
    assert parser_module._types_compatible(List[Foo], List[Bar]) is False
    # 同一类型则必须放行（这正是共享 Message 得以工作的原因）。
    assert parser_module._types_compatible(Foo, Foo) is True
    assert parser_module._types_compatible(List[Foo], List[Foo]) is True
