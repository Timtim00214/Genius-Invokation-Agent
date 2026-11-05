# -*- coding: utf-8 -*-
"""
可选 API 鉴权（``api/security.py``）测试。

覆盖：默认关闭（行为不变）、配置后拦截、正确密钥放行、
探活路径保持匿名、401 响应形状、幂等安装、常量时间比较的边界（前缀/超长）。
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
def security_module() -> Any:
    """加载鉴权模块。"""
    from api import security

    return security


@pytest.fixture
def client(security_module: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """构造带鉴权中间件的最小应用（含各接口的占位实现）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/")
    async def root() -> dict:
        return {"name": "vmaf"}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/schema")
    async def schema() -> dict:
        return {"count": 0}

    @app.post("/run")
    async def run() -> dict:
        return {"ok": True}

    security_module.install_security(app)

    with TestClient(app) as instance:
        yield instance


# ======================================================================
# 默认关闭
# ======================================================================
def test_auth_disabled_by_default(client, monkeypatch):
    """未配置密钥时不拦截任何请求（不破坏现有单机用法）。"""
    monkeypatch.delenv("VMAF_API_KEY", raising=False)
    assert client.get("/schema").status_code == 200
    assert client.post("/run").status_code == 200


def test_blank_key_means_disabled(client, monkeypatch):
    """只填空白字符等同于未配置。"""
    monkeypatch.setenv("VMAF_API_KEY", "   ")
    assert client.get("/schema").status_code == 200


# ======================================================================
# 启用后拦截
# ======================================================================
def test_missing_key_rejected(client, monkeypatch):
    """启用后无密钥请求返回 401，并给出可操作的中文提示。"""
    monkeypatch.setenv("VMAF_API_KEY", "s3cret")
    resp = client.get("/schema")
    assert resp.status_code == 401
    message = resp.json()["detail"]["message"]
    assert "X-API-Key" in message
    assert "VMAF_API_KEY" in message


def test_wrong_key_rejected(client, monkeypatch):
    monkeypatch.setenv("VMAF_API_KEY", "s3cret")
    resp = client.get("/schema", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


def test_key_prefix_not_accepted(client, monkeypatch):
    """前缀/多余字符都不算匹配（比较的是完整值）。"""
    monkeypatch.setenv("VMAF_API_KEY", "s3cret")
    for candidate in ("s3cre", "s3crets", "s3cret "):
        resp = client.get("/schema", headers={"X-API-Key": candidate})
        assert resp.status_code == 401, candidate


def test_correct_key_allowed(client, monkeypatch):
    monkeypatch.setenv("VMAF_API_KEY", "s3cret")
    assert client.get("/schema", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.post("/run", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_header_name_case_insensitive(client, monkeypatch):
    """HTTP 头名大小写不敏感，客户端写小写也应放行。"""
    monkeypatch.setenv("VMAF_API_KEY", "s3cret")
    assert client.get("/schema", headers={"x-api-key": "s3cret"}).status_code == 200


def test_public_paths_stay_anonymous(client, monkeypatch):
    """探活与服务信息保持匿名访问，便于监控。"""
    monkeypatch.setenv("VMAF_API_KEY", "s3cret")
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200


def test_options_preflight_allowed(client, monkeypatch):
    """CORS 预检不带自定义头，必须绕过鉴权，否则浏览器端全部失败。

    该最小应用未挂 CORS 中间件，因此 FastAPI 会返回 405；
    断言重点在于**中间件没有返回 401**。
    """
    monkeypatch.setenv("VMAF_API_KEY", "s3cret")
    resp = client.options("/run", headers={"Origin": "http://localhost:5173"})
    assert resp.status_code != 401


def test_key_can_be_changed_at_runtime(client, monkeypatch):
    """密钥现读现用：改环境变量后立即生效（无需重启）。"""
    monkeypatch.setenv("VMAF_API_KEY", "first")
    assert client.get("/schema", headers={"X-API-Key": "first"}).status_code == 200

    monkeypatch.setenv("VMAF_API_KEY", "second")
    assert client.get("/schema", headers={"X-API-Key": "first"}).status_code == 401
    assert client.get("/schema", headers={"X-API-Key": "second"}).status_code == 200


# ======================================================================
# 安装幂等
# ======================================================================
def test_install_is_idempotent(security_module):
    """重复安装不得重复挂中间件（否则每个请求多跑一遍）。"""
    from fastapi import FastAPI

    app = FastAPI()
    security_module.install_security(app)
    before = len(app.user_middleware)
    security_module.install_security(app)
    assert len(app.user_middleware) == before
