# -*- coding: utf-8 -*-
"""
进程级模块加载器（跨区共享的"唯一加载权威"）。

本模块解决 VMAF 一个**致命的架构隐患**：

    Pydantic 按**类身份**（而非结构）做校验。若 ``base.py`` 在进程内被
    执行两次，就会产生两个同名但不同身份的 ``Message`` / ``BaseNode`` 类。
    届时即使各插件都"从契约层导入 Message"，A 节点产出的 Message 也
    无法通过 B 节点的输入校验——因为它们是不同的类对象。

由于 VMAF 的插件与核心区都按**文件路径**动态加载（脱离包导入链），
不同区域各自加载同一文件极易发生上述问题。因此全工程必须统一
通过本模块的 :func:`load_module_once` 加载，以固定的模块名为键在
``sys.modules`` 中登记并复用。

设计红线:
    - 单一权威：所有跨区动态加载都必须走本模块，禁止各处自行
      调用 ``importlib.util.spec_from_file_location``。
    - 失败不留痕：模块执行抛异常时，必须从 ``sys.modules`` 摘除登记，
      避免留下半初始化的坏模块被后续复用。
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path
from typing import Optional

__all__ = [
    "load_module_once",
    "reload_module",
    "ensure_package",
    "get_or_create_module",
]

logger = logging.getLogger(__name__)

#: 禁止重载的模块名：这些模块是跨插件共享的"契约层"或进程级核心设施。
#: Pydantic 按类身份校验，重载它们会产生第二份同名但不同身份的类，
#: 导致跨节点连线全部失败。本清单是 :func:`reload_module` 的护栏。
_NON_RELOADABLE_MODULES: frozenset = frozenset(
    {
        "vmaf.plugins.base",
        "vmaf.plugins.scanner",
        "vmaf.core.blueprint",
        "vmaf.core.parser",
        "vmaf.core.context",
        "vmaf.core.engine",
        "vmaf.utils.loader",
        "vmaf.utils.errors",
        "vmaf.utils.expr",
        "vmaf.main",
    }
)


def load_module_once(module_name: str, file_path: Path) -> types.ModuleType:
    """按文件路径加载模块，并保证**全进程内每个模块名只执行一次**。

    若 ``sys.modules`` 中已存在同名模块则直接复用，绝不重复执行文件——
    这是保障 Pydantic 类身份全局唯一的关键。

    Args:
        module_name (str): 登记到 ``sys.modules`` 的模块名（应全局唯一且稳定，
            例如 ``"vmaf.plugins.base"``）。
        file_path (Path): 脚本文件路径。

    Returns:
        types.ModuleType: 已执行完毕的模块对象（可能是复用自缓存的）。

    Raises:
        ImportError: 无法为该文件构建模块规格时抛出。
        Exception: 模块执行期间抛出的任何异常（登记已被摘除）。
    """
    cached = sys.modules.get(module_name)
    if cached is not None:
        logger.debug("复用已加载模块: %s", module_name)
        return cached

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {file_path} 构建模块规格")

    module = importlib.util.module_from_spec(spec)
    # 先登记再执行：使模块内的相对导入与类型解析可见。
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        logger.exception("模块执行失败，已摘除登记: %s", module_name)
        raise

    logger.debug("按路径加载模块: %s <- %s", module_name, file_path)
    return module


def reload_module(module_name: str, file_path: Path) -> types.ModuleType:
    """受控地重新执行一个已加载的模块（插件热更新的底层支撑）。

    与 :func:`load_module_once` 的区别：本函数**主动摘除** ``sys.modules``
    中的旧缓存后重新执行文件，使代码修改真正生效。历史缺陷正是
    ``load_module_once`` 按模块名复用缓存、导致"热更新只更新指纹而
    模块代码不变"，修改插件必须重启后端。

    **调用围栏（红线）**：

    - 本函数**只允许** ``plugins/scanner.py`` 在文件指纹变化时调用；
      其它任何路径禁止直接调用，修改核心模块请重启进程。
    - **绝不能**对契约层与核心区模块使用（见 :data:`_NON_RELOADABLE_MODULES`）：
      Pydantic 按类身份校验，重载 ``vmaf.plugins.base`` 会产生第二份
      ``Message`` / ``BaseNode`` 类，跨节点连线将全部失败。
    - 重载失败时同样"失败不留痕"：摘除登记并重新抛出，由扫描器
      转为 :class:`~plugins.scanner.PluginLoadError` 做故障隔离。

    Args:
        module_name (str): 规范模块名（如 ``"vmaf.plugins.tools.llm_chat"``）。
        file_path (Path): 脚本文件路径。

    Returns:
        types.ModuleType: 重新执行完毕的新模块对象。

    Raises:
        ValueError: 模块名属于禁止重载的契约层/核心区模块时抛出。
        ImportError: 无法为该文件构建模块规格时抛出。
        Exception: 模块执行期间抛出的任何异常（登记已被摘除）。
    """
    if module_name in _NON_RELOADABLE_MODULES:
        raise ValueError(
            f"禁止重载受保护模块 {module_name!r}：契约层/核心区必须保持"
            f"全进程唯一（Pydantic 按类身份校验），如需生效请重启后端进程"
        )

    # 先摘除旧缓存再加载，保证文件被真正重新执行。
    old = sys.modules.pop(module_name, None)
    if old is not None:
        logger.debug("摘除旧模块缓存准备重载: %s", module_name)

    # 清除该文件对应的字节码缓存：.pyc 头部记录的源文件 mtime 只有
    # 秒级精度，同一秒内"改文件 -> 重载"会命中旧字节码，导致重载
    # 拿到的仍是旧代码（真实踩过：回归测试同秒内改 version 断言失败）。
    _invalidate_pyc(file_path)

    try:
        return load_module_once(module_name, file_path)
    except Exception:
        # load_module_once 失败时已自行摘除登记，这里只需记录日志。
        logger.exception("模块重载失败，已摘除登记: %s", module_name)
        raise


def _invalidate_pyc(file_path: Path) -> None:
    """删除源文件对应的 ``__pycache__`` 字节码缓存文件（若存在）。

    Args:
        file_path (Path): 源脚本路径；对应的缓存位于其同级的
            ``__pycache__/<stem>.cpython-<tag>.pyc``。

    Notes:
        删除失败（权限/占用）仅记录调试日志，不抛异常——字节码缓存
        只是性能优化，最坏情况是 Python 重新编译一次。
    """
    pycache = file_path.parent / "__pycache__"
    if not pycache.is_dir():
        return
    for pyc in pycache.glob(f"{file_path.stem}.cpython-*.pyc"):
        try:
            pyc.unlink()
            logger.debug("已清除字节码缓存: %s", pyc)
        except OSError as exc:
            logger.debug("清除字节码缓存失败（忽略）: %s, %r", pyc, exc)


def ensure_package(
    package_name: str,
    package_path: Optional[Path] = None,
    **attributes: object,
) -> types.ModuleType:
    """确保合成包存在，并按需刷新其 ``__path__`` 与属性。

    用于搭建 ``vmaf`` / ``vmaf.plugins`` / ``vmaf.plugins.tools`` 这类
    合成包链，使插件脚本内的 ``from ..base import ...`` 相对导入可用。

    本函数幂等：包已存在时仅刷新 ``__path__`` 与给定属性，
    不会重建模块对象（避免已持有的引用失效）。

    Args:
        package_name (str): 包名，例如 ``"vmaf.plugins.tools"``。
        package_path (Optional[Path]): 包的搜索路径；提供时刷新 ``__path__``。
        **attributes (object): 需挂载到包上的属性，例如 ``base=contract_module``。

    Returns:
        types.ModuleType: 已确保存在的包模块对象。
    """
    package = sys.modules.get(package_name)
    if package is None:
        package = types.ModuleType(package_name)
        package.__path__ = []  # type: ignore[attr-defined]
        sys.modules[package_name] = package

    if package_path is not None:
        package.__path__ = [str(package_path)]  # type: ignore[attr-defined]

    for name, value in attributes.items():
        setattr(package, name, value)

    return package


def get_or_create_module(module_name: str) -> types.ModuleType:
    """获取或创建一个纯命名空间模块（不含文件路径）。

    用于合成包的根（如 ``vmaf``），它不对应任何真实文件。

    Args:
        module_name (str): 模块名。

    Returns:
        types.ModuleType: 已登记到 ``sys.modules`` 的模块对象。
    """
    module = sys.modules.get(module_name)
    if module is None:
        module = types.ModuleType(module_name)
        module.__path__ = []  # type: ignore[attr-defined]
        sys.modules[module_name] = module
    return module
