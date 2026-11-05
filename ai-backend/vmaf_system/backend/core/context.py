# -*- coding: utf-8 -*-
"""
内存级数据总线（Context Bus）。

本模块是"彻底的状态隔离"红线的落点：图引擎用**节点 UUID** 作为唯一键
隔离各节点的输出，杜绝多实例间的资源竞态与变量污染。

设计要点::

    1. UUID 键控：每个节点的输出独立存放，互不覆盖
    2. 写入即校验：存入前用该节点的 output_model 校验，
       类型错配当场抛异常，绝不让脏数据流入下游
    3. 只读视图：给插件的 context 是快照，插件无法回写总线
    4. 无全局状态：每个 ExecutionContext 实例独立，多次执行互不干扰

契约:
    :class:`NodeResult` 单节点执行结果；:class:`ExecutionContext` 总线本体。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel

__all__ = ["NodeResult", "ExecutionContext"]


@dataclass
class NodeResult:
    """单个节点的执行结果。

    Attributes:
        node_id (str): 节点 UUID。
        node_type (str): 节点类型标识。
        output (BaseModel): 已通过 output_model 校验的输出实例。
        elapsed_ms (float): 该节点执行耗时（毫秒）。
        upstream_inputs (Dict[str, Any]): 本次执行实际接收到的上游注入值，
            便于问题追溯（"这个节点的输入到底来自哪里"）。
        skipped (bool): 是否因上游失败而被跳过。
        error (Optional[str]): 执行失败时的错误信息。
        cause (Optional[BaseException]): 原始异常，供结构化错误分类使用。
        inputs (Optional[BaseModel]): 本次执行实际使用的输入实例
            （循环等控制流需要回读，也便于问题追溯）。
    """

    node_id: str
    node_type: str
    output: Optional[BaseModel] = None
    elapsed_ms: float = 0.0
    upstream_inputs: Dict[str, Any] = field(default_factory=dict)
    skipped: bool = False
    error: Optional[str] = None
    cause: Optional[BaseException] = None
    inputs: Optional[BaseModel] = None

    @property
    def ok(self) -> bool:
        """该节点是否成功产出输出。

        Returns:
            bool: 未跳过且无错误时返回 True。
        """
        return not self.skipped and self.error is None and self.output is not None

    def summary(self) -> str:
        """生成一行结果摘要。

        Returns:
            str: 形如 ``"n1 (read_file) 完成 12.3ms"``。
        """
        if self.skipped:
            return f"{self.node_id} ({self.node_type}) 已跳过"
        if self.error:
            return f"{self.node_id} ({self.node_type}) 失败: {self.error}"
        return f"{self.node_id} ({self.node_type}) 完成 {self.elapsed_ms:.1f}ms"


class ExecutionContext:
    """内存数据总线：以节点 UUID 隔离的键值存储。

    Attributes:
        run_id (str): 本次执行的唯一标识。
    """

    def __init__(self, run_id: str = "default") -> None:
        """初始化数据总线。

        Args:
            run_id (str): 本次执行的标识，便于日志追踪。
        """
        self.run_id: str = run_id
        self._results: Dict[str, NodeResult] = {}
        self._shared: Dict[str, Any] = {}
        self._started_at: float = time.perf_counter()

    # ------------------------------------------------------------------
    # 节点结果存取
    # ------------------------------------------------------------------
    def store(
        self,
        node_id: str,
        node_type: str,
        output: BaseModel,
        elapsed_ms: float,
        upstream_inputs: Optional[Dict[str, Any]] = None,
        inputs: Optional[BaseModel] = None,
    ) -> NodeResult:
        """存入节点输出（写入前已由引擎完成 output_model 校验）。

        Args:
            node_id (str): 节点 UUID。
            node_type (str): 节点类型标识。
            output (BaseModel): 已校验的输出实例。
            elapsed_ms (float): 执行耗时（毫秒）。
            upstream_inputs (Optional[Dict[str, Any]]): 上游注入值快照。
            inputs (Optional[BaseModel]): 本次执行使用的输入实例。

        Returns:
            NodeResult: 本次存入的结果对象。
        """
        result = NodeResult(
            node_id=node_id,
            node_type=node_type,
            output=output,
            elapsed_ms=elapsed_ms,
            upstream_inputs=dict(upstream_inputs or {}),
            inputs=inputs,
        )
        self._results[node_id] = result
        return result

    def mark_skipped(self, node_id: str, node_type: str, reason: str) -> NodeResult:
        """把节点标记为已跳过（上游失败时）。

        Args:
            node_id (str): 节点 UUID。
            node_type (str): 节点类型标识。
            reason (str): 跳过原因。

        Returns:
            NodeResult: 跳过的记录对象。
        """
        result = NodeResult(
            node_id=node_id, node_type=node_type, skipped=True, error=reason
        )
        self._results[node_id] = result
        return result

    def mark_failed(
        self,
        node_id: str,
        node_type: str,
        error: str,
        cause: Optional[BaseException] = None,
    ) -> NodeResult:
        """把节点标记为执行失败。

        Args:
            node_id (str): 节点 UUID。
            node_type (str): 节点类型标识。
            error (str): 错误信息。
            cause (Optional[BaseException]): 原始异常，供结构化错误分类使用。

        Returns:
            NodeResult: 失败的记录对象。
        """
        result = NodeResult(
            node_id=node_id, node_type=node_type, error=error, cause=cause
        )
        self._results[node_id] = result
        return result

    def get_result(self, node_id: str) -> Optional[NodeResult]:
        """取回节点结果。

        Args:
            node_id (str): 节点 UUID。

        Returns:
            Optional[NodeResult]: 结果对象；不存在时返回 None。
        """
        return self._results.get(node_id)

    def get_output(self, node_id: str) -> Optional[BaseModel]:
        """取回节点的输出实例。

        Args:
            node_id (str): 节点 UUID。

        Returns:
            Optional[BaseModel]: 输出实例；不存在或未成功时返回 None。
        """
        result = self._results.get(node_id)
        if result is None or not result.ok:
            return None
        return result.output

    # ------------------------------------------------------------------
    # 共享数据（跨节点显式共享，区别于节点输出）
    # ------------------------------------------------------------------
    def set_shared(self, key: str, value: Any) -> None:
        """写入共享数据（如全局配置、运行期变量）。

        Args:
            key (str): 键名。
            value (Any): 值。
        """
        self._shared[key] = value

    def get_shared(self, key: str, default: Any = None) -> Any:
        """读取共享数据。

        Args:
            key (str): 键名。
            default (Any): 键不存在时的默认值。

        Returns:
            Any: 共享值或默认值。
        """
        return self._shared.get(key, default)

    # ------------------------------------------------------------------
    # 视图与快照
    # ------------------------------------------------------------------
    def readonly_view(self) -> Dict[str, Any]:
        """生成给插件使用的只读上下文视图。

        插件拿到的是**快照**，对其的任何修改都不会影响总线，
        这正是"纯函数 + 状态隔离"的保障。

        Returns:
            Dict[str, Any]: 包含 ``run_id``、节点输出快照与共享数据的字典。
        """
        outputs: Dict[str, Any] = {}
        for node_id, result in self._results.items():
            if result.ok and result.output is not None:
                outputs[node_id] = result.output.model_dump()
        return {
            "run_id": self.run_id,
            "node_outputs": outputs,
            "shared": dict(self._shared),
        }

    def snapshot(self) -> Dict[str, NodeResult]:
        """返回全部节点结果的快照副本（便于序列化与日志）。

        Returns:
            Dict[str, NodeResult]: 节点 id 到结果的映射副本。
        """
        return dict(self._results)

    @property
    def elapsed_ms(self) -> float:
        """自总线创建以来的总耗时。

        Returns:
            float: 耗时（毫秒）。
        """
        return (time.perf_counter() - self._started_at) * 1000

    @property
    def successful(self) -> List[NodeResult]:
        """全部成功的节点结果。

        Returns:
            List[NodeResult]: 成功结果列表（按节点 id 排序）。
        """
        return [
            self._results[k]
            for k in sorted(self._results)
            if self._results[k].ok
        ]

    @property
    def failed(self) -> List[NodeResult]:
        """全部失败或跳过的节点结果。

        Returns:
            List[NodeResult]: 失败结果列表（按节点 id 排序）。
        """
        return [
            self._results[k]
            for k in sorted(self._results)
            if not self._results[k].ok
        ]


def collect_upstream_values(
    context: ExecutionContext,
    incoming: List[Any],
    output_model: Type[BaseModel],
) -> Dict[str, Any]:
    """从上游结果中收集注入到当前节点输入的值。

    Args:
        context (ExecutionContext): 数据总线。
        incoming (List[Any]): 入边列表（ResolvedEdge 对象，需含
            ``edge.source`` / ``edge.source_handle`` / ``edge.target_handle``）。
        output_model (Type[BaseModel]): 上游节点的输出模型（当前实现要求
            所有入边来自同一上游模型，用于取值前确认字段存在）。

    Returns:
        Dict[str, Any]: 目标字段名 -> 上游值 的映射。
    """
    values: Dict[str, Any] = {}
    for resolved in incoming:
        edge = resolved.edge
        upstream = context.get_output(edge.source)
        if upstream is None:
            continue
        raw = getattr(upstream, edge.source_handle, None)
        if raw is None:
            continue
        values[edge.target_handle] = raw
    return values
