"""Tests for users API routes"""


class TestListUsers:
    def test_list_users(self, client, admin_headers):
        response = client.get("/api/v1/users", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_list_users_accepts_cookie_session(self, client, admin_user):
        login = client.post(
            "/api/v1/auth/login",
            data={"username": "admin", "password": "adminpass123"},
        )
        assert login.status_code == 200

        response = client.get("/api/v1/users")
        assert response.status_code == 200


class TestCreateUser:
    def test_create_user(self, client, admin_headers):
        response = client.post(
            "/api/v1/users",
            json={"username": "newuser", "password": "pass12345", "role": "user"},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["username"] == "newuser"

    def test_create_user_rejects_short_password(self, client, admin_headers):
        """#281 P1:密码最低长度已改为 8,6 位密码必须 422。"""
        response = client.post(
            "/api/v1/users",
            json={"username": "shortpw", "password": "pass123", "role": "user"},
            headers=admin_headers,
        )
        assert response.status_code == 422

    def test_create_user_rejects_password_over_72_bytes(self, client, admin_headers):
        """#281 CR Major:bcrypt 只使用前 72 字节——多字节字符密码不得被
        静默截断(19 个 4 字节 emoji = 76 字节 > 72,必须 422)。

        #2406：文案必须是**面向操作者**的中文可执行说明——它会原样出现在前端提示里
        （原先那句英文技术串正是「填完表单只看到一句看不懂的话」的来源）。
        """
        response = client.post(
            "/api/v1/users",
            json={"username": "bytepw", "password": "😀" * 19, "role": "user"},
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "72 字节" in response.text, "边界数值要出现在提示里（用户据此改密码）"
        assert "must not exceed 72 bytes" not in response.text, "不得回退成英文技术串"

    def test_create_user_accepts_hyphenated_username(self, client, admin_headers):
        """#2406：`stp-tester` 这类**带连字符**的用户名必须被接受。

        现场（2026-09-16）：前端字符集只允许 `[a-zA-Z0-9_]`，把连字符挡在表单里，
        提交根本没发出（nginx 访问日志零 `POST /api/v1/users`），用户只看到
        「填完了建不出来」。后端本无该限制——现两端同判据（`^[A-Za-z0-9_.-]+$`）。
        """
        resp = client.post(
            "/api/v1/users",
            json={"username": "stp-tester", "password": "pass-12345", "role": "user"},
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        assert resp.json()["username"] == "stp-tester"

    def test_create_user_rejects_username_outside_charset(self, client, admin_headers):
        """字符集外的用户名（空格/中文）→ 422，与前端提示同一判据。"""
        for bad in ("bad name", "测试账号"):
            resp = client.post(
                "/api/v1/users",
                json={"username": bad, "password": "pass-12345", "role": "user"},
                headers=admin_headers,
            )
            assert resp.status_code == 422, f"{bad!r} 应被拒: {resp.text}"

    def test_create_user_duplicate(self, client, admin_headers):
        client.post("/api/v1/users", json={"username": "dup", "password": "pass12345", "role": "user"}, headers=admin_headers)
        resp = client.post("/api/v1/users", json={"username": "dup", "password": "pass12345", "role": "user"}, headers=admin_headers)
        assert resp.status_code == 400


class TestToggleActive:
    def test_toggle_active(self, client, admin_headers):
        r = client.post("/api/v1/users", json={"username": "toggle", "password": "pass12345", "role": "user"}, headers=admin_headers)
        uid = r.json()["id"]
        resp = client.post(f"/api/v1/users/{uid}/toggle-active", headers=admin_headers)
        assert resp.status_code == 200


class TestChangePassword:
    def test_change_password(self, client, auth_headers):
        resp = client.post(
            "/api/v1/users/change-password",
            json={"old_password": "testpass123", "new_password": "newpass456"},
            headers=auth_headers,
        )
        assert resp.status_code == 200


class TestUserHardDeleteGuards:
    """#937: 有审计引用的用户硬删除返回 409（不裸 500）。"""

    @staticmethod
    def _create(client, admin_headers, username):
        created = client.post(
            "/api/v1/users",
            json={"username": username, "password": "pass12345", "role": "user"},
            headers=admin_headers,
        )
        assert created.status_code == 200, created.text
        return created.json()["id"]

    def test_delete_user_with_audit_records_is_409(
        self, client, db_session, admin_headers,
    ):
        from backend.models.audit import AuditLog
        from backend.models.user import User as UserModel

        uid = self._create(client, admin_headers, "del-audited")
        db_session.add(AuditLog(
            action="login", resource_type="user", resource_id=uid,
            user_id=uid, username="del-audited",
        ))
        db_session.commit()

        resp = client.delete(f"/api/v1/users/{uid}", headers=admin_headers)
        assert resp.status_code == 409, resp.text
        assert db_session.get(UserModel, uid) is not None

    def test_delete_user_without_dependencies_succeeds(
        self, client, db_session, admin_headers,
    ):
        from backend.models.user import User as UserModel

        uid = self._create(client, admin_headers, "del-clean")
        resp = client.delete(f"/api/v1/users/{uid}", headers=admin_headers)
        assert resp.status_code == 204, resp.text
        assert db_session.get(UserModel, uid) is None
