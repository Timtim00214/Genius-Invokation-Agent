# -*- coding: utf-8 -*-
"""
VMAF 插件动态反射扫描器。

本模块负责把 ``backend/plugins/tools/`` 下的**独立 Python 脚本**自动发现、
加载并注册为可被引擎调度的节点类型，同时向外（``/schema`` 接口）导出
聚合后的 JSON Schema 清单。

设计红线对应:
    - **纯数据驱动**: 扫描器只产出数据（Schema 字典与节点类映射），
      不感知任何具体业务，前端据此自动渲染模块卡片。
    - **契约驱动**: 只有通过了 :meth:`BaseNode.validate_contract` 自检的
      子类才会被注册；契约破损的插件被隔离并记录，绝不影响其它插件。
    - **彻底隔离**: 插件按文件路径动态加载（非 Python 包导入链），
      互不共享模块状态；扫描器自身除注册表外不持有业务状态。

加载机制:
    插件脚本内使用相对导入即可拿到契约基类的::

        from ..base import BaseNode, InputSchema, OutputSchema

    扫描器会预先在 ``sys.modules`` 中搭建 ``vmaf.plugins`` 包链并把
    ``base.py`` 注册为 ``vmaf.plugins.base``，因此上述相对导入可用。
    同时向下兼容 ``from vmaf.plugins.base import ...`` 与
    ``from base import ...`` 两种绝对导入写法。

Example:
    >>> scanner = PluginScanner()
    >>> report = scanner.scan()
    >>> schemas = scanner.get_all_schemas()
"""

from __future__ import annotations

import inspect
import logging
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Type

from pydantic import BaseModel

# ---- 路径常量（须在 utils/loader 导入之前定义） ----
_PLUGINS_DIR: Path = Path(__file__).resolve().parent
_BASE_FILE: Path = _PLUGINS_DIR / "base.py"
_DEFAULT_TOOLS_DIR: Path = _PLUGINS_DIR / "tools"

# 复用进程级加载权威，确保契约层全进程唯一（详见 utils/loader.py 的说明）。
_UTILS_DIR: Path = _PLUGINS_DIR.parent / "utils"
if str(_UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(_UTILS_DIR))
from loader import ensure_package, load_module_once, reload_module  # noqa: E402

__all__ = [
    "PluginLoadError",
    "PluginRecord",
    "ScanReport",
    "PluginScanner",
    "get_default_scanner",
]

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# 常量与路径解析
# ----------------------------------------------------------------------
#: 契约层（base.py）在合成包链中的规范模块名。
CONTRACT_MODULE_NAME: str = "vmaf.plugins.base"
#: 契约层在合成包链中的父包名。
CONTRACT_PACKAGE_NAME: str = "vmaf.plugins"
#: 插件模块在合成包链中的父包名。
TOOLS_PACKAGE_NAME: str = "vmaf.plugins.tools"


class PluginLoadError(Exception):
    """插件加载或契约校验失败时抛出的异常。

    Attributes:
        file_path (Path): 导致失败的插件脚本路径。
        reason (str): 人类可读的失败原因。
    """

    def __init__(self, file_path: Path, reason: str) -> None:
        """初始化异常实例。

        Args:
            file_path (Path): 导致失败的插件脚本路径。
            reason (str): 失败原因描述。
        """
        self.file_path = file_path
        self.reason = reason
        super().__init__(f"插件加载失败 [{file_path.name}]: {reason}")


