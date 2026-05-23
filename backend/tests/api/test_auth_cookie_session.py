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
    token = create_access_token(data={"sub": "testuser", "role": "user"})

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
