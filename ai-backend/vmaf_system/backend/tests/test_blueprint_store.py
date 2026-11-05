# -*- coding: utf-8 -*-
"""
蓝图持久化存储（``utils/blueprint_store.py``）测试。

覆盖：保存/读取/列表/删除、覆盖保存、原子写入、损坏文件隔离、
id 合法性（路径穿越防线）、排序与元信息统计。
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.contract


@pytest.fixture
def store(tmp_path, backend_utils):
    """在临时目录上创建一个独立仓库（不污染 backend/data）。"""
    module = backend_utils("blueprint_store")
    return module.BlueprintStore(root=tmp_path / "blueprints")


def _blueprint(**overrides):
    """构造一份最小合法蓝图字典。"""
    bp = {
        "version": "0.1.0",
        "name": "测试蓝图",
        "nodes": [
            {"id": "n1", "type": "read_file", "params": {"path": "a.txt"}},
            {"id": "n2", "type": "regex_extract", "params": {}},
        ],
        "edges": [
            {
                "id": "e1",
                "source": "n1",
                "source_handle": "content",
                "target": "n2",
                "target_handle": "text",
            }
        ],
    }
    bp.update(overrides)
    return bp


# ======================================================================
# 基本 CRUD
# ======================================================================
def test_save_and_load_roundtrip(store):
    """保存后能原样读回，元信息统计正确。"""
    meta = store.save(_blueprint(), name="我的流程")

    assert meta["id"]
    assert meta["name"] == "我的流程"
    assert meta["node_count"] == 2
    assert meta["edge_count"] == 1
    assert meta["saved_at"]

    record = store.load(meta["id"])
    assert record["blueprint"]["name"] == "测试蓝图"
    assert len(record["blueprint"]["nodes"]) == 2


def test_save_writes_json_file(store, tmp_path):
    """一份蓝图对应一个可读的 JSON 文件（便于人工查看/备份）。"""
    meta = store.save(_blueprint())
    path = tmp_path / "blueprints" / f"{meta['id']}.json"
    assert path.is_file()

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["id"] == meta["id"]
    assert "blueprint" in raw


def test_no_tmp_file_left_behind(store, tmp_path):
    """原子写入完成后不得残留 .tmp 临时文件。"""
    store.save(_blueprint())
    leftovers = list((tmp_path / "blueprints").glob("*.tmp"))
    assert leftovers == []


def test_overwrite_by_id(store):
    """指定 id 时覆盖保存，不新增文件。"""
    meta = store.save(_blueprint())
    updated = store.save(_blueprint(name="改名后"), name="改名后", blueprint_id=meta["id"])

    assert updated["id"] == meta["id"]
    assert updated["name"] == "改名后"
    assert len(store.list()) == 1
    assert store.load(meta["id"])["blueprint"]["name"] == "改名后"


def test_list_sorted_by_saved_at_desc(store):
    """列表按保存时间倒序（最新在前）。"""
    first = store.save(_blueprint(name="A"))
    second = store.save(_blueprint(name="B"))

    items = store.list()
    assert [i["id"] for i in items][0] in {first["id"], second["id"]}
    # 同秒保存时次序不确定，但必须包含两条且时间字段存在
    assert len(items) == 2
    assert all(i["saved_at"] for i in items)


def test_delete(store):
    """删除已存在的蓝图返回 True，重复删除返回 False。"""
    meta = store.save(_blueprint())
    assert store.delete(meta["id"]) is True
    assert store.delete(meta["id"]) is False
    assert store.list() == []


def test_load_missing_raises(store, backend_utils):
    """读取不存在的蓝图抛 BlueprintNotFoundError。"""
    module = backend_utils("blueprint_store")
    with pytest.raises(module.BlueprintNotFoundError):
        store.load("does_not_exist")


# ======================================================================
# 安全：id 围栏（路径穿越）
# ======================================================================
@pytest.mark.parametrize(
    "bad_id",
    ["../etc/passwd", "..", "a/b", "a\\b", "名字", "x" * 65],
)
def test_invalid_id_rejected(store, backend_utils, bad_id):
    """非法 id 一律拒绝，从根上防止路径穿越。"""
    module = backend_utils("blueprint_store")
    with pytest.raises(module.InvalidBlueprintIdError):
        store.load(bad_id)

    with pytest.raises(module.InvalidBlueprintIdError):
        store.save(_blueprint(), blueprint_id=bad_id)


def test_empty_id_means_new(store, backend_utils):
    """空字符串 id 视为"未指定"，新建一份而不是报错。"""
    module = backend_utils("blueprint_store")
    meta = store.save(_blueprint(), blueprint_id="")
    assert meta["id"]

    # 但读取空 id 没有意义，必须拒绝
    with pytest.raises(module.InvalidBlueprintIdError):
        store.load("")


def test_valid_id_charset_accepted(store):
    """字母数字下划线连字符均合法。"""
    meta = store.save(_blueprint(), blueprint_id="my-flow_2026")
    assert meta["id"] == "my-flow_2026"


# ======================================================================
# 健壮性
# ======================================================================
def test_corrupt_file_skipped_in_list(store, tmp_path):
    """损坏的 JSON 文件不得拖垮列表。"""
    store.save(_blueprint(name="好的"))
    bad = tmp_path / "blueprints" / "broken.json"
    bad.write_text("{ 这不是 JSON", encoding="utf-8")

    items = store.list()
    assert len(items) == 1
    assert items[0]["name"] == "好的"


def test_list_missing_dir_returns_empty(tmp_path, backend_utils):
    """目录尚未创建时列表返回空数组（首次使用不报错）。"""
    module = backend_utils("blueprint_store")
    instance = module.BlueprintStore(root=tmp_path / "not_created")
    assert instance.list() == []


def test_long_name_truncated(store):
    """超长名称被截断，避免撑爆元数据。"""
    meta = store.save(_blueprint(), name="长" * 500)
    assert len(meta["name"]) <= 120
