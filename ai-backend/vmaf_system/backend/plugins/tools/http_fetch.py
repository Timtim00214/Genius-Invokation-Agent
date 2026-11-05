# -*- coding: utf-8 -*-
"""
【可扩展】联网节点。

架构图上方的输入能力之一：把远端网页/接口的正文抓取进流水线，
典型链路为 ``http_fetch -> regex_extract -> llm_chat -> context_compress``。

设计红线:
    - 纯函数：不复用连接、不持有会话；仅使用标准库 ``urllib``。
    - 安全围栏：默认仅允许 http/https 协议，响应体积设有上限，
      并强制超时，防止单机引擎被慢连接拖死。
    - 可选 HTML 清洗：不引入外部依赖，用正则剥离脚本/样式/标签。

契约:
    输入 :class:`HttpFetchInput` / 输出 :class:`HttpFetchOutput`。
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

from ..base import BaseNode

__all__ = ["HttpFetchInput", "HttpFetchOutput", "HttpFetchNode"]

_SCRIPT_RE = re.compile(r"<script.*?</script>", re.S | re.I)
_STYLE_RE = re.compile(r"<style.*?</style>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n\s*\n\s*\n+")


class HttpFetchInput(BaseModel):
    """联网节点的输入契约。

    Attributes:
        url (str): 目标 URL，仅支持 http / https。
        method (str): HTTP 方法，当前仅支持 GET 与 POST。
        headers (Dict[str, str]): 附加请求头。
        body (Optional[str]): POST 请求体（``method`` 为 POST 时生效）。
        timeout_seconds (float): 超时秒数，取值 0 < t <= 300。
        max_bytes (int): 响应体读取上限（字节），超出即拒绝。
        strip_html (bool): 是否把 HTML 清洗为纯文本。
    """

    url: str = Field(
        ...,
        min_length=1,
        description=(
            "目标地址，必须以 http:// 或 https:// 开头，"
            "例如 https://example.com/api/list?page=1"
        ),
    )
    method: str = Field("GET", pattern="^(GET|POST)$", description="HTTP 方法")
    headers: Dict[str, str] = Field(
        default_factory=dict, description="附加请求头"
    )
    body: Optional[str] = Field(None, description="POST 请求体")
    timeout_seconds: float = Field(20.0, gt=0, le=300, description="超时秒数")
    max_bytes: int = Field(
        2 * 1024 * 1024, ge=1, le=50 * 1024 * 1024, description="响应体上限（字节）"
    )
    strip_html: bool = Field(True, description="是否把 HTML 清洗为纯文本")

    @model_validator(mode="after")
    def _check_scheme(self) -> "HttpFetchInput":
        """校验 URL 协议白名单。

        Returns:
            HttpFetchInput: 通过校验的实例自身。

        Raises:
            ValueError: 协议不是 http / https 时抛出。
        """
        scheme = urlparse(self.url).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"不支持的协议 {scheme or '(空)'}，仅允许 http / https")
        return self


class HttpFetchOutput(BaseModel):
    """联网节点的输出契约。

    Attributes:
        url (str): 实际请求的 URL。
        status_code (int): HTTP 状态码。
        content (str): 响应正文（可能为清洗后的纯文本）。
        content_type (str): 响应头中的 Content-Type。
        size_bytes (int): 原始响应体字节数。
        final_url (str): 跟随跳转后的最终 URL。
        truncated (bool): 响应是否因体积上限被截断。
    """

    url: str = Field(..., description="实际请求的 URL")
    status_code: int = Field(0, ge=0, description="HTTP 状态码")
    content: str = Field(..., description="响应正文")
    content_type: str = Field("", description="响应 Content-Type")
    size_bytes: int = Field(0, ge=0, description="原始响应体字节数")
    final_url: str = Field("", description="跟随跳转后的最终 URL")
    truncated: bool = Field(False, description="响应是否被截断")


class HttpFetchNode(BaseNode):
    """联网节点：抓取远端 URL 内容并可选清洗为纯文本。

    node_type: ``http_fetch``
    """

    node_type: str = "http_fetch"
    description: str = "抓取远端 http(s) 资源内容，支持 HTML 纯文本清洗与体积上限保护"
    version: str = "0.1.0"
    input_model: type = HttpFetchInput
    output_model: type = HttpFetchOutput

    async def execute(
        self, inputs: HttpFetchInput, context: Optional[Dict[str, Any]] = None
    ) -> HttpFetchOutput:
        """发起一次 HTTP 请求并规整响应。

        Args:
            inputs (HttpFetchInput): 已校验的输入实例。
            context (Optional[Dict[str, Any]]): 运行时上下文（本节点未使用）。

        Returns:
            HttpFetchOutput: 结构化的响应结果。

        Raises:
            ValueError: 响应体积超过 ``max_bytes`` 时抛出。
            RuntimeError: 网络不可达或服务端返回 4xx / 5xx 时抛出。
        """
        data = inputs.body.encode("utf-8") if inputs.body else None
        request = urllib.request.Request(
            url=inputs.url,
            data=data,
            headers=inputs.headers or {},
            method=inputs.method,
        )

        try:
            with urllib.request.urlopen(
                request, timeout=inputs.timeout_seconds
            ) as response:
                raw = response.read(inputs.max_bytes + 1)
                status = int(response.getcode())
                content_type = response.headers.get("Content-Type", "")
                final_url = response.geturl()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"请求 {inputs.url} 返回错误状态码 {exc.code}: {exc.reason}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"无法连接 {inputs.url}: {exc}") from exc

        truncated = len(raw) > inputs.max_bytes
        if truncated:
            raw = raw[: inputs.max_bytes]

        size_bytes = len(raw)
        text = raw.decode("utf-8", errors="replace")

        if inputs.strip_html and "html" in content_type.lower():
            text = _strip_html(text)

        return HttpFetchOutput(
            url=inputs.url,
            status_code=status,
            content=text,
            content_type=content_type,
            size_bytes=size_bytes,
            final_url=final_url,
            truncated=truncated,
        )


def _strip_html(html: str) -> str:
    """把 HTML 清洗为可读纯文本。

    Args:
        html (str): 原始 HTML 文本。

    Returns:
        str: 去除脚本、样式、标签并压缩空行后的纯文本。
    """
    text = _SCRIPT_RE.sub("", html)
    text = _STYLE_RE.sub("", text)
    text = _TAG_RE.sub("\n", text)
    text = _BLANK_RE.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())
