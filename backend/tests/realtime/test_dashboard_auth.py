"""ADR-0024 P0 + #281 P0 + #904 — DashboardNamespace auth.

- ADR-0024：/dashboard SocketIO 也走 cookie/auth 解出 JWT。refresh token
  不能在此通道冒充 access，否则会话注销（blacklist）被旁路。
- #281 P0：匿名接入规则与 ENV 无关——除 ``TESTING=1`` 外一律要求有效认证
  （旧实现只在 ENV=production 拒绝，生产部署 ENV=internal 时护栏从未生效）。
- #904：外来 Origin 在认证前服务端强制拒绝——Cookie 自动附带握手必带
  Origin，engineio 的 CORS 响应头只由浏览器执行、不构成服务端边界。

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
async def test_dashboard_rejects_foreign_origin_with_valid_token(
    monkeypatch, db_session, test_user
):
    """#904：外来 Origin + 有效凭据仍拒——来源不可信与凭据有效性正交。"""
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)  # 用默认白名单
    access = create_access_token(
        data={
            "sub": str(test_user.id),
            "username": test_user.username,
            "role": test_user.role,
            "ver": test_user.token_version,
        }
    )
    ns = DashboardNamespace("/dashboard")

    with pytest.raises(socketio.exceptions.ConnectionRefusedError, match="Origin"):
        await ns.on_connect(
            "sid-O1",
            environ={"HTTP_ORIGIN": "http://evil.example", "HTTP_COOKIE": f"stp_access_token={access}"},
            auth={},
        )


@pytest.mark.asyncio
async def test_dashboard_allows_allowlisted_origin_with_cookie(
    monkeypatch, db_session, test_user
):
    """#904：白名单 Origin + Cookie 认证握手放行（默认白名单含 localhost:5173）。"""
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    access = create_access_token(
        data={
            "sub": str(test_user.id),
            "username": test_user.username,
            "role": test_user.role,
            "ver": test_user.token_version,
        }
    )
    ns = DashboardNamespace("/dashboard")

    await ns.on_connect(
        "sid-O2",
        environ={
            "HTTP_ORIGIN": "http://localhost:5173",
            "HTTP_COOKIE": f"stp_access_token={access}",
        },
        auth={},
    )


@pytest.mark.asyncio
async def test_dashboard_origin_absent_keeps_token_path(monkeypatch, db_session, test_user):
    """#904：无 Origin（脚本/测试携 token）不触发 Origin 拦截，走既有认证。"""
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    access = create_access_token(
        data={
            "sub": str(test_user.id),
            "username": test_user.username,
            "role": test_user.role,
            "ver": test_user.token_version,
        }
    )
    ns = DashboardNamespace("/dashboard")

    await ns.on_connect("sid-O3", environ={}, auth={"token": access})


@pytest.mark.asyncio
async def test_dashboard_accepts_access_token(monkeypatch, db_session, test_user):
    """R02-D3（#903）：socket 与 REST 同校验面——有效 token = 真实存在的
    活跃用户（PK sub + 当前 ver）。"""
    monkeypatch.setenv("TESTING", "0")
    access = create_access_token(
        data={
            "sub": str(test_user.id),
            "username": test_user.username,
            "role": test_user.role,
            "ver": test_user.token_version,
        }
    )
    ns = DashboardNamespace("/dashboard")

    # 不抛 ConnectionRefusedError 即视为接受。
    await ns.on_connect("sid-C", environ={}, auth={"token": access})


@pytest.mark.asyncio
async def test_dashboard_rejects_token_of_disabled_user(
    monkeypatch, db_session, test_user
):
    """#903 核心场景：签名有效但用户已停用——此前签名级 decode 全通。"""
    monkeypatch.setenv("TESTING", "0")
    access = create_access_token(
        data={
            "sub": str(test_user.id),
            "username": test_user.username,
            "role": test_user.role,
            "ver": test_user.token_version,
        }
    )
    test_user.is_active = "N"
    db_session.commit()
    ns = DashboardNamespace("/dashboard")

    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await ns.on_connect("sid-G", environ={}, auth={"token": access})


@pytest.mark.asyncio
async def test_dashboard_rejects_stale_epoch_token(
    monkeypatch, db_session, test_user
):
    """R02-D2：ver 纪元不匹配（bump 后旧 token）必须被拒。"""
    monkeypatch.setenv("TESTING", "0")
    access = create_access_token(
        data={
            "sub": str(test_user.id),
            "username": test_user.username,
            "role": test_user.role,
            "ver": test_user.token_version,
        }
    )
    test_user.token_version = (test_user.token_version or 1) + 1
    db_session.commit()
    ns = DashboardNamespace("/dashboard")

    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await ns.on_connect("sid-H", environ={}, auth={"token": access})


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
