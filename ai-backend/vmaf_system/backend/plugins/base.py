# -*- coding: utf-8 -*-
"""
VMAF 插件系统核心契约层（"底层法律"）。

本模块是整个 VMAF 系统唯一的数据类型与插件行为契约来源。所有业务插件
（存放于 ``backend/plugins/tools/`` 下的独立 Python 脚本）都必须继承
:class:`BaseNode`，并通过 Pydantic V2 模型严格声明自身的输入与输出结构。

设计红线:
    1. **契约驱动**: 输入/输出必须是 Pydantic V2 ``BaseModel`` 子类，
       由 ``model_json_schema()`` 自动导出前端所需的 JSON Schema。
    2. **纯函数执行**: ``execute`` 为异步方法，插件内部禁止使用全局变量，
       表现为 "输入 Pydantic 实例 -> 输出 Pydantic 实例" 的无副作用纯函数。
    3. **状态隔离**: 运行时状态仅通过 ``context`` 字典（由引擎的 Context Bus 注入）
       流转，插件自身不持有跨调用状态。

Example:
    >>> from pydantic import BaseModel, Field
    >>> class EchoInput(BaseModel):
    ...     text: str = Field(..., description="待回显的文本")
    >>> class EchoOutput(BaseModel):
    ...     text: str = Field(..., description="回显结果")
    >>> class EchoNode(BaseNode):
    ...     node_type = "echo"
    ...     description = "回显输入文本"
    ...     input_model = EchoInput
    ...     output_model = EchoOutput
    ...     async def execute(self, inputs, context=None):
    ...         return EchoOutput(text=inputs.text)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, Literal, Optional, Type

from pydantic import BaseModel, Field

__all__ = [
    "InputSchema",
    "OutputSchema",
    "Message",
    "BaseNode",
]

# 当前契约层版本，随 base.py 的破坏性变更递增。
CONTRACT_VERSION: str = "0.1.0"


class InputSchema(BaseModel):
    """通用输入契约占位模型。

    当某个插件节点不需要任何输入参数时，可将 ``input_model`` 指向本类，
    以保持契约完整性（前端依然能导出一个合法的空对象 Schema）。

    Attributes:
        model_config: 禁止接收任何额外字段，落实"强类型防御"红线，
            任何未声明的入参都会在校验阶段抛出具象异常。
    """

    model_config = {"extra": "forbid"}


class OutputSchema(BaseModel):
    """通用输出契约占位模型。

    当某个插件节点不产生有效输出（例如纯粹的副作用写入类节点）时，
    可将 ``output_model`` 指向本类作为占位。

    Attributes:
        model_config: 禁止接收任何额外字段，防止数据漂移。
    """

    model_config = {"extra": "forbid"}


class Message(BaseModel):
    """跨节点共享的对话消息契约（引擎数据总线的通用货币）。

    本模型定义在**契约层**而非各插件内部，是关键的架构决定：
    Pydantic 按**类身份**（而非结构）做校验，若 llm_chat 与 context_compress
    各自定义结构相同的 Message 类，两者的输出/输入将无法直连，
    引擎在搬运数据时会抛出 model_type 异常。

    因此凡涉及对话历史的节点（对话、上下文压缩、RAG、tool-use 等）
    都必须从契约层导入本类，保证图上的连线两端是同一个类型。

    Attributes:
        role (Literal["system", "user", "assistant", "tool"]): 消息发出方角色。
        content (str): 消息正文。
        name (Optional[str]): 可选的发送方名称（用于 tool 消息或多智能体场景）。
    """

    role: Literal["system", "user", "assistant", "tool"] = Field(
        ..., description="消息角色：system / user / assistant / tool"
    )
    content: str = Field(..., description="消息正文")
    name: Optional[str] = Field(None, description="可选的发送方名称")

    model_config = {"extra": "forbid"}


class BaseNode(ABC):
    """VMAF 所有插件节点必须继承的抽象基类。

    子类必须提供两类信息:
        - **元数据**: ``node_type`` / ``description`` / ``version``，供前端
          动态渲染模块卡片与说明文本。
        - **数据契约**: ``input_model`` / ``output_model``，供运行时充当
          数据总线安检员，并向前端导出 JSON Schema。

    子类还必须实现异步方法 :meth:`execute`。

    Attributes:
        node_type (ClassVar[str]): 节点类型唯一标识（如 ``"http_fetch"``）。
            在插件仓库内必须全局唯一，由扫描器用作注册键。
        description (ClassVar[str]): 节点功能描述，展示在前端模块卡片上。
        version (ClassVar[str]): 节点版本号，默认继承契约层版本。
        input_model (ClassVar[Type[BaseModel]]): 输入数据的 Pydantic 模型类。
        output_model (ClassVar[Type[BaseModel]]): 输出数据的 Pydantic 模型类。
    """

    # ---------- 类属性元数据（子类必须覆写 node_type / description） ----------
    node_type: ClassVar[str] = ""
    """节点类型唯一标识。子类必须覆写，且在插件仓库内全局唯一。"""

    description: ClassVar[str] = ""
    """节点功能描述，用于前端展示。子类应当覆写。"""

    version: ClassVar[str] = CONTRACT_VERSION
    """节点版本号，默认与当前契约层版本一致。"""

    # ---------- 强制绑定的数据契约 ----------
    input_model: ClassVar[Type[BaseModel]] = InputSchema
    """输入数据的 Pydantic V2 模型类。子类必须绑定为具象模型。"""

    output_model: ClassVar[Type[BaseModel]] = OutputSchema
    """输出数据的 Pydantic V2 模型类。子类必须绑定为具象模型。"""

    # ---------- 控制流标记（可选） ----------
    control_flow: ClassVar[Optional[str]] = None
    """控制流类型标记。普通数据节点留空；控制流节点取以下值之一：

        - ``"branch"``：条件分支，引擎读取输出中的 ``route`` 字段
          决定激活哪条出边（``on_true`` / ``on_false``）。
        - ``"loop_start"``：循环起点，引擎会识别与其配对的
          ``loop_end`` 之间的节点区间并重复执行。
        - ``"loop_end"``：循环终点，由引擎在迭代完成后填充结果。

    引擎通过该标记区分"数据节点"与"控制流节点"，从而在保持插件
    纯函数契约的同时实现图级调度。
    """

    # ------------------------------------------------------------------
    # 核心执行入口
    # ------------------------------------------------------------------
    @abstractmethod
    async def execute(
        self,
        inputs: BaseModel,
        context: Optional[Dict[str, Any]] = None,
    ) -> BaseModel:
        """执行节点的核心业务逻辑（抽象方法，子类必须实现）。

        实现必须遵守"无副作用纯函数"约定：不读写全局变量、不持有跨调用状态，
        仅依据 ``inputs`` 与 ``context`` 计算并返回 ``output_model`` 的实例。

        Args:
            inputs (BaseModel): 已通过 ``input_model`` 校验的输入实例。
            context (Optional[Dict[str, Any]]): 引擎注入的运行时上下文字典
                （Context Bus 只读视图），用于读取跨节点共享数据；默认 ``None``。

        Returns:
            BaseModel: ``output_model`` 的实例，引擎会再次对其进行类型校验，
                类型错配将当场抛出具象异常。

        Raises:
            NotImplementedError: 子类未实现本方法时由 ABC 机制抛出。
        """
        raise NotImplementedError(
            f"{type(self).__name__} 必须实现异步方法 execute(inputs, context)"
        )

    # ------------------------------------------------------------------
    # Schema 导出与契约自检
    # ------------------------------------------------------------------
    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        """导出节点的完整规格字典（供前端动态渲染）。

        返回的字典包含节点元数据以及输入/输出的 JSON Schema，前端可据此
        自动渲染模块卡片与参数输入面板，无需硬编码任何业务字段。

        Returns:
            Dict[str, Any]: 结构如下::

                {
                    "node_type": str,        # 节点类型标识
                    "description": str,      # 节点功能描述
                    "version": str,          # 节点版本号
                    "input": dict,           # input_model.model_json_schema()
                    "output": dict,          # output_model.model_json_schema()
                }

        Example:
            >>> EchoNode.get_schema()["node_type"]
            'echo'
        """
        return {
            "node_type": cls.node_type,
            "description": cls.description,
            "version": cls.version,
            "control_flow": cls.control_flow,
            "input": cls.input_model.model_json_schema(),
            "output": cls.output_model.model_json_schema(),
        }

    # ------------------------------------------------------------------
    # 模块文档（给使用者看的说明书）
    # ------------------------------------------------------------------
    @classmethod
    def get_docs(cls) -> Dict[str, Any]:
        """导出面向使用者的模块说明书。

        与 :meth:`get_schema` 的区别：``get_schema`` 给**机器**用（渲染表单），
        ``get_docs`` 给**人**用（看懂这个模块是干什么的、参数该填什么格式）。

        说明书由契约自动推导，因此插件作者只需在 Pydantic ``Field`` 的
        ``description`` 里写清格式即可，不需要额外维护文档：

            - 参数名、类型、是否必填、默认值
            - 每个参数的说明文本（含格式与示例）
            - 输出字段的类型与含义
            - 控制流说明（如果是 branch / loop 节点）

        Returns:
            Dict[str, Any]: 说明书结构::

                {
                    "node_type": str,
                    "description": str,
                    "version": str,
                    "control_flow": Optional[str],
                    "usage": str,          # 使用提示
                    "inputs": [...],       # 参数说明列表
                    "outputs": [...],      # 输出说明列表
                }
        """
        return {
            "node_type": cls.node_type,
            "description": cls.description,
            "version": cls.version,
            "control_flow": cls.control_flow,
            "usage": _USAGE_HINTS.get(cls.control_flow or "", ""),
            "inputs": _describe_fields(cls.input_model),
            "outputs": _describe_fields(cls.output_model),
        }

    @classmethod
    def validate_contract(cls) -> None:
        """校验子类的契约完整性，在插件注册阶段由扫描器调用。

        检查项包括：``node_type`` 非空、``input_model`` / ``output_model``
        均为 ``BaseModel`` 的子类。

        Raises:
            ValueError: 当 ``node_type`` 为空时抛出。
            TypeError: 当 ``input_model`` 或 ``output_model`` 不是
                ``pydantic.BaseModel`` 的子类时抛出。
        """
        if not cls.node_type:
            raise ValueError(
                f"{cls.__name__} 未声明 node_type，无法在插件仓库中注册"
            )

        for field_name in ("input_model", "output_model"):
            model = getattr(cls, field_name)
            if not (isinstance(model, type) and issubclass(model, BaseModel)):
                raise TypeError(
                    f"{cls.__name__}.{field_name} 必须是 pydantic.BaseModel 的子类，"
                    f"当前为 {model!r}"
                )

    def __repr__(self) -> str:
        """返回节点的可读调试表示，便于引擎日志追踪。

        Returns:
            str: 形如 ``<EchoNode node_type='echo' version='0.1.0'>`` 的字符串。
        """
        return (
            f"<{type(self).__name__} node_type={self.node_type!r} "
            f"version={self.version!r}>"
        )


class NodeParameterDescriptor(BaseModel):
    """节点参数的描述性元信息（供前端与扫描器做扩展展示使用）。

    本模型不参与运行时数据校验，仅用于在需要人工补充说明参数语义时，
    以统一的 Pydantic 结构承载描述信息，避免散落的字典字面量。

    Attributes:
        name (str): 参数名称。
        description (str): 参数的中文说明文本。
        required (bool): 该参数是否为必填项，默认 True。
        default (Optional[Any]): 参数的默认值（若存在）。
    """

    name: str = Field(..., description="参数名称")
    description: str = Field("", description="参数的中文说明文本")
    required: bool = Field(True, description="该参数是否为必填项")
    default: Optional[Any] = Field(None, description="参数的默认值")


# ----------------------------------------------------------------------
# 模块文档辅助
# ----------------------------------------------------------------------
#: 控制流节点的使用提示，随 ``get_docs()`` 一起导出。
_USAGE_HINTS: Dict[str, str] = {
    "branch": (
        "把待判断的值连到 value 输入，在 expression 里写条件（value 代表该输入值）。"
        "把 on_true / on_false 两个输出分别连到两条路径；"
        "未被选中的那条路径上的节点会被自动跳过。"
    ),
    "loop_start": (
        "与 loop_end 成对使用。把需要重复的节点放在两者之间，"
        "在 loop_start 填写重复次数 iterations（或提供 items 列表），"
        "引擎会依次执行循环体并在 loop_end 汇总每轮结果。"
        "支持嵌套：内层 loop_end 先关闭；循环体内可用 loop.indexes"
        "（由外到内的下标列表）与 loop.depth 区分层级。"
    ),
    "loop_end": (
        "循环终点，无需填写参数。输出的 results 是每次迭代的聚合结果，"
        "形如 [{index: 0, outputs: {...}}, ...]。"
    ),
}

#: 类型注解 -> 面向使用者的类型名。
_SIMPLE_TYPE_NAMES: Dict[Any, str] = {
    str: "文本（string）",
    int: "整数（integer）",
    float: "小数（number）",
    bool: "布尔（boolean）",
    list: "列表（array）",
    dict: "对象（object）",
    type(None): "空（null）",
}


def _type_label(annotation: Any) -> str:
    """把 Python 类型注解转成面向使用者的中文类型说明。

    Args:
        annotation (Any): 类型注解。

    Returns:
        str: 形如 ``"文本（string）"`` / ``"列表[文本]"`` 的可读类型名。
    """
    from typing import Union, get_args, get_origin

    if annotation is Any:
        return "任意类型（any）"

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Union:
        members = [a for a in args if a is not type(None)]
        names = " 或 ".join(_type_label(a) for a in members)
        if type(None) in args:
            return f"{names}，可为空"
        return names

    if origin in (list, tuple, set) and args:
        return f"列表[{_type_label(args[0])}]"

    if origin is dict and len(args) == 2:
        return f"对象（键为{_type_label(args[0])}，值为{_type_label(args[1])}）"

    if annotation in _SIMPLE_TYPE_NAMES:
        return _SIMPLE_TYPE_NAMES[annotation]

    if isinstance(annotation, type):
        # 自定义 Pydantic 模型：给出类名，便于使用者对照契约
        if issubclass(annotation, BaseModel):
            return f"对象（{annotation.__name__}）"
        return annotation.__name__

    return str(annotation)


def _describe_fields(model: Type[BaseModel]) -> list:
    """从 Pydantic 模型推导字段说明列表。

    Args:
        model (Type[BaseModel]): 待描述的模型类。

    Returns:
        list: 每项含 ``name`` / ``type`` / ``required`` / ``default`` /
            ``description`` 的字典。
    """
    fields = []
    for name, info in model.model_fields.items():
        default: Any = None
        if not info.is_required():
            default = info.get_default(call_default_factory=True)
            # 默认值可能是不可 JSON 序列化的对象，做一次保守转换。
            if not isinstance(default, (str, int, float, bool, list, dict, type(None))):
                default = str(default)

        fields.append(
            {
                "name": name,
                "type": _type_label(info.annotation),
                "required": info.is_required(),
                "default": default,
                "description": info.description or "",
            }
        )
    return fields
