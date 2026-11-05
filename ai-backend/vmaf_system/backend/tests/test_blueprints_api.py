# -*- coding: utf-8 -*-
"""
蓝图持久化 HTTP 接口测试（``/blueprints``）。

只挂载路由层构造一个最小 FastAPI 应用，避免依赖 ``main.py`` 的启动扫描；
存储目录用临时目录，绝不污染 ``backend/data/blueprints``。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

pytestmark = pytest.mark.api

BACKEND_DIR = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND_DIR), str(BACKEND_DIR / "utils")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(scope="module")
def routes_module() -> Any:
    """加载路由模块。"""
    from api import routes

    return routes


@pytest.fixture
def client(routes_module: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """构造只挂载本工程路由的测试客户端，并把仓库指向临时目录。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from utils import blueprint_store

    monkeypatch.setattr(
        blueprint_store,
        "_DEFAULT_STORE",
        blueprint_store.BlueprintStore(root=tmp_path / "blueprints"),
    )

    app = FastAPI()
    app.include_router(routes_module.router)
    with TestClient(app) as instance:
        yield instance


def _blueprint(**overrides: Any) -> dict:
    """构造一份最小合法蓝图。"""
    bp = {
        "version": "0.1.0",
        "name": "接口测试蓝图",
        "nodes": [
            {"id": "n1", "type": "read_file", "params": {"path": "a.txt"}},
        ],
        "edges": [],
    }
    bp.update(overrides)
    return bp


# ======================================================================
# 保存 / 列出 / 读取 / 删除
# ======================================================================
def test_save_list_load_delete_flow(client):
    """完整生命周期：保存 → 列出 → 读取 → 删除 → 404。"""
    saved = client.post("/blueprints", json={"blueprint": _blueprint(), "name": "我的流程"})
    assert saved.status_code == 200, saved.text
    meta = saved.json()
    bid = meta["id"]
    assert meta["name"] == "我的流程"
    assert meta["node_count"] == 1

    listed = client.get("/blueprints")
    assert listed.status_code == 200
    assert [i["id"] for i in listed.json()["items"]] == [bid]

    loaded = client.get(f"/blueprints/{bid}")
    assert loaded.status_code == 200
    assert loaded.json()["blueprint"]["name"] == "接口测试蓝图"

    deleted = client.delete(f"/blueprints/{bid}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    assert client.get(f"/blueprints/{bid}").status_code == 404


def test_overwrite_by_id(client):
    """带 id 保存 = 覆盖，不新增条目。"""
    first = client.post("/blueprints", json={"blueprint": _blueprint()}).json()
    second = client.post(
        "/blueprints",
        json={"blueprint": _blueprint(name="新版"), "id": first["id"], "name": "新版"},
    ).json()

    assert second["id"] == first["id"]
    assert second["name"] == "新版"
    assert len(client.get("/blueprints").json()["items"]) == 1


def test_save_rejects_invalid_blueprint(client):
    """结构非法的蓝图（悬空连线）必须 422，不得落盘。"""
    bad = _blueprint(
        edges=[
            {
                "id": "e1",
                "source": "ghost",
                "source_handle": "content",
                "target": "n1",
                "target_handle": "path",
            }
        ]
    )
    resp = client.post("/blueprints", json={"blueprint": bad})
    assert resp.status_code == 422
    assert "蓝图结构非法" in resp.text
    assert client.get("/blueprints").json()["items"] == []


def test_load_unknown_returns_404(client):
    assert client.get("/blueprints/not_there").status_code == 404


def test_path_traversal_blocked(client):
    """路径穿越 id 必须被拒绝（400/404），绝不能读到仓库外的文件。"""
    resp = client.get("/blueprints/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code in (400, 404, 405)

    resp2 = client.post(
        "/blueprints", json={"blueprint": _blueprint(), "id": "../../evil"}
    )
    assert resp2.status_code == 400


def test_delete_unknown_returns_404(client):
    assert client.delete("/blueprints/not_there").status_code == 404
