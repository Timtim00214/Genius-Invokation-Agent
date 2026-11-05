# -*- coding: utf-8 -*-
"""
蓝图解析器（``parser.py``）测试。

覆盖：环检测、确定性拓扑排序、连线类型预检、必填检查、端点校验，
以及蓝图自身的 Pydantic 结构校验。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.parser


def _read_node(nid: str = "n1", path: str = "any.txt") -> Dict[str, Any]:
    """构造一个 read_file 节点。"""
    return {"id": nid, "type": "read_file", "params": {"path": path}}


def _regex_node(nid: str, **params: Any) -> Dict[str, Any]:
    """构造一个 regex_extract 节点，默认补齐必填的 text。"""
    merged = {"pattern": "x"}
    merged.update(params)
    merged.setdefault("text", "placeholder")
    return {"id": nid, "type": "regex_extract", "params": merged}


# ----------------------------------------------------------------------
# 蓝图自身结构校验
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "nodes,edges,keyword",
    [
        (
            [{"id": "a", "type": "read_file"}, {"id": "a", "type": "read_file"}],
            [],
            "重复的节点 id",
        ),
        (
            [{"id": "a", "type": "read_file"}],
            [
                {
                    "id": "e",
                    "source": "a",
                    "source_handle": "content",
                    "target": "ghost",
                    "target_handle": "text",
                }
            ],
            "目标节点不存在",
        ),
        (
            [{"id": "a", "type": "read_file"}],
            [
                {
                    "id": "e",
                    "source": "a",
                    "source_handle": "content",
                    "target": "a",
                    "target_handle": "path",
                }
            ],
            "自环",
        ),
        (
            [{"id": "a", "type": "read_file"}, {"id": "b", "type": "regex_extract"}],
            [
                {
                    "id": "e",
                    "source": "a",
                    "source_handle": "content",
                    "target": "b",
                    "target_handle": "text",
                },
                {
                    "id": "e",
                    "source": "a",
                    "source_handle": "content",
                    "target": "b",
                    "target_handle": "text",
                },
            ],
            "重复的连线 id",
        ),
    ],
    ids=["重复节点 id", "悬空连线", "自环", "重复连线 id"],
)
def test_blueprint_rejects_invalid_structure(
    blueprint_module, nodes, edges, keyword
):
    """蓝图必须在解析期拦截结构性问题。"""
    with pytest.raises(ValidationError) as exc:
        blueprint_module.Blueprint(nodes=nodes, edges=edges)

    messages = [str(e["msg"]) for e in exc.value.errors()]
    assert any(keyword in m for m in messages), f"期望包含「{keyword}」，实际：{messages}"


def test_blueprint_accepts_valid_structure(blueprint_module):
    """合法蓝图应通过校验。"""
    bp = blueprint_module.Blueprint(
        nodes=[_read_node()],
        edges=[],
    )
    assert len(bp.nodes) == 1
    assert bp.get_node("n1").type == "read_file"


# ----------------------------------------------------------------------
# 解析器：正常路径
# ----------------------------------------------------------------------
def test_parse_valid_chain(parser_module, registry, make_blueprint):
    """正常链路应产出有效计划与正确执行顺序。"""
    parser = parser_module.BlueprintParser(registry)
    bp = make_blueprint(
        nodes=[
            _read_node("n1"),
            _regex_node("n2", text=None),
            {"id": "n3", "type": "llm_chat",
             "params": {"messages": [{"role": "user", "content": "hi"}]}},
        ],
        edges=[
            {"id": "e1", "source": "n1", "source_handle": "content",
             "target": "n2", "target_handle": "text"},
            {"id": "e2", "source": "n2", "source_handle": "count",
             "target": "n3", "target_handle": "max_tokens"},
        ],
    )
    plan = parser.parse(bp)
    assert plan.valid, [str(i) for i in plan.errors]
    assert plan.order == ["n1", "n2", "n3"]
    assert plan.entry_nodes == ["n1"]
    assert plan.exit_nodes == ["n3"]


def test_parse_marks_entry_and_exit_nodes(parser_module, registry, make_blueprint):
    """入口（无入边）与出口（无出边）节点应被正确标记。"""
    parser = parser_module.BlueprintParser(registry)
    bp = make_blueprint(
        nodes=[_read_node("a"), _regex_node("b"), _regex_node("c")],
        edges=[
            {"id": "e1", "source": "a", "source_handle": "content",
             "target": "b", "target_handle": "text"},
            {"id": "e2", "source": "a", "source_handle": "content",
             "target": "c", "target_handle": "text"},
        ],
    )
    plan = parser.parse(bp)
    assert plan.entry_nodes == ["a"]
    assert sorted(plan.exit_nodes) == ["b", "c"]


# ----------------------------------------------------------------------
# 环检测
# ----------------------------------------------------------------------
def test_cycle_detected(parser_module, registry, make_blueprint):
    """有环蓝图必须被拒绝，且不产出执行顺序。"""
    parser = parser_module.BlueprintParser(registry)
    bp = make_blueprint(
        nodes=[
            _read_node("a"),
            _regex_node("b"),
            {"id": "c", "type": "llm_chat",
             "params": {"messages": [{"role": "user", "content": "hi"}]}},
        ],
        edges=[
            {"id": "e1", "source": "a", "source_handle": "content",
             "target": "b", "target_handle": "text"},
            {"id": "e2", "source": "b", "source_handle": "count",
             "target": "c", "target_handle": "max_tokens"},
            {"id": "e3", "source": "c", "source_handle": "reply",
             "target": "a", "target_handle": "path"},
        ],
    )
    plan = parser.parse(bp)
    assert not plan.valid
    assert any(i.code == "CYCLE" for i in plan.issues)
    assert plan.order == []


def test_diamond_is_not_cycle(parser_module, registry, make_blueprint):
    """菱形图（分叉后汇聚）不是环，应正常解析。"""
    parser = parser_module.BlueprintParser(registry)
    bp = make_blueprint(
        nodes=[
            _read_node("src"),
            _regex_node("l"),
            _regex_node("r"),
            _regex_node("sink"),
        ],
        edges=[
            {"id": "e1", "source": "src", "source_handle": "content",
             "target": "l", "target_handle": "text"},
            {"id": "e2", "source": "src", "source_handle": "content",
             "target": "r", "target_handle": "text"},
            {"id": "e3", "source": "l", "source_handle": "count",
             "target": "sink", "target_handle": "limit"},
        ],
    )
    plan = parser.parse(bp)
    assert plan.valid, [str(i) for i in plan.errors]
    assert plan.order.index("src") < plan.order.index("l")
    assert plan.order.index("l") < plan.order.index("sink")


# ----------------------------------------------------------------------
# 拓扑排序确定性
# ----------------------------------------------------------------------
def test_topological_order_is_deterministic(parser_module, registry, make_blueprint):
    """同一份蓝图多次解析必须得到完全相同的执行顺序。

    这是"单机高稳定性优先"红线的要求：否则调试与复现将无从谈起。
    """
    parser = parser_module.BlueprintParser(registry)
    bp = make_blueprint(
        nodes=[
            _read_node("d"), _regex_node("b"), _regex_node("c"),
            {"id": "a", "type": "llm_chat",
             "params": {"messages": [{"role": "user", "content": "hi"}]}},
        ],
        edges=[
            {"id": "e1", "source": "d", "source_handle": "content",
             "target": "b", "target_handle": "text"},
            {"id": "e2", "source": "d", "source_handle": "content",
             "target": "c", "target_handle": "text"},
            {"id": "e3", "source": "b", "source_handle": "count",
             "target": "a", "target_handle": "max_tokens"},
            {"id": "e4", "source": "c", "source_handle": "count",
             "target": "a", "target_handle": "temperature"},
        ],
    )
    orders = [parser.parse(bp).order for _ in range(5)]
    assert all(o == orders[0] for o in orders), "拓扑排序不确定"
    assert orders[0].index("d") < orders[0].index("b") < orders[0].index("a")


# ----------------------------------------------------------------------
# 连线类型预检
# ----------------------------------------------------------------------
def test_type_mismatch_rejected(parser_module, registry, make_blueprint):
    """类型不兼容的连线必须在解析期被拒绝。

    这是本模块最有价值的能力：让用户在连线时就能看到错误，
    而不是等到运行时崩溃。
    """
    parser = parser_module.BlueprintParser(registry)
    bp = make_blueprint(
        nodes=[_regex_node("a"), _regex_node("b", text=None)],
        edges=[
            {"id": "e", "source": "a", "source_handle": "matches",
             "target": "b", "target_handle": "text"},
        ],
    )
    plan = parser.parse(bp)
    assert not plan.valid
    mismatch = [i for i in plan.issues if i.code == "TYPE_MISMATCH"]
    assert len(mismatch) == 1
    # 错误信息必须说明具体原因，而不是笼统的"类型不匹配"
    assert "容器" in mismatch[0].message


def test_compatible_edge_accepted(parser_module, registry, make_blueprint):
    """类型兼容的连线必须放行。"""
    parser = parser_module.BlueprintParser(registry)
    bp = make_blueprint(
        nodes=[_read_node("a"), _regex_node("b", text=None)],
        edges=[
            {"id": "e", "source": "a", "source_handle": "content",
             "target": "b", "target_handle": "text"},
        ],
    )
    plan = parser.parse(bp)
    assert plan.valid, [str(i) for i in plan.errors]


# ----------------------------------------------------------------------
# 端点与必填校验
# ----------------------------------------------------------------------
def test_unknown_output_field_rejected(parser_module, registry, make_blueprint):
    """源节点不存在的输出字段必须被拒绝。"""
    parser = parser_module.BlueprintParser(registry)
    bp = make_blueprint(
        nodes=[_read_node("a"), _regex_node("b")],
        edges=[
            {"id": "e", "source": "a", "source_handle": "no_such_field",
             "target": "b", "target_handle": "text"},
        ],
    )
    plan = parser.parse(bp)
    assert any(i.code == "UNKNOWN_OUTPUT_FIELD" for i in plan.issues)


def test_unknown_input_field_rejected(parser_module, registry, make_blueprint):
    """目标节点不存在的输入字段必须被拒绝。"""
    parser = parser_module.BlueprintParser(registry)
    bp = make_blueprint(
        nodes=[_read_node("a"), _regex_node("b")],
        edges=[
            {"id": "e", "source": "a", "source_handle": "content",
             "target": "b", "target_handle": "no_such_field"},
        ],
    )
    plan = parser.parse(bp)
    assert any(i.code == "UNKNOWN_INPUT_FIELD" for i in plan.issues)


def test_unknown_node_type_rejected(parser_module, registry, make_blueprint):
    """未注册的节点类型必须被拒绝，并提示可用类型。"""
    parser = parser_module.BlueprintParser(registry)
    bp = make_blueprint(nodes=[{"id": "a", "type": "not_exist", "params": {}}], edges=[])
    plan = parser.parse(bp)
    errors = [i for i in plan.issues if i.code == "UNKNOWN_NODE_TYPE"]
    assert len(errors) == 1
    assert "llm_chat" in errors[0].message, "错误信息应列出已注册类型"


def test_missing_required_input_detected(parser_module, registry, make_blueprint):
    """既无连线也无静态参数的必填输入必须被检出。"""
    parser = parser_module.BlueprintParser(registry)
    bp = make_blueprint(
        nodes=[{"id": "a", "type": "regex_extract", "params": {}}], edges=[]
    )
    plan = parser.parse(bp)
    missing = [i for i in plan.issues if i.code == "MISSING_REQUIRED_INPUT"]
    assert {i.field for i in missing} == {"text", "pattern"}


def test_required_input_satisfied_by_params(parser_module, registry, make_blueprint):
    """由静态参数提供的必填输入不应报错。"""
    parser = parser_module.BlueprintParser(registry)
    bp = make_blueprint(nodes=[_regex_node("a", text="abc")], edges=[])
    plan = parser.parse(bp)
    assert not any(i.code == "MISSING_REQUIRED_INPUT" for i in plan.issues)


def test_required_input_satisfied_by_edge(parser_module, registry, make_blueprint):
    """由连线提供的必填输入不应报错。"""
    parser = parser_module.BlueprintParser(registry)
    bp = make_blueprint(
        nodes=[_read_node("a"), _regex_node("b", text=None)],
        edges=[
            {"id": "e", "source": "a", "source_handle": "content",
             "target": "b", "target_handle": "text"},
        ],
    )
    plan = parser.parse(bp)
    assert not any(i.code == "MISSING_REQUIRED_INPUT" for i in plan.issues)


# ----------------------------------------------------------------------
# 计划对象
# ----------------------------------------------------------------------
def test_plan_summary_contains_counts(parser_module, registry, make_blueprint):
    """计划摘要应包含节点数与问题数，便于日志排查。"""
    parser = parser_module.BlueprintParser(registry)
    bp = make_blueprint(nodes=[_regex_node("a", text="x")], edges=[])
    plan = parser.parse(bp)
    summary = plan.summary()
    assert "节点" in summary and "错误" in summary


def test_empty_blueprint_parses(parser_module, registry, make_blueprint):
    """空蓝图应能安全解析，不抛异常。"""
    parser = parser_module.BlueprintParser(registry)
    plan = parser.parse(make_blueprint(nodes=[], edges=[]))
    assert plan.order == []
    assert plan.valid
