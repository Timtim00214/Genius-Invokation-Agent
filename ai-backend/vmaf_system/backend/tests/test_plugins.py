# -*- coding: utf-8 -*-
"""
业务插件（``tools/*.py``）行为测试。

重点验证各节点的**安全围栏**：路径围栏、破坏性操作开关、体积上限、
协议白名单、工具名白名单、dry_run 演练等。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.plugin


# ======================================================================
# read_file
# ======================================================================
@pytest.mark.asyncio
async def test_read_file_reads_content(scanner, tmp_path):
    """读文件节点应正确读取内容并统计行数。"""
    target = tmp_path / "sample.txt"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    node = scanner.instantiate("read_file")
    model = scanner.get_node_class("read_file").input_model
    out = await node.execute(model(path=str(target)))

    assert out.line_count == 3
    assert out.total_lines == 3
    assert out.size_bytes > 0
    assert "alpha" in out.content
    assert out.truncated is False


@pytest.mark.asyncio
async def test_read_file_line_range(scanner, tmp_path):
    """按行区间读取时应返回切片内容并标记 truncated。"""
    target = tmp_path / "sample.txt"
    target.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")

    node = scanner.instantiate("read_file")
    model = scanner.get_node_class("read_file").input_model
    out = await node.execute(model(path=str(target), start_line=2, end_line=4))

    assert out.content == "b\nc\nd"
    assert out.line_count == 3
    assert out.total_lines == 5
    assert out.truncated is True


@pytest.mark.asyncio
async def test_read_file_missing_file_raises(scanner, tmp_path):
    """文件不存在时必须抛出具象异常，不得静默返回空。"""
    node = scanner.instantiate("read_file")
    model = scanner.get_node_class("read_file").input_model
    with pytest.raises(FileNotFoundError):
        await node.execute(model(path=str(tmp_path / "nope.txt")))


@pytest.mark.asyncio
async def test_read_file_rejects_directory(scanner, tmp_path):
    """目录路径必须被拒绝。"""
    node = scanner.instantiate("read_file")
    model = scanner.get_node_class("read_file").input_model
    with pytest.raises(IsADirectoryError):
        await node.execute(model(path=str(tmp_path)))


@pytest.mark.asyncio
async def test_read_file_size_limit(scanner, tmp_path):
    """超出体积上限时必须拒绝，防止撑爆内存总线。"""
    target = tmp_path / "big.txt"
    target.write_text("x" * 1000, encoding="utf-8")

    node = scanner.instantiate("read_file")
    model = scanner.get_node_class("read_file").input_model
    with pytest.raises(ValueError, match="超出上限"):
        await node.execute(model(path=str(target), max_bytes=10))


def test_read_file_end_line_before_start_rejected(scanner):
    """行号区间非法时应在校验期被拒。"""
    model = scanner.get_node_class("read_file").input_model
    with pytest.raises(ValidationError, match="end_line"):
        model(path="x", start_line=5, end_line=2)


# ======================================================================
# regex_extract
# ======================================================================
@pytest.mark.asyncio
async def test_regex_extract_findall(scanner):
    """findall 模式应返回全部匹配及捕获组。"""
    node = scanner.instantiate("regex_extract")
    model = scanner.get_node_class("regex_extract").input_model
    out = await node.execute(
        model(text="class Foo\nclass Bar\ndef baz", pattern=r"^class (\w+)",
              flags=["MULTILINE"])
    )

    assert out.count == 2
    assert [m.groups[0] for m in out.matches] == ["Foo", "Bar"]


@pytest.mark.asyncio
async def test_regex_extract_search_mode(scanner):
    """search 模式应只返回首个匹配。"""
    node = scanner.instantiate("regex_extract")
    model = scanner.get_node_class("regex_extract").input_model
    out = await node.execute(model(text="a1 b2 c3", pattern=r"\d", mode="search"))
    assert out.count == 1


@pytest.mark.asyncio
async def test_regex_extract_named_groups(scanner):
    """命名捕获组应被正确提取。"""
    node = scanner.instantiate("regex_extract")
    model = scanner.get_node_class("regex_extract").input_model
    out = await node.execute(
        model(text="id=42", pattern=r"id=(?P<value>\d+)")
    )
    assert out.matches[0].named_groups["value"] == "42"


@pytest.mark.asyncio
async def test_regex_extract_invalid_pattern_raises(scanner):
    """非法正则必须抛出具象异常，不得静默吞错。"""
    node = scanner.instantiate("regex_extract")
    model = scanner.get_node_class("regex_extract").input_model
    with pytest.raises(ValueError, match="非法的正则表达式"):
        await node.execute(model(text="abc", pattern=r"(unclosed"))


@pytest.mark.asyncio
async def test_regex_extract_limit(scanner):
    """超过 limit 时应截断并标记。"""
    node = scanner.instantiate("regex_extract")
    model = scanner.get_node_class("regex_extract").input_model
    out = await node.execute(model(text="a" * 50, pattern="a", limit=5))
    assert out.count == 5
    assert out.truncated is True


# ======================================================================
# llm_chat
# ======================================================================
@pytest.mark.asyncio
async def test_llm_chat_dry_run_without_key(scanner, Message):
    """未配置 api_key 时应进入 dry_run 模式，不发起真实请求。"""
    node = scanner.instantiate("llm_chat")
    model = scanner.get_node_class("llm_chat").input_model
    out = await node.execute(
        model(messages=[Message(role="user", content="你好")], model="gpt-4o-mini")
    )

    assert out.dry_run is True
    assert out.finish_reason == "dry_run"
    assert out.prompt_tokens > 0


@pytest.mark.asyncio
async def test_llm_chat_system_prompt_prepended(scanner, Message):
    """提供 system_prompt 时应被前置到消息列表。"""
    node = scanner.instantiate("llm_chat")
    model = scanner.get_node_class("llm_chat").input_model
    out = await node.execute(
        model(
            messages=[Message(role="user", content="hi")],
            system_prompt="你是助手",
        )
    )
    assert out.dry_run
    assert "2 条消息" in out.reply, "dry_run 回复应反映消息总数"


def test_llm_chat_requires_at_least_one_message(scanner):
    """消息列表不得为空。"""
    model = scanner.get_node_class("llm_chat").input_model
    with pytest.raises(ValidationError):
        model(messages=[])


def test_llm_chat_temperature_bounds(scanner, Message):
    """温度参数必须在合法区间内。"""
    model = scanner.get_node_class("llm_chat").input_model
    with pytest.raises(ValidationError):
        model(messages=[Message(role="user", content="hi")], temperature=5.0)


def _fake_chat_response(captured: dict, *, reply: str = "pong"):
    """构造一个假的 ``urlopen``，记录请求并返回最小合法响应。

    Args:
        captured (dict): 调用方传入的空字典，用于回传 url / headers / payload。
        reply (str): 假响应的回复正文。

    Returns:
        Callable[..., Any]: 可替换 ``urllib.request.urlopen`` 的函数。
    """

    class _FakeResponse:
        """最小化的 urlopen 响应上下文管理器。"""

        def __init__(self, body: dict) -> None:
            self._body = json.dumps(body, ensure_ascii=False).encode("utf-8")

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc_info) -> bool:
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = {
            name.lower(): value for name, value in request.header_items()
        }
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(
            {
                "model": "deepseek-flash",
                "choices": [
                    {"message": {"content": reply}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            }
        )

    return fake_urlopen


@pytest.mark.asyncio
async def test_llm_chat_extra_body_and_headers_passthrough(
    scanner, Message, monkeypatch
):
    """厂商专有参数应合并进请求体顶层，额外请求头应随请求发出。

    背景：不同厂商（尤其 OpenAI 兼容中转站）的可选参数并不一致。
    节点只保留稳定核心字段，其余通过 extra_body / extra_headers 透传，
    避免为每家厂商写死一套字段。
    """
    captured: dict = {}
    monkeypatch.setattr(
        urllib.request, "urlopen", _fake_chat_response(captured)
    )

    node = scanner.instantiate("llm_chat")
    model = scanner.get_node_class("llm_chat").input_model
    out = await node.execute(
        model(
            messages=[Message(role="user", content="你好")],
            model="deepseek-flash",
            api_base="https://api.deepseek.com",
            api_key="sk-test",
            extra_body={
                "reasoning_effort": "high",
                "thinking": {"type": "enabled"},
            },
            extra_headers={"X-Title": "vmaf"},
        )
    )

    assert out.dry_run is False
    assert out.reply == "pong"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    payload = captured["payload"]
    assert payload["reasoning_effort"] == "high"
    assert payload["thinking"] == {"type": "enabled"}
    # 未填写的可选消息字段不应序列化成 null：部分严格中转站会拒绝多余键。
    assert "name" not in payload["messages"][0]
    # 核心可选参数留空时不得下发，否则会撞上"中转站锁死字段"的问题。
    assert "temperature" not in payload
    assert "max_tokens" not in payload
    assert captured["headers"]["x-title"] == "vmaf"
    assert captured["headers"]["authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_llm_chat_accepts_full_endpoint(scanner, Message, monkeypatch):
    """api_base 填完整 .../chat/completions 时不得重复拼接路径。"""
    captured: dict = {}
    monkeypatch.setattr(
        urllib.request, "urlopen", _fake_chat_response(captured)
    )

    node = scanner.instantiate("llm_chat")
    model = scanner.get_node_class("llm_chat").input_model
    await node.execute(
        model(
            messages=[Message(role="user", content="hi")],
            api_base="https://relay.example.com/v1/chat/completions",
            api_key="sk-test",
        )
    )

    assert (
        captured["url"]
        == "https://relay.example.com/v1/chat/completions"
    )


@pytest.mark.asyncio
async def test_llm_chat_drop_params_wins_over_extra_body(
    scanner, Message, monkeypatch
):
    """drop_params 应在 extra_body 合并之后执行，最终请求体里不再包含该字段。

    背景：部分中转站不接受 temperature / top_p 等参数，直接发送会 400；
    用户可用 drop_params 显式抑制，而不必修改节点代码。
    """
    captured: dict = {}
    monkeypatch.setattr(
        urllib.request, "urlopen", _fake_chat_response(captured)
    )

    node = scanner.instantiate("llm_chat")
    model = scanner.get_node_class("llm_chat").input_model
    await node.execute(
        model(
            messages=[Message(role="user", content="hi")],
            api_key="sk-test",
            temperature=0.2,
            max_tokens=256,
            extra_body={"top_p": 0.9, "reasoning_effort": "low"},
            drop_params=["temperature", "top_p"],
        )
    )

    payload = captured["payload"]
    assert payload["max_tokens"] == 256
    assert payload["reasoning_effort"] == "low"
    assert "temperature" not in payload
    assert "top_p" not in payload


@pytest.mark.asyncio
async def test_llm_chat_authorization_header_disables_dry_run(
    scanner, Message, monkeypatch
):
    """只提供 Authorization 请求头（无 api_key）时也应发起真实请求。"""
    captured: dict = {}
    monkeypatch.setattr(
        urllib.request, "urlopen", _fake_chat_response(captured)
    )

    node = scanner.instantiate("llm_chat")
    model = scanner.get_node_class("llm_chat").input_model
    out = await node.execute(
        model(
            messages=[Message(role="user", content="hi")],
            extra_headers={"Authorization": "Bearer relay-token"},
        )
    )

    assert out.dry_run is False
    assert captured["headers"]["authorization"] == "Bearer relay-token"


@pytest.mark.asyncio
async def test_llm_chat_x_api_key_header_disables_dry_run(
    scanner, Message, monkeypatch
):
    """使用 X-API-Key 鉴权的中转站也应被识别为"已配置凭据"。"""
    captured: dict = {}
    monkeypatch.setattr(
        urllib.request, "urlopen", _fake_chat_response(captured)
    )

    node = scanner.instantiate("llm_chat")
    model = scanner.get_node_class("llm_chat").input_model
    out = await node.execute(
        model(
            messages=[Message(role="user", content="hi")],
            extra_headers={"X-API-Key": "relay-key"},
        )
    )

    assert out.dry_run is False
    assert captured["headers"]["x-api-key"] == "relay-key"


@pytest.mark.parametrize(
    "extra_body",
    [{"messages": []}, {"model": "hijacked"}, {"stream": True}],
)
def test_llm_chat_rejects_reserved_extra_body_keys(
    scanner, Message, extra_body
):
    """extra_body 不得覆盖 messages / model / stream 三个契约字段。"""
    model = scanner.get_node_class("llm_chat").input_model
    with pytest.raises(ValidationError, match="extra_body"):
        model(
            messages=[Message(role="user", content="hi")],
            extra_body=extra_body,
        )


@pytest.mark.parametrize("key", ["messages", "model", "stream"])
def test_llm_chat_rejects_dropping_reserved_keys(scanner, Message, key):
    """drop_params 不得删除请求体的必要结构。"""
    model = scanner.get_node_class("llm_chat").input_model
    with pytest.raises(ValidationError, match="drop_params"):
        model(
            messages=[Message(role="user", content="hi")],
            drop_params=[key],
        )


def test_llm_chat_rejects_unknown_top_level_param(scanner, Message):
    """顶层未知参数必须报错，而不是被 Pydantic 静默忽略。

    背景：Pydantic 默认 ``extra="ignore"``，拼错的厂商参数会"看似填写成功、
    实际没发出去"。这里用 ``extra="forbid"`` 强制走 extra_body。
    """
    model = scanner.get_node_class("llm_chat").input_model
    with pytest.raises(ValidationError, match="Extra inputs"):
        model(
            messages=[Message(role="user", content="hi")],
            reasoning_effort="high",
        )


def test_llm_chat_rejects_protected_headers(scanner, Message):
    """Content-Type 等协议保留头不得被 extra_headers 覆盖。"""
    model = scanner.get_node_class("llm_chat").input_model
    with pytest.raises(ValidationError, match="协议保留头"):
        model(
            messages=[Message(role="user", content="hi")],
            extra_headers={"Content-Type": "text/plain"},
        )


def test_llm_chat_rejects_header_newline_injection(scanner, Message):
    """请求头中不得出现换行符（防 HTTP 头注入）。"""
    model = scanner.get_node_class("llm_chat").input_model
    with pytest.raises(ValidationError, match="换行符"):
        model(
            messages=[Message(role="user", content="hi")],
            extra_headers={"X-Test": "a\r\nInjected: 1"},
        )


@pytest.mark.asyncio
async def test_llm_chat_http_error_includes_server_body(
    scanner, Message, monkeypatch
):
    """HTTP 错误信息应附带服务端响应正文，便于定位中转站拒因。"""
    detail = b'{"error":{"message":"temperature is not supported"}}'

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {},
            BytesIO(detail),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    node = scanner.instantiate("llm_chat")
    model = scanner.get_node_class("llm_chat").input_model
    with pytest.raises(RuntimeError, match="temperature is not supported"):
        await node.execute(
            model(
                messages=[Message(role="user", content="hi")],
                api_key="sk-test",
            )
        )


@pytest.mark.asyncio
async def test_llm_chat_http_200_error_object_surfaces_message(
    scanner, Message, monkeypatch
):
    """HTTP 200 但响应体带 error 时，应抛出服务端给出的真实原因。

    背景：部分中转站把"参数不支持"包装成 200 + {"error": {...}}，
    原实现只会报"缺少 choices"，掩盖了真正需要用户处理的字段问题。
    """

    class _FakeResponse:
        def read(self) -> bytes:
            return json.dumps(
                {"error": {"message": "reasoning_effort is not allowed"}}
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc_info) -> bool:
            return False

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None: _FakeResponse()
    )

    node = scanner.instantiate("llm_chat")
    model = scanner.get_node_class("llm_chat").input_model
    with pytest.raises(RuntimeError, match="reasoning_effort is not allowed"):
        await node.execute(
            model(
                messages=[Message(role="user", content="hi")],
                api_key="sk-test",
            )
        )


@pytest.mark.asyncio
async def test_llm_chat_dry_run_lists_payload_fields(scanner, Message):
    """演练模式应列出最终将发送的字段，便于无密钥时核对透传结果。"""
    node = scanner.instantiate("llm_chat")
    model = scanner.get_node_class("llm_chat").input_model
    out = await node.execute(
        model(
            messages=[Message(role="user", content="你好")],
            extra_body={"reasoning_effort": "high"},
        )
    )

    assert out.dry_run is True
    assert "reasoning_effort" in out.reply
    assert "将发送字段" in out.reply


# ======================================================================
# http_fetch
# ======================================================================
def test_http_fetch_rejects_non_http_scheme(scanner):
    """非 http/https 协议必须在校验期被拒（协议白名单）。"""
    model = scanner.get_node_class("http_fetch").input_model
    with pytest.raises(ValidationError, match="仅允许"):
        model(url="file:///etc/passwd")


def test_http_fetch_method_whitelist(scanner):
    """只允许 GET 与 POST 方法。"""
    model = scanner.get_node_class("http_fetch").input_model
    with pytest.raises(ValidationError):
        model(url="https://example.com", method="DELETE")


# ======================================================================
# context_compress
# ======================================================================
@pytest.mark.asyncio
async def test_context_compress_folds_old_turns(scanner, Message):
    """超长历史应被折叠为摘要 + 最近若干轮。"""
    node = scanner.instantiate("context_compress")
    model = scanner.get_node_class("context_compress").input_model

    messages = [Message(role="system", content="你是助手")] + [
        Message(role="user" if i % 2 == 0 else "assistant", content=f"第{i}条")
        for i in range(12)
    ]
    out = await node.execute(model(messages=messages, max_messages=8, keep_last_turns=2))

    assert out.compressed is True
    assert out.compressed_count <= 8
    assert out.dropped_count > 0
    # system 消息必须保留
    assert out.messages[0].content == "你是助手"


@pytest.mark.asyncio
async def test_context_compress_preserves_system(scanner, Message):
    """所有 system 消息都必须被保留。"""
    node = scanner.instantiate("context_compress")
    model = scanner.get_node_class("context_compress").input_model

    messages = [
        Message(role="system", content="规则一"),
        Message(role="system", content="规则二"),
        Message(role="user", content="问题"),
        Message(role="assistant", content="回答"),
    ] * 3
    out = await node.execute(model(messages=messages, max_messages=5, keep_last_turns=1))

    system_left = [m.content for m in out.messages if m.role == "system"]
    assert "规则一" in system_left
    assert "规则二" in system_left


@pytest.mark.asyncio
async def test_context_compress_no_compression_when_short(scanner, Message):
    """历史很短时不应发生压缩。"""
    node = scanner.instantiate("context_compress")
    model = scanner.get_node_class("context_compress").input_model

    messages = [Message(role="user", content="hi"), Message(role="assistant", content="hello")]
    out = await node.execute(model(messages=messages))
    assert out.compressed is False
    assert out.dropped_count == 0


# ======================================================================
# tool_use
# ======================================================================
@pytest.mark.asyncio
async def test_tool_use_parses_json_block(scanner):
    """应能从 ```json 块中解析出工具调用意图。"""
    node = scanner.instantiate("tool_use")
    model = scanner.get_node_class("tool_use").input_model

    out = await node.execute(
        model(
            model_output='我来帮你改文件。\n```json\n{"name": "edit_file", "arguments": {"path": "a.txt"}}\n```\n完成。',
            available_tools=["edit_file", "http_fetch"],
        )
    )
    assert out.has_calls
    assert out.calls[0].name == "edit_file"
    assert out.calls[0].arguments == {"path": "a.txt"}
    # 纯文本应剔除调用段
    assert "```json" not in out.plain_text


