# -*- coding: utf-8 -*-
"""
【输入/模型配置】模型参数档案（Model Profile）。

把"某个模型服务怎么调"这件事（地址 / 密钥 / 模型名 / 采样参数 /
厂商逃生舱）沉淀成一个可复用的配置节点：在画布上把本节点的输出连到
``llm_chat`` 的同名字段上，chat 节点就不再需要逐个手填参数。

设计红线:
    - 纯函数：本节点不读环境变量、不落盘、不访问网络，只做
      "静态参数 -> 结构化输出" 的转换；密钥只随数据总线在本蓝图内流转。
    - 类型对齐：输出模型的字段名与类型与 ``llm_chat`` 的输入契约一一对应，
      保证连线类型预检通过（``api_key`` 为 ``Optional[str]``，
      ``extra_body`` / ``extra_headers`` / ``drop_params`` 的容器形状一致）。
    - 密钥不回显：输出中的 ``api_key`` 原样传递（总线内流转），但
      ``mask`` 输出提供打码摘要（如 ``sk-***abcd``），供前端展示
      "已配置哪个密钥"而不暴露全文。

使用方式:
    在模型档案节点上填好参数，把输出连到 ``llm_chat`` 的对应输入字段::

        model_profile.api_base  ->  llm_chat.api_base
        model_profile.api_key   ->  llm_chat.api_key
        model_profile.model     ->  llm_chat.model
        ...（连线值优先于 chat 节点的静态参数，接线即生效）

    未连线的字段由 ``llm_chat`` 自己的默认值兜底，因此可以只接需要的
    几条线（例如只接 api_base + api_key，模型名仍在 chat 上填）。

契约:
    输入 :class:`ModelProfileInput` / 输出 :class:`ModelProfileOutput`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from ..base import BaseNode

__all__ = ["ModelProfileInput", "ModelProfileOutput", "ModelProfileNode"]


class ModelProfileInput(BaseModel):
    """模型参数档案的输入契约（即"要沉淀的配置"本身）。

    字段语义与 ``llm_chat.ChatInput`` 的对应字段保持一致，此处不再重复
    展开说明，只说明档案节点特有的差异点。

    Attributes:
        profile_name (str): 档案名称（如 ``deepseek-main``），仅用于区分。
        api_base (str): 服务地址，与 ChatInput.api_base 同形。
        api_key (Optional[str]): 访问凭据；留空时下游 llm_chat 保持其
            dry_run 演练行为。
        model (str): 模型标识。
        system_prompt (Optional[str]): 可选的默认系统提示词；留空则不下发，
            下游 chat 节点保留自己的 system_prompt。
        temperature (Optional[float]): 采样温度；留空则不下发。
        max_tokens (Optional[int]): 回复 token 上限；留空则不下发。
        timeout_seconds (float): 请求超时秒数。
        extra_body (Dict[str, Any]): 厂商专有请求参数（透传给 llm_chat）。
        extra_headers (Dict[str, str]): 额外 HTTP 请求头（透传给 llm_chat）。
        drop_params (List[str]): 需要从请求体移除的字段名（透传给 llm_chat）。
    """

    model_config = {"extra": "forbid"}

    profile_name: str = Field(
        "default",
        description="档案名称，仅用于在画布与输出里区分不同配置，例如 deepseek-main",
    )
    api_base: str = Field(
        "https://api.openai.com/v1",
        description=(
            "兼容 OpenAI Chat Completions 协议的服务地址，例如 "
            "https://api.deepseek.com 或 https://api.deepseek.com/v1"
        ),
    )
    api_key: Optional[str] = Field(
        None,
        description=(
            "访问凭据（Bearer Token）。留空则下游对话节点进入演练模式"
            "（不发起真实请求）；此值只在本蓝图的执行总线内流转"
        ),
    )
    model: str = Field(
        "gpt-4o-mini",
        description="模型标识，例如 gpt-4o-mini、deepseek-chat、deepseek-flash",
    )
    system_prompt: Optional[str] = Field(
        None,
        description=(
            "默认系统提示词。留空则不接线，下游对话节点使用自己的"
            " system_prompt 参数"
        ),
    )
    temperature: Optional[float] = Field(
        None,
        ge=0.0,
        le=2.0,
        description="采样温度 0~2；留空表示不下发，由服务端默认值决定",
    )
    max_tokens: Optional[int] = Field(
        None,
        ge=1,
        le=200000,
        description="单次回复最大 token 数；留空表示不下发，由服务端默认值决定",
    )
    timeout_seconds: float = Field(
        60.0, gt=0, le=600, description="下游对话节点发起请求的超时秒数"
    )
    extra_body: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "厂商专有请求参数（JSON 对象），原样透传给对话节点的 extra_body，"
            "例如 DeepSeek 的 {\"reasoning_effort\": \"high\"}"
        ),
    )
    extra_headers: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "额外 HTTP 请求头（JSON 对象），原样透传给对话节点的 extra_headers"
        ),
    )
    drop_params: List[str] = Field(
        default_factory=list,
        description=(
            "要从请求体移除的字段名（每行一个），原样透传给对话节点的 drop_params"
        ),
    )

    @model_validator(mode="after")
    def _check_headers(self) -> "ModelProfileInput":
        """提前拦截明显非法的 extra_headers，把错误暴露在配置节点上。

        Returns:
            ModelProfileInput: 通过校验的实例自身。

        Raises:
            ValueError: 请求头名称为空或包含换行符时抛出。
        """
        for name, value in self.extra_headers.items():
            if not name.strip():
                raise ValueError("extra_headers 的请求头名称不能为空")
            if any(ch in name or ch in value for ch in "\r\n"):
                raise ValueError(f"extra_headers 中的 {name!r} 不能包含换行符")
        return self


class ModelProfileOutput(BaseModel):
    """模型参数档案的输出契约。

    字段名与类型与 ``llm_chat.ChatInput`` 的对应字段**逐一相同**，
    因此每条 ``profile -> chat`` 连线都能通过解析器的类型预检。

    Attributes:
        profile_name (str): 档案名称（原样回显，便于确认连线接对了）。
        api_base (str): 服务地址。
        api_key (Optional[str]): 访问凭据（未配置时为 None，不产出）。
        model (str): 模型标识。
        system_prompt (Optional[str]): 默认系统提示词（未配置时为 None）。
        temperature (Optional[float]): 采样温度（未配置时为 None）。
        max_tokens (Optional[int]): token 上限（未配置时为 None）。
        timeout_seconds (float): 超时秒数。
        extra_body (Dict[str, Any]): 厂商专有请求参数。
        extra_headers (Dict[str, str]): 额外请求头。
        drop_params (List[str]): 待移除字段名列表。
        mask (str): 密钥打码摘要，供展示而不暴露全文。
        ready (bool): 是否配置了凭据（False 时下游将以演练模式运行）。
    """

    model_config = {"extra": "forbid"}

    profile_name: str = Field(..., description="档案名称（原样回显）")
    api_base: str = Field(..., description="服务地址（透传给对话节点）")
    api_key: Optional[str] = Field(
        None, description="访问凭据（透传给对话节点；未配置时为空，不产出该字段值）"
    )
    model: str = Field(..., description="模型标识（透传给对话节点）")
    system_prompt: Optional[str] = Field(
        None, description="默认系统提示词（未配置时为空，不产出该字段值）"
    )
    temperature: Optional[float] = Field(
        None, description="采样温度（未配置时为空，不产出该字段值）"
    )
    max_tokens: Optional[int] = Field(
        None, description="单次回复 token 上限（未配置时为空，不产出该字段值）"
    )
    timeout_seconds: float = Field(..., description="请求超时秒数")
    extra_body: Dict[str, Any] = Field(
        default_factory=dict, description="厂商专有请求参数（透传）"
    )
    extra_headers: Dict[str, str] = Field(
        default_factory=dict, description="额外 HTTP 请求头（透传）"
    )
    drop_params: List[str] = Field(
        default_factory=list, description="待移除字段名（透传）"
    )
    mask: str = Field(..., description="密钥打码摘要，例如 sk-***abcd")
    ready: bool = Field(
        False, description="是否已配置凭据；False 时下游对话节点将以演练模式运行"
    )


def _mask_key(api_key: Optional[str]) -> str:
    """生成密钥的打码摘要，只暴露末 4 位（不足则全部打码）。

    Args:
        api_key (Optional[str]): 原始密钥；可为空。

    Returns:
        str: 形如 ``"sk-***abcd"`` 的摘要；未配置时返回 ``"(未配置)"``。
    """
    if not api_key:
        return "(未配置)"
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:3]}***{api_key[-4:]}"


class ModelProfileNode(BaseNode):
    """模型参数档案节点：把一组模型调用配置结构化，供下游 chat 节点连线复用。

    node_type: ``model_profile``
    """

    node_type: str = "model_profile"
    description: str = (
        "【输入】模型参数档案：沉淀某模型服务的地址/密钥/模型名/采样参数，"
        "连线到对话节点后无需再逐项手填"
    )
    version: str = "0.1.0"
    input_model: type = ModelProfileInput
    output_model: type = ModelProfileOutput

    async def execute(
        self, inputs: ModelProfileInput, context: Optional[Dict[str, Any]] = None
    ) -> ModelProfileOutput:
        """把静态配置转为结构化输出。

        Args:
            inputs (ModelProfileInput): 已校验的档案配置。
            context (Optional[Dict[str, Any]]): 运行时上下文（本节点未使用）。

        Returns:
            ModelProfileOutput: 与 ``llm_chat`` 输入字段一一对应的配置包。
        """
        return ModelProfileOutput(
            profile_name=inputs.profile_name,
            api_base=inputs.api_base,
            api_key=inputs.api_key,
            model=inputs.model,
            system_prompt=inputs.system_prompt,
            temperature=inputs.temperature,
            max_tokens=inputs.max_tokens,
            timeout_seconds=inputs.timeout_seconds,
            extra_body=dict(inputs.extra_body),
            extra_headers=dict(inputs.extra_headers),
            drop_params=list(inputs.drop_params),
            mask=_mask_key(inputs.api_key),
            ready=bool(inputs.api_key),
        )
