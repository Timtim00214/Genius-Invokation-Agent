# -*- coding: utf-8 -*-
"""
API 路由层。

本模块是"纯数据驱动"红线的落点之一：**不写任何业务硬编码**。
所有节点信息来自扫描器，所有执行逻辑交给引擎，路由层只做三件事::

    1. 请求校验（由 Pydantic 契约自动完成）
    2. 调用扫描器 / 引擎
    3. 把结果包装为稳定的响应结构

对外接口::

    GET    /health            服务健康状态与插件概况
    GET    /schema            全部节点规格清单（前端据此动态渲染卡片）
    POST   /validate          只校验蓝图不执行（前端保存前的预检）
    POST   /run               执行蓝图
    GET    /blueprints        列出已保存的蓝图
    POST   /blueprints        保存蓝图（新建或覆盖）
    GET    /blueprints/{id}   读取一份蓝图
    DELETE /blueprints/{id}   删除一份蓝图

关于 CORS：本地开发时前端与后端端口不同，默认允许全部来源，
便于 React Flow 前端联调。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError as PydanticValidationError

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_BACKEND_DIR / "utils") not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR / "utils"))

from api.schemas import (  # noqa: E402
    BlueprintDetailResponse,
    BlueprintListResponse,
    BlueprintMeta,
    HealthResponse,
    RunRequest,
    RunResponse,
    SaveBlueprintRequest,
    SchemaResponse,
    ValidateRequest,
    ValidateResponse,
)
from plugins.scanner import get_default_scanner  # noqa: E402
from utils.blueprint_store import (  # noqa: E402
    BlueprintNotFoundError,
    InvalidBlueprintIdError,
    get_default_store,
)
from utils.errors import (  # noqa: E402
    ErrorCategory,
    ErrorCode,
    ISSUE_CODE_MAP,
    to_error_detail,
)

__all__ = ["router", "build_router"]

#: 架构版本，与 project_status.md 保持一致。
ARCH_VERSION = "0.1.0-alpha"

router = APIRouter()


# ----------------------------------------------------------------------
# 内部辅助
# ----------------------------------------------------------------------
def _load_core_modules() -> Dict[str, Any]:
    """按需加载核心区模块（延迟到首次请求，缩短启动时间）。

    Returns:
        Dict[str, Any]: 含 ``Blueprint`` / ``GraphEngine`` / ``BlueprintParser`` 的字典。
    """
    from core.blueprint import Blueprint
    from core.engine import GraphEngine
    from core.parser import BlueprintParser

    return {
        "Blueprint": Blueprint,
        "GraphEngine": GraphEngine,
        "BlueprintParser": BlueprintParser,
    }


def _registry() -> Dict[str, Any]:
    """获取节点注册表（节点类型 -> 节点类）。

    Returns:
        Dict[str, Any]: 注册表映射。
    """
    scanner = get_default_scanner()
    return {t: r.node_class for t, r in scanner.get_registry().items()}


def _scan_once() -> Any:
    """确保插件已被扫描，并返回扫描报告。

    Returns:
        Any: 扫描报告对象。
    """
    return get_default_scanner().scan()


def _issue_to_dict(issue: Any) -> Dict[str, Any]:
    """把解析器的问题对象转为可序列化字典。

    **错误码必须统一为 `utils/errors.py` 的编码**：解析器内部用的是
    ``TYPE_MISMATCH`` 这类短码，若直接透传给前端，就会出现
    ``/validate`` 与 ``/run`` 两套编码体系，前端需要做双重映射。
    本函数负责做这一次翻译。

    Args:
        issue (Any): ``ValidationIssue`` 对象。

    Returns:
        Dict[str, Any]: 含 level / code / message 及定位信息的字典。
    """
    code, category = ISSUE_CODE_MAP.get(
        issue.code, (ErrorCode.UNKNOWN.value, ErrorCategory.INTERNAL)
    )
    return {
        "level": issue.level,
        "code": code,
        "category": category.value,
        "retryable": category.retryable,
        "message": issue.message,
        "node_id": issue.node_id,
        "edge_id": issue.edge_id,
        "field": issue.field,
    }


# ----------------------------------------------------------------------
# 接口
# ----------------------------------------------------------------------
@router.get("/health", response_model=HealthResponse, summary="服务健康检查")
async def health() -> HealthResponse:
    """返回服务状态与插件加载概况。

    Returns:
        HealthResponse: 健康状态。有插件加载失败时状态为 ``degraded``
            （服务仍可用，只是部分节点缺失）。
    """
    report = _scan_once()
    scanner = get_default_scanner()
    return HealthResponse(
        status="ok" if report.ok else "degraded",
        version=ARCH_VERSION,
        node_count=len(scanner.get_registry()),
        scan_summary=report.summary(),
        plugin_errors=len(report.errors),
    )


@router.get("/schema", response_model=SchemaResponse, summary="导出全部节点规格")
async def get_schema() -> SchemaResponse:
    """导出全部已注册节点的 JSON Schema 清单。

    前端据此动态渲染左侧模块卡片与参数输入面板，**无需硬编码任何业务字段**。

    Returns:
        SchemaResponse: 节点规格清单与插件加载错误。
    """
    report = _scan_once()
    scanner = get_default_scanner()

    contract = scanner.load_contract()
    contract_version = getattr(contract, "CONTRACT_VERSION", ARCH_VERSION)

    return SchemaResponse(
        version=ARCH_VERSION,
        contract_version=contract_version,
        count=len(scanner.get_registry()),
        nodes=scanner.get_all_schemas(),
        errors=[to_error_detail(e).model_dump(mode="json") for e in report.errors],
    )


@router.post("/validate", response_model=ValidateResponse, summary="校验蓝图（不执行）")
async def validate(payload: ValidateRequest) -> ValidateResponse:
    """只校验蓝图而不执行任何节点。

    供前端在**保存前**预检：连线类型错误、必填缺失、有环等问题
    即时反馈给用户，而不必等到执行时才发现。

    Args:
        payload (ValidateRequest): 含蓝图 JSON 的请求。

    Returns:
        ValidateResponse: 校验结论与问题清单。

    Raises:
        HTTPException: 蓝图 JSON 本身结构非法时返回 422。
    """
    modules = _load_core_modules()
    Blueprint = modules["Blueprint"]

    try:
        blueprint = Blueprint(**payload.blueprint)
    except PydanticValidationError as exc:
        # 蓝图结构非法（重复 id / 悬空连线 / 自环等）
        details = [
            {
                "level": "error",
                "code": ErrorCode.UNKNOWN.value,
                "message": "蓝图结构非法：" + "; ".join(
                    str(e["msg"]) for e in exc.errors()
                ),
                "node_id": None,
                "edge_id": None,
                "field": None,
            }
        ]
        raise HTTPException(
            status_code=422,
            detail={
                "valid": False,
                "order": [],
                "entry_nodes": [],
                "exit_nodes": [],
                "issues": details,
            },
        ) from exc

    parser = modules["BlueprintParser"](registry=_registry())
    plan = parser.parse(blueprint)

    return ValidateResponse(
        valid=plan.valid,
        order=plan.order,
        entry_nodes=plan.entry_nodes,
        exit_nodes=plan.exit_nodes,
        issues=[_issue_to_dict(i) for i in plan.issues],
    )


@router.post("/run", response_model=RunResponse, summary="执行蓝图")
async def run(payload: RunRequest) -> RunResponse:
    """执行一份蓝图。

    **本接口不因节点失败而返回 5xx**：节点级错误已汇总进响应的
    ``errors`` / ``error_details`` 字段，前端据此高亮出错的节点与连线。
    仅当蓝图 JSON 本身非法时才返回 422。

    Args:
        payload (RunRequest): 含蓝图 JSON 与执行选项的请求。

    Returns:
        RunResponse: 执行报告。

    Raises:
        HTTPException: 蓝图 JSON 结构非法时返回 422。
    """
    modules = _load_core_modules()
    Blueprint = modules["Blueprint"]

    try:
        blueprint = Blueprint(**payload.blueprint)
    except PydanticValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "蓝图结构非法："
                + "; ".join(str(e["msg"]) for e in exc.errors()),
            },
        ) from exc

    engine = modules["GraphEngine"](registry=_registry(), strict=payload.strict)
    report = await engine.run(blueprint, shared=payload.shared, run_id=payload.run_id)

    return RunResponse(
        run_id=report.run_id,
        ok=report.ok,
        aborted=report.aborted,
        abort_reason=report.abort_reason,
        total_ms=round(report.total_ms, 3),
        results=[
            {
                "node_id": r.node_id,
                "node_type": r.node_type,
                "ok": r.ok,
                "skipped": r.skipped,
                "error": r.error,
                "elapsed_ms": round(r.elapsed_ms, 3),
                "output": (
                    r.output.model_dump(mode="json") if r.output is not None else None
                ),
            }
            for r in report.results
        ],
        errors=list(report.errors),
        error_details=[
            d.model_dump(mode="json") for d in report.error_details
        ],
    )


# ----------------------------------------------------------------------
# 蓝图持久化
# ----------------------------------------------------------------------
def _validate_blueprint_payload(payload: Dict[str, Any]) -> Any:
    """用核心契约校验蓝图结构（保存前拦截垃圾数据）。

    Args:
        payload (Dict[str, Any]): 蓝图 JSON。

    Returns:
        Any: 校验通过的 ``Blueprint`` 实例。

    Raises:
        HTTPException: 结构非法时返回 422。
    """
    Blueprint = _load_core_modules()["Blueprint"]
    try:
        return Blueprint(**payload)
    except PydanticValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "蓝图结构非法：" + "; ".join(
                    str(e["msg"]) for e in exc.errors()
                ),
            },
        ) from exc


@router.get(
    "/blueprints",
    response_model=BlueprintListResponse,
    summary="列出已保存的蓝图",
)
async def list_blueprints() -> BlueprintListResponse:
    """列出服务端保存的全部蓝图元信息（按保存时间倒序）。

    Returns:
        BlueprintListResponse: 蓝图元信息列表。
    """
    items = get_default_store().list()
    return BlueprintListResponse(items=[BlueprintMeta(**item) for item in items])


@router.post(
    "/blueprints",
    response_model=BlueprintMeta,
    summary="保存蓝图（新建或覆盖）",
)
async def save_blueprint(payload: SaveBlueprintRequest) -> BlueprintMeta:
    """保存一份蓝图到服务端。

    保存前会用核心契约做一次结构校验：非法蓝图（重复 id、悬空连线等）
    直接 422，不会污染仓库。

    Args:
        payload (SaveBlueprintRequest): 蓝图 JSON、名称与可选的目标 id。

    Returns:
        BlueprintMeta: 保存后的元信息（含生成的 id）。

    Raises:
        HTTPException: 蓝图结构非法返回 422；id 非法返回 400。
    """
    validated = _validate_blueprint_payload(payload.blueprint)
    store = get_default_store()
    try:
        meta = store.save(
            validated.model_dump(mode="json"),
            name=payload.name,
            blueprint_id=payload.id,
        )
    except InvalidBlueprintIdError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc)}) from exc
    return BlueprintMeta(**meta)


@router.get(
    "/blueprints/{blueprint_id}",
    response_model=BlueprintDetailResponse,
    summary="读取一份蓝图",
)
async def load_blueprint(blueprint_id: str) -> BlueprintDetailResponse:
    """读取指定 id 的完整蓝图。

    Args:
        blueprint_id (str): 蓝图 id。

    Returns:
        BlueprintDetailResponse: 元信息与蓝图内容。

    Raises:
        HTTPException: id 非法返回 400；不存在返回 404。
    """
    store = get_default_store()
    try:
        record = store.load(blueprint_id)
    except InvalidBlueprintIdError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc)}) from exc
    except BlueprintNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail={"message": str(exc)}
        ) from exc

    return BlueprintDetailResponse(
        meta=BlueprintMeta(**store.meta(blueprint_id, record)),
        blueprint=record.get("blueprint") or {},
    )


@router.delete(
    "/blueprints/{blueprint_id}",
    summary="删除一份蓝图",
)
async def delete_blueprint(blueprint_id: str) -> Dict[str, Any]:
    """删除指定 id 的蓝图。

    Args:
        blueprint_id (str): 蓝图 id。

    Returns:
        Dict[str, Any]: ``{"deleted": bool, "id": str}``。

    Raises:
        HTTPException: id 非法返回 400；不存在返回 404。
    """
    store = get_default_store()
    try:
        deleted = store.delete(blueprint_id)
    except InvalidBlueprintIdError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc)}) from exc
    if not deleted:
        raise HTTPException(
            status_code=404, detail={"message": f"蓝图不存在: {blueprint_id}"}
        )
    return {"deleted": True, "id": blueprint_id}


def build_router() -> APIRouter:
    """构造路由实例（供 ``main.py`` 挂载）。

    Returns:
        APIRouter: 已注册全部接口的路由对象。
    """
    return router
