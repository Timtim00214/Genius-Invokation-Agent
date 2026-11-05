# -*- coding: utf-8 -*-
"""
异常结构化模块。

本模块把散落在插件、解析器、引擎中的各类异常统一为**前端可直接消费的
结构化格式**。

为什么需要这一层::

    前端画布要能在出错时高亮"是哪条连线/哪个节点"出了问题。
    若直接把 Python 原生异常文本（如 ``FileNotFoundError: 文件不存在:
    D:\\...\\xyz.txt``）丢给前端，它既无法定位节点，也无法做国际化，
    更无法区分"应该让用户改蓝图"和"应该让用户重试"。

因此本模块定义::

    VmafError        统一异常基类，携带结构化上下文
    ErrorDetail      Pydantic 模型，可直接 JSON 序列化回传前端
    ErrorCatalog     错误码 -> 语义分类 的对照表

错误码采用 ``<子系统>.<问题>`` 的命名（见 NAMING_CONVENTIONS.md），
前端据此映射样式与文案。

契约:
    :class:`ErrorDetail` 是唯一对外的错误表示。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

__all__ = [
    "ErrorCategory",
    "ErrorCode",
    "ErrorDetail",
    "VmafError",
    "to_error_detail",
    "to_error_list",
    "format_native_exception",
]

#: 未知错误码的兜底值。
UNKNOWN_CODE = "internal.unknown"


class ErrorCategory(str, Enum):
    """错误的语义分类，决定**前端应该让用户做什么**。

    Attributes:
        VALIDATION: 蓝图本身有问题（连线类型错、必填缺失、有环）。
            用户应当修改蓝图，重试无用。
        NOT_FOUND: 目标资源不存在（文件、节点类型）。用户应检查配置。
        PERMISSION: 越权操作（如写入越出根目录围栏）。用户应调整权限配置。
        NETWORK: 网络不可达或服务端错误。**可重试**。
        TIMEOUT: 超时。**可重试**。
        CONTRACT: 契约破损（插件未实现 execute、IO 模型非法）。属开发者错误。
        INTERNAL: 未归类错误。
    """

    VALIDATION = "validation"
    NOT_FOUND = "not_found"
    PERMISSION = "permission"
    NETWORK = "network"
    TIMEOUT = "timeout"
    CONTRACT = "contract"
    INTERNAL = "internal"

    @property
    def retryable(self) -> bool:
        """该类别的错误是否值得重试。

        Returns:
            bool: 网络与超时类错误可重试，其余不可。
        """
        return self in (ErrorCategory.NETWORK, ErrorCategory.TIMEOUT)

    @property
    def label(self) -> str:
        """中文可读标签，供前端直接展示。

        Returns:
            str: 分类的中文名。
        """
        return {
            ErrorCategory.VALIDATION: "蓝图校验失败",
            ErrorCategory.NOT_FOUND: "资源不存在",
            ErrorCategory.PERMISSION: "权限不足",
            ErrorCategory.NETWORK: "网络错误",
            ErrorCategory.TIMEOUT: "超时",
            ErrorCategory.CONTRACT: "契约错误",
            ErrorCategory.INTERNAL: "内部错误",
        }[self]


class ErrorCode(str, Enum):
    """错误码枚举，采用 ``<子系统>.<问题>`` 命名。

    前端据此映射样式与文案；日志据此做聚合统计。
    """

    # ---- 蓝图解析期 ----
    CYCLE = "blueprint.cycle"
    UNKNOWN_NODE_TYPE = "blueprint.unknown_node_type"
    UNKNOWN_OUTPUT_FIELD = "blueprint.unknown_output_field"
    UNKNOWN_INPUT_FIELD = "blueprint.unknown_input_field"
    TYPE_MISMATCH = "blueprint.type_mismatch"
    MISSING_REQUIRED_INPUT = "blueprint.missing_required_input"
    NULLABLE_FLOW = "blueprint.nullable_flow"

    # ---- 蓝图解析期：循环 ----
    LOOP_UNPAIRED = "blueprint.loop_unpaired"
    LOOP_NESTED = "blueprint.loop_nested"
    LOOP_LEAK = "blueprint.loop_leak"
    LOOP_AMBIGUOUS = "blueprint.loop_ambiguous"

    # ---- 插件注册期 ----
    PLUGIN_LOAD_FAILED = "plugin.load_failed"
    PLUGIN_CONTRACT_BROKEN = "plugin.contract_broken"
    PLUGIN_TYPE_CONFLICT = "plugin.type_conflict"

    # ---- 运行期：校验 ----
    INPUT_VALIDATION = "runtime.input_validation"
    OUTPUT_VALIDATION = "runtime.output_validation"

    # ---- 运行期：资源 ----
    FILE_NOT_FOUND = "runtime.file_not_found"
    IS_A_DIRECTORY = "runtime.is_a_directory"
    PERMISSION_DENIED = "runtime.permission_denied"
    RESOURCE_TOO_LARGE = "runtime.resource_too_large"

    # ---- 运行期：外部依赖 ----
    NETWORK_ERROR = "runtime.network_error"
    TIMEOUT = "runtime.timeout"
    INVALID_ARGUMENT = "runtime.invalid_argument"

    # ---- 兜底 ----
    UNKNOWN = UNKNOWN_CODE


class ErrorDetail(BaseModel):
    """结构化错误详情，可直接 JSON 序列化回传前端。

    Attributes:
        code (str): 错误码，前端据此映射样式。
        category (str): 语义分类（见 :class:`ErrorCategory`）。
        message (str): 面向用户的中文说明，**必须说清具体原因**。
        retryable (bool): 是否值得重试。
        node_id (Optional[str]): 出错节点 id（前端据此高亮节点）。
        node_type (Optional[str]): 出错节点类型。
        edge_id (Optional[str]): 出错连线 id（前端据此高亮连线）。
        field (Optional[str]): 出错字段名。
        detail (Optional[str]): 补充技术细节（如原始异常文本）。
    """

    code: str = Field(UNKNOWN_CODE, description="错误码")
    category: str = Field(ErrorCategory.INTERNAL.value, description="语义分类")
    message: str = Field(..., description="面向用户的中文说明")
    retryable: bool = Field(False, description="是否值得重试")
    node_id: Optional[str] = Field(None, description="出错节点 id")
    node_type: Optional[str] = Field(None, description="出错节点类型")
    edge_id: Optional[str] = Field(None, description="出错连线 id")
    field: Optional[str] = Field(None, description="出错字段名")
    detail: Optional[str] = Field(None, description="补充技术细节")

    model_config = {"extra": "forbid"}


class VmafError(Exception):
    """VMAF 统一异常基类，携带结构化上下文。

    Attributes:
        message (str): 面向用户的中文说明。
        code (str): 错误码。
        category (ErrorCategory): 语义分类。
        node_id (Optional[str]): 关联节点 id。
        node_type (Optional[str]): 关联节点类型。
        edge_id (Optional[str]): 关联连线 id。
        field (Optional[str]): 关联字段名。
        detail (Optional[str]): 技术细节。
        cause (Optional[BaseException]): 原始异常（用于异常链）。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = UNKNOWN_CODE,
        category: ErrorCategory = ErrorCategory.INTERNAL,
        node_id: Optional[str] = None,
        node_type: Optional[str] = None,
        edge_id: Optional[str] = None,
        field: Optional[str] = None,
        detail: Optional[str] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        """初始化异常实例。

        Args:
            message (str): 面向用户的中文说明。
            code (str): 错误码。
            category (ErrorCategory): 语义分类。
            node_id (Optional[str]): 关联节点 id。
            node_type (Optional[str]): 关联节点类型。
            edge_id (Optional[str]): 关联连线 id。
            field (Optional[str]): 关联字段名。
            detail (Optional[str]): 技术细节。
            cause (Optional[BaseException]): 原始异常。
        """
        super().__init__(message)
        self.message = message
        self.code = code
        self.category = category
        self.node_id = node_id
        self.node_type = node_type
        self.edge_id = edge_id
        self.field = field
        self.detail = detail
        self.cause = cause

    def to_detail(self) -> ErrorDetail:
        """转换为可序列化的错误详情。

        Returns:
            ErrorDetail: 结构化错误详情。
        """
        return ErrorDetail(
            code=self.code,
            category=self.category.value,
            message=self.message,
            retryable=self.category.retryable,
            node_id=self.node_id,
            node_type=self.node_type,
            edge_id=self.edge_id,
            field=self.field,
            detail=self.detail,
        )


