# -*- coding: utf-8 -*-
"""
核心图调度循环与串行执行器。

本模块是"单机高稳定性优先"红线的落点：单线程异步串行执行，
状态全在内存字典流转，时序完全可预测，本地 I/O 绝对安全。

执行流程::

    1. 解析蓝图 -> ExecutionPlan（环检测 / 拓扑排序 / 类型预检已完成）
    2. 计划无效（有 error）-> 直接拒绝执行，不跑任何一个节点
    3. 按拓扑序逐个执行节点：
       a. 收集上游注入值 + 节点静态参数
       b. 用 input_model 校验（连线值优先于静态参数）
       c. 调用节点的 async execute
       d. 用 output_model 校验输出（数据总线安检员）
       e. 存入 Context Bus
    4. 节点失败：记录错误，其所有下游标记为跳过，继续跑无关分支

关于第 3d 点：即使插件内部返回的已是 output_model 实例，引擎仍会
再校验一次。这是刻意的**双保险**——插件是外部脚本，不可完全信任，
输出必须经总线安检才能流入下游。

契约:
    输入 :class:`Blueprint`；输出 :class:`RunReport`。
"""

from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, ValidationError

_CORE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _CORE_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from core.blueprint import Blueprint  # noqa: E402
from core.context import ExecutionContext, NodeResult  # noqa: E402
from core.parser import BlueprintParser, ExecutionPlan  # noqa: E402
from utils.errors import (  # noqa: E402
    ErrorCategory,
    ErrorCode,
    ErrorDetail,
    VmafError,
    format_native_exception,
    issue_to_error_detail,
    to_error_detail,
)

__all__ = ["RunReport", "GraphEngine"]


