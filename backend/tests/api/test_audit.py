"""Tests for audit log API routes"""

from backend.core.security import REFRESH_COOKIE_NAME


class TestAuditLogs:
    def test_list_audit_logs(self, client, admin_headers):
        response = client.get("/api/v1/audit-logs", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_list_audit_logs_with_filters(self, client, admin_headers):
        response = client.get(
            "/api/v1/audit-logs",
            params={"resource_type": "task", "action": "create"},
            headers=admin_headers,
        )
        assert response.status_code == 200

    def test_list_audit_logs_requires_auth(self, client):
        response = client.get("/api/v1/audit-logs")
        assert response.status_code in (401, 403)

    def test_login_failed_audit_persists(self, client, admin_headers):
        """#281 P1:失败认证审计必须独立落库——此前 record_audit 后直接抛
        异常,审计行随会话关闭被回滚(get_db 不自动 commit)。"""
        resp = client.post(
            "/api/v1/auth/login",
            data={"username": "ghost_user", "password": "wrongpass123"},
        )
        assert resp.status_code == 401
        logs = client.get("/api/v1/audit-logs", headers=admin_headers).json()
        assert any(
            item["action"] == "login_failed" and item["resource_id"] == "ghost_user"
            for item in logs["items"]
        )

    def test_audit_ip_not_spoofable_via_xff(self, client, admin_headers):
        """#281 CR Major:审计 IP 不得直接采信 X-Forwarded-For 链首
        (客户端可伪造前置值污染审计来源),必须经可信代理解析。"""
        resp = client.post(
            "/api/v1/auth/login",
            data={"username": "ghost_spoof", "password": "wrongpass123"},
            headers={"X-Forwarded-For": "9.9.9.9, 1.2.3.4"},
        )
        assert resp.status_code == 401
        logs = client.get("/api/v1/audit-logs", headers=admin_headers).json()
        entry = next(i for i in logs["items"] if i["action"] == "login_failed")
        assert entry["ip_address"] != "9.9.9.9"  # 伪造链首不得成为审计 IP

    def test_change_password_failed_audit_persists(self, client, admin_headers):
        """#281 P1:改密失败审计同样必须持久化(users.py 失败路径)。"""
        resp = client.post(
            "/api/v1/users/change-password",
            json={"old_password": "wrongpass999", "new_password": "newpass1234"},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        logs = client.get("/api/v1/audit-logs", headers=admin_headers).json()
        assert any(item["action"] == "change_password_failed" for item in logs["items"])

    def test_token_failed_audit_persists(self, client, admin_headers):
        """#281 P1 缺测项:token_failed 与 login_failed 同模式,必须落库。"""
        resp = client.post(
            "/api/v1/auth/token",
            data={"username": "ghost_token", "password": "wrongpass123"},
        )
        assert resp.status_code == 401
        logs = client.get("/api/v1/audit-logs", headers=admin_headers).json()
        assert any(
            item["action"] == "token_failed" and item["resource_id"] == "ghost_token"
            for item in logs["items"]
        )

    def test_refresh_rejected_audit_persists(self, client, admin_headers):
        """#281 P1 缺测项:logout 后(黑名单)的 refresh token 被拒时,
        拒绝审计必须落库(refresh 路径此前写审计后直接 return,随会话回滚)。"""
        login = client.post(
            "/api/v1/auth/login",
            data={"username": "admin", "password": "adminpass123"},
        )
        assert login.status_code == 200
        refresh_cookie = login.cookies.get(REFRESH_COOKIE_NAME)
        assert refresh_cookie

        logout = client.post("/api/v1/auth/logout")
        assert logout.status_code == 200

        rejected = client.post(
            "/api/v1/auth/refresh",
            cookies={REFRESH_COOKIE_NAME: refresh_cookie},
        )
        assert rejected.status_code == 401

        logs = client.get("/api/v1/audit-logs", headers=admin_headers).json()
        assert any(item["action"] == "refresh_rejected" for item in logs["items"])
