# -*- coding: utf-8 -*-
"""
【核心节点】对话（LLM Chat）。

架构图中的中心方块：一切能力（联网 / 读文件 / 正则 / RAG / 上下文压缩）
都围绕"对话"节点挂载。本节点负责把拼装好的消息列表发送给兼容
OpenAI Chat Completions 协议的服务端，并把回复规整为强类型输出。

设计红线:
    - 纯函数：不持有会话历史，历史完全由上游的上下文压缩节点
      或引擎的 Context Bus 注入。
    - 无外部硬依赖：仅使用标准库 ``urllib``，未安装 ``httpx`` /
      ``requests`` 亦可运行；无凭据时进入 ``dry_run`` 模式。
    - 稳定核心 + 显式逃生舱：``messages`` / ``model`` 由契约掌控；
      各厂商（尤其 OpenAI 兼容中转站）的专有参数走 ``extra_body``，
      额外请求头走 ``extra_headers``，不兼容字段可用 ``drop_params`` 抑制。
      不为每家厂商写一套字段，也不允许在顶层静默塞入未知参数。

契约:
    输入 :class:`ChatInput` / 输出 :class:`ChatOutput`。
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from ..base import BaseNode, Message

__all__ = ["ChatMessage", "ChatInput", "ChatOutput", "LLMChatNode"]

#: 复用契约层共享消息类型，保证与 context_compress 等节点可直连。
#: 保留 ChatMessage 名称仅为阅读便利，它与 Message 是同一个类对象。
ChatMessage = Message

#: 请求体中由契约完全掌控的字段。
#: 禁止通过 ``extra_body`` 覆盖，也禁止通过 ``drop_params`` 删除——
#: 否则"输入契约"与实际发出的请求就会脱节，前端展示的参数也会说谎。
_RESERVED_BODY_KEYS = frozenset({"messages", "model", "stream"})

#: 节点自己维护的协议级请求头（大小写不敏感），禁止被 ``extra_headers`` 覆盖。
_PROTECTED_HEADERS = frozenset({"content-type", "content-length", "host"})

#: 常见的中转站鉴权头。出现任意一个非空值即可视为"已配置凭据"，
#: 从而关闭 dry_run（有些中转站不用 Bearer Authorization）。
_AUTH_HEADER_NAMES = frozenset({"authorization", "x-api-key", "api-key"})


class ChatInput(BaseModel):
    """对话节点的输入契约。

    除若干稳定核心字段外，本节点还提供三个"逃生舱"字段，用于适配不同厂商
    （尤其是中转站）的差异：

        - ``extra_body``：厂商专有请求参数，原样合并进请求体顶层；
        - ``extra_headers``：额外 HTTP 请求头，可覆盖 Authorization；
        - ``drop_params``：从最终请求体中移除指定字段。

    契约结构保持强类型（``extra="forbid"``），厂商参数只能走显式声明的
    ``extra_body``，不允许在顶层随意塞字段——否则拼错的参数会被静默忽略。

    Attributes:
        messages (List[ChatMessage]): 待发送的完整消息列表（含历史）。
        model (str): 目标模型标识。
        system_prompt (Optional[str]): 系统提示词；提供时会被前置到消息列表。
        temperature (Optional[float]): 采样温度；留空则不下发该字段。
        max_tokens (Optional[int]): 回复 token 上限；留空则不下发该字段。
        api_base (str): 兼容 OpenAI 协议的服务端地址。
        api_key (Optional[str]): 访问凭据；为空且未提供 Authorization 头时进入 dry_run。
        timeout_seconds (float): 网络请求超时秒数。
        extra_body (Dict[str, Any]): 厂商专有请求参数（合并进请求体顶层）。
        extra_headers (Dict[str, str]): 额外 HTTP 请求头。
        drop_params (List[str]): 需要从最终请求体中移除的字段名。
    """

    model_config = {"extra": "forbid"}

    messages: List[Message] = Field(
        ...,
        min_length=1,
        description=(
            "对话消息列表，至少 1 条。每条包含 role（system / user / assistant / tool）"
            "与 content（纯文本正文）。通常由上游节点连线注入"
        ),
    )
    model: str = Field(
        "gpt-4o-mini",
        description=(
            "模型标识，必须与 api_base 指向的服务商一致，"
            "例如 gpt-4o-mini、deepseek-chat、deepseek-flash"
        ),
    )
    system_prompt: Optional[str] = Field(
        None, description="系统提示词（设定角色与规则）。填写后会自动插入为第一条 system 消息"
    )
    temperature: Optional[float] = Field(
        None,
        ge=0.0,
        le=2.0,
        description=(
            "采样温度，范围 0~2。越小输出越稳定，越大越发散；需要可复现结果时填 0。"
            "留空表示不发送该字段，由服务端默认值决定"
        ),
    )
    max_tokens: Optional[int] = Field(
        None,
        ge=1,
        le=200000,
        description=(
            "单次回复允许生成的最大 token 数。留空表示不发送该字段，"
            "由服务端默认值决定；中转站锁死该参数时可保持留空"
        ),
    )
    api_base: str = Field(
        "https://api.openai.com/v1",
        description=(
            "兼容 OpenAI Chat Completions 协议的服务地址。可填服务根"
            "（https://api.deepseek.com）、带版本（https://api.deepseek.com/v1），"
            "也可填完整的 .../v1/chat/completions；缺少时节点会自动补全 /chat/completions"
        ),
    )
    api_key: Optional[str] = Field(
        None,
        description=(
            "访问凭据（Bearer Token）。留空时进入演练模式：不发起真实网络请求，"
            "只返回占位回复。若中转站用自定义请求头鉴权，可在 extra_headers 里"
            "提供 Authorization / X-API-Key / api-key，此时同样会发起真实请求"
        ),
    )
    timeout_seconds: float = Field(
        60.0, gt=0, le=600, description="单次请求的超时时间（秒），超时会报错终止"
    )
    extra_body: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "厂商专有请求参数的 JSON 对象，会原样合并到请求体顶层（不是嵌套字段）。"
            "例如 DeepSeek 的 {\"reasoning_effort\": \"high\", "
            "\"thinking\": {\"type\": \"enabled\"}}。"
            "不得包含 messages / model / stream，这三个字段由节点契约掌控"
        ),
    )
    extra_headers: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "额外 HTTP 请求头的 JSON 对象，例如中转站要求的 {\"X-Title\": \"vmaf\"}。"
            "同名头会覆盖默认值（包括 Authorization，可用于自定义鉴权）；"
            "Content-Type / Content-Length / Host 为协议保留头，禁止覆盖"
        ),
    )
    drop_params: List[str] = Field(
        default_factory=list,
        description=(
            "要从最终请求体中移除的字段名，每行一个。"
            "用于中转站不接受某参数（例如 temperature）的场景：填 temperature 即可不发。"
            "不得移除 messages / model / stream"
        ),
    )

    @model_validator(mode="after")
    def _check_escape_hatches(self) -> "ChatInput":
        """校验三个逃生舱字段，防止它们破坏核心契约或协议头。

        Returns:
            ChatInput: 通过校验的实例自身。

        Raises:
            ValueError: ``extra_body`` / ``drop_params`` 触碰保留字段，
                或 ``extra_headers`` 覆盖协议保留头、包含换行符时抛出。
        """
        blocked_body = sorted(
            key for key in self.extra_body if key in _RESERVED_BODY_KEYS
        )
        if blocked_body:
            raise ValueError(
                "extra_body 不得覆盖契约字段："
                f"{'、'.join(blocked_body)}；这些字段请使用节点自身参数填写"
            )

        blocked_drop = sorted(
            key for key in self.drop_params if key in _RESERVED_BODY_KEYS
        )
        if blocked_drop:
            raise ValueError(
                "drop_params 不得移除契约字段："
                f"{'、'.join(blocked_drop)}；否则请求将缺少必要结构"
            )

        blocked_headers = sorted(
            key for key in self.extra_headers if key.lower() in _PROTECTED_HEADERS
        )
        if blocked_headers:
            raise ValueError(
                "extra_headers 不得覆盖协议保留头："
                f"{'、'.join(blocked_headers)}"
            )

        for name, value in self.extra_headers.items():
            if not name.strip():
                raise ValueError("extra_headers 的请求头名称不能为空")
            if any(ch in name or ch in value for ch in "\r\n"):
                raise ValueError(
                    f"extra_headers 中的 {name!r} 不能包含换行符"
                )

        return self


class ChatOutput(BaseModel):
    """对话节点的输出契约。

    Attributes:
        reply (str): 模型回复正文。
        finish_reason (str): 结束原因（如 ``stop`` / ``length`` / ``dry_run``）。
        model (str): 实际使用的模型标识。
        prompt_tokens (int): 提示词消耗的 token 估算值。
        completion_tokens (int): 回复消耗的 token 估算值。
        raw (Dict[str, Any]): 服务端原始响应（dry_run 时为空字典）。
        reasoning (str): 模型"思考过程"（DeepSeek 的 ``reasoning_content``）；普通模型为空。
        reasoning_tokens (int): 思考消耗的 token 数（服务端提供才有值）。
        dry_run (bool): 本次是否为演练模式（未发起真实请求）。
    """

    reply: str = Field(..., description="模型回复正文")
    finish_reason: str = Field("stop", description="结束原因")
    model: str = Field(..., description="实际使用的模型标识")
    prompt_tokens: int = Field(0, ge=0, description="提示词 token 估算值")
    completion_tokens: int = Field(0, ge=0, description="回复 token 估算值")
    raw: Dict[str, Any] = Field(
        default_factory=dict, description="服务端原始响应"
    )
    reasoning: str = Field(
        "",
        description=(
            "模型的思考过程（DeepSeek 系把思维链放在 reasoning_content）；"
            "不支持的模型为空字符串。想单独落盘/展示思维链时接这个字段，"
            "不要再去挖 raw"
        ),
    )
    reasoning_tokens: int = Field(
        0, ge=0, description="思考消耗的 token 数（服务端提供 usage 明细时才有值）"
    )
    dry_run: bool = Field(False, description="是否为演练模式（未发起真实请求）")


def _estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数量（本地启发式，不依赖分词器）。

    采用"中文按字符、英文按 4 字符/token"的混合启发式，
    仅用于配额估算展示，不作为计费依据。

    Args:
        text (str): 待估算的文本。

    Returns:
        int: 估算的 token 数（至少为 0）。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    rest = max(len(text) - cjk, 0)
    return cjk + rest // 4


def _build_payload(inputs: ChatInput) -> Dict[str, Any]:
    """按契约组装最终请求体，并应用厂商逃生舱。

    组装顺序（后写覆盖先写，删除最后执行）::

        核心字段 -> 可选字段（非 None 才写） -> extra_body 合并 -> drop_params 删除

    ``temperature`` / ``max_tokens`` 留空时不写入请求体，这是对中转站的
    重要兼容手段：不少中转站会拒绝或锁死这类参数，不发送才最安全。

    Args:
        inputs (ChatInput): 已通过校验的输入实例。

    Returns:
        Dict[str, Any]: 可直接 ``json.dumps`` 的请求体字典。
    """
    messages: List[Dict[str, str]] = []
    if inputs.system_prompt:
        messages.append({"role": "system", "content": inputs.system_prompt})
    messages.extend(msg.model_dump(exclude_none=True) for msg in inputs.messages)

    payload: Dict[str, Any] = {
        "model": inputs.model,
        "messages": messages,
    }
    if inputs.temperature is not None:
        payload["temperature"] = inputs.temperature
    if inputs.max_tokens is not None:
        payload["max_tokens"] = inputs.max_tokens

    # 厂商专有参数：与 OpenAI SDK 的 extra_body 语义一致，合并到请求体顶层。
    payload.update(inputs.extra_body)

    # 中转站明确的"不要传这个字段"：即使上游连线或 extra_body 给了也要移除。
    for key in inputs.drop_params:
        payload.pop(key, None)

    return payload


class LLMChatNode(BaseNode):
    """对话核心节点：调用 LLM 服务端完成一次对话补全。

    node_type: ``llm_chat``
    """

    node_type: str = "llm_chat"
    description: str = "【核心】对话节点：向兼容 OpenAI 协议的模型服务发起一次对话补全"
    version: str = "0.2.0"
    input_model: type = ChatInput
    output_model: type = ChatOutput

    async def execute(
        self, inputs: ChatInput, context: Optional[Dict[str, Any]] = None
    ) -> ChatOutput:
        """执行一次对话补全。

        当 ``api_key`` 为空、且 ``extra_headers`` 未提供常见鉴权头
        （Authorization / X-API-Key / api-key）时，进入 dry_run 模式：
        不发起网络请求，返回结构化的占位回复，并在回复里列出
        "最终会发送哪些字段"，便于排查中转站的字段兼容问题。

        Args:
            inputs (ChatInput): 已校验的输入实例。
            context (Optional[Dict[str, Any]]): 运行时上下文（本节点未使用）。

        Returns:
            ChatOutput: 结构化的回复结果。

        Raises:
            RuntimeError: 真实请求失败时抛出（网络错误或服务端非 2xx），
                错误信息会尽量附带服务端响应正文，便于定位中转站拒因。
        """
        payload = _build_payload(inputs)
        messages = payload["messages"]

        prompt_chars = sum(len(m["content"]) for m in messages)
        prompt_tokens = _estimate_tokens("".join(m["content"] for m in messages))

        extra_authorization = next(
            (
                value
                for name, value in inputs.extra_headers.items()
                if name.lower() in _AUTH_HEADER_NAMES and value.strip()
            ),
            None,
        )
        if not inputs.api_key and not extra_authorization:
            return ChatOutput(
                reply=(
                    f"[dry_run] 已接收 {len(messages)} 条消息（约 {prompt_chars} 字符），"
                    f"目标模型 {inputs.model}；将发送字段：{'、'.join(payload.keys())}。"
                    "配置 api_key（或在 extra_headers 提供 Authorization / "
                    "X-API-Key）后即可发起真实请求。"
                ),
                finish_reason="dry_run",
                model=inputs.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=0,
                raw={},
                dry_run=True,
            )

        headers = {"Content-Type": "application/json"}
        if inputs.api_key:
            headers["Authorization"] = f"Bearer {inputs.api_key}"
        # 额外请求头最后合并：允许中转站覆盖 Authorization，但协议保留头
        # 已在 ChatInput 的校验器里拦下。
        headers.update(inputs.extra_headers)

        url = inputs.api_base.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"

        # urllib 是阻塞的：直接在 async execute 里调用会把整个事件循环卡住，
        # 别的 /run（例如对局中同时跑的"回合复盘"）就只能排在这一次 LLM 调用后面。
        # 用 to_thread 把它挪到线程池，节点之间就能真并发。
        def _send() -> Dict[str, Any]:
            request = urllib.request.Request(
                url=url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(
                request, timeout=inputs.timeout_seconds
            ) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            body = await asyncio.to_thread(_send)
        except urllib.error.HTTPError as exc:
            # 把服务端响应正文带进错误信息：中转站的"字段不支持"说明通常在这里。
            try:
                detail = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:  # noqa: BLE001 - 读取失败不应掩盖原始 HTTP 错误
                detail = ""
            if len(detail) > 500:
                detail = detail[:500] + "…"
            suffix = f"；服务端响应：{detail}" if detail else ""
            raise RuntimeError(
                f"模型服务返回错误状态码 {exc.code}: {exc.reason}{suffix}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"无法连接模型服务 {inputs.api_base}: {exc}") from exc

        # 部分中转站会用 HTTP 200 返回错误对象；不识别的话只会报"缺少 choices"，
        # 把真正的拒因（如 "temperature is not supported"）藏起来。
        error = body.get("error")
        if error:
            detail = (
                error.get("message")
                if isinstance(error, dict)
                else str(error)
            )
            raise RuntimeError(f"模型服务返回错误：{detail or error}")

        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError("模型服务响应中缺少 choices 字段")

        message = choices[0].get("message", {})
        reply = message.get("content", "") or ""
        # 思考型模型（deepseek-flash / deepseek-reasoner）把思维链放在这里；
        # 普通 chat 模型没有该字段，取空串即可。
        reasoning = message.get("reasoning_content", "") or ""
        usage = body.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}

        return ChatOutput(
            reply=reply,
            finish_reason=choices[0].get("finish_reason", "stop"),
            model=body.get("model", inputs.model),
            prompt_tokens=int(usage.get("prompt_tokens", prompt_tokens)),
            completion_tokens=int(
                usage.get("completion_tokens", _estimate_tokens(reply))
            ),
            raw=body,
            reasoning=reasoning,
            reasoning_tokens=int(details.get("reasoning_tokens") or 0),
            dry_run=False,
        )
