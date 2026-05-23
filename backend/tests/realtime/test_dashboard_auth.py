"""ADR-0024 P0 — DashboardNamespace auth must reject non-access tokens.

Why: /dashboard SocketIO 也走 cookie/auth 解出 JWT。如果 refresh token 能在
此通道冒充 access,会话注销(blacklist)就被旁路。

仅测 on_connect 鉴权分支;subscribe/unsubscribe 与本 P0 无关。
"""
from __future__ import annotations

import pytest
import socketio.exceptions

from backend.core.security import create_access_token, create_refresh_token
from backend.realtime.socketio_server import DashboardNamespace


@pytest.mark.asyncio
async def test_dashboard_rejects_refresh_token_via_auth_dict(monkeypatch):
    monkeypatch.setenv("ENV", "")  # 非 production:无 token 时本来就放行
    refresh = create_refresh_token({"sub": "alice"})
    ns = DashboardNamespace("/dashboard")

    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await ns.on_connect("sid-A", environ={}, auth={"token": refresh})


@pytest.mark.asyncio
async def test_dashboard_rejects_refresh_token_via_cookie(monkeypatch):
    monkeypatch.setenv("ENV", "")
    refresh = create_refresh_token({"sub": "alice"})
    ns = DashboardNamespace("/dashboard")

    # 模拟浏览器 Cookie 头携带 access cookie,但值是 refresh token。
    cookie_header = f"stp_access_token={refresh}"

    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await ns.on_connect("sid-B", environ={"HTTP_COOKIE": cookie_header}, auth={})


@pytest.mark.asyncio
async def test_dashboard_accepts_access_token(monkeypatch):
    monkeypatch.setenv("ENV", "")
    access = create_access_token({"sub": "alice", "role": "admin"})
    ns = DashboardNamespace("/dashboard")

    # 不抛 ConnectionRefusedError 即视为接受。
    await ns.on_connect("sid-C", environ={}, auth={"token": access})


@pytest.mark.asyncio
async def test_dashboard_rejects_garbage_token(monkeypatch):
    monkeypatch.setenv("ENV", "")
    ns = DashboardNamespace("/dashboard")

    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await ns.on_connect("sid-D", environ={}, auth={"token": "garbage.value.bad"})


@pytest.mark.asyncio
async def test_dashboard_no_token_outside_production_still_allowed(monkeypatch):
    """回归保护:本 P0 修复不能破坏现有的 "non-production 无 token 放行" 路径。"""
    monkeypatch.setenv("ENV", "")
    ns = DashboardNamespace("/dashboard")

    await ns.on_connect("sid-E", environ={}, auth={})
