# -*- coding: utf-8 -*-
"""从 Context Bus 的 ``shared`` 里取一个值。

为什么需要它:
    ``shared`` 是司机塞进来的原始数据（局面、牌组、赛前计划…）。之前每个领域
    都要写一个"读 shared 的入口节点"（``arena_input`` 就是这么来的），节点越来
    越多、逻辑重复。``shared_get`` 把它们统一成一个通用的降维点：告诉它键名，
    它把值取出来，配合 ``json_pick`` / ``deck_brief`` 这类节点使用。

    输出 ``value`` 的类型是 Any：引擎的类型预检对 Any 放行，所以可以接到任何
    输入（Dict、str…），这也是它"通用"的原因。

用法示例::

    shared_get(key="deck")        -> 牌组
    shared_get(key="ctx.plan")    -> 赛前计划（计划存在 shared.ctx 里）
    shared_get(key="catalog")     -> 名字表
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..base import BaseNode

_MISSING = object()


class SharedGetInput(BaseModel):
    """``shared_get`` 的输入。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., min_length=1, description="要取的键，支持 a.b.c 下钻（纯数字段当列表下标）")
    default: Any = Field(None, description="取不到时返回的值（默认空）")
    required: bool = Field(False, description="取不到就报错（默认 False：安静返回 default）")


class SharedGetOutput(BaseModel):
    """``shared_get`` 的输出。"""

    model_config = ConfigDict(extra="forbid")

    value: Any = Field(None, description="取到的值，原样返回")
    found: bool = Field(False, description="是否真的取到了（不是用 default 兜的）")
    kind: str = Field("absent", description="值类型：str/number/bool/dict/list/none/absent")


def _walk(data: Any, keys: list) -> Any:
    """按点号路径下钻；任何一步走不通都返回 ``_MISSING``。"""
    current = data
    for key in keys:
        if isinstance(current, dict):
            if key in current:
                current = current[key]
                continue
            return _MISSING
        if isinstance(current, (list, tuple)):
            if not key.lstrip("-").isdigit():
                return _MISSING
            index = int(key)
            if index >= len(current) or index < -len(current):
                return _MISSING
            current = current[index]
            continue
        return _MISSING
    return current


def _kind_of(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "str"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, (list, tuple)):
        return "list"
    return type(value).__name__


class SharedGetNode(BaseNode):
    """按点号路径从 ``shared`` 取值。"""

    node_type = "shared_get"
    description = "从 Context Bus 的 shared 里按路径取一个值（通用入口）"
    version = "0.1.0"
    input_model = SharedGetInput
    output_model = SharedGetOutput

    async def execute(self, inputs: SharedGetInput, context: Optional[Dict[str, Any]] = None) -> SharedGetOutput:
        """执行取值。"""
        shared = (context or {}).get("shared") or {}
        value = _walk(shared, inputs.key.split("."))
        if value is _MISSING:
            if inputs.required:
                raise ValueError(f"shared 里没有 {inputs.key}（当前顶层键：{'、'.join(sorted(shared)) or '空'}）")
            return SharedGetOutput(value=inputs.default, found=False, kind="absent")
        return SharedGetOutput(value=value, found=True, kind=_kind_of(value))