# ----------------------------------------------------------------------
# 原生异常 -> 结构化错误 的映射
# ----------------------------------------------------------------------
#: 原生异常类型 -> (错误码, 语义分类)
_NATIVE_MAP: Dict[type, tuple] = {
    FileNotFoundError: (ErrorCode.FILE_NOT_FOUND.value, ErrorCategory.NOT_FOUND),
    IsADirectoryError: (ErrorCode.IS_A_DIRECTORY.value, ErrorCategory.VALIDATION),
    PermissionError: (ErrorCode.PERMISSION_DENIED.value, ErrorCategory.PERMISSION),
    TimeoutError: (ErrorCode.TIMEOUT.value, ErrorCategory.TIMEOUT),
    ValueError: (ErrorCode.INVALID_ARGUMENT.value, ErrorCategory.VALIDATION),
    TypeError: (ErrorCode.INVALID_ARGUMENT.value, ErrorCategory.VALIDATION),
}

#: 异常类名包含这些关键字时按网络/超时归类（用于 urllib 等库的异常）。
_NETWORK_HINTS = ("URLError", "HTTPError", "ConnectionError", "ConnectTimeout")
_TIMEOUT_HINTS = ("Timeout", "TimedOut")


def to_error_detail(
    exc: BaseException,
    *,
    node_id: Optional[str] = None,
    node_type: Optional[str] = None,
    edge_id: Optional[str] = None,
    field: Optional[str] = None,
) -> ErrorDetail:
    """把任意异常转换为结构化错误详情。

    Args:
        exc (BaseException): 原始异常。
        node_id (Optional[str]): 关联节点 id。
        node_type (Optional[str]): 关联节点类型。
        edge_id (Optional[str]): 关联连线 id。
        field (Optional[str]): 关联字段名。

    Returns:
        ErrorDetail: 结构化错误详情；``VmafError`` 会保留其原有结构化信息。
    """
    if isinstance(exc, VmafError):
        detail = exc.to_detail()
        # 调用方补充的上下文优先填入空缺字段。
        for name, value in (
            ("node_id", node_id),
            ("node_type", node_type),
            ("edge_id", edge_id),
            ("field", field),
        ):
            if getattr(detail, name) is None and value is not None:
                setattr(detail, name, value)
        return detail

    code, category = _classify_native(exc)
    return ErrorDetail(
        code=code,
        category=category.value,
        message=str(exc) or f"{type(exc).__name__}",
        retryable=category.retryable,
        node_id=node_id,
        node_type=node_type,
        edge_id=edge_id,
        field=field,
        detail=f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__,
    )


