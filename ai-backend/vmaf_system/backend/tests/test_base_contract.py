# -*- coding: utf-8 -*-
"""
契约层（``base.py``）测试。

验证"底层法律"本身的行为：抽象约束、契约自检、Schema 导出、
共享消息类型的互通性。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest
from pydantic import BaseModel, Field, ValidationError

pytestmark = pytest.mark.contract


class _TextIn(BaseModel):
    """测试用输入模型。"""

    text: str = Field(..., description="输入文本")
    times: int = Field(1, description="重复次数")


class _TextOut(BaseModel):
    """测试用输出模型。"""

    result: str = Field(..., description="结果文本")


def _make_echo(BaseNode: type) -> type:
    """构造一个合法的具体节点类。

    Args:
        BaseNode (type): 契约层的抽象基类。

    Returns:
        type: 实现了 ``execute`` 的 ``BaseNode`` 子类。
    """

    class Echo(BaseNode):  # type: ignore[misc,valid-type]
        node_type = "test_echo"
        description = "测试用回显节点"
        input_model = _TextIn
        output_model = _TextOut

        async def execute(self, inputs, context=None):
            return _TextOut(result=inputs.text * inputs.times)

    return Echo


# ----------------------------------------------------------------------
# 抽象约束
# ----------------------------------------------------------------------
def test_base_node_cannot_be_instantiated(BaseNode):
    """``BaseNode`` 是抽象基类，不得被直接实例化。"""
    with pytest.raises(TypeError):
        BaseNode()  # type: ignore[abstract]


def test_subclass_must_implement_execute(BaseNode):
    """未实现 ``execute`` 的子类仍是抽象类，不得实例化。"""

    class Incomplete(BaseNode):  # type: ignore[misc,valid-type]
        node_type = "incomplete"
        description = "未实现 execute"

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


# ----------------------------------------------------------------------
# 契约自检
# ----------------------------------------------------------------------
def test_validate_contract_passes_for_valid_node(BaseNode):
    """契约完整的节点应通过自检。"""
    Echo = _make_echo(BaseNode)
    Echo.validate_contract()  # 不抛异常即通过


def test_validate_contract_rejects_empty_node_type(BaseNode):
    """``node_type`` 为空时必须抛出 ``ValueError``。"""

    class NoType(BaseNode):  # type: ignore[misc,valid-type]
        description = "缺少 node_type"
        input_model = _TextIn
        output_model = _TextOut

        async def execute(self, inputs, context=None):
            return _TextOut(result="")

    with pytest.raises(ValueError, match="node_type"):
        NoType.validate_contract()


def test_validate_contract_rejects_non_pydantic_io(BaseNode):
    """输入/输出模型不是 Pydantic 模型时必须抛出 ``TypeError``。"""

    class BadIO(BaseNode):  # type: ignore[misc,valid-type]
        node_type = "bad_io"
        description = "IO 模型非法"
        input_model = dict  # type: ignore[assignment]
        output_model = _TextOut

        async def execute(self, inputs, context=None):
            return _TextOut(result="")

    with pytest.raises(TypeError, match="input_model"):
        BadIO.validate_contract()


# ----------------------------------------------------------------------
# Schema 导出
# ----------------------------------------------------------------------
def test_get_schema_contains_all_keys(BaseNode):
    """``get_schema()`` 必须导出前端所需的全部字段。"""
    Echo = _make_echo(BaseNode)
    schema = Echo.get_schema()

    assert set(schema) >= {
        "node_type",
        "description",
        "version",
        "input",
        "output",
    }
    assert schema["node_type"] == "test_echo"
    assert schema["description"] == "测试用回显节点"


def test_get_schema_includes_field_metadata(BaseNode):
    """Schema 中必须包含字段的类型、描述与必填信息。"""
    Echo = _make_echo(BaseNode)
    schema = Echo.get_schema()

    input_props = schema["input"]["properties"]
    assert input_props["text"]["type"] == "string"
    assert input_props["text"]["description"] == "输入文本"
    assert input_props["times"]["default"] == 1
    assert schema["input"]["required"] == ["text"]


def test_placeholder_schemas_are_valid(contract):
    """占位契约本身必须是合法的 Pydantic 模型。"""
    assert issubclass(contract.InputSchema, BaseModel)
    assert issubclass(contract.OutputSchema, BaseModel)
    # 占位模型不得接受任何额外字段（强类型防御）。
    with pytest.raises(ValidationError):
        contract.InputSchema(unexpected_field=1)


# ----------------------------------------------------------------------
# 共享消息类型
# ----------------------------------------------------------------------
def test_message_accepts_all_roles(Message):
    """``Message`` 必须支持系统/用户/助手/工具四种角色。"""
    for role in ("system", "user", "assistant", "tool"):
        msg = Message(role=role, content="内容")
        assert msg.role == role
        assert msg.content == "内容"


def test_message_rejects_unknown_role(Message):
    """未知角色必须被当场拒绝。"""
    with pytest.raises(ValidationError):
        Message(role="unknown_role", content="内容")


def test_message_rejects_extra_fields(Message):
    """``Message`` 禁止额外字段，落实强类型防御。"""
    with pytest.raises(ValidationError):
        Message(role="user", content="内容", unexpected=1)


def test_message_optional_name_defaults_none(Message):
    """``name`` 字段可选，默认值为 ``None``。"""
    msg = Message(role="user", content="内容")
    assert msg.name is None


# ----------------------------------------------------------------------
# 占位契约
# ----------------------------------------------------------------------
def test_input_schema_can_be_used_as_default_io(contract, BaseNode):
    """无需输入的节点可直接使用占位契约，保持契约完整性。"""

    class NoInput(BaseNode):  # type: ignore[misc,valid-type]
        node_type = "no_input"
        description = "无需输入"
        output_model = _TextOut

        async def execute(self, inputs, context=None):
            return _TextOut(result="固定输出")

    NoInput.validate_contract()
    schema = NoInput.get_schema()
    assert schema["input"]["properties"] == {}


def test_repr_contains_node_type(BaseNode):
    """``repr`` 必须包含节点类型，便于引擎日志追踪。"""
    Echo = _make_echo(BaseNode)
    text = repr(Echo())
    assert "test_echo" in text
