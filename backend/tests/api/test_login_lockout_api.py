"""登录锁定 API 级测试（#281）。"""


def _bad_login(client, username: str):
    return client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": "wrongpass123"},
    )


class TestLoginLockoutApi:
    def test_unknown_usernames_share_one_bucket(self, client):
        """#281 CR Major:未注册用户名共享一个桶——5 个不同未知名各失败一次
        后,第 6 个未知名也 429。按 ``unknown:<用户名>`` 逐名跟踪的键空间
        由攻击者控制,可先占满跟踪表让真实账户失去锁定保护。"""
        for i in range(5):
            resp = _bad_login(client, f"ghost_{i}")
            assert resp.status_code == 401, resp.text
        resp = _bad_login(client, "ghost_final")
        assert resp.status_code == 429, resp.text

    def test_locked_account_rejects_correct_password(self, client, test_user):
        """真实账户达阈值后,锁定期内即使密码正确也 429。"""
        for _ in range(5):
            assert _bad_login(client, "testuser").status_code == 401
        ok = client.post(
            "/api/v1/auth/login",
            data={"username": "testuser", "password": "testpass123"},
        )
        assert ok.status_code == 429

    def test_lockout_hit_recorded_in_audit(self, client, admin_headers, test_user):
        """#281 CR:锁定命中(429)是暴力破解关键信号,必须进审计。"""
        for _ in range(5):
            client.post(
                "/api/v1/auth/login",
                data={"username": "testuser", "password": "wrongpass123"},
            )
        resp = client.post(
            "/api/v1/auth/login",
            data={"username": "testuser", "password": "wrongpass123"},
        )
        assert resp.status_code == 429
        logs = client.get("/api/v1/audit-logs", headers=admin_headers).json()
        assert any(
            item["action"] == "login_locked" and item["resource_id"] == "testuser"
            for item in logs["items"]
        )
