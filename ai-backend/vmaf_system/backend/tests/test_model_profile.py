# -*- coding: utf-8 -*-
"""
模型参数档案节点（``tools/model_profile.py``）测试。

覆盖：输出与 ``llm_chat`` 输入契约的类型对齐（连线预检的前提）、
密钥打码、ready 标记、字段透传、可选字段 None 语义、坏配置拦截，
以及 profile -> llm_chat 全连线的端到端执行（dry_run）。
"""

from __future__ import annotations

from typing import Any, Dict, get_args, get_origin, get_type_hints

import pytest
from pydantic import BaseModel, ValidationError

pytestmark = pytest.mark.plugin


# ======================================================================
# 契约对齐：profile 输出字段必须与 llm_chat 输入字段同名同型
# ======================================================================
def test_profile_output_fields_align_with_chat_input(scanner):
    """每条 profile -> chat 连线都必须通过类型预检（同名且同型）。

    这是本节点存在的意义：若某字段名与 llm_chat 输入不一致，或类型
    不同（如 Optional[str] vs str），连线在解析期就会被拒绝。
    """
    profile_out = scanner.get_node_class("model_profile").output_model
    chat_in = scanner.get_node_class("llm_chat").input_model

    chat_fields = set(chat_in.model_fields) - {"messages"}
    profile_fields = set(profile_out.model_fields) - {"profile_name", "mask", "ready"}

    assert profile_fields == chat_fields, (
        f"输出字段与 llm_chat 输入不对齐：多 {profile_fields - chat_fields}，"
        f"少 {chat_fields - profile_fields}"
    )

    # 逐字段比较类型注解（Pydantic 解析后的最终注解）。
    for name in profile_fields:
        p_ann = profile_out.model_fields[name].annotation
        c_ann = chat_in.model_fields[name].annotation
        assert p_ann == c_ann, (
            f"字段 {name} 类型不对齐：profile 输出 {p_ann!r} vs "
            f"chat 输入 {c_ann!r}"
        )


def test_profile_to_chat_edges_pass_type_precheck(
    scanner, parser_module, blueprint_module
):
    """全字段连线的蓝图必须通过解析器类型预检，零不兼容。"""
    registry = {t: r.node_class for t, r in scanner.get_registry().items()}
    parser = parser_module.BlueprintParser(registry)

    links = [
        ("api_base", "api_base"),
        ("api_key", "api_key"),
        ("model", "model"),
        ("system_prompt", "system_prompt"),
        ("temperature", "temperature"),
        ("max_tokens", "max_tokens"),
        ("timeout_seconds", "timeout_seconds"),
        ("extra_body", "extra_body"),
        ("extra_headers", "extra_headers"),
        ("drop_params", "drop_params"),
    ]
    blueprint = blueprint_module.Blueprint(
        nodes=[
            blueprint_module.BlueprintNode(
                id="prof", type="model_profile", params={}
            ),
            blueprint_module.BlueprintNode(
                id="chat", type="llm_chat", params={"messages": [
                    {"role": "user", "content": "hi"}
                ]}
            ),
        ],
        edges=[
            blueprint_module.BlueprintEdge(
                id=f"e_{s}", source="prof", source_handle=s,
                target="chat", target_handle=t,
            )
            for s, t in links
        ],
    )
    plan = parser.parse(blueprint)
    incompatible = [
        i for i in plan.issues
        if i.code == "TYPE_MISMATCH" or "不兼容" in i.message
    ]
    assert not incompatible, f"类型预检拒绝了连线: {[str(i) for i in incompatible]}"


# ======================================================================
# 行为：打码 / ready / 透传
# ======================================================================
@pytest.mark.asyncio
async def test_profile_masks_key_and_sets_ready(scanner):
    """密钥应被打码展示，ready 反映凭据是否配置。"""
    node = scanner.instantiate("model_profile")
    model = scanner.get_node_class("model_profile").input_model

    out = await node.execute(model(api_key="sk-abcdefg1234567890"))
    assert out.mask == "sk-***7890"
    assert out.mask != "sk-abcdefg1234567890"
    assert out.ready is True
    assert out.api_key == "sk-abcdefg1234567890", "api_key 本体必须完整透传"

    empty = await node.execute(model())
    assert empty.mask == "(未配置)"
    assert empty.ready is False
    assert empty.api_key is None


