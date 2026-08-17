"""ADR-0024 P0 + #281 P0 — DashboardNamespace auth.

- ADR-0024：/dashboard SocketIO 也走 cookie/auth 解出 JWT。refresh token
  不能在此通道冒充 access，否则会话注销（blacklist）被旁路。
- #281 P0：匿名接入规则与 ENV 无关——除 ``TESTING=1`` 外一律要求有效认证
  （旧实现只在 ENV=production 拒绝，生产部署 ENV=internal 时护栏从未生效）。

仅测 on_connect 鉴权分支；subscribe/unsubscribe 与本 P0 无关。
"""
from __future__ import annotations

import pytest
import socketio.exceptions

from backend.core.security import create_access_token, create_refresh_token
from backend.realtime.socketio_server import DashboardNamespace


@pytest.mark.asyncio
async def test_dashboard_rejects_refresh_token_via_auth_dict(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    refresh = create_refresh_token({"sub": "alice"})
    ns = DashboardNamespace("/dashboard")

    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await ns.on_connect("sid-A", environ={}, auth={"token": refresh})


@pytest.mark.asyncio
async def test_dashboard_rejects_refresh_token_via_cookie(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    refresh = create_refresh_token({"sub": "alice"})
    ns = DashboardNamespace("/dashboard")

    # 模拟浏览器 Cookie 头携带 access cookie,但值是 refresh token。
    cookie_header = f"stp_access_token={refresh}"

    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await ns.on_connect("sid-B", environ={"HTTP_COOKIE": cookie_header}, auth={})


@pytest.mark.asyncio
async def test_dashboard_accepts_access_token(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    access = create_access_token({"sub": "alice", "role": "admin"})
    ns = DashboardNamespace("/dashboard")

    # 不抛 ConnectionRefusedError 即视为接受。
    await ns.on_connect("sid-C", environ={}, auth={"token": access})


@pytest.mark.asyncio
async def test_dashboard_rejects_garbage_token(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    ns = DashboardNamespace("/dashboard")

    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await ns.on_connect("sid-D", environ={}, auth={"token": "garbage.value.bad"})


@pytest.mark.asyncio
async def test_dashboard_anonymous_refused_outside_testing(monkeypatch):
    """#281 P0 回归:除 TESTING=1 外无 token 一律拒绝——与 ENV 无关,
    修复前 ENV=internal 的生产部署可匿名接入。"""
    monkeypatch.setenv("TESTING", "0")
    ns = DashboardNamespace("/dashboard")

    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await ns.on_connect("sid-E", environ={}, auth={})


@pytest.mark.asyncio
async def test_dashboard_anonymous_allowed_under_testing():
    """TESTING=1(conftest 设置):测试套件匿名直连放行。"""
    ns = DashboardNamespace("/dashboard")

    await ns.on_connect("sid-F", environ={}, auth={})


@pytest.mark.asyncio
async def test_dashboard_default_dev_token_rejected_outside_testing(monkeypatch):
    """#281 二轮:源码默认值 dev-token-12345 不算「已配置」——未显式设置
    WS_TOKEN 时静态口令旁路不生效(否则任何部署都有一把公开万能口令)。"""
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.delenv("WS_TOKEN", raising=False)
    ns = DashboardNamespace("/dashboard")

    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await ns.on_connect("sid-G", environ={}, auth={"token": "dev-token-12345"})


@pytest.mark.asyncio
async def test_dashboard_explicit_ws_token_accepted_outside_testing(monkeypatch):
    """显式配置 WS_TOKEN 后,静态口令旁路按配置生效(生产部署自带独立值)。"""
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("WS_TOKEN", "configured-token-abc")
    ns = DashboardNamespace("/dashboard")

    await ns.on_connect("sid-H", environ={}, auth={"token": "configured-token-abc"})