@pytest.mark.asyncio
async def test_tool_use_rejects_unknown_tool(scanner):
    """白名单之外的工具名必须被拒绝（防止模型越权调用）。"""
    node = scanner.instantiate("tool_use")
    model = scanner.get_node_class("tool_use").input_model

    with pytest.raises(ValueError, match="未注册工具"):
        await node.execute(
            model(
                model_output='```json\n{"name": "rm_rf"}\n```',
                available_tools=["edit_file"],
            )
        )


@pytest.mark.asyncio
async def test_tool_use_allow_unknown_records_rejected(scanner):
    """容忍模式下，未知工具应被记录但不抛异常。"""
    node = scanner.instantiate("tool_use")
    model = scanner.get_node_class("tool_use").input_model

    out = await node.execute(
        model(
            model_output='```json\n{"name": "rm_rf"}\n```',
            available_tools=["edit_file"],
            allow_unknown=True,
        )
    )
    assert "rm_rf" in out.rejected
    assert out.count == 0


@pytest.mark.asyncio
async def test_tool_use_no_calls_returns_plain_text(scanner):
    """无工具调用时应原样返回纯文本。"""
    node = scanner.instantiate("tool_use")
    model = scanner.get_node_class("tool_use").input_model

    out = await node.execute(model(model_output="这是一段普通回复。"))
    assert not out.has_calls
    assert out.plain_text == "这是一段普通回复。"


