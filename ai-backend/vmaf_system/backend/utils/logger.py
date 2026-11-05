# -*- coding: utf-8 -*-
"""
统一日志配置（解决 Windows 控制台中文乱码）。

背景缺陷：``logging.StreamHandler`` 默认使用 ``sys.stderr`` 的编码。
在 Windows 上控制台编码常为 GBK（cp936），而 VMAF 的日志消息几乎全是
中文，GBK 无法表示的字符会触发 ``UnicodeEncodeError``，或被
``errors='replace'`` 静默替换为 ``?`` / ``æ`` 之类的乱码（本服务
``/health`` 的 ``scan_summary`` 在 PowerShell 里就出现过乱码）。

修复策略：本模块提供 :func:`setup_logging`，为 root logger 安装
**显式 UTF-8** 的 StreamHandler：

    - ``sys.stdout`` / ``sys.stderr`` 是 ``io.TextIOWrapper`` 时，直接
      **重配其编码为 UTF-8**（``reconfigure``，Python 3.7+）——这比
      换一个自定义 handler 更彻底：print、uvicorn 输出同样受益；
    - 非常规流（如 pytest 捕获的 ``EncodedFile``）无法 reconfigure，
      退而求其次：包一层 UTF-8 的 ``TextIOWrapper`` 再交给 handler。

调用约定（红线）:
    - 仅在**进程入口**调用一次：``main.py`` 模块级已调用，其余代码
      禁止重复调用（幂等保护 ``_LOGGING_READY`` 防御二次配置）；
    - 测试环境不自动安装：``pytest`` 的 capsys 与全局 handler 互相干扰，
      测试通过夹具按需调用。
"""

from __future__ import annotations

import io
import logging
import sys
import types
from typing import List, Optional

__all__ = ["setup_logging", "LOG_FORMAT"]

#: 全工程统一的日志行格式（与旧 basicConfig 保持一致，方便对齐旧日志）。
LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

#: 进程级幂等标记：setup_logging 只允许真正生效一次。
_LOGGING_READY: bool = False


def _ensure_utf8_stream(stream: object) -> object:
    """确保流以 UTF-8 编码输出，必要时重配或包装。

    Args:
        stream (object): ``sys.stdout`` / ``sys.stderr`` 或其它文本流。

    Returns:
        object: 可安全输出中文的流对象（尽量原对象，退而求其次返回包装）。
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
            return stream
        except Exception:  # noqa: BLE001 - 极端平台不支持 reconfigure
            pass

    # 流不可重配（如 pytest 的 EncodedFile）：包一层 UTF-8 wrapper。
    try:
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            return io.TextIOWrapper(buffer, encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    return stream


def setup_logging(
    level: int = logging.INFO,
    *,
    force: bool = False,
    stream: Optional[object] = None,
) -> logging.Handler:
    """安装统一的 UTF-8 日志配置（幂等，重复调用直接返回现有 handler）。

    Args:
        level (int): root logger 的级别，默认 ``logging.INFO``。
        force (bool): 为 True 时忽略幂等标记强制重装（测试用）。
        stream (Optional[object]): 指定输出流（测试用）；默认 ``sys.stderr``。

    Returns:
        logging.Handler: 本次安装（或已存在）的 StreamHandler。
    """
    global _LOGGING_READY
    root = logging.getLogger()

    if _LOGGING_READY and not force:
        for handler in root.handlers:
            if isinstance(handler, logging.StreamHandler):
                return handler
        # 标记在但 handler 被外部清空了：视为未初始化，继续安装。

    # 1) 先修流编码（常规路径下 stderr 可 reconfigure，print 同步受益）。
    target = stream if stream is not None else sys.stderr
    utf8_stream = _ensure_utf8_stream(target)

    # 2) 重装 root：清掉 basicConfig / uvicorn 预先挂的 handler，
    #    避免同一行日志双份输出（uvicorn 启动早于本函数时会挂上自己的）。
    root.setLevel(level)
    for old in list(root.handlers):
        root.removeHandler(old)

    handler = logging.StreamHandler(utf8_stream)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)

    _LOGGING_READY = True
    logging.getLogger(__name__).debug("统一日志已安装（UTF-8）")
    return handler
