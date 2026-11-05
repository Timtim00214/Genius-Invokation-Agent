# -*- coding: utf-8 -*-
"""
API 层测试。

用 FastAPI 的 ``TestClient`` 直接测 HTTP 接口，不依赖真实端口。
覆盖：健康检查、Schema 导出、蓝图校验预检、蓝图执行、结构化错误。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

pytestmark = pytest.mark.api

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _load(name: str, rel: str) -> Any:
    """按文件路径加载模块。"""
    spec = importlib.util.spec_from_file_location(name, BACKEND_DIR / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def app_module() -> Any:
    """加载应用模块（整个测试模块共享，避免重复扫描）。"""
    return _load("vmaf.main", "main.py")


@pytest.fixture(scope="module")
def client(app_module):
    """构造测试客户端。

    ``TestClient`` 作为上下文管理器使用，以确保 startup 事件被触发
    （启动时会执行一次插件扫描）。
    """
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as instance:
        yield instance


def _read_node(nid: str = "n1", path: str = "any.txt") -> Dict[str, Any]:
    """构造 read_file 节点。"""
    return {"id": nid, "type": "read_file", "params": {"path": path}}


def _regex_node(nid: str, **params: Any) -> Dict[str, Any]:
    """构造 regex_extract 节点。"""
    merged = {"pattern": "x"}
    merged.update(params)
    merged.setdefault("text", "placeholder")
    return {"id": nid, "type": "regex_extract", "params": merged}


# ======================================================================
# 健康检查
# ======================================================================
def test_health_ok(client):
    """健康检查应返回 ok 且与注册表数量一致。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["node_count"] == 28
    assert data["plugin_errors"] == 0
    assert "扫描" in data["scan_summary"]


def test_root_lists_endpoints(client):
    """根路径应列出可用接口。"""
    resp = client.get("/")
    assert resp.status_code == 200
    endpoints = resp.json()["endpoints"]
    assert {"health", "schema", "validate", "run"} <= set(endpoints)


# ======================================================================
# Schema 导出
# ======================================================================
def test_schema_returns_all_nodes(client):
    """``/schema`` 应返回全部 28 个节点的规格。"""
    resp = client.get("/schema")
    assert resp.status_code == 200
    data = resp.json()

    assert data["count"] == 28
    assert len(data["nodes"]) == 28
    assert data["errors"] == []

    types = {n["node_type"] for n in data["nodes"]}
    assert types == {
        "llm_chat", "read_file", "http_fetch",
        "regex_extract", "context_compress", "tool_use", "edit_file",
        "model_profile", "rag_search",
        "branch", "loop_start", "loop_end",
        # 对局决策节点（七圣召唤 AI 司机接入）
        "arena_input", "action_menu", "state_brief",
        "prompt_pack", "choice_parse", "choice_map", "rpc_build",
        "json_pick", "action_rank", "playbook", "game_log",
        # 复盘节点
        "winrate_table", "review_parse",
        # 备赛（牌组码 / 赛前计划）
        "shared_get", "deck_brief", "plan_guard",
    }


def test_schema_nodes_have_renderable_metadata(client):
    """每个节点规格都必须含前端渲染所需的元数据。

    这是"UI 零代码自动化"的前提：前端不写死任何业务字段，
    完全依据 description 与 input/output Schema 渲染。
    """
    resp = client.get("/schema")
    for node in resp.json()["nodes"]:
        assert node["description"], f"{node['node_type']} 缺少描述"
        assert node["version"]
        assert "properties" in node["input"]
        assert "properties" in node["output"]
        assert node["file"].endswith(".py")


def test_schema_field_descriptions_present(client):
    """字段必须携带 description，供前端生成表单标签。"""
    resp = client.get("/schema")
    node = next(n for n in resp.json()["nodes"] if n["node_type"] == "read_file")
    props = node["input"]["properties"]
    assert props["path"]["description"]
    assert props["encoding"]["default"] == "utf-8"


