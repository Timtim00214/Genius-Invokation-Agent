# -*- coding: utf-8 -*-
"""
统一日志（``utils/logger.py``）测试。

覆盖：UTF-8 编码输出（中文不乱码）、幂等保护、force 重装、
格式统一、非常规流的包装降级。
"""

from __future__ import annotations

import io
import logging

import pytest

pytestmark = pytest.mark.logger


@pytest.fixture
def logger_module(backend_utils):
    """加载统一日志模块。"""
    return backend_utils("logger")


def _reset_flag(logger_module):
    """测试前后复位幂等标记，避免用例之间互相污染。"""
    old = logger_module._LOGGING_READY
    logger_module._LOGGING_READY = False
    yield
    logger_module._LOGGING_READY = old


# ----------------------------------------------------------------------
# 核心：中文以 UTF-8 输出
# ----------------------------------------------------------------------
def test_chinese_log_writes_utf8(logger_module):
    """中文日志经 setup_logging 的 handler 后必须是合法 UTF-8 文本。

    缺陷背景：Windows 控制台编码常为 GBK，StreamHandler 默认跟随流的
    编码，中文（如"扫描 10 个文件"）会被替换成乱码。setup_logging 必须
    显式让流以 UTF-8 输出。
    """
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="gbk")
    logger_module.setup_logging(logging.INFO, force=True, stream=stream)

    logging.getLogger("vmaf.test").warning("扫描 10 个文件：新注册 1")

    stream.flush()
    raw = buffer.getvalue()
    # 经 GBK 流写出却拿到合法 UTF-8 的中文，证明编码被重配为 UTF-8。
    text = raw.decode("utf-8")
    assert "扫描 10 个文件：新注册 1" in text


def test_log_format_contains_level_and_name(logger_module):
    """统一格式的日志行应包含时间、级别、logger 名与消息。"""
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="utf-8")
    logger_module.setup_logging(logging.INFO, force=True, stream=stream)

    logging.getLogger("vmaf.fmt").info("格式探针")

    text = buffer.getvalue().decode("utf-8")
    assert "[INFO]" in text
    assert "vmaf.fmt" in text
    assert "格式探针" in text


# ----------------------------------------------------------------------
# 幂等与重装
# ----------------------------------------------------------------------
def test_setup_logging_idempotent(logger_module):
    """重复调用不得重复安装 handler（否则每行日志双份输出）。"""
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="utf-8")
    logger_module.setup_logging(logging.INFO, force=True, stream=stream)

    before = len(logging.getLogger().handlers)
    logger_module.setup_logging(logging.INFO)  # 无 force，应命中幂等
    after = len(logging.getLogger().handlers)

    assert after == before, "幂等保护失效：setup_logging 重复安装了 handler"


def test_setup_logging_force_reinstalls(logger_module):
    """force=True 时应清掉旧 handler 重装，且只保留一份。"""
    logging.getLogger().addHandler(logging.NullHandler())

    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="utf-8")
    logger_module.setup_logging(logging.INFO, force=True, stream=stream)

    handlers = logging.getLogger().handlers
    stream_handlers = [h for h in handlers if isinstance(h, logging.StreamHandler)]
    null_handlers = [h for h in handlers if isinstance(h, logging.NullHandler)]
    assert len(stream_handlers) == 1, "重装后应只有 1 个 StreamHandler"
    assert not null_handlers, "重装应清掉外部挂的 handler"


def test_log_level_applied(logger_module):
    """DEBUG 日志在 INFO 级别下不得输出，调级后应输出。"""
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="utf-8")
    logger_module.setup_logging(logging.INFO, force=True, stream=stream)
    logging.getLogger("vmaf.lvl").debug("不应出现")
    assert "不应出现" not in buffer.getvalue().decode("utf-8")

    logger_module.setup_logging(logging.DEBUG, force=True, stream=stream)
    logging.getLogger("vmaf.lvl").debug("应当出现")
    assert "应当出现" in buffer.getvalue().decode("utf-8")


# ----------------------------------------------------------------------
# 降级路径：不可 reconfigure 的流
# ----------------------------------------------------------------------
def test_fallback_wraps_unreconfigurable_stream(logger_module):
    """流不支持 reconfigure 时应包一层 UTF-8 wrapper，而非原样透传。"""
    import io as _io

    class EncodedLike:  # 模拟 pytest 的 EncodedFile：无 reconfigure
        def __init__(self):
            self.buffer = _io.BytesIO()

        def write(self, s):
            return len(s)

        def flush(self):
            pass

    odd = EncodedLike()
    logger_module.setup_logging(logging.INFO, force=True, stream=odd)

    # handler 持有的流应能正常写中文（编码错误也不会炸）。
    stream = logging.getLogger().handlers[0].stream
    stream.write("中文降级路径\n")
    stream.flush()
    assert "中文降级路径".encode("utf-8") in odd.buffer.getvalue()


@pytest.fixture(autouse=True)
def _cleanup_root_handlers():
    """每个用例结束后清空 root handlers，避免污染其它测试的日志输出。"""
    root = logging.getLogger()
    before = list(root.handlers)
    before_level = root.level
    yield
    for handler in root.handlers:
        if handler not in before:
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # noqa: BLE001
                pass
    root.setLevel(before_level)
