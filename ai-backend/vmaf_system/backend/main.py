# -*- coding: utf-8 -*-
"""
VMAF 服务启动入口。

职责：创建 FastAPI 应用、挂载路由、配置 CORS，并在**启动时完成
一次插件扫描**——把插件加载错误暴露在服务启动日志里，
而不是等到用户请求时才炸。

启动方式::

    # 开发模式（代码变更自动重载）
    python -m uvicorn main:app --reload --port 8000

    # 或直接用本文件
    python main.py

启动后访问：
    - 接口文档（Swagger UI）：http://127.0.0.1:8000/docs
    - 节点规格清单：http://127.0.0.1:8000/schema

设计红线:
    - 本文件只做装配，不含业务逻辑。
    - 插件扫描在启动时执行一次；运行期新增插件可通过再次调用
      ``GET /schema`` 触发重扫（扫描器按文件指纹热更新）。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

# 确保以脚本方式直接运行时也能正确导入 backend 下的模块。
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_BACKEND_DIR / "utils") not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR / "utils"))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from api.routes import ARCH_VERSION, router  # noqa: E402
from api.security import install_security  # noqa: E402
from plugins.scanner import get_default_scanner  # noqa: E402
from utils.logger import setup_logging  # noqa: E402

#: 应用版本号
__version__ = ARCH_VERSION

# 统一日志：显式 UTF-8，解决 Windows 控制台（GBK）中文乱码。
# 幂等：重复导入本模块不会重复安装 handler。
setup_logging()
logger = logging.getLogger("vmaf.main")


def scan_plugins_at_startup() -> Any:
    """启动时扫描插件，把加载错误直接暴露在启动日志中。

    若某个插件破损，开发者在服务启动时就能看到，
    而不是等到用户拖拽该节点时才报错。

    Returns:
        Any: 扫描报告对象。
    """
    scanner = get_default_scanner()
    report = scanner.scan()

    logger.info("VMAF %s 启动", ARCH_VERSION)
    logger.info("插件扫描结果：%s", report.summary())

    for record in scanner.get_registry().values():
        logger.debug(
            "  已注册节点 %s v%s（%s）",
            record.node_type,
            record.version,
            record.file_path.name,
        )

    if not report.ok:
        for error in report.errors:
            logger.error("  插件加载失败：%s", error)
        logger.warning(
            "有 %d 个插件加载失败，服务仍可启动，但相关节点不可用",
            len(report.errors),
        )
    return report


# ---- CORS：本地开发时前端与后端端口不同，放开来源便于联调 ----
app = FastAPI(
    title="VMAF (Visual Modular Agent Framework)",
    description=(
        "可视化、纯数据驱动的智能体工作流引擎。"
        "前端通过 GET /schema 动态获取节点规格，通过 POST /run 执行蓝图。"
    ),
    version=ARCH_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

# 可选鉴权：未设置 VMAF_API_KEY 时不启用（默认行为不变）。
install_security(app)

# 导入即扫描：插件问题在进程启动时立即可见。
scan_plugins_at_startup()


@app.get("/", summary="服务根路径", tags=["meta"])
async def root() -> dict:
    """返回服务基本信息与可用接口清单。

    Returns:
        dict: 含服务名、版本与接口路径的字典。
    """
    return {
        "name": "VMAF",
        "version": ARCH_VERSION,
        "docs": "/docs",
        "endpoints": {
            "health": "/health",
            "schema": "/schema",
            "validate": "/validate",
            "run": "/run",
        },
    }


def main() -> None:
    """以脚本方式启动服务（开发用）。

    生产环境建议使用 ``uvicorn main:app --workers N``。
    """
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
