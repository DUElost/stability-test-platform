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
        静默截断(19 个 4 字节 emoji = 76 字节 > 72,必须 422)。"""
        response = client.post(
            "/api/v1/users",
            json={"username": "bytepw", "password": "😀" * 19, "role": "user"},
            headers=admin_headers,
        )
        assert response.status_code == 422

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