@dataclass
class RunReport:
    """一次蓝图执行的完整报告。

    Attributes:
        run_id (str): 本次执行标识。
        ok (bool): 整体是否成功（无任何节点失败）。
        results (List[NodeResult]): 全部节点结果（按执行顺序）。
        errors (List[str]): 节点级错误信息汇总。
        plan (Optional[ExecutionPlan]): 本次使用的执行计划。
        total_ms (float): 总耗时（毫秒）。
        aborted (bool): 是否因计划无效而未执行（连一个节点都没跑）。
        abort_reason (Optional[str]): 未执行时的原因。
    """

    run_id: str
    ok: bool = True
    results: List[NodeResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    error_details: List[ErrorDetail] = field(default_factory=list)
    plan: Optional[ExecutionPlan] = None
    total_ms: float = 0.0
    aborted: bool = False
    abort_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """导出为可直接 JSON 序列化的字典（供 API 层回传前端）。

        Returns:
            Dict[str, Any]: 含 run_id / ok / 各节点结果 / 结构化错误的字典。
        """
        return {
            "run_id": self.run_id,
            "ok": self.ok,
            "total_ms": round(self.total_ms, 3),
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
            "errors": list(self.errors),
            "error_details": [d.model_dump(mode="json") for d in self.error_details],
            "results": [
                {
                    "node_id": r.node_id,
                    "node_type": r.node_type,
                    "ok": r.ok,
                    "skipped": r.skipped,
                    "error": r.error,
                    "elapsed_ms": round(r.elapsed_ms, 3),
                    "output": (
                        r.output.model_dump(mode="json")
                        if r.output is not None
                        else None
                    ),
                }
                for r in self.results
            ],
        }

    def summary(self) -> str:
        """生成一行执行摘要。

        Returns:
            str: 形如 ``"run-abc: 成功 3 节点，耗时 45.2ms"``。
        """
        if self.aborted:
            return f"{self.run_id}: 未执行（{self.abort_reason}）"
        done = sum(1 for r in self.results if r.ok)
        return (
            f"{self.run_id}: {done}/{len(self.results)} 节点成功，"
            f"耗时 {self.total_ms:.1f}ms"
            + (f"，{len(self.errors)} 错误" if self.errors else "")
        )

    def output_of(self, node_id: str) -> Optional[BaseModel]:
        """取回指定节点的输出实例。

        Args:
            node_id (str): 节点 UUID。

        Returns:
            Optional[BaseModel]: 输出实例；不存在时返回 None。
        """
        for result in self.results:
            if result.node_id == node_id:
                return result.output
        return None


class GraphEngine:
    """图执行引擎：按拓扑序单线程异步串行执行节点。

    Attributes:
        parser (BlueprintParser): 蓝图解析器。
        strict (bool): 严格模式。为 True 时，任一节点失败立即中止整个执行；
            为 False 时（默认），失败节点的下游被跳过，无关分支继续执行。
    """

    def __init__(
        self,
        registry: Optional[Dict[str, Any]] = None,
        strict: bool = False,
    ) -> None:
        """初始化引擎。

        Args:
            registry (Optional[Dict[str, Any]]): 节点类型 -> 节点类 的映射。
            strict (bool): 是否启用严格模式（见类文档）。
        """
        self.parser = BlueprintParser(registry or {})
        self.strict = strict

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    async def run(
        self,
        blueprint: Blueprint,
        shared: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
    ) -> RunReport:
        """执行一份蓝图。

        Args:
            blueprint (Blueprint): 待执行的蓝图。
            shared (Optional[Dict[str, Any]]): 注入总线的共享数据。
            run_id (Optional[str]): 执行标识；留空时自动生成。

        Returns:
            RunReport: 执行报告。**本方法不抛异常**：所有节点级错误都
                被捕获并汇总进报告，调用方通过 :attr:`RunReport.ok` 判定。
        """
        report = RunReport(run_id=run_id or f"run-{uuid.uuid4().hex[:8]}")
        context = ExecutionContext(run_id=report.run_id)

        if shared:
            for key, value in shared.items():
                context.set_shared(key, value)

        started = time.perf_counter()

        plan = self.parser.parse(blueprint)
        report.plan = plan

        # 计划无效：一个节点都不跑，直接返回。
        if not plan.valid:
            report.ok = False
            report.aborted = True
            report.abort_reason = (
                f"蓝图校验未通过（{len(plan.errors)} 个错误）："
                + "; ".join(i.message for i in plan.errors[:3])
            )
            report.errors = [i.message for i in plan.errors]
            report.error_details = _issues_to_details(plan.issues)
            report.total_ms = (time.perf_counter() - started) * 1000
            return report

        # 分支路由状态：被条件判定排除的边 id 集合。
        inactive: Set[str] = set()

        for node_id in plan.order:
            resolved = plan.resolved_nodes[node_id]

            # 已被上游失败波及的节点不再执行，避免与主循环重复登记结果。
            existing = context.get_result(node_id)
            if existing is not None and existing.skipped:
                continue

            # 条件分支未选中该路径：入边全部失活则跳过。
            if _all_incoming_inactive(resolved, inactive):
                skipped = context.mark_skipped(
                    node_id, resolved.node.type, "条件分支未选中该路径"
                )
                report.results.append(skipped)
                _deactivate_outgoing(resolved, inactive)
                continue

            result = await self._execute_node(node_id, resolved, context)
            report.results.append(result)

            if not result.ok:
                report.ok = False
                report.errors.append(f"[{node_id}] {result.error}")
                report.error_details.append(
                    to_error_detail(
                        result.cause or RuntimeError(result.error or "节点执行失败"),
                        node_id=node_id,
                        node_type=resolved.node.type,
                    )
                )
                _deactivate_outgoing(resolved, inactive)
                self._skip_downstream(node_id, plan, context, report)
                if self.strict:
                    break
                continue

            control_flow = getattr(resolved.node_class, "control_flow", None)

            # 条件分支：只保留命中的那条出边。
            if control_flow == "branch":
                route = getattr(result.output, "route", None)
                for resolved_edge in resolved.outgoing:
                    if route is None or resolved_edge.edge.source_handle != route:
                        inactive.add(resolved_edge.edge.id)

            # 循环：在此处展开执行循环体 N 次。
            elif control_flow == "loop_start":
                region = plan.loop_regions.get(node_id)
                if region is None:
                    report.ok = False
                    report.errors.append(f"[{node_id}] 循环区间解析失败")
                    continue
                loop_ok, loop_records = await self._run_loop(
                    region, plan, context, inactive
                )
                report.results.extend(loop_records)
                if not loop_ok:
                    report.ok = False
                    report.errors.append(f"[{node_id}] 循环体执行失败")
                    if self.strict:
                        break

        report.total_ms = (time.perf_counter() - started) * 1000
        return report

    # ------------------------------------------------------------------
    # 循环执行
    # ------------------------------------------------------------------
    async def _run_loop(
        self,
        region: Any,
        plan: ExecutionPlan,
        context: ExecutionContext,
        inactive: Set[str],
    ) -> Tuple[bool, List[NodeResult]]:
        """展开执行一个循环区间（支持任意层嵌套）。

        每轮迭代都会重置循环体内部分支状态（上一轮的 if 判定不影响下一轮），
        并通过 Context 的 ``loop`` 命名空间暴露当前迭代位置：

            - ``loop.index`` / ``loop.item``：**最内层**当前迭代的下标与元素；
            - ``loop.indexes``：由外到内的下标列表，例如 ``[1, 0]``；
            - ``loop.depth``：当前嵌套深度（最外层为 1）。

        嵌套子循环在嵌入的 ``loop_start`` 节点处递归展开；进入子循环前会
        保存外层的 ``loop.*`` 共享键，返回时恢复，避免内外层互相污染。

        Args:
            region (LoopRegion): 循环区间。
            plan (ExecutionPlan): 执行计划。
            context (ExecutionContext): 数据总线。
            inactive (Set[str]): 全局失活边集合（循环体内的边会按轮重置）。

        Returns:
            Tuple[bool, List[NodeResult]]: ``(是否全部成功, 最后一轮的结果记录)``。
        """
        return await self._run_region(region, plan, context, inactive, [])

    async def _run_region(
        self,
        region: Any,
        plan: ExecutionPlan,
        context: ExecutionContext,
        inactive: Set[str],
        stack: List[Dict[str, Any]],
    ) -> Tuple[bool, List[NodeResult]]:
        """递归执行一个循环区间（``_run_loop`` 的实现体）。

        Args:
            region (LoopRegion): 当前循环区间。
            plan (ExecutionPlan): 执行计划。
            context (ExecutionContext): 数据总线。
            inactive (Set[str]): 失活边集合。
            stack (List[Dict[str, Any]]): 外层循环帧栈（原地压栈/弹栈）。

        Returns:
            Tuple[bool, List[NodeResult]]: ``(是否全部成功, 最后一轮的结果记录)``。
        """
        start_id = region.loop_start_id
        start_result = context.get_result(start_id)
        planned = 1
        items: Optional[List[Any]] = None

        if start_result is not None and start_result.output is not None:
            planned = int(getattr(start_result.output, "iterations_planned", 1) or 1)
        if start_result is not None and start_result.inputs is not None:
            items = getattr(start_result.inputs, "items", None)

        body_ids = set(region.body_order)
        body_edges = {
            e.edge.id
            for nid in body_ids
            for e in plan.resolved_nodes[nid].outgoing
            if e.edge.target in body_ids
        }

        # 保存外层的聚合键：子循环返回后要恢复，不能让外层误读子循环的结果。
        saved = {
            key: context.get_shared(key)
            for key in ("loop.results", "loop.stopped_early")
        }

        collected: List[Dict[str, Any]] = []
        last_records: List[NodeResult] = []
        stopped_early = False

        for index in range(planned):
            # 重置循环体内部的分支状态：每轮重新判定。
            inactive.difference_update(body_edges)
            item = items[index] if items is not None and index < len(items) else None
            stack.append({"start": start_id, "index": index, "item": item})
            _publish_loop_frame(context, stack)

            iteration_records: List[NodeResult] = []
            iteration_outputs: Dict[str, Any] = {}

            for node_id in region.body_order:
                resolved = plan.resolved_nodes[node_id]

                if _all_incoming_inactive(resolved, inactive):
                    skipped = context.mark_skipped(
                        node_id, resolved.node.type, "条件分支未选中该路径"
                    )
                    iteration_records.append(skipped)
                    _deactivate_outgoing(resolved, inactive)
                    continue

                result = await self._execute_node(node_id, resolved, context)
                iteration_records.append(result)

                if not result.ok:
                    stopped_early = True
                    break

                iteration_outputs[node_id] = (
                    result.output.model_dump(mode="json")
                    if result.output is not None
                    else None
                )

                control = getattr(resolved.node_class, "control_flow", None)

                if control == "branch":
                    route = getattr(result.output, "route", None)
                    for resolved_edge in resolved.outgoing:
                        if route is None or resolved_edge.edge.source_handle != route:
                            inactive.add(resolved_edge.edge.id)

                # 嵌套循环：先执行 loop_start 自身，再递归展开它的区间。
                elif control == "loop_start":
                    child = plan.loop_regions.get(node_id)
                    if child is not None:
                        child_ok, child_records = await self._run_region(
                            child, plan, context, inactive, stack
                        )
                        iteration_records.extend(child_records)
                        if not child_ok:
                            stopped_early = True
                            break

            collected.append({"index": index, "outputs": iteration_outputs})
            last_records = iteration_records

            stack.pop()
            _publish_loop_frame(context, stack)

            if stopped_early:
                break

        context.set_shared("loop.results", collected)
        context.set_shared("loop.stopped_early", stopped_early)

        # 执行循环终点，产出聚合结果。
        if region.loop_end_id in plan.resolved_nodes:
            end_resolved = plan.resolved_nodes[region.loop_end_id]
            end_result = await self._execute_node(
                region.loop_end_id, end_resolved, context
            )
            last_records = list(last_records) + [end_result]
            if not end_result.ok:
                stopped_early = True

        # 恢复外层循环的聚合键（最外层恢复后即为调用前的状态）。
        for key, value in saved.items():
            context.set_shared(key, value)

        return (not stopped_early, last_records)

    # ------------------------------------------------------------------
    # 节点执行
    # ------------------------------------------------------------------
    async def _execute_node(
        self,
        node_id: str,
        resolved: Any,
        context: ExecutionContext,
    ) -> NodeResult:
        """执行单个节点。

        Args:
            node_id (str): 节点 UUID。
            resolved (ResolvedNode): 解析后的节点信息。
            context (ExecutionContext): 数据总线。

        Returns:
            NodeResult: 节点执行结果（失败时错误已记录在其中）。
        """
        node_type = resolved.node.type
        node_class = resolved.node_class

        # 1) 组装输入：静态参数为底，连线值覆盖。
        merged = dict(resolved.node.params or {})
        upstream = _collect_values(context, resolved.incoming)
        merged.update(upstream)

        # 2) 输入校验（数据总线安检员）。
        try:
            inputs = node_class.input_model(**merged)
        except ValidationError as exc:
            message = _format_validation_error("输入校验失败", exc)
            return context.mark_failed(
                node_id, node_type, message, cause=_as_input_validation_error(exc)
            )

        # 3) 执行（异步）。
        started = time.perf_counter()
        try:
            output = await node_class().execute(inputs, context.readonly_view())
        except Exception as exc:  # noqa: BLE001 - 插件异常不得逃逸
            elapsed = (time.perf_counter() - started) * 1000
            result = context.mark_failed(
                node_id, node_type, format_native_exception(exc), cause=exc
            )
            result.elapsed_ms = elapsed
            result.upstream_inputs = upstream
            return result
        elapsed = (time.perf_counter() - started) * 1000

        # 4) 输出校验（双保险：即便插件已返回 output_model 实例也再校验一次）。
        try:
            validated = node_class.output_model.model_validate(output)
        except ValidationError as exc:
            message = _format_validation_error("输出校验失败", exc)
            return context.mark_failed(
                node_id, node_type, message, cause=_as_output_validation_error(exc)
            )

        # 5) 存入总线。
        return context.store(
            node_id,
            node_type,
            validated,
            elapsed,
            upstream_inputs=upstream,
            inputs=inputs,
        )

    def _skip_downstream(
        self,
        failed_id: str,
        plan: ExecutionPlan,
        context: ExecutionContext,
        report: RunReport,
    ) -> None:
        """把失败节点的全部下游标记为跳过（递归传播）。

        Args:
            failed_id (str): 失败节点的 id。
            plan (ExecutionPlan): 执行计划。
            context (ExecutionContext): 数据总线。
            report (RunReport): 执行报告。
        """
        to_skip: List[str] = [
            e.edge.target
            for e in plan.resolved_nodes[failed_id].outgoing
        ]
        seen = {failed_id}

        while to_skip:
            current = to_skip.pop(0)
            if current in seen or current not in plan.resolved_nodes:
                continue
            seen.add(current)

            resolved = plan.resolved_nodes[current]
            result = context.mark_skipped(
                current,
                resolved.node.type,
                f"上游节点 {failed_id} 失败，本节点被跳过",
            )
            # 跳过记录登记进报告，但不得与主循环已有的记录重复。
            if not any(r.node_id == current for r in report.results):
                report.results.append(result)
            to_skip.extend(e.edge.target for e in resolved.outgoing)


# ----------------------------------------------------------------------
# 辅助函数
# ----------------------------------------------------------------------
def _all_incoming_inactive(resolved: Any, inactive: Set[str]) -> bool:
    """判断节点是否因条件分支而整体失活。

    规则：节点有入边，且**所有**入边都被分支判定排除时才跳过。
    只要还有一条活跃入边（例如两条分支汇聚到同一节点），就照常执行。

    Args:
        resolved (ResolvedNode): 解析后的节点。
        inactive (Set[str]): 失活边 id 集合。

    Returns:
        bool: 应当跳过时返回 True。
    """
    if not resolved.incoming:
        return False
    return all(edge.edge.id in inactive for edge in resolved.incoming)


def _deactivate_outgoing(resolved: Any, inactive: Set[str]) -> None:
    """把节点的全部出边标记为失活。

    Args:
        resolved (ResolvedNode): 解析后的节点。
        inactive (Set[str]): 失活边 id 集合（原地修改）。
    """
    for edge in resolved.outgoing:
        inactive.add(edge.edge.id)


def _publish_loop_frame(
    context: ExecutionContext, stack: List[Dict[str, Any]]
) -> None:
    """把当前循环帧栈发布到共享上下文（供插件与表达式读取）。

    发布的键（向后兼容旧行为）::

        loop.index    最内层当前迭代下标
        loop.item     最内层当前迭代元素
        loop.indexes  由外到内的下标列表，例如 [1, 0]
        loop.depth    当前嵌套深度（最外层为 1）

    Args:
        context (ExecutionContext): 数据总线。
        stack (List[Dict[str, Any]]): 循环帧栈。
    """
    top = stack[-1] if stack else {}
    context.set_shared("loop.depth", len(stack))
    context.set_shared("loop.indexes", [frame["index"] for frame in stack])
    context.set_shared("loop.index", top.get("index"))
    context.set_shared("loop.item", top.get("item"))


def _collect_values(context: ExecutionContext, incoming: List[Any]) -> Dict[str, Any]:
    """收集注入到当前节点输入的上游值。

    Args:
        context (ExecutionContext): 数据总线。
        incoming (List[ResolvedEdge]): 入边列表。

    Returns:
        Dict[str, Any]: 目标字段名 -> 上游值。
    """
    values: Dict[str, Any] = {}
    for resolved in incoming:
        edge = resolved.edge
        upstream_output = context.get_output(edge.source)
        if upstream_output is None:
            continue
        raw = getattr(upstream_output, edge.source_handle, None)
        if raw is None:
            continue
        values[edge.target_handle] = raw
    return values


def _issues_to_details(issues: List[Any]) -> List[ErrorDetail]:
    """把解析器的 ``ValidationIssue`` 列表转为结构化错误详情。

    Args:
        issues (List[ValidationIssue]): 解析器产出的问题列表。

    Returns:
        List[ErrorDetail]: 结构化错误详情列表。
    """
    details: List[ErrorDetail] = []
    for issue in issues:
        details.append(issue_to_error_detail(issue))
    return details


def _as_input_validation_error(exc: ValidationError) -> VmafError:
    """把输入校验异常包装为带正确错误码的 ``VmafError``。

    Args:
        exc (ValidationError): Pydantic 校验异常。

    Returns:
        VmafError: 结构化异常。
    """
    return VmafError(
        _format_validation_error("输入校验失败", exc),
        code=ErrorCode.INPUT_VALIDATION.value,
        category=ErrorCategory.VALIDATION,
        detail=f"{type(exc).__name__}: {exc}",
        cause=exc,
    )


def _as_output_validation_error(exc: ValidationError) -> VmafError:
    """把输出校验异常包装为带正确错误码的 ``VmafError``。

    Args:
        exc (ValidationError): Pydantic 校验异常。

    Returns:
        VmafError: 结构化异常。
    """
    return VmafError(
        _format_validation_error("输出校验失败", exc),
        code=ErrorCode.OUTPUT_VALIDATION.value,
        category=ErrorCategory.CONTRACT,
        detail=f"{type(exc).__name__}: {exc}",
        cause=exc,
    )


def _format_validation_error(prefix: str, exc: ValidationError) -> str:
    """把 Pydantic 校验错误格式化为简洁的单行文本。

    Args:
        prefix (str): 前缀说明。
        exc (ValidationError): Pydantic 校验异常。

    Returns:
        str: 形如 ``"输入校验失败: text: Field required; pattern: Field required"``。
    """
    parts: List[str] = []
    for error in exc.errors():
        location = ".".join(str(x) for x in error.get("loc", ()))
        parts.append(f"{location}: {error.get('msg', '校验失败')}")
    return f"{prefix}: " + "; ".join(parts)
