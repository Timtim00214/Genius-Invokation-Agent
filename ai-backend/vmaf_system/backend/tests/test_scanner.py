# -*- coding: utf-8 -*-
"""
插件扫描器（``scanner.py``）测试。

覆盖：自动发现、抽象过滤、故障隔离、热更新、删除注销、
``node_type`` 冲突检测、Schema 导出完整性。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.scanner

EXPECTED_NODE_TYPES = {
    # 数据节点
    "llm_chat",
    "read_file",
    "http_fetch",
    "regex_extract",
    "context_compress",
    "tool_use",
    "edit_file",
    "model_profile",
    "rag_search",
    # 控制流节点
    "branch",
    "loop_start",
    "loop_end",
    # 对局决策节点
    "arena_input",
    "action_menu",
    "state_brief",
    "prompt_pack",
    "choice_parse",
    "choice_map",
    "rpc_build",
    "json_pick",
    "action_rank",
    "playbook",
    "game_log",
    # 备赛节点
    "shared_get",
    "deck_brief",
    "plan_guard",
    # 复盘节点
    "winrate_table",
    "review_parse",
}


# ----------------------------------------------------------------------
# 发现与注册
# ----------------------------------------------------------------------
def test_scan_registers_all_expected_nodes(scanner):
    """扫描器应自动发现全部 28 个节点。"""
    assert set(scanner.get_registry()) == EXPECTED_NODE_TYPES


def test_scan_report_clean(scanner_module):
    """首次扫描应无错误。"""
    instance = scanner_module.PluginScanner()
    report = instance.scan()
    assert report.ok, [str(e) for e in report.errors]
    assert report.scanned == 27
    assert report.registered == 28


def test_abstract_classes_not_registered(scanner):
    """抽象基类与占位契约不得被收录为节点。"""
    registry = scanner.get_registry()
    for forbidden in ("BaseNode", "InputSchema", "OutputSchema", "Message"):
        assert forbidden not in registry


def test_registry_records_carry_metadata(scanner):
    """登记记录必须携带版本、描述与来源文件。"""
    for node_type, record in scanner.get_registry().items():
        assert record.node_type == node_type
        assert record.description, f"{node_type} 缺少描述"
        assert record.version
        assert record.file_path.exists()


# ----------------------------------------------------------------------
# Schema 导出
# ----------------------------------------------------------------------
def test_all_schemas_complete(scanner):
    """每个节点的 Schema 都必须包含前端渲染所需的全部键。"""
    schemas = scanner.get_all_schemas()
    assert len(schemas) == 28
    for schema in schemas:
        assert set(schema) >= {
            "node_type",
            "description",
            "version",
            "input",
            "output",
            "file",
        }


def test_all_schemas_sorted_by_node_type(scanner):
    """Schema 清单应按 ``node_type`` 升序排列，保证输出确定性。"""
    types = [s["node_type"] for s in scanner.get_all_schemas()]
    assert types == sorted(types)


# ----------------------------------------------------------------------
# 缓存与热更新
# ----------------------------------------------------------------------
def test_second_scan_hits_cache(scanner_module):
    """文件未变更时二次扫描不得重复注册。"""
    instance = scanner_module.PluginScanner()
    instance.scan()
    report = instance.scan()
    assert report.registered == 0
    assert report.reloaded == 0
    assert report.removed == 0


def test_hot_reload_on_file_change(scanner_module, blank_tools_dir):
    """文件指纹变化时应只重载该文件。"""
    instance = scanner_module.PluginScanner()
    instance.scan()

    target = blank_tools_dir / "llm_chat.py"
    original = target.read_text(encoding="utf-8")
    try:
        target.write_text(original + "\n# hot reload probe\n", encoding="utf-8")
        report = instance.scan()
        assert report.reloaded == 1
        assert report.registered == 0, "其它文件不应被重复注册"
        assert "llm_chat" in instance.get_registry()
    finally:
        target.write_text(original, encoding="utf-8")


def test_hot_reload_reexecutes_changed_code(scanner_module, blank_tools_dir):
    """回归测试：热更新必须真正重新执行改过的插件文件。

    缺陷背景：load_module_once 按模块名复用 sys.modules 缓存，历史上的
    "热更新"只更新了文件指纹与注册表，模块代码并不会重新执行——修改
    插件 version 后注册表仍是旧值，必须重启后端才能生效。
    """
    instance = scanner_module.PluginScanner()
    instance.scan()

    target = blank_tools_dir / "llm_chat.py"
    original = target.read_text(encoding="utf-8")
    bumped = original.replace('version: str = "0.2.0"', 'version: str = "0.2.1"')
    assert bumped != original, "探针前提不成立：未找到 version 字符串"
    try:
        target.write_text(bumped, encoding="utf-8")
        report = instance.scan()
        assert report.reloaded == 1
        record = instance.get_registry()["llm_chat"]
        assert record.version == "0.2.1", (
            f"热更新未重新执行模块代码，注册表版本仍为 {record.version!r}"
        )
    finally:
        target.write_text(original, encoding="utf-8")

    # 恢复后再次扫描，版本应回到 0.2.0，证明重载是双向生效的。
    report = instance.scan()
    assert instance.get_registry()["llm_chat"].version == "0.2.0"
    assert report.reloaded == 1


def test_hot_reload_failure_isolates_and_recovers(scanner_module, blank_tools_dir):
    """热更新遇到坏代码时应报错隔离，且恢复文件后能正常重新注册。"""
    instance = scanner_module.PluginScanner()
    instance.scan()

    target = blank_tools_dir / "llm_chat.py"
    original = target.read_text(encoding="utf-8")
    try:
        target.write_text("def (this is not valid python!!!\n", encoding="utf-8")
        report = instance.scan()
        assert len(report.errors) == 1
        # 重载摘除了旧缓存且坏代码注册失败，注册表里不再有该节点；
        # 恢复原文件后必须能重新注册成功。
        assert "llm_chat" not in instance.get_registry()

        target.write_text(original, encoding="utf-8")
        report = instance.scan()
        assert report.ok
        assert "llm_chat" in instance.get_registry()
    finally:
        target.write_text(original, encoding="utf-8")


def test_removed_file_unregisters_nodes(scanner_module, blank_tools_dir):
    """插件文件被删除后，其节点应自动注销。"""
    instance = scanner_module.PluginScanner()
    instance.scan()

    temp = blank_tools_dir / "zz_probe_temp.py"
    temp.write_text(
        "from ..base import BaseNode\n"
        "class ProbeTempNode(BaseNode):\n"
        "    node_type = 'probe_temp'\n"
        "    description = '临时探针'\n"
        "    async def execute(self, inputs, context=None):\n"
        "        return None\n",
        encoding="utf-8",
    )
    instance.scan()
    assert "probe_temp" in instance.get_registry()

    temp.unlink()
    report = instance.scan()
    assert "probe_temp" not in instance.get_registry()
    assert report.removed >= 1


# ----------------------------------------------------------------------
# 故障隔离
# ----------------------------------------------------------------------
def test_node_type_conflict_isolated(scanner_module, blank_tools_dir):
    """``node_type`` 冲突的插件必须被隔离，不得影响其它节点。"""
    instance = scanner_module.PluginScanner()
    instance.scan()
    before = set(instance.get_registry())

    bad = blank_tools_dir / "zz_probe_conflict.py"
    bad.write_text(
        "from ..base import BaseNode\n"
        "class ConflictNode(BaseNode):\n"
        "    node_type = 'llm_chat'   # 故意与已有节点冲突\n"
        "    description = '冲突探针'\n"
        "    async def execute(self, inputs, context=None):\n"
        "        return None\n",
        encoding="utf-8",
    )
    try:
        report = instance.scan()
        assert len(report.errors) == 1
        assert "node_type" in report.errors[0].reason
        # 关键：正常节点一个都不能少
        assert set(instance.get_registry()) == before
    finally:
        if bad.exists():
            bad.unlink()


def test_broken_plugin_does_not_break_others(scanner_module, blank_tools_dir):
    """语法错误的插件不得影响其它插件的注册。"""
    instance = scanner_module.PluginScanner()
    instance.scan()
    before = set(instance.get_registry())

    broken = blank_tools_dir / "zz_probe_broken.py"
    broken.write_text("def (this is not valid python!!!\n", encoding="utf-8")
    try:
        report = instance.scan()
        assert len(report.errors) == 1
        assert set(instance.get_registry()) == before
    finally:
        if broken.exists():
            broken.unlink()


def test_private_files_skipped(scanner_module, blank_tools_dir):
    """下划线开头的文件应被视为私有模块而跳过扫描。"""
    instance = scanner_module.PluginScanner()
    instance.scan()
    before = set(instance.get_registry())

    private = blank_tools_dir / "_private_probe.py"
    private.write_text(
        "from ..base import BaseNode\n"
        "class PrivateNode(BaseNode):\n"
        "    node_type = 'private_probe'\n"
        "    description = '私有'\n"
        "    async def execute(self, inputs, context=None):\n"
        "        return None\n",
        encoding="utf-8",
    )
    try:
        instance.scan()
        assert "private_probe" not in instance.get_registry()
        assert set(instance.get_registry()) == before
    finally:
        if private.exists():
            private.unlink()


def test_plugin_without_node_class_rejected(scanner_module, blank_tools_dir):
    """不含任何节点类的脚本应被拒绝，避免静默注册空插件。"""
    instance = scanner_module.PluginScanner()
    instance.scan()

    empty = blank_tools_dir / "zz_probe_empty.py"
    empty.write_text("# 这是一个不含任何节点的脚本\nVALUE = 1\n", encoding="utf-8")
    try:
        report = instance.scan()
        assert len(report.errors) == 1
        assert "BaseNode" in report.errors[0].reason
    finally:
        if empty.exists():
            empty.unlink()


# ----------------------------------------------------------------------
# 工厂方法
# ----------------------------------------------------------------------
def test_get_node_class_and_instantiate(scanner):
    """工厂方法应能取回节点类并实例化。"""
    for node_type in EXPECTED_NODE_TYPES:
        instance = scanner.instantiate(node_type)
        assert instance.node_type == node_type


def test_get_unknown_node_class_raises(scanner):
    """取回未注册类型时必须抛出带提示的 ``KeyError``。"""
    with pytest.raises(KeyError, match="节点类型未注册"):
        scanner.get_node_class("definitely_not_exist")


# ----------------------------------------------------------------------
# 契约层唯一性（关键回归测试）
# ----------------------------------------------------------------------
def test_contract_is_singleton_across_scanners(scanner_module):
    """多个扫描器实例必须共享同一份契约层。

    这是"共享消息类型"这一架构决定的前提：若契约层被重复执行，
    会产生不同身份的 ``Message`` 类，跨节点连线将全部失败。

    Args:
        scanner_module: 扫描器模块夹具。
    """
    a = scanner_module.PluginScanner().BaseNode
    b = scanner_module.PluginScanner().BaseNode
    c = scanner_module.PluginScanner().BaseNode
    assert a is b is c, "契约层被重复加载，类身份不唯一"


def test_shared_message_type_across_nodes(scanner):
    """不同插件使用的 ``Message`` 必须是同一个类对象。"""
    chat = scanner.get_node_class("llm_chat")
    compress = scanner.get_node_class("context_compress")

    chat_msg = chat.input_model.model_fields["messages"].annotation.__args__[0]
    comp_msg = compress.input_model.model_fields["messages"].annotation.__args__[0]

    assert chat_msg is comp_msg, "跨节点的 Message 类型不统一，连线会失败"


# ----------------------------------------------------------------------
# reload_module 护栏（热更新底层）
# ----------------------------------------------------------------------
def test_reload_module_refuses_protected_modules(scanner_module, backend_utils):
    """契约层与核心区模块必须被 reload_module 拒绝重载。

    缺陷背景：Pydantic 按类身份校验，若契约层 base.py 被重载执行第二次，
    会产生不同身份的 Message 类，跨节点连线全部失败。护栏必须先于任何
    文件操作生效。
    """
    loader = backend_utils("loader")
    for protected in (
        "vmaf.plugins.base",
        "vmaf.plugins.scanner",
        "vmaf.core.parser",
        "vmaf.core.engine",
        "vmaf.utils.loader",
        "vmaf.main",
    ):
        with pytest.raises(ValueError, match="禁止重载"):
            loader.reload_module(protected, Path("base.py"))


def test_reload_module_removes_cache_and_reexecutes(scanner_module, backend_utils, tmp_path):
    """普通业务模块的重载必须摘除缓存并重新执行文件。"""
    loader = backend_utils("loader")

    script = tmp_path / "probe_reload.py"
    script.write_text("VALUE = 1\n", encoding="utf-8")
    module_name = "vmaf.plugins.tools.probe_reload"

    first = loader.load_module_once(module_name, script)
    assert first.VALUE == 1

    script.write_text("VALUE = 2\n", encoding="utf-8")
    # load_module_once 此时必须仍返回旧缓存（这正是历史缺陷本身）。
    assert loader.load_module_once(module_name, script).VALUE == 1

    # reload_module 摘除缓存后重新执行，新代码生效。
    reloaded = loader.reload_module(module_name, script)
    assert reloaded.VALUE == 2
    assert loader.load_module_once(module_name, script).VALUE == 2

    # 清理：摘除测试模块登记，避免污染其它测试。
    sys.modules.pop(module_name, None)


def test_reload_module_failure_leaves_no_trace(scanner_module, backend_utils, tmp_path):
    """重载执行失败时必须摘除登记，不留半初始化的坏模块。"""
    loader = backend_utils("loader")

    script = tmp_path / "probe_reload_bad.py"
    script.write_text("VALUE = 1\n", encoding="utf-8")
    module_name = "vmaf.plugins.tools.probe_reload_bad"

    loader.load_module_once(module_name, script)
    assert module_name in sys.modules

    script.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        loader.reload_module(module_name, script)

    assert module_name not in sys.modules, "失败的模块不得残留在 sys.modules"