def to_error_list(
    errors: List[BaseException],
    **context: Any,
) -> List[Dict[str, Any]]:
    """批量转换异常为可直接 JSON 序列化的字典列表。

    Args:
        errors (List[BaseException]): 异常列表。
        **context (Any): 统一的上下文（node_id / edge_id 等）。

    Returns:
        List[Dict[str, Any]]: 错误详情字典列表。
    """
    return [to_error_detail(e, **context).model_dump(mode="json") for e in errors]


def format_native_exception(exc: BaseException) -> str:
    """把原生异常格式化为单行中文说明（用于日志与兜底展示）。

    Args:
        exc (BaseException): 原始异常。

    Returns:
        str: 形如 ``"FileNotFoundError: 文件不存在: /tmp/x"``。
    """
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


#: 解析器的 ``ValidationIssue.code`` -> （错误码, 语义分类）。
#:
#: 这是**唯一的权威映射**：解析器内部使用 ``TYPE_MISMATCH`` 这类短码，
#: 对外则统一为 ``blueprint.type_mismatch``。engine.py 与 api/routes.py
#: 都从这里取映射，避免两处各自维护导致编码不一致。
ISSUE_CODE_MAP: Dict[str, tuple] = {
    "CYCLE": (ErrorCode.CYCLE.value, ErrorCategory.VALIDATION),
    "UNKNOWN_NODE_TYPE": (
        ErrorCode.UNKNOWN_NODE_TYPE.value,
        ErrorCategory.NOT_FOUND,
    ),
    "UNKNOWN_OUTPUT_FIELD": (
        ErrorCode.UNKNOWN_OUTPUT_FIELD.value,
        ErrorCategory.VALIDATION,
    ),
    "UNKNOWN_INPUT_FIELD": (
        ErrorCode.UNKNOWN_INPUT_FIELD.value,
        ErrorCategory.VALIDATION,
    ),
    "TYPE_MISMATCH": (ErrorCode.TYPE_MISMATCH.value, ErrorCategory.VALIDATION),
    "MISSING_REQUIRED_INPUT": (
        ErrorCode.MISSING_REQUIRED_INPUT.value,
        ErrorCategory.VALIDATION,
    ),
    "NULLABLE_FLOW": (ErrorCode.NULLABLE_FLOW.value, ErrorCategory.VALIDATION),
    "LOOP_UNPAIRED": (ErrorCode.LOOP_UNPAIRED.value, ErrorCategory.VALIDATION),
    "LOOP_NESTED": (ErrorCode.LOOP_NESTED.value, ErrorCategory.VALIDATION),
    "LOOP_LEAK": (ErrorCode.LOOP_LEAK.value, ErrorCategory.VALIDATION),
    "LOOP_AMBIGUOUS": (
        ErrorCode.LOOP_AMBIGUOUS.value,
        ErrorCategory.VALIDATION,
    ),
}