@dataclass(frozen=True)
class PluginRecord:
    """单个已注册节点的不可变登记记录。

    Attributes:
        node_type (str): 节点类型唯一标识。
        description (str): 节点功能描述。
        version (str): 节点版本号。
        node_class (Type[Any]): 节点类本体（``BaseNode`` 子类）。
        module_name (str): 该节点所属的合成模块名。
        file_path (Path): 该节点所在的脚本文件路径。
        signature (Tuple[int, int]): 文件指纹 ``(mtime_ns, size)``，用于热更新判定。
    """

    node_type: str
    description: str
    version: str
    node_class: Type[Any]
    module_name: str
    file_path: Path
    signature: Tuple[int, int]

    def to_schema(self) -> Dict[str, Any]:
        """导出该节点的完整规格字典。

        Returns:
            Dict[str, Any]: 由 ``BaseNode.get_schema()`` 产出的规格字典，
                额外附加 ``file``（来源脚本）与 ``docs``（面向人的说明书），
                使前端一次请求即可同时渲染表单与帮助面板。
        """
        schema = self.node_class.get_schema()
        schema["file"] = self.file_path.name
        schema["docs"] = self.node_class.get_docs()
        return schema


@dataclass
class ScanReport:
    """一次扫描的结果报告。

    Attributes:
        scanned (int): 被扫描的脚本文件数量。
        registered (int): 本次新注册成功的节点数量。
        reloaded (int): 本次因文件变更而热更新的节点数量。
        removed (int): 本次因文件删除或变更而移除的节点数量。
        errors (List[PluginLoadError]): 本次扫描中发生的加载/校验错误列表。
    """

    scanned: int = 0
    registered: int = 0
    reloaded: int = 0
    removed: int = 0
    errors: List[PluginLoadError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """本次扫描是否完全无错误。

        Returns:
            bool: 无错误时返回 True。
        """
        return not self.errors

    def summary(self) -> str:
        """生成一行人类可读的扫描摘要。

        Returns:
            str: 形如 ``"扫描 6 个文件：新注册 6，热更新 0，移除 0，错误 0"``。
        """
        return (
            f"扫描 {self.scanned} 个文件：新注册 {self.registered}，"
            f"热更新 {self.reloaded}，移除 {self.removed}，错误 {len(self.errors)}"
        )


class PluginScanner:
    """插件目录的反射扫描与注册表维护器。

    扫描器维护两张内部表：节点类型 -> 登记记录 的注册表，以及
    文件路径 -> 文件指纹 的指纹表。指纹变化时自动热更新对应节点，
    文件被删除时自动注销其节点，从而实现"改完脚本即刻生效"。

    Attributes:
        tools_dir (Path): 被扫描的插件目录。
    """

    def __init__(self, tools_dir: Optional[Path] = None) -> None:
        """初始化扫描器。

        Args:
            tools_dir (Optional[Path]): 插件目录路径；默认使用
                ``backend/plugins/tools/``。
        """
        self.tools_dir: Path = Path(tools_dir) if tools_dir else _DEFAULT_TOOLS_DIR
        self._registry: Dict[str, PluginRecord] = {}
        self._file_nodes: Dict[Path, set] = {}
        self._file_signatures: Dict[Path, Tuple[int, int]] = {}
        self._contract: Optional[types.ModuleType] = None

    # ------------------------------------------------------------------
    # 契约层加载（按文件路径，脱离包导入链）
    # ------------------------------------------------------------------
    def load_contract(self) -> types.ModuleType:
        """加载并注册契约层模块（幂等，进程级复用）。

        首次调用时按文件路径加载 ``base.py``，并预先在 ``sys.modules``
        中搭建 ``vmaf.plugins`` 合成包链，使插件脚本内的相对导入可用。

        **进程级复用是本方法的关键**：契约层在 ``sys.modules`` 中以固定键
        ``vmaf.plugins.base`` 登记。若本模块（scanner.py）在进程内被不同
        路径加载多次，每次加载都会得到一个新的模块对象，若各自再加载一遍
        ``base.py``，就会产生多个同名但不同身份的 ``Message`` / ``BaseNode``
        类——跨节点连线会因 Pydantic 按类身份校验而失败。
        因此这里先查 ``sys.modules``：命中即复用，保证全进程内契约层唯一。

        Returns:
            types.ModuleType: 已加载的契约层模块对象。

        Raises:
            PluginLoadError: 当 ``base.py`` 缺失或加载失败时抛出。
        """
        if self._contract is not None:
            return self._contract

        cached = sys.modules.get(CONTRACT_MODULE_NAME)
        if cached is not None:
            self._contract = cached
            return cached

        if not _BASE_FILE.is_file():
            raise PluginLoadError(_BASE_FILE, "契约文件 base.py 不存在")

        try:
            module = load_module_once(CONTRACT_MODULE_NAME, _BASE_FILE)
        except Exception as exc:  # noqa: BLE001 - 统一转为领域异常
            raise PluginLoadError(_BASE_FILE, f"加载异常: {exc!r}") from exc

        module.__package__ = CONTRACT_PACKAGE_NAME

        # 搭建合成包链，使插件内的 `from ..base import BaseNode` 可用。
        root_pkg = ensure_package("vmaf")
        plugins_pkg = ensure_package(
            CONTRACT_PACKAGE_NAME, _PLUGINS_DIR, base=module
        )
        tools_pkg = ensure_package(TOOLS_PACKAGE_NAME, self.tools_dir)
        # 同时暴露 vmaf.utils，使插件可以 `from ...utils.expr import ...`
        # 复用核心工具（如受限表达式求值器），而不必自行复制实现。
        utils_pkg = ensure_package("vmaf.utils", _UTILS_DIR)
        root_pkg.plugins = plugins_pkg  # type: ignore[attr-defined]
        root_pkg.utils = utils_pkg  # type: ignore[attr-defined]
        plugins_pkg.tools = tools_pkg  # type: ignore[attr-defined]

        # 兼容 `from base import ...` 的简写写法。
        sys.modules.setdefault("base", module)

        self._contract = module
        logger.debug("契约层加载完成: %s", _BASE_FILE)
        return module

    @property
    def BaseNode(self) -> Type[Any]:  # noqa: N802 - 保持与契约层同名
        """契约层中的 ``BaseNode`` 抽象基类。

        Returns:
            Type[Any]: ``BaseNode`` 类本体。
        """
        return self.load_contract().BaseNode

    # ------------------------------------------------------------------
    # 扫描主流程
    # ------------------------------------------------------------------
    def scan(self, force_reload: bool = False) -> ScanReport:
        """扫描插件目录并同步注册表。

        Args:
            force_reload (bool): 为 True 时忽略文件指纹，强制重新加载所有脚本。

        Returns:
            ScanReport: 本次扫描的结果报告。

        Raises:
            PluginLoadError: 契约层本身加载失败时抛出（属于致命错误）。
        """
        self.load_contract()
        report = ScanReport()

        if not self.tools_dir.is_dir():
            logger.warning("插件目录不存在: %s", self.tools_dir)
            return report

        self._prune_deleted_files(report)

        for file_path in self._iter_plugin_files():
            report.scanned += 1
            try:
                self._process_file(file_path, report, force_reload=force_reload)
            except PluginLoadError as exc:
                report.errors.append(exc)
                logger.error("%s", exc)
            except Exception as exc:  # noqa: BLE001 - 单文件故障隔离
                error = PluginLoadError(file_path, f"未预期异常: {exc!r}")
                report.errors.append(error)
                logger.exception("%s", error)

        logger.info("扫描完成: %s", report.summary())
        return report

    def rescan_file(self, file_path: Path) -> int:
        """重新扫描单个脚本文件（用于文件监听或手工触发热更新）。

        Args:
            file_path (Path): 目标脚本路径。

        Returns:
            int: 该文件重新注册出的节点数量。

        Raises:
            PluginLoadError: 该文件加载或契约校验失败时抛出。
        """
        report = ScanReport()
        self._process_file(Path(file_path), report, force_reload=True)
        if report.errors:
            raise report.errors[-1]
        return report.registered + report.reloaded

    # ------------------------------------------------------------------
    # 注册表读取
    # ------------------------------------------------------------------
    def get_registry(self) -> Dict[str, PluginRecord]:
        """获取当前注册表快照。

        Returns:
            Dict[str, PluginRecord]: 节点类型到登记记录的映射副本。
        """
        return dict(self._registry)

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        """导出全部已注册节点的规格清单（供 ``/schema`` 接口使用）。

        Returns:
            List[Dict[str, Any]]: 按 ``node_type`` 升序排列的规格字典列表。
        """
        records = sorted(self._registry.values(), key=lambda r: r.node_type)
        return [record.to_schema() for record in records]

    def get_node_class(self, node_type: str) -> Type[Any]:
        """按节点类型取回节点类。

        Args:
            node_type (str): 节点类型标识。

        Returns:
            Type[Any]: 对应的 ``BaseNode`` 子类。

        Raises:
            KeyError: 该节点类型未注册时抛出。
        """
        try:
            return self._registry[node_type].node_class
        except KeyError as exc:
            raise KeyError(
                f"节点类型未注册: {node_type!r}；已注册类型: "
                f"{sorted(self._registry)}"
            ) from exc

    def instantiate(self, node_type: str) -> Any:
        """实例化指定类型的节点（引擎执行前的工厂入口）。

        Args:
            node_type (str): 节点类型标识。

        Returns:
            Any: 该节点类的实例。
        """
        return self.get_node_class(node_type)()

    def unregister_all(self) -> None:
        """清空注册表与指纹表（测试或整体重载前使用）。"""
        self._registry.clear()
        self._file_nodes.clear()
        self._file_signatures.clear()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _iter_plugin_files(self) -> Iterator[Path]:
        """迭代插件目录内待扫描的脚本文件。

        忽略以 ``_`` 开头的文件（视为内部私有模块/模板）。

        Yields:
            Path: 候选脚本文件路径（按文件名升序）。
        """
        for path in sorted(self.tools_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            yield path

    def _prune_deleted_files(self, report: ScanReport) -> None:
        """注销已被删除的脚本文件所对应的节点。

        Args:
            report (ScanReport): 用于累计移除数量的报告对象。
        """
        for file_path in list(self._file_signatures):
            if file_path.is_file():
                continue
            removed = self._unregister_file(file_path)
            report.removed += removed
            if removed:
                logger.info("文件已删除，注销其 %d 个节点: %s", removed, file_path.name)

    def _process_file(
        self,
        file_path: Path,
        report: ScanReport,
        force_reload: bool = False,
    ) -> None:
        """处理单个脚本文件：判定指纹、加载模块、登记节点。

        Args:
            file_path (Path): 脚本文件路径。
            report (ScanReport): 用于累计统计的报告对象。
            force_reload (bool): 是否忽略指纹强制重载。

        Raises:
            PluginLoadError: 加载或契约校验失败时抛出。
        """
        signature = _file_signature(file_path)
        known = self._file_signatures.get(file_path)

        if known == signature and not force_reload:
            return  # 未变更，直接命中缓存

        removed = self._unregister_file(file_path)
        report.removed += removed

        node_classes = self._discover_node_classes(file_path)
        for node_class in node_classes:
            node_type = node_class.node_type
            if node_type in self._registry:
                owner = self._registry[node_type].file_path
                raise PluginLoadError(
                    file_path,
                    f"node_type 冲突: {node_type!r} 已被 {owner.name} 占用",
                )

            record = PluginRecord(
                node_type=node_type,
                description=node_class.description,
                version=node_class.version,
                node_class=node_class,
                module_name=node_class.__module__,
                file_path=file_path,
                signature=signature,
            )
            self._registry[node_type] = record
            self._file_nodes.setdefault(file_path, set()).add(node_type)

            if removed:
                report.reloaded += 1
            else:
                report.registered += 1

        self._file_signatures[file_path] = signature
        logger.debug(
            "登记 %d 个节点，来源: %s", len(node_classes), file_path.name
        )

    def _discover_node_classes(self, file_path: Path) -> List[Type[Any]]:
        """加载脚本并返回其中定义的具体节点类。

        仅收录**在该模块内定义**、非抽象、且通过契约自检的直接/间接
        ``BaseNode`` 子类；抽象中间基类与占位契约不会被收录。

        Args:
            file_path (Path): 脚本文件路径。

        Returns:
            List[Type[Any]]: 合格节点类列表（按定义顺序）。

        Raises:
            PluginLoadError: 模块加载失败、未发现节点类或契约校验失败时抛出。
        """
        module_name = f"{TOOLS_PACKAGE_NAME}.{file_path.stem}"
        base_node = self.BaseNode

        try:
            if self._plugin_module_executed(module_name):
                # 模块此前已在进程内执行过且文件指纹变化：走受控重载，
                # 让修改过的代码真正生效（否则 load_module_once 只会
                # 复用 sys.modules 缓存，"热更新"名存实亡）。
                module = reload_module(module_name, file_path)
            else:
                module = load_module_once(module_name, file_path)
        except Exception as exc:  # noqa: BLE001 - 统一转为领域异常
            raise PluginLoadError(file_path, f"加载异常: {exc!r}") from exc

        module.__package__ = TOOLS_PACKAGE_NAME

        found: List[Type[Any]] = []
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is base_node or not issubclass(obj, base_node):
                continue
            # 只收录本模块内定义的类，排除 import 进来的契约类。
            if obj.__module__ != module.__name__:
                continue
            # 跳过仍是抽象的类（未实现 execute 的中间基类）。
            if inspect.isabstract(obj):
                continue
            try:
                obj.validate_contract()
            except (ValueError, TypeError) as exc:
                raise PluginLoadError(file_path, f"契约校验失败: {exc}") from exc
            found.append(obj)

        if not found:
            raise PluginLoadError(
                file_path, "未发现任何实现了 execute 的 BaseNode 子类"
            )

        return found

    def _plugin_module_executed(self, module_name: str) -> bool:
        """判断插件模块是否已在当前进程内执行过。

        Args:
            module_name (str): 插件的规范模块名。

        Returns:
            bool: ``sys.modules`` 中已登记同名模块时返回 True。
        """
        return module_name in sys.modules

    def _unregister_file(self, file_path: Path) -> int:
        """注销某个脚本文件登记的所有节点。

        Args:
            file_path (Path): 脚本文件路径。

        Returns:
            int: 被注销的节点数量。
        """
        node_types = self._file_nodes.pop(file_path, set())
        for node_type in node_types:
            self._registry.pop(node_type, None)
        self._file_signatures.pop(file_path, None)
        return len(node_types)


# ----------------------------------------------------------------------
# 模块级工具与默认单例
# ----------------------------------------------------------------------
def _file_signature(file_path: Path) -> Tuple[int, int]:
    """计算文件指纹，用于热更新判定。

    Args:
        file_path (Path): 脚本文件路径。

    Returns:
        Tuple[int, int]: ``(修改时间纳秒, 字节大小)`` 二元组。
    """
    stat = file_path.stat()
    return (stat.st_mtime_ns, stat.st_size)


_DEFAULT_SCANNER: Optional[PluginScanner] = None


def get_default_scanner() -> PluginScanner:
    """获取进程级默认扫描器单例（供 API 路由直接使用）。

    Returns:
        PluginScanner: 默认扫描器实例。
    """
    global _DEFAULT_SCANNER
    if _DEFAULT_SCANNER is None:
        _DEFAULT_SCANNER = PluginScanner()
    return _DEFAULT_SCANNER


def is_pydantic_model(obj: Any) -> bool:
    """判断对象是否为 Pydantic V2 模型类（供外部做类型围栏使用）。

    Args:
        obj (Any): 待检测对象。

    Returns:
        bool: 是 ``pydantic.BaseModel`` 的子类时返回 True。
    """
    return isinstance(obj, type) and issubclass(obj, BaseModel)
