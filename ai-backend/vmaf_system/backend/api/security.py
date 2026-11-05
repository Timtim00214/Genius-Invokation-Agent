# -*- coding: utf-8 -*-
"""
可选的 API 鉴权（单机部署的安全围栏）。

背景：VMAF 默认只监听本机，但一旦用 ``--host 0.0.0.0`` 暴露到局域网，
任何人都能通过 ``/run`` 让引擎执行 ``edit_file`` 之类的写操作，
``/blueprints`` 也能读走保存的蓝图（可能包含模型密钥）。

设计:
    - **默认关闭**：未设置环境变量 ``VMAF_API_KEY`` 时行为完全不变，
      不破坏现有单机用法与测试；
    - 设置后要求请求头 ``X-API-Key`` 与之匹配（常量时间比较，防时序侧信道）；
    - **只保护数据与执行类接口**：``/`` 与 ``/health`` 保持匿名可访问，
      便于探活与排查（不含敏感数据）；
    - 401 响应统一为 ``{"detail": {"message": ...}}``，与其它错误的形状一致。

配置::

    set VMAF_API_KEY=your-secret      # Windows
    export VMAF_API_KEY=your-secret   # Linux/macOS

契约:
    :func:`install_security` 把中间件挂到 FastAPI 应用上（幂等）。
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any, Awaitable, Callable, Set

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

__all__ = ["API_KEY_ENV", "API_KEY_HEADER", "PUBLIC_PATHS", "install_security"]

logger = logging.getLogger(__name__)

#: 环境变量名：设置后启用鉴权。
API_KEY_ENV = "VMAF_API_KEY"

#: 客户端携带密钥的请求头（大小写不敏感）。
API_KEY_HEADER = "X-API-Key"

#: 始终匿名的路径（探活 / 服务信息，不含敏感数据）。
PUBLIC_PATHS: Set[str] = {"/", "/health", "/docs", "/openapi.json", "/redoc"}

#: 中间件在应用状态里的标记，用于幂等安装。
_MIDDLEWARE_FLAG = "_vmaf_security_installed"


def expected_api_key() -> str:
    """读取当前期望的 API Key（每次请求现读，便于运行期改环境变量）。

    Returns:
        str: 密钥；未配置时为空字符串（表示不启用鉴权）。
    """
    return os.environ.get(API_KEY_ENV, "").strip()


def install_security(app: FastAPI) -> None:
    """给应用安装 API Key 中间件（幂等）。

    Args:
        app (FastAPI): 目标应用。
    """
    if getattr(app.state, _MIDDLEWARE_FLAG, False):
        return

    @app.middleware("http")
    async def _api_key_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Any]]
    ) -> Any:
        """校验 ``X-API-Key``；未配置密钥时直接放行。"""
        key = expected_api_key()
        path = request.url.path

        # 未配置密钥 / 白名单路径 / CORS 预检：放行。
        if not key or path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        provided = request.headers.get(API_KEY_HEADER, "")
        if not hmac.compare_digest(provided, key):
            logger.warning("拒绝未授权请求: %s %s", request.method, path)
            return JSONResponse(
                status_code=401,
                content={
                    "detail": {
                        "message": (
                            "缺少或错误的 API Key。请在请求头携带 "
                            f"{API_KEY_HEADER}（服务端通过环境变量 "
                            f"{API_KEY_ENV} 配置）"
                        )
                    }
                },
            )
        return await call_next(request)

    app.state.__setattr__(_MIDDLEWARE_FLAG, True)
    logger.info(
        "API 鉴权已安装（%s %s）",
        API_KEY_ENV,
        "已配置" if expected_api_key() else "未配置，当前不启用",
    )