# ======================================================================
# edit_file（副作用节点，必须重点验证围栏）
# ======================================================================
def test_edit_file_overwrite_requires_explicit_switch(scanner):
    """overwrite 是破坏性操作，必须显式开启开关。"""
    model = scanner.get_node_class("edit_file").input_model
    with pytest.raises(ValidationError, match="allow_overwrite"):
        model(path="x.txt", mode="overwrite", content="boom")


def test_edit_file_replace_requires_old_text(scanner):
    """replace 模式必须提供 old_text。"""
    model = scanner.get_node_class("edit_file").input_model
    with pytest.raises(ValidationError, match="old_text"):
        model(path="x.txt", mode="replace", content="new")


def test_edit_file_insert_line_requires_line_number(scanner):
    """insert_line 模式必须提供行号。"""
    model = scanner.get_node_class("edit_file").input_model
    with pytest.raises(ValidationError, match="line_number"):
        model(path="x.txt", mode="insert_line", content="new")


@pytest.mark.asyncio
async def test_edit_file_dry_run_does_not_write(scanner, tmp_path):
    """dry_run 模式下不得落盘（演练不产生副作用）。"""
    target = tmp_path / "demo.txt"
    original = "alpha\nbeta\ngamma\n"
    target.write_text(original, encoding="utf-8")

    node = scanner.instantiate("edit_file")
    model = scanner.get_node_class("edit_file").input_model
    out = await node.execute(
        model(path=str(target), mode="replace", old_text="beta",
              content="BETA", dry_run=True)
    )

    assert out.dry_run is True
    assert out.replacements == 1
    assert out.changed is True
    # 关键：磁盘内容必须保持不变
    assert target.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_edit_file_replace_applies(scanner, tmp_path):
    """真实写入应生效并报告字节数变化。"""
    target = tmp_path / "demo.txt"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    node = scanner.instantiate("edit_file")
    model = scanner.get_node_class("edit_file").input_model
    out = await node.execute(
        model(path=str(target), mode="replace", old_text="beta", content="BETA")
    )

    assert out.changed is True
    assert target.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"


