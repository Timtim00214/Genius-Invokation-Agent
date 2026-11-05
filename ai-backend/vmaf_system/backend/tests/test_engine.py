# -*- coding: utf-8 -*-
"""
图引擎（``engine.py``）测试。

覆盖：端到端链路执行、失败传播、严格模式、输出校验双保险、
Context Bus 隔离性、上游值注入。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

pytestmark = pytest.mark.engine

#: 用于 read_file 测试的样本文件（backend/core/blueprint.py）
SAMPLE_FILE = Path(__file__).resolve().parents[1] / "core" / "blueprint.py"


def _read_node(nid: str, path: str) -> Dict[str, Any]:
    """构造 read_file 节点。"""
    return {"id": nid, "type": "read_file", "params": {"path": path}}


def _regex_node(nid: str, **params: Any) -> Dict[str, Any]:
    """构造 regex_extract 节点。"""
    merged = {"pattern": "x"}
    merged.update(params)
    merged.setdefault("text", "placeholder")
    return {"id": nid, "type": "regex_extract", "params": merged}


# ----------------------------------------------------------------------
# 端到端执行
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_end_to_end_chain(engine, make_blueprint):
    """完整链路应成功执行，且上游值经连线注入下游。"""
    bp = make_blueprint(
        nodes=[
            _read_node("read", str(SAMPLE_FILE)),
            _regex_node("rg", pattern=r"^class (\w+)", flags=["MULTILINE"], text=None),
            {"id": "chat", "type": "llm_chat",
             "params": {"messages": [{"role": "user", "content": "总结"}]}},
        ],
        edges=[
            {"id": "e1", "source": "read", "source_handle": "content",
             "target": "rg", "target_handle": "text"},
            {"id": "e2", "source": "rg", "source_handle": "count",
             "target": "chat", "target_handle": "max_tokens"},
        ],
    )
    report = await engine.run(bp)

    assert report.ok, report.errors
    assert len(report.results) == 3

    read_out = report.output_of("read")
    rg_out = report.output_of("rg")
    chat_out = report.output_of("chat")

    assert read_out.line_count > 0
    assert rg_out.count > 0
    assert chat_out.dry_run, "未配置 api_key 时应进入 dry_run 模式"
    # 关键：rg.count 必须经连线注入到 chat.max_tokens
    assert rg_out.count == report.results[2].upstream_inputs.get("max_tokens")


@pytest.mark.asyncio
async def test_invalid_plan_aborts_execution(engine, make_blueprint):
    """计划无效时一个节点都不能执行（拒绝执行，及时失败）。"""
    bp = make_blueprint(
        nodes=[_regex_node("a"), _regex_node("b", text=None)],
        edges=[
            {"id": "e", "source": "a", "source_handle": "matches",
             "target": "b", "target_handle": "text"},
        ],
    )
    report = await engine.run(bp)

    assert report.aborted
    assert not report.ok
    assert len(report.results) == 0, "非法蓝图不得执行任何节点"
    assert report.abort_reason is not None


# ----------------------------------------------------------------------
# 失败传播
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failure_skips_downstream(engine, make_blueprint):
    """节点失败时，其下游应被跳过，无关分支继续执行。"""
    bp = make_blueprint(
        nodes=[
            _read_node("bad", "不存在的路径/xyz.txt"),
            _regex_node("down", text=None),
            _regex_node("other", text="hello world"),
        ],
        edges=[
            {"id": "e1", "source": "bad", "source_handle": "content",
             "target": "down", "target_handle": "text"},
        ],
    )
    report = await engine.run(bp)

    assert not report.ok
    assert report.output_of("down") is None, "失败节点的下游不应产出输出"
    assert report.output_of("other") is not None, "无关分支应继续执行"


@pytest.mark.asyncio
async def test_skipped_node_not_executed_twice(engine, make_blueprint):
    """回归测试：被跳过的节点不得再进入主循环被执行第二次。

    跳过传播是递归的，主循环是线性的，若不去重会产生
    "已跳过"与"失败"两条重复记录。
    """
    bp = make_blueprint(
        nodes=[
            _read_node("bad", "不存在的路径/xyz.txt"),
            _regex_node("down", text=None),
        ],
        edges=[
            {"id": "e1", "source": "bad", "source_handle": "content",
             "target": "down", "target_handle": "text"},
        ],
    )
    report = await engine.run(bp)

    down_results = [r for r in report.results if r.node_id == "down"]
    assert len(down_results) == 1, f"down 节点被重复登记：{down_results}"
    assert down_results[0].skipped


@pytest.mark.asyncio
async def test_strict_mode_stops_early(strict_engine, make_blueprint):
    """严格模式下首个节点失败即中止，不再执行后续无关分支。"""
    bp = make_blueprint(
        nodes=[
            _read_node("bad", "不存在的路径/xyz.txt"),
            _regex_node("down", text=None),
            _regex_node("other", text="hello"),
        ],
        edges=[
            {"id": "e1", "source": "bad", "source_handle": "content",
             "target": "down", "target_handle": "text"},
        ],
    )
    report = await strict_engine.run(bp)

    assert not report.ok
    assert len(report.results) < 3, "严格模式应提前中止"


# ----------------------------------------------------------------------
# 输出校验双保险
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_outputs_are_validated(engine, make_blueprint):
    """引擎必须对每个节点的输出做 output_model 校验。

    即便插件已返回 output_model 实例，引擎仍再校验一次——
    插件是外部脚本，不可完全信任。
    """
    bp = make_blueprint(nodes=[_regex_node("a", pattern="a", text="aaa")], edges=[])
    report = await engine.run(bp)

    assert report.ok
    assert report.output_of("a").count == 3


# ----------------------------------------------------------------------
# Context Bus 隔离性
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_runs_are_isolated(engine, make_blueprint):
    """两次执行互不干扰，各自拥有独立的 run_id。"""
    bp = make_blueprint(nodes=[_regex_node("a", pattern="a", text="aaa")], edges=[])

    first = await engine.run(bp, run_id="run-A")
    second = await engine.run(bp, run_id="run-B")

    assert first.run_id != second.run_id
    assert first.ok and second.ok


@pytest.mark.asyncio
async def test_run_id_auto_generated(engine, make_blueprint):
    """未指定 run_id 时应自动生成。"""
    bp = make_blueprint(nodes=[_regex_node("a", pattern="a", text="aaa")], edges=[])
    report = await engine.run(bp)
    assert report.run_id.startswith("run-")


@pytest.mark.asyncio
async def test_shared_data_injected(engine, make_blueprint):
    """共享数据应能被注入总线。"""
    bp = make_blueprint(nodes=[_regex_node("a", pattern="a", text="aaa")], edges=[])
    report = await engine.run(bp, shared={"tenant": "acme"})
    assert report.ok


# ----------------------------------------------------------------------
# 分叉与汇聚
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_diamond_fork_and_join(engine, make_blueprint):
    """菱形图应正确执行：一个源分叉到两个分支，再汇聚到汇点。"""
    bp = make_blueprint(
        nodes=[
            _read_node("src", str(SAMPLE_FILE)),
            _regex_node("l", pattern="class", text=None),
            _regex_node("r", pattern="def", text=None),
            _regex_node("sink", pattern="self"),
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
    report = await engine.run(bp)

    assert report.ok, report.errors
    sink_result = next(r for r in report.results if r.node_id == "sink")
    # 上游 l.count 必须注入到 sink.limit
    assert sink_result.upstream_inputs.get("limit") == report.output_of("l").count


# ----------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_report_summary_readable(engine, make_blueprint):
    """报告摘要应包含成功数与耗时，便于日志排查。"""
    bp = make_blueprint(nodes=[_regex_node("a", pattern="a", text="aaa")], edges=[])
    report = await engine.run(bp)
    summary = report.summary()
    assert "节点成功" in summary and "耗时" in summary


@pytest.mark.asyncio
async def test_engine_never_raises(engine, make_blueprint):
    """引擎不得向外抛出异常：所有节点级错误都应汇总进报告。"""
    bp = make_blueprint(
        nodes=[_read_node("bad", "不存在的路径/xyz.txt")], edges=[]
    )
    # 若本调用抛出任何异常，测试即失败
    report = await engine.run(bp)
    assert not report.ok
    assert report.errors
