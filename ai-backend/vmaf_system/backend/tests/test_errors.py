# -*- coding: utf-8 -*-
"""
异常结构化模块（``utils/errors.py``）专项测试。

此前该模块仅有间接覆盖（engine / api 的测试顺带用到）。
本文件补齐直接专项：原生异常归类、VmafError 上下文填充优先级、
ISSUE_CODE_MAP 全键翻译、retryable 语义、format_native_exception。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def errors_mod(backend_utils):
    """加载被测模块（会话级复用，与生产同一路径加载）。"""
    return backend_utils("errors")


# ======================================================================
# 原生异常归类（_NATIVE_MAP）
# ======================================================================
class TestNativeClassification:
    """常见原生异常必须映射到正确错误码与语义分类。"""

    @pytest.mark.parametrize(
        ("exc", "code", "category"),
        [
            (FileNotFoundError("文件不存在: x.txt"), "runtime.file_not_found", "not_found"),
            (IsADirectoryError("是个目录"), "runtime.is_a_directory", "validation"),
            (PermissionError("拒绝访问"), "runtime.permission_denied", "permission"),
            (TimeoutError("超时了"), "runtime.timeout", "timeout"),
            (ValueError("非法参数"), "runtime.invalid_argument", "validation"),
            (TypeError("类型错了"), "runtime.invalid_argument", "validation"),
        ],
    )
    def test_native_map(self, errors_mod, exc, code, category):
        detail = errors_mod.to_error_detail(exc)
        assert detail.code == code
        assert detail.category == category
        assert detail.message, "message 不得为空"

    def test_retryable_semantics(self, errors_mod):
        """网络/超时可重试，其余不可重试。"""
        assert errors_mod.to_error_detail(TimeoutError("t")).retryable is True
        assert errors_mod.to_error_detail(FileNotFoundError("f")).retryable is False

    def test_unknown_exception_falls_back_to_internal(self, errors_mod):
        """未归类异常必须落到 internal.unknown，不得抛 KeyError。"""
        class WeirdError(Exception):
            pass

        detail = errors_mod.to_error_detail(WeirdError("怪异失败"))
        assert detail.code == "internal.unknown"
        assert detail.category == "internal"
        assert detail.retryable is False
        assert "怪异失败" in detail.message


# ======================================================================
# 类名关键字识别（urllib 等库的异常）
# ======================================================================
class TestHintClassification:
    """异常类名含网络/超时关键字时按对应类别归类。"""

    @pytest.mark.parametrize(
        ("name", "expected_code"),
        [
            ("URLError", "runtime.network_error"),
            ("HTTPError", "runtime.network_error"),
            ("ConnectTimeout", "runtime.timeout"),  # 超时判定优先于网络
            ("ReadTimeout", "runtime.timeout"),
            ("TimedOut", "runtime.timeout"),
        ],
    )
    def test_name_hints(self, errors_mod, name, expected_code):
        # 动态构造类名命中关键字的异常（不继承任何映射过的基类）。
        exc_type = type(name, (Exception,), {})
        detail = errors_mod.to_error_detail(exc_type("x"))
        assert detail.code == expected_code

    def test_timeout_hint_beats_network_hint(self, errors_mod):
        """同时含 Timeout 与网络字样时，超时判定优先（对用户更有用）。"""
        exc_type = type("FooTimeoutBarConnection", (Exception,), {})
        detail = errors_mod.to_error_detail(exc_type("x"))
        assert detail.code == "runtime.timeout"


# ======================================================================
# VmafError 结构化信息
# ======================================================================
class TestVmafError:
    def test_to_detail_carries_all_context(self, errors_mod):
        """VmafError 的全部结构化字段必须完整落到 ErrorDetail。"""
        err = errors_mod.VmafError(
            "连线类型不兼容",
            code="blueprint.type_mismatch",
            category=errors_mod.ErrorCategory.VALIDATION,
            node_id="n1",
            node_type="llm_chat",
            edge_id="e9",
            field="text",
            detail="List[RegexMatchItem] -> str",
        )
        d = err.to_detail()
        assert d.code == "blueprint.type_mismatch"
        assert d.category == "validation"
        assert d.retryable is False
        assert (d.node_id, d.node_type, d.edge_id, d.field) == (
            "n1", "llm_chat", "e9", "text",
        )
        assert d.detail == "List[RegexMatchItem] -> str"

    def test_caller_context_fills_only_gaps(self, errors_mod):
        """调用方补充的上下文只填空缺字段，不得覆盖 VmafError 自带的。"""
        err = errors_mod.VmafError(
            "x",
            code="runtime.timeout",
            category=errors_mod.ErrorCategory.TIMEOUT,
            node_id="mine",
        )
        d = errors_mod.to_error_detail(err, node_id="caller", node_type="nt")
        assert d.node_id == "mine", "不得覆盖异常自带上下文"
        assert d.node_type == "nt", "空缺字段应由调用方补上"

    def test_retryable_from_network_category(self, errors_mod):
        """网络类 VmafError 的 retryable 必须为 True。"""
        err = errors_mod.VmafError(
            "连不上", code="runtime.network_error",
            category=errors_mod.ErrorCategory.NETWORK,
        )
        assert err.to_detail().retryable is True


# ======================================================================
# ISSUE_CODE_MAP 全键翻译
# ======================================================================
class TestIssueCodeMap:
    def test_all_parser_shortcodes_covered(self, errors_mod):
        """解析器全部短码必须有翻译，漏一个就会退化为 internal.unknown。"""
        known_shortcodes = {
            "CYCLE", "UNKNOWN_NODE_TYPE", "UNKNOWN_OUTPUT_FIELD",
            "UNKNOWN_INPUT_FIELD", "TYPE_MISMATCH", "MISSING_REQUIRED_INPUT",
            "NULLABLE_FLOW", "LOOP_UNPAIRED", "LOOP_NESTED", "LOOP_LEAK",
            "LOOP_AMBIGUOUS",
        }
        missing = known_shortcodes - set(errors_mod.ISSUE_CODE_MAP)
        assert not missing, f"以下短码缺翻译（会退化成 internal.unknown）: {missing}"

    def test_issue_to_error_detail_translation(self, errors_mod):
        """issue.code 必须翻译为长错误码，且带 issue 的定位信息。"""

        class FakeIssue:
            code = "TYPE_MISMATCH"
            message = "连线类型不兼容：A -> B"
            node_id = "n2"
            edge_id = "e1"
            field = "text"

        d = errors_mod.issue_to_error_detail(FakeIssue())
        assert d.code == "blueprint.type_mismatch"
        assert d.category == "validation"
        assert d.edge_id == "e1"
        assert d.field == "text"

    def test_unknown_shortcode_falls_back(self, errors_mod):
        class FakeIssue:
            code = "TOTALLY_NEW_CODE"
            message = "新问题"
            node_id = None
            edge_id = None
            field = None

        d = errors_mod.issue_to_error_detail(FakeIssue())
        assert d.code == "internal.unknown"


# ======================================================================
# 工具函数
# ======================================================================
class TestFormatHelpers:
    def test_format_native_exception_with_text(self, errors_mod):
        out = errors_mod.format_native_exception(ValueError("参数错"))
        assert out == "ValueError: 参数错"

    def test_format_native_exception_empty_text(self, errors_mod):
        out = errors_mod.format_native_exception(ValueError())
        assert out == "ValueError"

    def test_to_error_list_batch(self, errors_mod):
        out = errors_mod.to_error_list(
            [FileNotFoundError("a"), TimeoutError("b")], node_id="n3"
        )
        assert len(out) == 2
        assert out[0]["code"] == "runtime.file_not_found"
        assert out[1]["code"] == "runtime.timeout"
        assert all(item["node_id"] == "n3" for item in out)
