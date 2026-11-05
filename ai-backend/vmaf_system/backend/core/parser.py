# -*- coding: utf-8 -*-
"""
蓝图解析器与拓扑排序器。

职责：把前端产出的 :class:`Blueprint` 解析为**可直接执行的计划**
（:class:`ExecutionPlan`），并在执行前尽可能把错误全部暴露出来。

这是"纯数据驱动"红线的关键落点：解析器不感知任何业务语义，
只做三件通用的事::

    1. 环检测 —— DAG 是硬性前提，有环必须当场拒绝
    2. 拓扑排序 —— 产出确定性的串行执行顺序
    3. 连线类型预检 —— 在**保存时**就拒绝类型不通的连线，
                        而不是等到运行时崩溃

关于第 3 点：这是本模块最有价值的设计。若不做预检，一条
``context_compress.messages -> llm_chat.messages`` 的类型不匹配连线
会一直潜伏到运行时才抛异常，用户得到的反馈是"跑一半崩了"；
做了预检后，用户连线时就能立刻看到"这两个字段类型不兼容"。

类型兼容判定规则（保守优先）::

    1. 两端注解完全相同            -> 兼容
    2. 两端注解是同一个类对象       -> 兼容（覆盖共享 Message 等场景）
    3. 一端是 Any / 未标注          -> 兼容（不做无谓拦截）
    4. 数值放宽 int -> float        -> 兼容
    5. 其余情况                     -> 不兼容，报 error

契约:
    输入 :class:`Blueprint` + 节点仓库；输出 :class:`ExecutionPlan`。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Type, Union, get_args, get_origin

from pydantic import BaseModel

_CORE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _CORE_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from core.blueprint import Blueprint, BlueprintEdge, BlueprintNode  # noqa: E402

__all__ = [
    "ValidationIssue",
    "ResolvedEdge",
    "ResolvedNode",
    "ExecutionPlan",
    "BlueprintParser",
]

# 被视为"通配"的注解，任何类型都可接入。
_WILDCARD_ANNOTATIONS: Tuple[Any, ...] = (Any, object)

# 数值放宽白名单：key 可以安全地流入 value。
_NUMERIC_WIDENING: Dict[Any, Any] = {int: float}

#: ``None`` 的类型对象，用于判定 Optional / Union 中的空值成员。
NoneType: type = type(None)


@dataclass(frozen=True)
class ValidationIssue:
    """一条解析期发现的问题。

    Attributes:
        level (str): ``"error"`` 或 ``"warning"``。
        code (str): 机器可读的错误码，便于前端做国际化与样式映射。
        message (str): 人类可读的中文说明。
        node_id (Optional[str]): 关联的节点 id。
        edge_id (Optional[str]): 关联的连线 id。
        field (Optional[str]): 关联的字段名。
    """

    level: str
    code: str
    message: str
    node_id: Optional[str] = None
    edge_id: Optional[str] = None
    field: Optional[str] = None

    @property
    def is_error(self) -> bool:
        """是否为阻断性问题。

        Returns:
            bool: level 为 ``error`` 时返回 True。
        """
        return self.level == "error"

    def __str__(self) -> str:
        """返回可读表示。

        Returns:
            str: 形如 ``"[error] CYCLE: 蓝图存在环: a -> b -> a"``。
        """
        return f"[{self.level}] {self.code}: {self.message}"


@dataclass(frozen=True)
class ResolvedEdge:
    """解析后的连线，附带类型兼容判定结果。

    Attributes:
        edge (BlueprintEdge): 原始连线。
        source_type (Optional[str]): 源字段类型名。
        target_type (Optional[str]): 目标字段类型名。
        compatible (bool): 类型是否兼容。
    """

    edge: BlueprintEdge
    source_type: Optional[str]
    target_type: Optional[str]
    compatible: bool


@dataclass(frozen=True)
class ResolvedNode:
    """解析后的节点，附带其入边与执行所需信息。

    Attributes:
        node (BlueprintNode): 原始节点。
        node_class (Type[Any]): 已注册的节点类。
        incoming (List[ResolvedEdge]): 该节点的入边（含类型判定）。
        outgoing (List[ResolvedEdge]): 该节点的出边（含类型判定）。
    """

    node: BlueprintNode
    node_class: Type[Any]
    incoming: List[ResolvedEdge] = field(default_factory=list)
    outgoing: List[ResolvedEdge] = field(default_factory=list)


@dataclass
class LoopRegion:
    """一个循环区间：``loop_start`` 与 ``loop_end`` 之间的循环体。

    Attributes:
        loop_start_id (str): 循环起点节点 id。
        loop_end_id (str): 循环终点节点 id。
        body_order (List[str]): **直接**循环体内节点的执行顺序（已拓扑排序）。
            嵌套子循环的循环体与 loop_end 不在此列——它们由嵌入的
            ``loop_start`` 节点在运行时递归展开。
        parent_start_id (Optional[str]): 父循环的 loop_start id；最外层为 None。
        depth (int): 嵌套深度，最外层为 0。
        children (List[str]): 直接子循环的 loop_start id 列表（按 id 升序）。
    """

    loop_start_id: str
    loop_end_id: str
    body_order: List[str] = field(default_factory=list)
    parent_start_id: Optional[str] = None
    depth: int = 0
    children: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        """返回可读表示。

        Returns:
            str: 形如 ``"loop(s1 -> [a, b] -> e1)"``；嵌套时带深度前缀。
        """
        prefix = f"{'  ' * self.depth}" if self.depth else ""
        return (
            f"{prefix}loop({self.loop_start_id} -> "
            f"[{', '.join(self.body_order)}] -> {self.loop_end_id})"
        )


@dataclass
class ExecutionPlan:
    """可执行计划：解析器的产物，引擎的唯一输入。

    Attributes:
        blueprint (Blueprint): 原始蓝图引用。
        order (List[str]): 拓扑排序后的节点 id 执行顺序。
        resolved_nodes (Dict[str, ResolvedNode]): 节点 id 到解析结果的映射。
        issues (List[ValidationIssue]): 解析期发现的全部问题。
        entry_nodes (List[str]): 无入边的起始节点 id 列表。
        exit_nodes (List[str]): 无出边的终止节点 id 列表。
        loop_regions (Dict[str, LoopRegion]): 循环起点 id -> 循环区间。
            循环体与 loop_end 不在 :attr:`order` 中，由引擎在循环起点处调度。
    """

    blueprint: Blueprint
    order: List[str] = field(default_factory=list)
    resolved_nodes: Dict[str, ResolvedNode] = field(default_factory=dict)
    issues: List[ValidationIssue] = field(default_factory=list)
    entry_nodes: List[str] = field(default_factory=list)
    exit_nodes: List[str] = field(default_factory=list)
    loop_regions: Dict[str, LoopRegion] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        """计划是否可安全执行（无任何 error 级问题）。

        Returns:
            bool: 无 error 时返回 True。
        """
        return not any(issue.is_error for issue in self.issues)

    @property
    def errors(self) -> List[ValidationIssue]:
        """全部 error 级问题。

        Returns:
            List[ValidationIssue]: error 级问题列表。
        """
        return [i for i in self.issues if i.is_error]

    @property
    def warnings(self) -> List[ValidationIssue]:
        """全部 warning 级问题。

        Returns:
            List[ValidationIssue]: warning 级问题列表。
        """
        return [i for i in self.issues if not i.is_error]

    def summary(self) -> str:
        """生成一行摘要。

        Returns:
            str: 形如 ``"计划：6 节点，顺序 a -> b -> c，0 错误 1 警告"``。
        """
        flow = " -> ".join(self.order) if self.order else "(空)"
        return (
            f"计划：{len(self.resolved_nodes)} 节点，顺序 {flow}，"
            f"{len(self.errors)} 错误 {len(self.warnings)} 警告"
        )


class BlueprintParser:
    """蓝图解析器：环检测 + 拓扑排序 + 连线类型预检。

    Attributes:
        registry (Dict[str, Type]): 节点类型到节点类的映射（由扫描器提供）。
    """

    def __init__(self, registry: Optional[Dict[str, Any]] = None) -> None:
        """初始化解析器。

        Args:
            registry (Optional[Dict[str, Any]]): 节点类型 -> 节点类 的映射；
                一般来自 ``PluginScanner.get_registry()``（值为 PluginRecord，
                本类会自动取其中的 ``node_class``）。
        """
        self.registry: Dict[str, Type[Any]] = _normalize_registry(registry or {})

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def parse(self, blueprint: Blueprint) -> ExecutionPlan:
        """解析蓝图为执行计划。

        Args:
            blueprint (Blueprint): 已通过自身结构校验的蓝图。

        Returns:
            ExecutionPlan: 执行计划。无论是否有错都返回计划对象，
                调用方通过 :attr:`ExecutionPlan.valid` 判定能否执行。
        """
        plan = ExecutionPlan(blueprint=blueprint)

        self._resolve_nodes(plan)
        self._resolve_edges(plan)
        self._detect_cycle(plan)

        # 有环时无法拓扑排序，跳过后续步骤但保留已发现的问题。
        if not any(i.code == "CYCLE" for i in plan.issues):
            self._topological_sort(plan)
            # 先识别循环区间：循环体与 loop_end 要从主执行序列中摘出，
            # 由引擎在 loop_start 处调度，否则会被当成普通节点跑一遍。
            self._detect_loops(plan)
            if not any(i.code.startswith("LOOP_") for i in plan.issues):
                self._collapse_loops(plan)
                self._mark_entry_exit(plan)
                self._check_missing_inputs(plan)

        return plan

    # ------------------------------------------------------------------
    # 解析步骤
    # ------------------------------------------------------------------
    def _resolve_nodes(self, plan: ExecutionPlan) -> None:
        """解析每个节点：确认其类型已注册。

        Args:
            plan (ExecutionPlan): 待填充的计划对象。
        """
        for node in plan.blueprint.nodes:
            node_class = self.registry.get(node.type)
            if node_class is None:
                plan.issues.append(
                    ValidationIssue(
                        level="error",
                        code="UNKNOWN_NODE_TYPE",
                        message=(
                            f"节点类型未注册: {node.type!r}；"
                            f"已注册类型: {sorted(self.registry)}"
                        ),
                        node_id=node.id,
                    )
                )
                continue
            plan.resolved_nodes[node.id] = ResolvedNode(
                node=node, node_class=node_class
            )

    def _resolve_edges(self, plan: ExecutionPlan) -> None:
        """解析每条连线：校验端点字段存在性并做类型兼容判定。

        Args:
            plan (ExecutionPlan): 待填充的计划对象。
        """
        for edge in plan.blueprint.edges:
            source = plan.resolved_nodes.get(edge.source)
            target = plan.resolved_nodes.get(edge.target)

            # 端点节点未注册时，节点级问题已报告，这里跳过避免重复噪音。
            if source is None or target is None:
                continue

            source_field = _get_field(
                source.node_class.output_model, edge.source_handle
            )
            target_field = _get_field(
                target.node_class.input_model, edge.target_handle
            )

            if source_field is None:
                plan.issues.append(
                    ValidationIssue(
                        level="error",
                        code="UNKNOWN_OUTPUT_FIELD",
                        message=(
                            f"节点 {edge.source}（{source.node.type}）的输出模型中"
                            f"不存在字段 {edge.source_handle!r}"
                        ),
                        node_id=edge.source,
                        edge_id=edge.id,
                        field=edge.source_handle,
                    )
                )
                continue

            if target_field is None:
                plan.issues.append(
                    ValidationIssue(
                        level="error",
                        code="UNKNOWN_INPUT_FIELD",
                        message=(
                            f"节点 {edge.target}（{target.node.type}）的输入模型中"
                            f"不存在字段 {edge.target_handle!r}"
                        ),
                        node_id=edge.target,
                        edge_id=edge.id,
                        field=edge.target_handle,
                    )
                )
                continue

            verdict, reason = _type_verdict(
                source_field.annotation, target_field.annotation
            )
            resolved = ResolvedEdge(
                edge=edge,
                source_type=_type_name(source_field.annotation),
                target_type=_type_name(target_field.annotation),
                compatible=verdict != "incompatible",
            )

            if verdict == "incompatible":
                detail = f"（{reason}）" if reason else ""
                plan.issues.append(
                    ValidationIssue(
                        level="error",
                        code="TYPE_MISMATCH",
                        message=(
                            f"连线类型不兼容：{edge.source}.{edge.source_handle} "
                            f"（{resolved.source_type}）无法流入 "
                            f"{edge.target}.{edge.target_handle}"
                            f"（{resolved.target_type}）{detail}"
                        ),
                        node_id=edge.target,
                        edge_id=edge.id,
                        field=edge.target_handle,
                    )
                )
                continue

            if verdict == "nullable":
                plan.issues.append(
                    ValidationIssue(
                        level="warning",
                        code="NULLABLE_FLOW",
                        message=(
                            f"{edge.source}.{edge.source_handle} 的值可能为空，"
                            f"接入 {edge.target}.{edge.target_handle} 时"
                            f"若目标为必填将在运行时失败"
                        ),
                        node_id=edge.target,
                        edge_id=edge.id,
                        field=edge.target_handle,
                    )
                )

            # 两端都健康，登记到各自的入/出边列表。
            source.outgoing.append(resolved)
            target.incoming.append(resolved)

    def _detect_cycle(self, plan: ExecutionPlan) -> None:
        """检测环（Kahn 算法计数法）。

        以"入度归零"的方式剥离节点：若最终仍有节点未被剥离，
        说明它们处于环中。此处只报告环的存在与涉及节点，
        不展开具体环路（避免实现复杂度，且对用户而言"哪些节点在环里"已足够）。

        Args:
            plan (ExecutionPlan): 待填充的计划对象。
        """
        nodes = list(plan.resolved_nodes)
        if not nodes:
            return

        indegree: Dict[str, int] = {n: 0 for n in nodes}
        adjacency: Dict[str, List[str]] = {n: [] for n in nodes}

        for edge in plan.blueprint.edges:
            if edge.source in indegree and edge.target in indegree:
                adjacency[edge.source].append(edge.target)
                indegree[edge.target] += 1

        queue = [n for n in nodes if indegree[n] == 0]
        removed = 0
        while queue:
            current = queue.pop()
            removed += 1
            for nxt in adjacency[current]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)

        if removed != len(nodes):
            cycled = sorted(n for n in nodes if indegree[n] > 0)
            plan.issues.append(
                ValidationIssue(
                    level="error",
                    code="CYCLE",
                    message=f"蓝图存在环，无法拓扑排序。环中节点: {cycled}",
                )
            )

    def _topological_sort(self, plan: ExecutionPlan) -> None:
        """产出确定性的拓扑执行顺序。

        使用 Kahn 算法，并在每层用**节点 id 排序**保证输出的确定性——
        这是"单机高稳定性优先"红线的要求：同一份蓝图每次解析
        必须得到完全相同的执行顺序，否则调试与复现将无从谈起。

        Args:
            plan (ExecutionPlan): 待填充的计划对象。
        """
        nodes = list(plan.resolved_nodes)
        indegree: Dict[str, int] = {n: 0 for n in nodes}
        adjacency: Dict[str, List[str]] = {n: [] for n in nodes}

        for edge in plan.blueprint.edges:
            if edge.source in indegree and edge.target in indegree:
                adjacency[edge.source].append(edge.target)
                indegree[edge.target] += 1

        ready: List[str] = sorted(n for n in nodes if indegree[n] == 0)
        order: List[str] = []

        while ready:
            current = ready.pop(0)
            order.append(current)
            newly_ready: List[str] = []
            for nxt in adjacency[current]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    newly_ready.append(nxt)
            if newly_ready:
                ready.extend(newly_ready)
                ready.sort()

        plan.order = order

    # ------------------------------------------------------------------
    # 循环区间识别
    # ------------------------------------------------------------------
    def _detect_loops(self, plan: ExecutionPlan) -> None:
        """识别 loop_start / loop_end 配对，计算循环体。

        配对规则（全部静态完成，不依赖用户手工指定 ID）::

            1. 每个 loop_start 必须与一个 loop_end 配对；多余的 loop_end 报错
            2. **允许嵌套**：按拓扑序用栈配对，天然得到父子关系；
               交叉嵌套（A 开、B 开、A 关、B 关）会被明确拒绝
            3. 直接循环体节点不得绕过本循环的 loop_end 直连循环外的节点
               （否则"循环外"的语义会变得不确定）

        嵌套的区间结构::

            loop(s1 -> [a, s2] -> e1)          # s1 的直接循环体含子循环起点 s2
              loop(s2 -> [b] -> e2)            # s2 的循环体由运行时递归展开

        Args:
            plan (ExecutionPlan): 待填充的计划对象。
        """
        starts = [
            nid
            for nid, resolved in plan.resolved_nodes.items()
            if _control_flow_of(resolved) == "loop_start"
        ]
        ends = [
            nid
            for nid, resolved in plan.resolved_nodes.items()
            if _control_flow_of(resolved) == "loop_end"
        ]
        if not starts and not ends:
            return

        adjacency = self._adjacency(plan)
        topo = plan.order or sorted(plan.resolved_nodes)

        # ---- 1) 栈式配对：按拓扑序扫描，天然得到嵌套父子关系 ----
        stack: List[str] = []
        pairs: Dict[str, str] = {}
        parents: Dict[str, Optional[str]] = {}
        orphan_ends: List[str] = []

        for nid in topo:
            control = _control_flow_of(plan.resolved_nodes[nid])
            if control == "loop_start":
                parents[nid] = stack[-1] if stack else None
                stack.append(nid)
            elif control == "loop_end":
                if not stack:
                    orphan_ends.append(nid)
                    continue
                pairs[stack.pop()] = nid

        for start_id in list(stack):
            plan.issues.append(
                ValidationIssue(
                    level="error",
                    code="LOOP_UNPAIRED",
                    message=(
                        f"循环起点 {start_id} 没有找到配对的 loop_end；"
                        "请在循环结束处放置一个 loop_end 节点"
                    ),
                    node_id=start_id,
                )
            )
        for end_id in orphan_ends:
            plan.issues.append(
                ValidationIssue(
                    level="error",
                    code="LOOP_UNPAIRED",
                    message=(
                        f"循环终点 {end_id} 没有对应的 loop_start；"
                        "请在上面放置一个 loop_start 节点"
                    ),
                    node_id=end_id,
                )
            )

        # ---- 2) 逐区间做连通性、嵌套与泄漏校验 ----
        for start_id in sorted(pairs):
            end_id = pairs[start_id]
            reachable = _forward_reachable(start_id, adjacency)

            # 拓扑顺序相邻不等于真的连通：必须存在 start -> end 的路径。
            if end_id not in reachable:
                plan.issues.append(
                    ValidationIssue(
                        level="error",
                        code="LOOP_UNPAIRED",
                        message=(
                            f"循环起点 {start_id} 与 loop_end {end_id} 之间"
                            "没有连通路径，无法确定循环体"
                        ),
                        node_id=start_id,
                    )
                )
                continue

            # 完整循环体（含所有嵌套子循环的节点）= 起点可达且能到达终点。
            full_body = {
                nid
                for nid in reachable
                if nid not in (start_id, end_id)
                and end_id in _forward_reachable(nid, adjacency)
            }

            # 嵌套子循环：体内出现的 loop_start。要求其 loop_end 也在体内，
            # 否则就是"交叉嵌套"，配对结果没有确定语义。
            nested = sorted(
                nid
                for nid in full_body
                if _control_flow_of(plan.resolved_nodes[nid]) == "loop_start"
            )
            crossing = [
                nid for nid in nested if nid in pairs and pairs[nid] not in full_body
            ]
            if crossing:
                plan.issues.append(
                    ValidationIssue(
                        level="error",
                        code="LOOP_NESTED",
                        message=(
                            f"循环 {start_id} 与 {crossing} 的区间发生交叉"
                            "（A 开、B 开、A 关、B 关），嵌套必须完全包含；"
                            "请调整 loop_end 的位置使内层先关闭"
                        ),
                        node_id=start_id,
                    )
                )
                continue

            # 直接循环体 = 完整循环体 - 各子循环的（循环体 + 终点）。
            # 子循环自己由运行时在嵌入的 loop_start 处递归展开。
            excluded: set = set()
            children: List[str] = []
            for child in nested:
                if parents.get(child) != start_id:
                    continue  # 更深的孙循环由它的父级负责排除
                children.append(child)
                child_end = pairs.get(child)
                if child_end is None:
                    continue
                excluded.add(child_end)
                excluded |= {
                    nid
                    for nid in _forward_reachable(child, adjacency)
                    if nid not in (child, child_end)
                    and child_end in _forward_reachable(nid, adjacency)
                }
            direct_body = full_body - excluded

            # 直接循环体不得绕过本循环的 loop_end 直连循环外节点。
            # 注意：连向嵌套子循环内部（excluded）是合法的——那是子循环的
            # 自身接线，例如嵌套起点 s2 -> 子循环体 body。
            leaked = [
                (u, v)
                for u in direct_body
                for v in adjacency.get(u, [])
                if v not in direct_body
                and v not in (end_id, start_id)
                and v not in excluded
            ]
            if leaked:
                plan.issues.append(
                    ValidationIssue(
                        level="error",
                        code="LOOP_LEAK",
                        message=(
                            f"循环体节点直接连到了循环外的节点 {leaked}；"
                            "请统一经由 loop_end 输出"
                        ),
                        node_id=start_id,
                    )
                )
                continue

            plan.loop_regions[start_id] = LoopRegion(
                loop_start_id=start_id,
                loop_end_id=end_id,
                body_order=[n for n in topo if n in direct_body],
                parent_start_id=parents.get(start_id),
                children=sorted(children),
            )

        # ---- 3) 计算嵌套深度（父区间可能比子区间后建立） ----
        for region in plan.loop_regions.values():
            depth = 0
            cursor = region.parent_start_id
            while cursor is not None and depth <= len(plan.loop_regions):
                parent = plan.loop_regions.get(cursor)
                if parent is None:
                    break
                depth += 1
                cursor = parent.parent_start_id
            region.depth = depth

    def _collapse_loops(self, plan: ExecutionPlan) -> None:
        """把循环体与 loop_end 从主执行序列中摘出。

        摘除后 :attr:`ExecutionPlan.order` 只保留"主流程"节点，
        循环由引擎在 loop_start 处展开执行。

        Args:
            plan (ExecutionPlan): 待折叠的计划对象。
        """
        if not plan.loop_regions:
            return

        removed: set = set()
        for region in plan.loop_regions.values():
            removed.update(region.body_order)
            removed.add(region.loop_end_id)

        plan.order = [n for n in plan.order if n not in removed]

    @staticmethod
    def _adjacency(plan: ExecutionPlan) -> Dict[str, List[str]]:
        """由蓝图连线构建邻接表（仅含已解析的节点）。

        Args:
            plan (ExecutionPlan): 计划对象。

        Returns:
            Dict[str, List[str]]: 节点 id -> 后继节点 id 列表。
        """
        adjacency: Dict[str, List[str]] = {nid: [] for nid in plan.resolved_nodes}
        for edge in plan.blueprint.edges:
            if edge.source in adjacency and edge.target in adjacency:
                adjacency[edge.source].append(edge.target)
        return adjacency

    def _mark_entry_exit(self, plan: ExecutionPlan) -> None:
        """标记起始节点（无入边）与终止节点（无出边）。

        Args:
            plan (ExecutionPlan): 待填充的计划对象。
        """
        plan.entry_nodes = sorted(
            nid
            for nid, resolved in plan.resolved_nodes.items()
            if not resolved.incoming
        )
        plan.exit_nodes = sorted(
            nid
            for nid, resolved in plan.resolved_nodes.items()
            if not resolved.outgoing
        )

    def _check_missing_inputs(self, plan: ExecutionPlan) -> None:
        """检查必填输入是否被满足（既无连线提供，也无静态参数提供）。

        Args:
            plan (ExecutionPlan): 待填充的计划对象。
        """
        for node_id, resolved in plan.resolved_nodes.items():
            provided: Set[str] = {e.edge.target_handle for e in resolved.incoming}
            params = resolved.node.params or {}

            for name, model_field in resolved.node_class.input_model.model_fields.items():
                if not model_field.is_required():
                    continue
                if name in provided:
                    continue
                if name in params and params[name] is not None:
                    continue
                plan.issues.append(
                    ValidationIssue(
                        level="error",
                        code="MISSING_REQUIRED_INPUT",
                        message=(
                            f"节点 {node_id}（{resolved.node.type}）的必填输入 "
                            f"{name!r} 既无连线提供，也无静态参数提供"
                        ),
                        node_id=node_id,
                        field=name,
                    )
                )


# ----------------------------------------------------------------------
# 类型兼容判定
# ----------------------------------------------------------------------
def _types_compatible(source: Any, target: Any) -> bool:
    """判定源字段类型能否安全流入目标字段（严格判定）。

    与早期实现的关键区别：**不再对无法判定的情况默认放行**。
    早期实现末尾有一句 ``return True``，导致 ``List[RegexMatchItem] -> str``
    这类明显不兼容的连线也被判为合法，使类型预检形同虚设。
    本实现改为逐层严格判定，真正守住"保存时就拒绝错误连线"的目标。

    判定规则（按优先级）::

        1. 任一端是 Any / 缺失注解          -> 兼容（通配，不阻拦）
        2. 注解完全相同                      -> 兼容（含泛型参数一致）
        3. 同为容器（如 List -> List）      -> 递归比较内部类型
        4. 容器与标量互连                    -> 不兼容
        5. 源为 Union（含 Optional）        -> 逐个成员判定，含 None 时降级为警告
        6. 目标为 Union                      -> 能匹配任一成员即兼容
        7. 都是标量：相等 或 int->float      -> 兼容；否则不兼容

    Args:
        source (Any): 源字段的类型注解。
        target (Any): 目标字段的类型注解。

    Returns:
        bool: 兼容时返回 True（含"可空但可接受"的警告级情况）。
    """
    verdict, _ = _type_verdict(source, target)
    return verdict != "incompatible"


def _type_verdict(source: Any, target: Any) -> Tuple[str, Optional[str]]:
    """给出类型判定的详细结论（含警告级的可空降级）。

    Args:
        source (Any): 源字段的类型注解。
        target (Any): 目标字段的类型注解。

    Returns:
        Tuple[str, Optional[str]]: ``(结论, 说明)``。结论取值为
            ``"compatible"`` / ``"nullable"`` / ``"incompatible"``；
            ``"nullable"`` 表示可接但源可能为空（警告级）。
    """
    # 缺失注解或通配：不阻拦。
    if source is None or target is None:
        return "compatible", None
    if source is Any or target is Any:
        return "compatible", None

    # 完全相同（含泛型参数一致，例如 List[Message] == List[Message]）。
    if source == target:
        return "compatible", None

    s_origin = get_origin(source)
    t_origin = get_origin(target)

    # ---- Union 必须优先于"容器 vs 标量"判定 ----
    # get_origin(Optional[str]) 返回的是 Union，若让容器分支先跑，
    # Union 会被误当成容器，导致 Optional[str] -> str 被错判为
    # "容器不能作为标量"，Union 分支将永远不可达。

    # 源为 Union（含 Optional）：逐个成员判定；含 None 说明源可能为空，降级为警告。
    if s_origin is Union:
        members = [a for a in get_args(source) if a is not NoneType]
        if not members:
            return "incompatible", "源类型仅可能为 None"
        results = [_type_verdict(m, target) for m in members]
        if all(r[0] == "incompatible" for r in results):
            return "incompatible", results[0][1]
        if any(r[0] == "nullable" for r in results):
            return "nullable", "源值可能为空"
        if NoneType in get_args(source):
            return "nullable", "源值可能为 None"
        return "compatible", None

    # 目标为 Union：能匹配任一非 None 成员即兼容。
    if t_origin is Union:
        members = [a for a in get_args(target) if a is not NoneType]
        if not members:
            return "incompatible", "目标类型仅接受 None"
        results = [_type_verdict(source, m) for m in members]
        if any(r[0] == "compatible" for r in results):
            return "compatible", None
        if any(r[0] == "nullable" for r in results):
            return "nullable", "目标接受多种类型，源可能为空"
        return "incompatible", results[0][1]

    # 同为容器族：递归比较内部类型参数。
    if s_origin is not None and s_origin is t_origin:
        s_args = get_args(source)
        t_args = get_args(target)
        if not s_args or not t_args:
            return "compatible", None
        if len(s_args) != len(t_args):
            return "incompatible", "容器类型参数个数不同"
        for s_arg, t_arg in zip(s_args, t_args):
            inner, _ = _type_verdict(s_arg, t_arg)
            if inner == "incompatible":
                return (
                    "incompatible",
                    f"容器元素类型不兼容：{_type_name(s_arg)} -> {_type_name(t_arg)}",
                )
        return "compatible", None

    # 不同容器族（如 Dict -> List）：不兼容。
    if s_origin is not None and t_origin is not None and s_origin is not t_origin:
        return (
            "incompatible",
            f"容器类型不同：{_type_name(source)} 不能作为 {_type_name(target)} 使用",
        )

    # 容器与标量互连：不兼容（例如 List[RegexMatchItem] -> str）。
    if (s_origin is None) != (t_origin is None):
        if s_origin is not None:
            return (
                "incompatible",
                f"容器 {_type_name(source)} 不能直接作为标量 {_type_name(target)} 使用",
            )
        return (
            "incompatible",
            f"标量 {_type_name(source)} 不能直接作为容器 {_type_name(target)} 使用",
        )

    # 都是标量。
    if isinstance(source, type) and isinstance(target, type):
        if source is target:
            return "compatible", None
        if _NUMERIC_WIDENING.get(source) is target:
            return "compatible", None
        return "incompatible", f"{_type_name(source)} 不能作为 {_type_name(target)} 使用"

    # 无法识别的注解形式：放行，交由运行时 Pydantic 校验把关。
    return "compatible", None


def _type_name(annotation: Any) -> str:
    """生成类型注解的可读名称（泛型会展开内部参数）。

    展开泛型参数很重要：若只显示 ``List``，用户看到
    "List 不能流入 str" 时无法判断是哪个元素类型出了问题。

    Args:
        annotation (Any): 类型注解。

    Returns:
        str: 可读的类型名，例如 ``"List[Message]"`` / ``"str"`` /
            ``"Optional[int]"`` / ``"Any"``。
    """
    if annotation is None:
        return "None"
    if annotation is Any:
        return "Any"

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Union:
        members = [a for a in args if a is not NoneType]
        names = [_type_name(m) for m in members]
        names.append("None")
        return " | ".join(names)

    if origin is not None and args:
        base = getattr(origin, "__name__", str(origin))
        # 统一为工程惯例的大写容器名：list -> List，dict -> Dict。
        if base.islower():
            base = base.capitalize()
        return f"{base}[{', '.join(_type_name(a) for a in args)}]"

    return getattr(annotation, "__name__", str(annotation))


def _get_field(model: Type[BaseModel], name: str) -> Any:
    """从 Pydantic 模型中取回字段信息。

    Args:
        model (Type[BaseModel]): Pydantic 模型类。
        name (str): 字段名。

    Returns:
        Any: 对应的 ``FieldInfo``；字段不存在时返回 None。
    """
    return model.model_fields.get(name)


def _control_flow_of(resolved: Any) -> Optional[str]:
    """读取节点的控制流标记。

    Args:
        resolved (ResolvedNode): 解析后的节点。

    Returns:
        Optional[str]: ``"branch"`` / ``"loop_start"`` / ``"loop_end"``；
            普通数据节点返回 None。
    """
    return getattr(resolved.node_class, "control_flow", None)


def _forward_reachable(start: str, adjacency: Dict[str, List[str]]) -> set:
    """计算从某节点出发可达的全部节点（含起点）。

    Args:
        start (str): 起点节点 id。
        adjacency (Dict[str, List[str]]): 邻接表。

    Returns:
        set: 可达节点 id 集合。
    """
    seen = {start}
    stack = [start]
    while stack:
        current = stack.pop()
        for nxt in adjacency.get(current, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _normalize_registry(registry: Dict[str, Any]) -> Dict[str, Type[Any]]:
    """把扫描器注册表规范化为 节点类型 -> 节点类 的映射。

    兼容两种输入：值为节点类，或值为 ``PluginRecord``（自动取 ``node_class``）。

    Args:
        registry (Dict[str, Any]): 原始注册表。

    Returns:
        Dict[str, Type[Any]]: 规范化后的映射。
    """
    normalized: Dict[str, Type[Any]] = {}
    for key, value in registry.items():
        node_class = getattr(value, "node_class", value)
        normalized[key] = node_class
    return normalized