@pytest.mark.asyncio
async def test_edit_file_replace_miss_raises(scanner, tmp_path):
    """replace 未命中时必须抛异常，不得静默跳过。"""
    target = tmp_path / "demo.txt"
    target.write_text("alpha\n", encoding="utf-8")

    node = scanner.instantiate("edit_file")
    model = scanner.get_node_class("edit_file").input_model
    with pytest.raises(ValueError, match="找到目标片段"):
        await node.execute(
            model(path=str(target), mode="replace", old_text="nope", content="x")
        )


@pytest.mark.asyncio
async def test_edit_file_path_fence(scanner, tmp_path):
    """越出 allow_root 的路径必须被拒绝（路径围栏）。"""
    outside = tmp_path / "outside.txt"

    node = scanner.instantiate("edit_file")
    model = scanner.get_node_class("edit_file").input_model
    with pytest.raises(PermissionError, match="越出"):
        await node.execute(
            model(
                path=str(outside),
                mode="append",
                content="x",
                allow_root=str(tmp_path / "sandbox"),
            )
        )


@pytest.mark.asyncio
async def test_edit_file_insert_line(scanner, tmp_path):
    """insert_line 应在指定行号处插入内容。"""
    target = tmp_path / "demo.txt"
    target.write_text("alpha\ngamma\n", encoding="utf-8")

    node = scanner.instantiate("edit_file")
    model = scanner.get_node_class("edit_file").input_model
    await node.execute(
        model(path=str(target), mode="insert_line", content="BETA", line_number=2)
    )
    assert target.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"


@pytest.mark.asyncio
async def test_edit_file_missing_file_without_create(scanner, tmp_path):
    """文件不存在且 create=False 时必须抛异常。"""
    node = scanner.instantiate("edit_file")
    model = scanner.get_node_class("edit_file").input_model
    with pytest.raises(FileNotFoundError):
        await node.execute(
            model(path=str(tmp_path / "new.txt"), mode="append",
                  content="x", create=False)
        )