@pytest.mark.asyncio
async def test_profile_short_key_fully_masked(scanner):
    """过短的密钥必须整体打码，不能暴露可猜测的片段。"""
    node = scanner.instantiate("model_profile")
    model = scanner.get_node_class("model_profile").input_model

    out = await node.execute(model(api_key="abc123"))
    assert out.mask == "***"


@pytest.mark.asyncio
async def test_profile_passthrough_fields(scanner):
    """逃生舱字段（extra_body / extra_headers / drop_params）应原样透传。"""
    node = scanner.instantiate("model_profile")
    model = scanner.get_node_class("model_profile").input_model

    out = await node.execute(model(
        profile_name="relay-x",
        api_base="https://relay.example.com/v1",
        model="vendor-pro",
        extra_body={"reasoning_effort": "high"},
        extra_headers={"X-Title": "vmaf"},
        drop_params=["temperature"],
    ))
    assert out.profile_name == "relay-x"
    assert out.extra_body == {"reasoning_effort": "high"}
    assert out.extra_headers == {"X-Title": "vmaf"}
    assert out.drop_params == ["temperature"]
    assert out.temperature is None, "留空的可选字段应保持 None（不下发语义）"


@pytest.mark.asyncio
async def test_profile_rejects_bad_headers(scanner):
    """空名称或带换行符的请求头应在档案节点上就地报错。"""
    node = scanner.instantiate("model_profile")
    model = scanner.get_node_class("model_profile").input_model

    with pytest.raises(ValidationError, match="不能为空"):
        await node.execute(model(extra_headers={"  ": "v"}))

    with pytest.raises(ValidationError, match="换行"):
        await node.execute(model(extra_headers={"X-A": "v1\nv2"}))


# ======================================================================
# 端到端：profile -> llm_chat 连线执行
# ======================================================================
@pytest.mark.asyncio
async def test_profile_feeds_chat_end_to_end_dry_run(
    scanner, engine_module, make_blueprint, blueprint_module
):
    """profile 全连线喂给 llm_chat：无密钥时应进入 dry_run 且使用档案参数。"""
    registry = {t: r.node_class for t, r in scanner.get_registry().items()}
    engine = engine_module.GraphEngine(registry)

    blueprint = make_blueprint(
        nodes=[
            {"id": "prof", "type": "model_profile", "params": {
                "profile_name": "e2e",
                "api_base": "https://dry-run.example.com/v1",
                "model": "probe-model",
                "temperature": 0.5,
            }},
            {"id": "chat", "type": "llm_chat", "params": {
                "messages": [{"role": "user", "content": "你好"}]
            }},
        ],
        edges=[
            {"id": "e_base", "source": "prof", "source_handle": "api_base",
             "target": "chat", "target_handle": "api_base"},
            {"id": "e_model", "source": "prof", "source_handle": "model",
             "target": "chat", "target_handle": "model"},
            {"id": "e_temp", "source": "prof", "source_handle": "temperature",
             "target": "chat", "target_handle": "temperature"},
        ],
    )

    report = await engine.run(blueprint)
    assert report.ok, report.errors
    chat_result = next(r for r in report.results if r.node_id == "chat")
    output = chat_result.output
    assert output is not None
    assert output.dry_run is True, "未配置密钥时必须走演练模式"
    assert output.model == "probe-model", "模型名应来自档案节点而非默认值"
    assert "api_base" not in (output.reply or "") or "probe-model" in output.reply


@pytest.mark.asyncio
async def test_profile_without_connection_chat_uses_own_defaults(
    scanner, engine_module, make_blueprint
):
    """未连线时 llm_chat 行为不变（回归：新节点不得影响既有路径）。"""
    registry = {t: r.node_class for t, r in scanner.get_registry().items()}
    engine = engine_module.GraphEngine(registry)

    blueprint = make_blueprint(
        nodes=[
            {"id": "chat", "type": "llm_chat", "params": {
                "messages": [{"role": "user", "content": "hi"}]
            }},
        ],
        edges=[],
    )

    report = await engine.run(blueprint)
    assert report.ok, report.errors
    chat_result = next(r for r in report.results if r.node_id == "chat")
    assert chat_result.output.dry_run is True
    assert chat_result.output.model == "gpt-4o-mini"
