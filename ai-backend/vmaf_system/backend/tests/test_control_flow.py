# -*- coding: utf-8 -*-
"""
控制流测试：条件分支（branch）与循环（loop_start / loop_end）。

同时校验"每个模块的入参出参都必须有说明"这一硬性要求——
这是用户明确提出的可读性需求，用测试锁住比靠自觉可靠。
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

pytestmark = pytest.mark.engine


def _regex(nid: str, **params: Any) -> Dict[str, Any]:
    """构造一个 regex_extract 节点。"""
    merged = {"pattern": "a", "text": "aaa"}
    merged.update(params)
    return {"id": nid, "type": "regex_extract", "params": merged}


# ======================================================================
# 条件分支
# ======================================================================
@pytest.mark.asyncio
async def test_branch_takes_true_path(engine, make_blueprint):
    """条件成立时应执行 on_true 分支，跳过 on_false 分支。"""
    bp = make_blueprint(
        nodes=[
            {"id": "b", "type": "branch",
             "params": {"value": 8, "expression": "value > 5"}},
            _regex("t"),
            _regex("f"),
        ],
        edges=[
            {"id": "e1", "source": "b", "source_handle": "on_true",
             "target": "t", "target_handle": "limit"},
            {"id": "e2", "source": "b", "source_handle": "on_false",
             "target": "f", "target_handle": "limit"},
        ],
    )
    report = await engine.run(bp)

    assert report.ok, report.errors
    assert report.output_of("t") is not None, "true 分支应执行"
    assert report.output_of("f") is None, "false 分支应被跳过"


@pytest.mark.asyncio
async def test_branch_takes_false_path(engine, make_blueprint):
    """条件不成立时应执行 on_false 分支。"""
    bp = make_blueprint(
        nodes=[
            {"id": "b", "type": "branch",
             "params": {"value": 2, "expression": "value > 5"}},
            _regex("t"),
            _regex("f"),
        ],
        edges=[
            {"id": "e1", "source": "b", "source_handle": "on_true",
             "target": "t", "target_handle": "limit"},
            {"id": "e2", "source": "b", "source_handle": "on_false",
             "target": "f", "target_handle": "limit"},
        ],
    )
    report = await engine.run(bp)

    assert report.ok, report.errors
    assert report.output_of("f") is not None
    assert report.output_of("t") is None


@pytest.mark.asyncio
async def test_branch_explicit_condition(engine, make_blueprint):
    """显式给出 condition 时应优先于 expression。"""
    bp = make_blueprint(
        nodes=[
            {"id": "b", "type": "branch",
             "params": {"value": 100, "expression": "value > 5", "condition": False}},
            _regex("t"),
            _regex("f"),
        ],
        edges=[
            {"id": "e1", "source": "b", "source_handle": "on_true",
             "target": "t", "target_handle": "limit"},
            {"id": "e2", "source": "b", "source_handle": "on_false",
             "target": "f", "target_handle": "limit"},
        ],
    )
    report = await engine.run(bp)
    assert report.output_of("f") is not None
    assert report.output_of("t") is None


@pytest.mark.asyncio
async def test_branch_merges_back(engine, make_blueprint):
    """两条分支汇聚到同一节点时，该节点只要有一条活跃入边就应执行。"""
    bp = make_blueprint(
        nodes=[
            {"id": "b", "type": "branch",
             "params": {"value": 8, "expression": "value > 5"}},
            _regex("t"),
            _regex("f"),
            _regex("join", limit=10),
        ],
        edges=[
            {"id": "e1", "source": "b", "source_handle": "on_true",
             "target": "t", "target_handle": "limit"},
            {"id": "e2", "source": "b", "source_handle": "on_false",
             "target": "f", "target_handle": "limit"},
            {"id": "e3", "source": "t", "source_handle": "count",
             "target": "join", "target_handle": "limit"},
        ],
    )
    report = await engine.run(bp)
    assert report.ok, report.errors
    assert report.output_of("join") is not None


@pytest.mark.asyncio
async def test_branch_rejects_dangerous_expression(engine, make_blueprint):
    """分支表达式不得执行任意代码。"""
    bp = make_blueprint(
        nodes=[
            {"id": "b", "type": "branch",
             "params": {"value": 1, "expression": "__import__('os').system('echo x')"}},
        ],
        edges=[],
    )
    report = await engine.run(bp)

    assert not report.ok
    assert "禁止的语法" in report.errors[0]


# ======================================================================
# 循环
# ======================================================================
def _loop_blueprint(make_blueprint, **start_params: Any):
    """构造一个最简单的循环蓝图：loop_start -> body -> loop_end。"""
    params = {"iterations": 3}
    params.update(start_params)
    return make_blueprint(
        nodes=[
            {"id": "s", "type": "loop_start", "params": params},
            _regex("body"),
            {"id": "e", "type": "loop_end", "params": {}},
        ],
        edges=[
            {"id": "e1", "source": "s", "source_handle": "value",
             "target": "body", "target_handle": "text"},
            {"id": "e2", "source": "body", "source_handle": "count",
             "target": "e", "target_handle": "value"},
        ],
    )


@pytest.mark.asyncio
async def test_loop_runs_n_iterations(engine, make_blueprint):
    """循环体应被执行 N 次，并在 loop_end 汇总。"""
    report = await engine.run(_loop_blueprint(make_blueprint, iterations=3))

    assert report.ok, report.errors
    end_output = report.output_of("e")
    assert end_output is not None
    assert end_output.iterations_done == 3
    assert len(end_output.results) == 3
    # 每轮都应记录循环体的输出
    assert end_output.results[0]["outputs"]["body"]["count"] == 3


@pytest.mark.asyncio
async def test_loop_driven_by_items(engine, make_blueprint):
    """提供 items 时迭代次数取其长度。"""
    report = await engine.run(
        _loop_blueprint(make_blueprint, items=["x", "y", "z", "w"])
    )
    assert report.output_of("e").iterations_done == 4


@pytest.mark.asyncio
async def test_loop_single_iteration(engine, make_blueprint):
    """iterations=1 时应只执行一次。"""
    report = await engine.run(_loop_blueprint(make_blueprint, iterations=1))
    assert report.output_of("e").iterations_done == 1


@pytest.mark.asyncio
async def test_loop_body_runs_after_loop(engine, make_blueprint):
    """循环结束后的后续节点应正常执行。"""
    bp = make_blueprint(
        nodes=[
            {"id": "s", "type": "loop_start", "params": {"iterations": 2}},
            _regex("body"),
            {"id": "e", "type": "loop_end", "params": {}},
            _regex("after", pattern="b", text="bbb"),
        ],
        edges=[
            {"id": "e1", "source": "s", "source_handle": "value",
             "target": "body", "target_handle": "text"},
            {"id": "e2", "source": "body", "source_handle": "count",
             "target": "e", "target_handle": "value"},
            {"id": "e3", "source": "e", "source_handle": "iterations_done",
             "target": "after", "target_handle": "limit"},
        ],
    )
    report = await engine.run(bp)

    assert report.ok, report.errors
    assert report.output_of("after") is not None


@pytest.mark.asyncio
async def test_loop_without_end_rejected(engine, make_blueprint):
    """缺少 loop_end 时应在执行前被拒绝。"""
    bp = make_blueprint(
        nodes=[
            {"id": "s", "type": "loop_start", "params": {}},
            _regex("body"),
        ],
        edges=[
            {"id": "e1", "source": "s", "source_handle": "value",
             "target": "body", "target_handle": "text"},
        ],
    )
    report = await engine.run(bp)

    assert report.aborted
    assert any("loop_end" in e for e in report.errors)


@pytest.mark.asyncio
async def test_nested_loop_runs_inner_per_outer(engine, make_blueprint):
    """嵌套循环：外层每迭代一轮，内层完整跑一遍。"""
    bp = make_blueprint(
        nodes=[
            {"id": "s1", "type": "loop_start", "params": {"iterations": 2}},
            {"id": "s2", "type": "loop_start", "params": {"iterations": 3}},
            _regex("body"),
            {"id": "e2", "type": "loop_end", "params": {}},
            _regex("after"),
            {"id": "e1", "type": "loop_end", "params": {}},
        ],
        edges=[
            {"id": "a1", "source": "s1", "source_handle": "value",
             "target": "s2", "target_handle": "value"},
            {"id": "a2", "source": "s2", "source_handle": "value",
             "target": "body", "target_handle": "text"},
            {"id": "a3", "source": "body", "source_handle": "count",
             "target": "e2", "target_handle": "value"},
            {"id": "a4", "source": "e2", "source_handle": "iterations_done",
             "target": "after", "target_handle": "limit"},
            {"id": "a5", "source": "after", "source_handle": "count",
             "target": "e1", "target_handle": "value"},
        ],
    )
    report = await engine.run(bp)

    assert report.ok, report.errors
    inner = report.output_of("e2")
    outer = report.output_of("e1")
    assert inner.iterations_done == 3, "内层每次应完整跑 3 轮"
    assert outer.iterations_done == 2, "外层应跑 2 轮（内层结果每轮被重置并重跑）"
    # 外层聚合只记录"外层直接循环体"的节点（嵌套起点 s2 与外层体 after）
    assert all({"s2", "after"} <= set(item["outputs"]) for item in outer.results)


@pytest.mark.asyncio
async def test_nested_loop_plan_structure(parser_module, registry, make_blueprint):
    """解析器应产出父子区间：外层体含内层起点，内层体归内层。"""
    bp = make_blueprint(
        nodes=[
            {"id": "s1", "type": "loop_start", "params": {"iterations": 2}},
            {"id": "s2", "type": "loop_start", "params": {"iterations": 2}},
            _regex("body"),
            {"id": "e2", "type": "loop_end", "params": {}},
            {"id": "e1", "type": "loop_end", "params": {}},
        ],
        edges=[
            {"id": "a1", "source": "s1", "source_handle": "value",
             "target": "s2", "target_handle": "value"},
            {"id": "a2", "source": "s2", "source_handle": "value",
             "target": "body", "target_handle": "text"},
            {"id": "a3", "source": "body", "source_handle": "count",
             "target": "e2", "target_handle": "value"},
            {"id": "a4", "source": "e2", "source_handle": "iterations_done",
             "target": "e1", "target_handle": "value"},
        ],
    )
    plan = parser_module.BlueprintParser(registry).parse(bp)

    assert plan.valid, [str(i) for i in plan.errors]
    outer = plan.loop_regions["s1"]
    inner = plan.loop_regions["s2"]
    assert outer.depth == 0 and inner.depth == 1
    assert inner.parent_start_id == "s1"
    assert outer.children == ["s2"]
    assert outer.body_order == ["s2"], "外层直接体只含嵌套起点，不含内层体"
    assert inner.body_order == ["body"]
    assert plan.order == ["s1"], "所有循环体与终点都已从主序列摘出"


@pytest.mark.asyncio
async def test_nested_loop_exposes_indexes_stack(
    engine_module, registry, make_blueprint, contract
):
    """嵌套时 loop.indexes / loop.depth 必须反映完整栈，不被内层覆盖丢弃。"""
    from typing import List, Optional

    from pydantic import BaseModel, Field

    class ProbeInput(BaseModel):
        text: str = Field("", description="占位文本")

    class ProbeOutput(BaseModel):
        indexes: List[int] = Field(default_factory=list, description="loop.indexes")
        depth: int = Field(0, description="loop.depth")
        index: Optional[int] = Field(None, description="loop.index")

    class LoopProbeNode(contract.BaseNode):
        node_type = "loop_probe"
        description = "测试探针：回显 loop.* 变量"
        input_model = ProbeInput
        output_model = ProbeOutput

        async def execute(self, inputs, context=None):
            shared = (context or {}).get("shared", {}) or {}
            return ProbeOutput(
                indexes=list(shared.get("loop.indexes", [])),
                depth=int(shared.get("loop.depth", 0) or 0),
                index=shared.get("loop.index"),
            )

    merged_registry = dict(registry)
    merged_registry["loop_probe"] = LoopProbeNode
    engine = engine_module.GraphEngine(merged_registry)

    bp = make_blueprint(
        nodes=[
            {"id": "s1", "type": "loop_start", "params": {"iterations": 2}},
            {"id": "s2", "type": "loop_start", "params": {"iterations": 3}},
            {"id": "probe", "type": "loop_probe", "params": {}},
            {"id": "e2", "type": "loop_end", "params": {}},
            {"id": "e1", "type": "loop_end", "params": {}},
        ],
        edges=[
            {"id": "a1", "source": "s1", "source_handle": "value",
             "target": "s2", "target_handle": "value"},
            {"id": "a2", "source": "s2", "source_handle": "value",
             "target": "probe", "target_handle": "text"},
            {"id": "a3", "source": "probe", "source_handle": "index",
             "target": "e2", "target_handle": "value"},
            {"id": "a4", "source": "e2", "source_handle": "iterations_done",
             "target": "e1", "target_handle": "value"},
        ],
    )
    report = await engine.run(bp)

    assert report.ok, report.errors
    probe = report.output_of("probe")
    # 最后一轮 = 外层第 2 轮（index 1）、内层第 3 轮（index 2）
    assert probe.indexes == [1, 2], f"应保留完整下标栈，实际 {probe.indexes}"
    assert probe.depth == 2
    assert probe.index == 2, "loop.index 指向最内层"


@pytest.mark.asyncio
async def test_loop_end_without_start_rejected(engine, make_blueprint):
    """孤立的 loop_end 必须报错，而不是被当成普通节点执行。"""
    bp = make_blueprint(
        nodes=[_regex("body"), {"id": "e", "type": "loop_end", "params": {}}],
        edges=[
            {"id": "e1", "source": "body", "source_handle": "count",
             "target": "e", "target_handle": "value"},
        ],
    )
    report = await engine.run(bp)

    assert report.aborted
    assert any("loop_start" in e for e in report.errors)


# ======================================================================
# 模块文档
# ======================================================================
def test_every_node_exposes_docs(scanner):
    """每个节点都必须导出说明书。"""
    for schema in scanner.get_all_schemas():
        docs = schema.get("docs")
        assert docs, f"{schema['node_type']} 缺少 docs"
        assert docs["node_type"] == schema["node_type"]
        assert docs["description"]
        assert "inputs" in docs and "outputs" in docs


def test_every_input_field_documented(scanner):
    """每个节点的每个输入字段都必须有说明文本。

    用户明确要求"每个模块入参出参都应该写清楚备注"，
    这里用测试强制约束，避免新增插件时漏写。
    """
    missing = []
    for schema in scanner.get_all_schemas():
        for field in schema["docs"]["inputs"]:
            if not field["description"].strip():
                missing.append(f"{schema['node_type']}.{field['name']}")
    assert not missing, f"以下输入字段缺少说明: {missing}"


def test_every_output_field_documented(scanner):
    """每个节点的每个输出字段都必须有说明文本。"""
    missing = []
    for schema in scanner.get_all_schemas():
        for field in schema["docs"]["outputs"]:
            if not field["description"].strip():
                missing.append(f"{schema['node_type']}.{field['name']}")
    assert not missing, f"以下输出字段缺少说明: {missing}"


def test_docs_expose_type_and_required(scanner):
    """说明书中必须给出类型与是否必填，否则使用者无法判断该填什么。"""
    for schema in scanner.get_all_schemas():
        for field in schema["docs"]["inputs"]:
            assert field["type"], f"{schema['node_type']}.{field['name']} 缺少类型"
            assert isinstance(field["required"], bool)


def test_control_flow_nodes_flagged(scanner):
    """控制流节点必须带 control_flow 标记，前端据此特殊渲染。"""
    expected = {
        "branch": "branch",
        "loop_start": "loop_start",
        "loop_end": "loop_end",
    }
    for node_type, flow in expected.items():
        schema = next(
            s for s in scanner.get_all_schemas() if s["node_type"] == node_type
        )
        assert schema["control_flow"] == flow


def test_data_nodes_have_no_control_flow(scanner):
    """普通数据节点不应带控制流标记。"""
    for schema in scanner.get_all_schemas():
        if schema["node_type"] in ("llm_chat", "read_file", "regex_extract"):
            assert schema["control_flow"] is None


def test_control_flow_docs_include_usage(scanner):
    """控制流节点的说明书应包含使用提示。"""
    for node_type in ("branch", "loop_start", "loop_end"):
        schema = next(
            s for s in scanner.get_all_schemas() if s["node_type"] == node_type
        )
        assert schema["docs"]["usage"], f"{node_type} 缺少使用提示"
