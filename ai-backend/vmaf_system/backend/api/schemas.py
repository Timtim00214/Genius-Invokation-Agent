# -*- coding: utf-8 -*-
"""
API 层数据契约。

把引擎与扫描器的产物包装为**稳定的 HTTP 响应结构**。

为什么要单独一层而不直接返回引擎对象::

    引擎内部对象（RunReport / ExecutionPlan）会随重构而变动，
    而 HTTP 响应是外部契约，必须稳定。这一层做了隔离，
    使得内部重构不必担心破坏前端。

    同时也是"契约驱动"红线的延伸：API 边界同样要有强类型定义，
    由 FastAPI 自动生成 OpenAPI 文档。

契约:
    :class:`SchemaResponse` / :class:`RunRequest` / :class:`RunResponse`
    / :class:`ValidateRequest` / :class:`ValidateResponse` / :class:`HealthResponse`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

__all__ = [
    "SchemaResponse",
    "RunRequest",
    "RunResponse",
    "ValidateRequest",
    "ValidateResponse",
    "HealthResponse",
    "BlueprintMeta",
    "BlueprintListResponse",
    "SaveBlueprintRequest",
    "BlueprintDetailResponse",
]


class SchemaResponse(BaseModel):
    """``GET /schema`` 的响应：全部节点规格清单。

    Attributes:
        version (str): 架构版本。
        contract_version (str): 契约层版本。
        count (int): 节点类型数量。
        nodes (List[Dict[str, Any]]): 节点规格列表，每项含
            ``node_type`` / ``description`` / ``version`` / ``input`` /
            ``output`` / ``file``。
        errors (List[Dict[str, Any]]): 插件加载失败的结构化错误。
    """

    version: str = Field(..., description="架构版本")
    contract_version: str = Field(..., description="契约层版本")
    count: int = Field(0, ge=0, description="节点类型数量")
    nodes: List[Dict[str, Any]] = Field(
        default_factory=list, description="节点规格列表"
    )
    errors: List[Dict[str, Any]] = Field(
        default_factory=list, description="插件加载错误"
    )


class RunRequest(BaseModel):
    """``POST /run`` 的请求：一份待执行的蓝图。

    Attributes:
        blueprint (Dict[str, Any]): 蓝图 JSON（含 nodes / edges）。
        shared (Dict[str, Any]): 注入总线的共享数据。
        strict (bool): 是否启用严格模式（首个节点失败即中止）。
        run_id (Optional[str]): 执行标识；留空自动生成。
    """

    blueprint: Dict[str, Any] = Field(..., description="蓝图 JSON")
    shared: Dict[str, Any] = Field(
        default_factory=dict, description="注入总线的共享数据"
    )
    strict: bool = Field(False, description="首个节点失败即中止")
    run_id: Optional[str] = Field(None, description="执行标识")

    model_config = {"extra": "forbid"}


class RunResponse(BaseModel):
    """``POST /run`` 的响应：执行报告。

    Attributes:
        run_id (str): 执行标识。
        ok (bool): 整体是否成功。
        aborted (bool): 是否因蓝图校验失败而未执行。
        abort_reason (Optional[str]): 未执行的原因。
        total_ms (float): 总耗时（毫秒）。
        results (List[Dict[str, Any]]): 各节点结果。
        errors (List[str]): 错误信息文本列表。
        error_details (List[Dict[str, Any]]): 结构化错误详情，
            前端据此高亮出错的节点/连线。
    """

    run_id: str = Field(..., description="执行标识")
    ok: bool = Field(..., description="整体是否成功")
    aborted: bool = Field(False, description="是否未执行")
    abort_reason: Optional[str] = Field(None, description="未执行原因")
    total_ms: float = Field(0.0, ge=0, description="总耗时（毫秒）")
    results: List[Dict[str, Any]] = Field(
        default_factory=list, description="各节点结果"
    )
    errors: List[str] = Field(default_factory=list, description="错误信息")
    error_details: List[Dict[str, Any]] = Field(
        default_factory=list, description="结构化错误详情"
    )


class ValidateRequest(BaseModel):
    """``POST /validate`` 的请求：只校验不执行。

    Attributes:
        blueprint (Dict[str, Any]): 蓝图 JSON。
    """

    blueprint: Dict[str, Any] = Field(..., description="蓝图 JSON")

    model_config = {"extra": "forbid"}


class ValidateResponse(BaseModel):
    """``POST /validate`` 的响应：校验结论。

    供前端在**保存蓝图前**预检，即时提示连线类型错误等问题，
    而不必等到执行时才发现。

    Attributes:
        valid (bool): 是否可安全执行。
        order (List[str]): 拓扑执行顺序。
        entry_nodes (List[str]): 入口节点。
        exit_nodes (List[str]): 出口节点。
        issues (List[Dict[str, Any]]): 结构化问题列表。
    """

    valid: bool = Field(..., description="是否可安全执行")
    order: List[str] = Field(default_factory=list, description="执行顺序")
    entry_nodes: List[str] = Field(
        default_factory=list, description="入口节点"
    )
    exit_nodes: List[str] = Field(default_factory=list, description="出口节点")
    issues: List[Dict[str, Any]] = Field(
        default_factory=list, description="结构化问题列表"
    )


class HealthResponse(BaseModel):
    """``GET /health`` 的响应：服务健康状态。

    Attributes:
        status (str): ``"ok"`` 或 ``"degraded"``（有插件加载失败时）。
        version (str): 架构版本。
        node_count (int): 已注册节点类型数量。
        scan_summary (str): 最近一次扫描的摘要。
        plugin_errors (int): 插件加载错误数量。
    """

    status: str = Field(..., description="服务状态：ok / degraded")
    version: str = Field(..., description="架构版本")
    node_count: int = Field(0, ge=0, description="已注册节点数量")
    scan_summary: str = Field("", description="扫描摘要")
    plugin_errors: int = Field(0, ge=0, description="插件加载错误数")


# ----------------------------------------------------------------------
# 蓝图持久化
# ----------------------------------------------------------------------
class BlueprintMeta(BaseModel):
    """一份已保存蓝图的元信息（列表项）。

    Attributes:
        id (str): 蓝图 id（用作文件名）。
        name (str): 蓝图名称。
        saved_at (str): 保存时间（ISO 8601，UTC）。
        node_count (int): 节点数量。
        edge_count (int): 连线数量。
    """

    id: str = Field(..., description="蓝图 id")
    name: str = Field(..., description="蓝图名称")
    saved_at: Optional[str] = Field(None, description="保存时间（ISO 8601）")
    node_count: int = Field(0, ge=0, description="节点数量")
    edge_count: int = Field(0, ge=0, description="连线数量")


class BlueprintListResponse(BaseModel):
    """``GET /blueprints`` 的响应。

    Attributes:
        items (List[BlueprintMeta]): 蓝图元信息列表（按保存时间倒序）。
    """

    items: List[BlueprintMeta] = Field(
        default_factory=list, description="蓝图元信息列表"
    )


class SaveBlueprintRequest(BaseModel):
    """``POST /blueprints`` 的请求：保存一份蓝图。

    Attributes:
        blueprint (Dict[str, Any]): 蓝图 JSON（服务端会再做一次结构校验）。
        name (Optional[str]): 保存名称；留空沿用蓝图内的 name。
        id (Optional[str]): 指定 id 表示覆盖保存；留空则新建。
    """

    blueprint: Dict[str, Any] = Field(..., description="蓝图 JSON")
    name: Optional[str] = Field(None, description="保存名称")
    id: Optional[str] = Field(None, description="覆盖保存的目标 id")

    model_config = {"extra": "forbid"}


class BlueprintDetailResponse(BaseModel):
    """``GET /blueprints/{id}`` 的响应：元信息 + 完整蓝图。

    Attributes:
        meta (BlueprintMeta): 元信息。
        blueprint (Dict[str, Any]): 完整蓝图 JSON。
    """

    meta: BlueprintMeta = Field(..., description="元信息")
    blueprint: Dict[str, Any] = Field(..., description="完整蓝图 JSON")