def issue_to_error_detail(issue: Any) -> ErrorDetail:
    """把解析器的 ``ValidationIssue`` 转为结构化错误详情。

    Args:
        issue (Any): 需具有 ``code`` / ``message`` / ``node_id`` /
            ``edge_id`` / ``field`` 属性的问题对象。

    Returns:
        ErrorDetail: 结构化错误详情。
    """
    code, category = ISSUE_CODE_MAP.get(
        issue.code, (ErrorCode.UNKNOWN.value, ErrorCategory.INTERNAL)
    )
    return ErrorDetail(
        code=code,
        category=category.value,
        message=issue.message,
        retryable=category.retryable,
        node_id=getattr(issue, "node_id", None),
        edge_id=getattr(issue, "edge_id", None),
        field=getattr(issue, "field", None),
    )


def _classify_native(exc: BaseException) -> tuple:
    """判定原生异常对应的错误码与语义分类。

    Args:
        exc (BaseException): 原始异常。

    Returns:
        tuple: ``(错误码, 语义分类)``。
    """
    for exc_type, mapped in _NATIVE_MAP.items():
        if isinstance(exc, exc_type):
            return mapped

    name = type(exc).__name__
    if any(hint in name for hint in _TIMEOUT_HINTS):
        return ErrorCode.TIMEOUT.value, ErrorCategory.TIMEOUT
    if any(hint in name for hint in _NETWORK_HINTS):
        return ErrorCode.NETWORK_ERROR.value, ErrorCategory.NETWORK

    return UNKNOWN_CODE, ErrorCategory.INTERNAL
