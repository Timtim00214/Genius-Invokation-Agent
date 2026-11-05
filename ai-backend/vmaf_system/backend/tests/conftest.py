# -*- coding: utf-8 -*-
"""
pytest 共享夹具。

本模块是测试套件的**模块加载唯一入口**：所有对 VMAF 内部模块的加载
都必须经过 :mod:`utils.loader` 的 :func:`load_module_once`，
与生产代码保持一致。

这一点至关重要：若测试自行用 ``importlib`` 加载契约层，就会在
``sys.modules`` 中产生第二份 ``base.py``，得到不同身份的 ``Message`` /
``BaseNode`` 类，届时"跨节点直连"等测试会因 Pydantic 按类身份校验
而失败——而这恰恰是生产环境中被小心规避的陷阱。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Type

import pytest

#: 后端根目录（backend/）
BACKEND_DIR = Path(__file__).resolve().parents[1]
#: 插件脚本目录
TOOLS_DIR = BACKEND_DIR / "plugins" / "tools"

# 让 utils/loader 与被测模块可被直接导入。
for _path in (str(BACKEND_DIR), str(BACKEND_DIR / "utils")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from loader import load_module_once  # noqa: E402

# ---- 规范模块名：与生产代码完全一致，保证测试与生产共享同一份模块 ----
CONTRACT_MODULE = "vmaf.plugins.base"
SCANNER_MODULE = "vmaf.plugins.scanner"
BLUEPRINT_MODULE = "vmaf.core.blueprint"
PARSER_MODULE = "vmaf.core.parser"
CONTEXT_MODULE = "vmaf.core.context"
ENGINE_MODULE = "vmaf.core.engine"

# ---- 规范包名 ----
TOOLS_PACKAGE = "vmaf.plugins.tools"


def _load(name: str, rel: str) -> Any:
    """通过加载权威按文件路径加载模块。

    Args:
        name (str): 规范模块名。
        rel (str): 相对 backend/ 的路径。

    Returns:
        Any: 已加载的模块对象。
    """
    return load_module_once(name, BACKEND_DIR / rel)


# ----------------------------------------------------------------------
# 契约层相关夹具
# ----------------------------------------------------------------------
@pytest.fixture(scope="session")
def contract() -> Any:
    """契约层模块（``base.py``），全会话唯一。

    Returns:
        Any: 契约层模块对象。
    """
    return _load(CONTRACT_MODULE, "plugins/base.py")


@pytest.fixture(scope="session")
def backend_utils():
    """加载 ``backend/utils/`` 下的工具模块。

    Returns:
        Callable[[str], Any]: 接收模块名（如 ``"expr"``），返回模块对象。
    """

    def _get(name: str) -> Any:
        return _load(f"vmaf.utils.{name}", f"utils/{name}.py")

    return _get


@pytest.fixture(scope="session")
def BaseNode(contract: Any) -> Type[Any]:  # noqa: N802 - 与契约层同名
    """契约层的 ``BaseNode`` 抽象基类。

    Args:
        contract (Any): 契约层模块。

    Returns:
        Type[Any]: ``BaseNode`` 类。
    """
    return contract.BaseNode


@pytest.fixture(scope="session")
def Message(contract: Any) -> Type[Any]:  # noqa: N802 - 与契约层同名
    """契约层的共享 ``Message`` 模型。

    Args:
        contract (Any): 契约层模块。

    Returns:
        Type[Any]: ``Message`` 类。
    """
    return contract.Message


# ----------------------------------------------------------------------
# 扫描器与注册表夹具
# ----------------------------------------------------------------------
@pytest.fixture(scope="session")
def scanner_module() -> Any:
    """扫描器模块（``scanner.py``）。

    Returns:
        Any: 扫描器模块对象。
    """
    return _load(SCANNER_MODULE, "plugins/scanner.py")


@pytest.fixture(scope="session")
def scanner(scanner_module: Any) -> Any:
    """已完成首次扫描的扫描器实例（全会话共享，避免重复加载插件）。

    Args:
        scanner_module (Any): 扫描器模块。

    Returns:
        Any: 已完成扫描的 ``PluginScanner`` 实例。
    """
    instance = scanner_module.PluginScanner()
    report = instance.scan()
    assert report.ok, f"插件扫描存在错误: {[str(e) for e in report.errors]}"
    return instance


@pytest.fixture(scope="session")
def registry(scanner: Any) -> Dict[str, Type[Any]]:
    """节点类型 -> 节点类 的注册表。

    Args:
        scanner (Any): 扫描器实例。

    Returns:
        Dict[str, Type[Any]]: 注册表映射。
    """
    return {t: r.node_class for t, r in scanner.get_registry().items()}


# ----------------------------------------------------------------------
# 核心区模块夹具
# ----------------------------------------------------------------------
@pytest.fixture(scope="session")
def blueprint_module() -> Any:
    """蓝图契约模块。

    Returns:
        Any: ``core/blueprint.py`` 模块对象。
    """
    return _load(BLUEPRINT_MODULE, "core/blueprint.py")


@pytest.fixture(scope="session")
def parser_module() -> Any:
    """解析器模块。

    Returns:
        Any: ``core/parser.py`` 模块对象。
    """
    return _load(PARSER_MODULE, "core/parser.py")


@pytest.fixture(scope="session")
def context_module() -> Any:
    """数据总线模块。

    Returns:
        Any: ``core/context.py`` 模块对象。
    """
    return _load(CONTEXT_MODULE, "core/context.py")


@pytest.fixture(scope="session")
def engine_module() -> Any:
    """图引擎模块。

    Returns:
        Any: ``core/engine.py`` 模块对象。
    """
    return _load(ENGINE_MODULE, "core/engine.py")


@pytest.fixture
def make_blueprint(blueprint_module: Any):
    """构造蓝图的工厂函数。

    Args:
        blueprint_module (Any): 蓝图契约模块。

    Returns:
        Callable: 接收 ``nodes`` / ``edges`` / ``name`` 关键字参数，
            返回校验通过的 ``Blueprint`` 实例。
    """

    def _make(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], **kwargs: Any):
        return blueprint_module.Blueprint(nodes=nodes, edges=edges, **kwargs)

    return _make


@pytest.fixture
def engine(engine_module: Any, registry: Dict[str, Type[Any]]) -> Any:
    """默认（非严格）模式的图引擎实例。

    Args:
        engine_module (Any): 引擎模块。
        registry (Dict[str, Type[Any]]): 节点注册表。

    Returns:
        Any: ``GraphEngine`` 实例。
    """
    return engine_module.GraphEngine(registry)


@pytest.fixture
def strict_engine(engine_module: Any, registry: Dict[str, Type[Any]]) -> Any:
    """严格模式的图引擎实例（首个节点失败即中止）。

    Args:
        engine_module (Any): 引擎模块。
        registry (Dict[str, Type[Any]]): 节点注册表。

    Returns:
        Any: ``GraphEngine(strict=True)`` 实例。
    """
    return engine_module.GraphEngine(registry, strict=True)


@pytest.fixture
def blank_tools_dir() -> Iterator[Path]:
    """确保插件目录内没有测试残留文件（用于热更新/删除类测试）。

    会在测试结束后清理掉测试期间新建的 ``.py`` 文件。

    Yields:
        Path: 插件脚本目录路径。
    """
    before = {p for p in TOOLS_DIR.glob("*.py")}
    try:
        yield TOOLS_DIR
    finally:
        after = {p for p in TOOLS_DIR.glob("*.py")}
        for leftover in after - before:
            try:
                leftover.unlink()
            except OSError:  # 已被引擎或扫描器删除
                pass
