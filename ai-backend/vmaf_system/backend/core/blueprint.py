# -*- coding: utf-8 -*-
"""
蓝图（Blueprint）数据契约。

蓝图是前端画布产出的唯一产物，也是引擎执行的唯一依据：
前端只负责产出 JSON，后端只负责读取 JSON。本模块用 Pydantic V2
把这份"前后端之间的法律"固化下来。

**关键设计：为什么需要 Pydantic 模型而不是裸字典？**

    蓝图来自外部（前端 / 保存的 JSON 文件 / API 调用），属于不可信输入。
    若直接把字典喂给引擎，缺失字段、类型错配、重复 ID 等问题会推迟到
    执行期才以莫名其妙的方式崩溃。用 Pydantic 在**解析期**一次性拦截，
    正是"契约驱动与强类型防御"红线的直接体现。

契约:
    :class:`BlueprintNode` 单一节点；:class:`BlueprintEdge` 单一连线；
    :class:`Blueprint` 完整蓝图（含完整性校验）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Set

from pydantic import BaseModel, Field, model_validator

__all__ = ["BlueprintNode", "BlueprintEdge", "Blueprint"]


class BlueprintNode(BaseModel):
    """蓝图中的单个节点。

    Attributes:
        id (str): 节点在**本蓝图内唯一**的 UUID，引擎用它隔离各节点输出。
        type (str): 节点类型标识，必须已在插件仓库中注册。
        params (Dict[str, Any]): 该节点的静态参数（连线覆盖的字段由引擎注入）。
        label (Optional[str]): 画布上的自定义显示名。
        position (Optional[Dict[str, float]]): 画布坐标 ``{"x": ..., "y": ...}``，
            仅用于前端还原布局，引擎不使用。
        accent (Optional[str]): 节点强调色（``#rrggbb``），仅前端区分节点用，
            引擎不读取。与 ``position`` 同属展示元数据，随蓝图保存。
    """

    id: str = Field(..., min_length=1, description="节点唯一 UUID")
    type: str = Field(..., min_length=1, description="已注册的节点类型标识")
    params: Dict[str, Any] = Field(
        default_factory=dict, description="节点静态参数"
    )
    label: Optional[str] = Field(None, description="画布自定义显示名")
    position: Optional[Dict[str, float]] = Field(
        None, description="画布坐标，仅前端使用"
    )
    accent: Optional[str] = Field(
        None,
        max_length=32,
        description=(
            "节点强调色（形如 #4c8dff），仅用于前端区分节点，引擎忽略；"
            "与 position 同属“展示用元数据”，因此也随蓝图保存"
        ),
    )

    model_config = {"extra": "forbid"}


class BlueprintEdge(BaseModel):
    """蓝图中的单条连线（有向边），描述数据流向。

    语义：**源节点的某个输出字段** → **目标节点的某个输入字段**。

    Attributes:
        id (str): 连线唯一标识。
        source (str): 源节点 id。
        source_handle (str): 源节点的输出字段名。
        target (str): 目标节点 id。
        target_handle (str): 目标节点的输入字段名。
        label (Optional[str]): 连线显示名。
    """

    id: str = Field(..., min_length=1, description="连线唯一标识")
    source: str = Field(..., min_length=1, description="源节点 id")
    source_handle: str = Field(..., min_length=1, description="源节点输出字段名")
    target: str = Field(..., min_length=1, description="目标节点 id")
    target_handle: str = Field(..., min_length=1, description="目标节点输入字段名")
    label: Optional[str] = Field(None, description="连线显示名")

    model_config = {"extra": "forbid"}


class Blueprint(BaseModel):
    """完整蓝图：一次可执行的 DAG 定义。

    Attributes:
        version (str): 蓝图格式版本，便于后续兼容升级。
        name (str): 蓝图名称。
        description (str): 蓝图说明。
        nodes (List[BlueprintNode]): 节点列表。
        edges (List[BlueprintEdge]): 连线列表。
    """

    version: str = Field("0.1.0", description="蓝图格式版本")
    name: str = Field("未命名蓝图", description="蓝图名称")
    description: str = Field("", description="蓝图说明")
    nodes: List[BlueprintNode] = Field(default_factory=list, description="节点列表")
    edges: List[BlueprintEdge] = Field(default_factory=list, description="连线列表")

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _check_structure(self) -> "Blueprint":
        """校验蓝图的结构完整性（ID 唯一性与连线端点存在性）。

        注意：本校验**只做结构检查**，不检查是否有环（那是拓扑排序的职责），
        也不检查节点类型是否已注册或字段类型是否匹配（需要插件仓库参与，
        由 :mod:`backend.core.parser` 完成）。

        Returns:
            Blueprint: 通过校验的实例自身。

        Raises:
            ValueError: 节点/连线 id 重复，或连线端点指向不存在的节点时抛出。
        """
        node_ids = [n.id for n in self.nodes]
        duplicates = _find_duplicates(node_ids)
        if duplicates:
            raise ValueError(f"存在重复的节点 id: {sorted(duplicates)}")

        edge_ids = [e.id for e in self.edges]
        edge_dups = _find_duplicates(edge_ids)
        if edge_dups:
            raise ValueError(f"存在重复的连线 id: {sorted(edge_dups)}")

        known: Set[str] = set(node_ids)
        for edge in self.edges:
            if edge.source not in known:
                raise ValueError(
                    f"连线 {edge.id} 的源节点不存在: {edge.source}"
                )
            if edge.target not in known:
                raise ValueError(
                    f"连线 {edge.id} 的目标节点不存在: {edge.target}"
                )
            if edge.source == edge.target:
                raise ValueError(
                    f"连线 {edge.id} 的源与目标为同一节点，形成自环: {edge.source}"
                )
        return self

    def get_node(self, node_id: str) -> BlueprintNode:
        """按 id 取回节点。

        Args:
            node_id (str): 节点 id。

        Returns:
            BlueprintNode: 对应节点。

        Raises:
            KeyError: 节点不存在时抛出。
        """
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(f"蓝图中不存在节点: {node_id}")

    def incoming_edges(self, node_id: str) -> List[BlueprintEdge]:
        """取回指向某节点的所有入边。

        Args:
            node_id (str): 目标节点 id。

        Returns:
            List[BlueprintEdge]: 入边列表。
        """
        return [e for e in self.edges if e.target == node_id]

    def outgoing_edges(self, node_id: str) -> List[BlueprintEdge]:
        """取回从某节点发出的所有出边。

        Args:
            node_id (str): 源节点 id。

        Returns:
            List[BlueprintEdge]: 出边列表。
        """
        return [e for e in self.edges if e.source == node_id]

    def to_dict(self) -> Dict[str, Any]:
        """导出为纯字典（供序列化落盘或回传前端）。

        Returns:
            Dict[str, Any]: 蓝图的字典表示。
        """
        return self.model_dump(mode="json")


def _find_duplicates(items: List[str]) -> Set[str]:
    """找出列表中重复出现的元素。

    Args:
        items (List[str]): 待检查的列表。

    Returns:
        Set[str]: 出现次数大于 1 的元素集合；无重复时为空集合。
    """
    seen: Set[str] = set()
    duplicates: Set[str] = set()
    for item in items:
        if item in seen:
            duplicates.add(item)
        seen.add(item)
    return duplicates
