"""json_pick：从任意 JSON 对象里取一个字段，转成纯文本。

为什么需要它：引擎的类型预检不允许 ``Dict[str, Any]`` 直接连到 ``str``
（"无法直接作为普通 str 使用"），而"把反馈原因、上下文摘要塞进提示词"
这类需求恰恰是把结构化数据降维成文本。这个节点就是那个安全的降维点。

设计上刻意保持通用（不写死任何对局字段）：
- ``key`` 支持点号路径，例如 ``retry.reason``、``ctx.notes.last``；
- 路径里的纯数字段当作列表下标，例如 ``actions.3.name``；
- 值本身是 dict/list 时序列化成 JSON 文本（可选缩进），再按 ``max_chars`` 截断；
- 取不到就返回 ``default``，``found=False``，**不报错**——提示词缺一段不该让整张图失败。
"""

from typing import Any, Dict, List, Union

from pydantic import BaseModel, ConfigDict, Field

from ..base import BaseNode


_MISSING = object()


def _walk(data: Any, parts: List[str]) -> Any:
    """按路径取值；取不到返回 ``_MISSING``。"""
    cur = data
    for part in parts:
        if isinstance(cur, dict):
            if part not in cur:
                return _MISSING
            cur = cur[part]
        elif isinstance(cur, (list, tuple)):
            if not part.isdigit():
                return _MISSING
            index = int(part)
            if index < 0 or index >= len(cur):
                return _MISSING
            cur = cur[index]
        else:
            return _MISSING
    return cur


def _render(value: Any, pretty: bool, max_chars: int) -> str:
    """把任意值渲染成文本；dict/list 走 JSON。"""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (int, float)):
        text = str(value)
    else:
        import json

        try:
            text = json.dumps(value, ensure_ascii=False, indent=2 if pretty else None)
        except (TypeError, ValueError):
            text = repr(value)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "…（已截断）"
    return text


def _type_name(value: Any) -> str:
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


class JsonPickInput(BaseModel):
    """json_pick 的入参。"""

    model_config = ConfigDict(extra="forbid")

    data: Union[Dict[str, Any], List[Any]] = Field(
        default_factory=dict,
        description="要取值的对象（dict 或 list）。留空表示没有数据，此时返回 default。",
    )
    key: str = Field(
        ...,
        min_length=1,
        description="字段路径。用点号分隔层级，例如 retry.reason；纯数字段表示列表下标，例如 actions.0.name。",
    )
    default: str = Field(
        "",
        description="取不到时返回的文本。留空表示什么都不输出。",
    )
    max_chars: int = Field(
        800,
        ge=20,
        le=20000,
        description="输出文本的最大字符数，超出部分截断并加省略号。",
    )
    pretty: bool = Field(
        False,
        description="取到的值是 dict/list 时是否缩进格式化（更易读，但更占 token）。",
    )


class JsonPickOutput(BaseModel):
    """json_pick 的出参。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., description="取到的文本；取不到时是 default。")
    found: bool = Field(..., description="路径是否命中。false 表示用的是 default。")
    type: str = Field(..., description="命中值的类型：str/number/bool/dict/list/none/absent。")


class JsonPickNode(BaseNode):
    """从 JSON 里取一个字段并转成文本。"""

    node_type = "json_pick"
    description = "从 JSON 对象里按路径取一个字段，转成纯文本（取不到返回默认值，不报错）"
    version = "0.1.0"
    input_model = JsonPickInput
    output_model = JsonPickOutput
    control_flow = None

    async def execute(
        self,
        inputs: JsonPickInput,
        context: Dict[str, Any] | None = None,
    ) -> JsonPickOutput:
        parts = [seg for seg in inputs.key.strip().split(".") if seg != ""]
        if not parts:
            return JsonPickOutput(text=inputs.default, found=False, type="absent")

        value = _walk(inputs.data, parts)
        if value is _MISSING:
            return JsonPickOutput(text=inputs.default, found=False, type="absent")

        return JsonPickOutput(
            text=_render(value, inputs.pretty, inputs.max_chars),
            found=True,
            type=_type_name(value),
        )


__all__ = ["JsonPickNode", "JsonPickInput", "JsonPickOutput"]
