# -*- coding: utf-8 -*-
"""
蓝图持久化存储（服务端文件仓库）。

背景：此前蓝图只存在浏览器 ``localStorage`` 与手工导入/导出的 JSON 里，
换机器、换浏览器或清缓存就丢失。本模块把蓝图落盘到
``backend/data/blueprints/<id>.json``，由 API 层暴露增删查改。

设计红线:
    - **一个蓝图一个文件**：便于人工查看、备份与版本管理，不用数据库。
    - **原子写入**：先写 ``<id>.json.tmp`` 再 ``os.replace``，避免进程中断
      留下半截 JSON 导致以后加载失败。
    - **ID 围栏**：只允许 ``[A-Za-z0-9_-]{1,64}``，从根上杜绝
      ``../`` 之类的路径穿越（外部输入永远不可信）。
    - **损坏文件不拖垮列表**：单个文件解析失败只记录告警并跳过，
      列表接口照常返回其它蓝图。

契约:
    :class:`BlueprintStore` 是唯一入口；API 层只做请求校验与响应包装。
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "BlueprintStore",
    "BlueprintNotFoundError",
    "InvalidBlueprintIdError",
    "get_default_store",
]

logger = logging.getLogger(__name__)

#: 合法蓝图 id 的字符集与长度（同时充当路径穿越防线）。
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: 默认存储目录：backend/data/blueprints。
_DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "data" / "blueprints"

#: 名称最大长度，防止超长字符串撑爆元数据。
_MAX_NAME_LENGTH = 120


class BlueprintNotFoundError(KeyError):
    """请求的蓝图不存在。"""

    def __init__(self, blueprint_id: str) -> None:
        """初始化异常。

        Args:
            blueprint_id (str): 缺失的蓝图 id。
        """
        self.blueprint_id = blueprint_id
        super().__init__(f"蓝图不存在: {blueprint_id}")


class InvalidBlueprintIdError(ValueError):
    """蓝图 id 非法（包含路径穿越字符或超长）。"""

    def __init__(self, blueprint_id: str) -> None:
        """初始化异常。

        Args:
            blueprint_id (str): 非法的 id。
        """
        self.blueprint_id = blueprint_id
        super().__init__(
            f"非法的蓝图 id: {blueprint_id!r}；"
            "只允许字母、数字、下划线与连字符，长度 1~64"
        )


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串（秒级精度）。

    Returns:
        str: 形如 ``2026-10-25T03:40:00+00:00``。
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_id(blueprint_id: str) -> str:
    """校验蓝图 id 合法性（防路径穿越）。

    Args:
        blueprint_id (str): 待校验的 id。

    Returns:
        str: 通过校验的 id。

    Raises:
        InvalidBlueprintIdError: id 不符合字符集或长度要求时抛出。
    """
    if not isinstance(blueprint_id, str) or not _ID_PATTERN.match(blueprint_id):
        raise InvalidBlueprintIdError(str(blueprint_id))
    return blueprint_id


class BlueprintStore:
    """基于文件系统的蓝图仓库。

    Attributes:
        root (Path): 存储目录。
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        """初始化仓库。

        Args:
            root (Optional[Path]): 存储目录；默认 ``backend/data/blueprints``。
        """
        self.root: Path = Path(root) if root else _DEFAULT_ROOT

    # ------------------------------------------------------------------
    # 路径与序列化辅助
    # ------------------------------------------------------------------
    def _path_of(self, blueprint_id: str) -> Path:
        """把蓝图 id 解析为文件路径（已做合法性校验）。

        Args:
            blueprint_id (str): 蓝图 id。

        Returns:
            Path: 对应的 JSON 文件路径。
        """
        return self.root / f"{_validate_id(blueprint_id)}.json"

    def _ensure_root(self) -> None:
        """确保存储目录存在。"""
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def meta(blueprint_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """从落盘记录中提取列表用的元信息。

        Args:
            blueprint_id (str): 蓝图 id。
            payload (Dict[str, Any]): 落盘记录的完整内容。

        Returns:
            Dict[str, Any]: 含 id / name / saved_at / 节点与连线数量的字典。
        """
        blueprint = payload.get("blueprint") or {}
        nodes = blueprint.get("nodes") or []
        edges = blueprint.get("edges") or []
        return {
            "id": blueprint_id,
            "name": payload.get("name") or "未命名蓝图",
            "saved_at": payload.get("saved_at"),
            "node_count": len(nodes),
            "edge_count": len(edges),
        }

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def save(
        self,
        blueprint: Dict[str, Any],
        *,
        name: Optional[str] = None,
        blueprint_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """保存（新建或覆盖）一份蓝图。

        Args:
            blueprint (Dict[str, Any]): 蓝图 JSON（调用方应已通过
                :class:`core.blueprint.Blueprint` 校验）。
            name (Optional[str]): 蓝图名称；留空则沿用蓝图内的 ``name``。
            blueprint_id (Optional[str]): 指定 id 时覆盖保存；留空则新建。

        Returns:
            Dict[str, Any]: 元信息（同 :meth:`list` 的条目结构）。

        Raises:
            InvalidBlueprintIdError: 指定的 id 非法时抛出。
        """
        self._ensure_root()
        new_id = _validate_id(blueprint_id) if blueprint_id else uuid.uuid4().hex[:12]

        resolved_name = (name or blueprint.get("name") or "未命名蓝图").strip()
        if len(resolved_name) > _MAX_NAME_LENGTH:
            resolved_name = resolved_name[:_MAX_NAME_LENGTH]

        payload = {
            "id": new_id,
            "name": resolved_name,
            "saved_at": _now_iso(),
            "blueprint": blueprint,
        }

        target = self._path_of(new_id)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 原子替换：要么旧内容，要么新内容，不会出现半截文件。
        os.replace(tmp, target)

        logger.info("蓝图已保存: %s（%s）", new_id, resolved_name)
        return self.meta(new_id, payload)

    def load(self, blueprint_id: str) -> Dict[str, Any]:
        """读取一份蓝图的完整记录。

        Args:
            blueprint_id (str): 蓝图 id。

        Returns:
            Dict[str, Any]: 含 ``id`` / ``name`` / ``saved_at`` / ``blueprint``。

        Raises:
            InvalidBlueprintIdError: id 非法时抛出。
            BlueprintNotFoundError: 文件不存在时抛出。
        """
        path = self._path_of(blueprint_id)
        if not path.is_file():
            raise BlueprintNotFoundError(blueprint_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BlueprintNotFoundError(blueprint_id) from exc

    def delete(self, blueprint_id: str) -> bool:
        """删除一份蓝图。

        Args:
            blueprint_id (str): 蓝图 id。

        Returns:
            bool: 真正删除了文件返回 True；不存在返回 False。
        """
        path = self._path_of(blueprint_id)
        if not path.is_file():
            return False
        path.unlink()
        logger.info("蓝图已删除: %s", blueprint_id)
        return True

    def list(self) -> List[Dict[str, Any]]:
        """列出全部蓝图元信息（按保存时间倒序）。

        Returns:
            List[Dict[str, Any]]: 元信息列表；目录不存在时为空列表。
        """
        if not self.root.is_dir():
            return []

        items: List[Dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                # 单个损坏文件不能拖垮整个列表。
                logger.warning("跳过损坏的蓝图文件: %s", path.name)
                continue
            items.append(self.meta(path.stem, payload))

        items.sort(key=lambda item: item.get("saved_at") or "", reverse=True)
        return items


_DEFAULT_STORE: Optional[BlueprintStore] = None


def get_default_store() -> BlueprintStore:
    """获取进程级默认仓库单例。

    Returns:
        BlueprintStore: 默认仓库实例。
    """
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = BlueprintStore()
    return _DEFAULT_STORE
