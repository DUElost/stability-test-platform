from backend.core.security import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME, create_access_token, create_refresh_token


ACCESS_COOKIE = ACCESS_COOKIE_NAME
REFRESH_COOKIE = REFRESH_COOKIE_NAME


def test_login_sets_http_only_auth_cookies(client, test_user):
    response = client.post(
        "/api/v1/auth/login",
        data={"username": "testuser", "password": "testpass123"},
    )

    assert response.status_code == 200
    assert response.cookies.get(ACCESS_COOKIE)
    assert response.cookies.get(REFRESH_COOKIE)
    set_cookie = response.headers.get("set-cookie", "")
    assert ACCESS_COOKIE in set_cookie
    assert REFRESH_COOKIE in set_cookie
    assert "HttpOnly" in set_cookie
    assert response.json() == {"ok": True}


def test_auth_me_accepts_cookie_session_without_bearer_header(client, test_user):
    login = client.post(
        "/api/v1/auth/login",
        data={"username": "testuser", "password": "testpass123"},
    )
    assert login.status_code == 200

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["username"] == "testuser"


def test_refresh_uses_refresh_cookie_when_request_body_missing(client, test_user):
    login = client.post(
        "/api/v1/auth/login",
        data={"username": "testuser", "password": "testpass123"},
    )
    assert login.status_code == 200

    client.cookies.pop(ACCESS_COOKIE, None)
    response = client.post("/api/v1/auth/refresh")

    assert response.status_code == 200
    assert response.cookies.get(ACCESS_COOKIE)
    assert response.cookies.get(REFRESH_COOKIE)
    assert response.json() == {"ok": True}


def test_token_endpoint_returns_bearer_tokens_without_setting_auth_cookies(client, test_user):
    response = client.post(
        "/api/v1/auth/token",
        data={"username": "testuser", "password": "testpass123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert response.cookies.get(ACCESS_COOKIE) is None
    assert response.cookies.get(REFRESH_COOKIE) is None


def test_logout_clears_auth_cookies_and_invalidates_session(client, test_user):
    login = client.post(
        "/api/v1/auth/login",
        data={"username": "testuser", "password": "testpass123"},
    )
    assert login.status_code == 200

    response = client.post("/api/v1/auth/logout")

    assert response.status_code == 200
    assert client.cookies.get(ACCESS_COOKIE) is None
    assert client.cookies.get(REFRESH_COOKIE) is None
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 401


def test_auth_me_still_accepts_bearer_header(client, test_user):
    token = create_access_token(
        data={"sub": str(test_user.id), "username": "testuser", "role": "user"}
    )

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["username"] == "testuser"


# ── ADR-0024 P0: get_current_user 必须拒绝 refresh token 冒充 access ────────


def test_get_current_user_rejects_refresh_token_via_access_cookie(client, test_user):
    """ADR-0024 P0 回归:把 refresh 塞进 access cookie 不能认证。

    这是 logout 后会话失效的核心:blacklist 只在 /auth/refresh 检查,如果
    refresh 能当 access 用,leaked refresh 在 logout 后仍可访问全部 cookie
    鉴权端点。
    """
    refresh = create_refresh_token({"sub": "testuser"})
    client.cookies.set(ACCESS_COOKIE_NAME, refresh)

    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_get_current_user_rejects_refresh_token_via_bearer(client, test_user):
    """ADR-0024 P0 回归:refresh 通过 Bearer 头也不能冒充 access。"""
    refresh = create_refresh_token({"sub": "testuser"})

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {refresh}"},
    )
    assert response.status_code == 401


# ── R02-D1/D4（#900/#901；设计 note docs/design/2026-09-08-session-identity-revocation.md）──


def test_token_sub_is_immutable_user_id(client, test_user):
    """R02-D1：sub=用户 PK；username/role 为信息性 claim。"""
    from backend.core.security import decode_token

    response = client.post(
        "/api/v1/auth/token",
        data={"username": "testuser", "password": "testpass123"},
    )
    assert response.status_code == 200

    payload = decode_token(response.json()["access_token"], expected_type="access")
    assert payload["sub"] == str(test_user.id)
    assert payload["username"] == "testuser"


def test_legacy_username_sub_access_token_rejected(client, test_user):
    """R02-D1 硬切换回归：存量 username-sub token（#900 冒充载体）一律 401。"""
    legacy = create_access_token({"sub": "testuser"})

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {legacy}"},
    )
    assert response.status_code == 401


def test_recreated_same_username_cannot_honor_old_token(client, test_user, db_session):
    """#900 核心场景：删建同名账户后旧 token 必须失效。

    旧实现按 username 查找——删除普通用户后重建同名 admin 账户，旧 token
    即可冒充新 admin。sub=PK 后旧 token 指向已不存在的 id。"""
    from backend.core.security import get_password_hash
    from backend.models.user import User

    login = client.post(
        "/api/v1/auth/token",
        data={"username": "testuser", "password": "testpass123"},
    )
    access = login.json()["access_token"]
    old_id = test_user.id

    # 第一步（正确行为断言）：改名不换身份——旧 token 认证回原 id 本尊，
    # 而不是认领 username 的新主人（旧实现的冒充路径）。
    test_user.username = "testuser_renamed"
    db_session.commit()
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["username"] == "testuser_renamed"

    # 第二步（#900 核心）：旧账户删除后，username 由新高权账户接手——
    # 旧 token 必须失效。审计行先行清理以绕过 users 硬删除的 FK 约束
    # （该约束即 #937/R03-F04「硬删除缺策略」的实证）。
    reborn = User(
        username="testuser",
        hashed_password=get_password_hash("testpass123"),
        role="admin",
        is_active="Y",
    )
    db_session.add(reborn)
    db_session.commit()
    assert reborn.id != old_id

    from backend.models.audit import AuditLog
    db_session.query(AuditLog).filter(AuditLog.user_id == old_id).delete(
        synchronize_session=False
    )
    db_session.delete(test_user)
    db_session.commit()

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert response.status_code == 401


def test_refresh_rotation_rejects_replayed_refresh_token(client, test_user):
    """R02-D4：refresh 消费即吊销——同一 refresh token 第二次使用必须 401。"""
    login = client.post(
        "/api/v1/auth/token",
        data={"username": "testuser", "password": "testpass123"},
    )
    refresh_token = login.json()["refresh_token"]

    first = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert first.status_code == 200

    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert replay.status_code == 401