# ======================================================================
# 校验预检（不执行）
# ======================================================================
def test_validate_accepts_valid_blueprint(client):
    """合法蓝图应通过校验并返回执行顺序。"""
    payload = {
        "blueprint": {
            "nodes": [
                _read_node("a"),
                _regex_node("b", text=None),
                {"id": "c", "type": "llm_chat",
                 "params": {"messages": [{"role": "user", "content": "hi"}]}},
            ],
            "edges": [
                {"id": "e1", "source": "a", "source_handle": "content",
                 "target": "b", "target_handle": "text"},
                {"id": "e2", "source": "b", "source_handle": "count",
                 "target": "c", "target_handle": "max_tokens"},
            ],
        }
    }
    resp = client.post("/validate", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["valid"] is True
    assert data["order"] == ["a", "b", "c"]
    assert data["entry_nodes"] == ["a"]
    assert data["exit_nodes"] == ["c"]
    assert data["issues"] == []


def test_validate_reports_type_mismatch(client):
    """类型不兼容的连线应在校验阶段被报出（不执行任何节点）。"""
    payload = {
        "blueprint": {
            "nodes": [_regex_node("a"), _regex_node("b", text=None)],
            "edges": [
                {"id": "e", "source": "a", "source_handle": "matches",
                 "target": "b", "target_handle": "text"},
            ],
        }
    }
    resp = client.post("/validate", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["valid"] is False
    codes = [i["code"] for i in data["issues"]]
    assert any("type_mismatch" in c for c in codes)


def test_validate_reports_cycle(client):
    """有环蓝图应被检出。"""
    payload = {
        "blueprint": {
            "nodes": [
                _read_node("a"), _regex_node("b"), _regex_node("c"),
            ],
            "edges": [
                {"id": "e1", "source": "a", "source_handle": "content",
                 "target": "b", "target_handle": "text"},
                {"id": "e2", "source": "b", "source_handle": "count",
                 "target": "c", "target_handle": "limit"},
                {"id": "e3", "source": "c", "source_handle": "count",
                 "target": "a", "target_handle": "max_bytes"},
            ],
        }
    }
    resp = client.post("/validate", json=payload)
    data = resp.json()
    assert data["valid"] is False
    assert any(i["code"] == "blueprint.cycle" for i in [
        {**i, "code": i["code"]} for i in data["issues"]
    ]) or any("cycle" in i["code"].lower() for i in data["issues"])


def test_validate_rejects_malformed_blueprint(client):
    """结构非法的蓝图应返回 422 而非 500。"""
    payload = {
        "blueprint": {
            "nodes": [{"id": "a", "type": "read_file"}],
            "edges": [
                {"id": "e", "source": "a", "source_handle": "content",
                 "target": "ghost", "target_handle": "text"},
            ],
        }
    }
    resp = client.post("/validate", json=payload)
    assert resp.status_code == 422


# ======================================================================
# 执行
# ======================================================================
def test_run_executes_chain(client):
    """``/run`` 应成功执行合法蓝图并返回各节点输出。"""
    payload = {
        "blueprint": {
            "nodes": [_regex_node("a", pattern="a", text="aaa")],
            "edges": [],
        }
    }
    resp = client.post("/run", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["ok"] is True
    assert data["aborted"] is False
    assert len(data["results"]) == 1
    assert data["results"][0]["output"]["count"] == 3


def test_run_reports_node_failure_with_details(client):
    """节点失败时应返回结构化错误，含节点定位信息。

    前端据此高亮出错节点——这正是 errors.py 存在的意义。
    """
    payload = {
        "blueprint": {
            "nodes": [_read_node("bad", "不存在的路径/xyz.txt")],
            "edges": [],
        }
    }
    resp = client.post("/run", json=payload)
    assert resp.status_code == 200  # 节点失败不返回 5xx
    data = resp.json()

    assert data["ok"] is False
    assert data["error_details"], "必须返回结构化错误详情"
    detail = data["error_details"][0]
    assert detail["node_id"] == "bad"
    assert detail["node_type"] == "read_file"
    assert detail["code"]
    assert detail["category"]
    assert detail["message"]


def test_run_error_category_for_missing_file(client):
    """文件不存在应归类为 not_found 且不可重试。"""
    payload = {
        "blueprint": {
            "nodes": [_read_node("bad", "不存在的路径/xyz.txt")],
            "edges": [],
        }
    }
    detail = client.post("/run", json=payload).json()["error_details"][0]
    assert detail["category"] == "not_found"
    assert detail["retryable"] is False


def test_run_aborts_on_invalid_plan(client):
    """蓝图校验不通过时不应执行任何节点。"""
    payload = {
        "blueprint": {
            "nodes": [_regex_node("a"), _regex_node("b", text=None)],
            "edges": [
                {"id": "e", "source": "a", "source_handle": "matches",
                 "target": "b", "target_handle": "text"},
            ],
        }
    }
    resp = client.post("/run", json=payload)
    data = resp.json()

    assert data["aborted"] is True
    assert len(data["results"]) == 0
    assert data["abort_reason"]


def test_run_malformed_blueprint_returns_422(client):
    """蓝图 JSON 结构非法应返回 422。"""
    payload = {
        "blueprint": {
            "nodes": [{"id": "a", "type": "read_file"},
                      {"id": "a", "type": "read_file"}],
            "edges": [],
        }
    }
    resp = client.post("/run", json=payload)
    assert resp.status_code == 422


def test_run_supports_strict_mode(client):
    """严格模式参数应被接受并生效。"""
    payload = {
        "blueprint": {
            "nodes": [
                _read_node("bad", "不存在的路径/xyz.txt"),
                _regex_node("other", text="hello"),
            ],
            "edges": [],
        },
        "strict": True,
    }
    resp = client.post("/run", json=payload)
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_run_custom_run_id(client):
    """可指定 run_id。"""
    payload = {
        "blueprint": {"nodes": [_regex_node("a", text="aaa")], "edges": []},
        "run_id": "my-run-001",
    }
    data = client.post("/run", json=payload).json()
    assert data["run_id"] == "my-run-001"


def test_run_shared_data(client):
    """共享数据字段应被接受。"""
    payload = {
        "blueprint": {"nodes": [_regex_node("a", text="aaa")], "edges": []},
        "shared": {"tenant": "acme"},
    }
    data = client.post("/run", json=payload).json()
    assert data["ok"] is True


# ======================================================================
# OpenAPI 文档
# ======================================================================
def test_openapi_schema_generated(client):
    """FastAPI 应能自动生成 OpenAPI 文档（供前端生成 SDK）。"""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/schema" in paths
    assert "/run" in paths
    assert "/validate" in paths
    assert "/health" in paths
