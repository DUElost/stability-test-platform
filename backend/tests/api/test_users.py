"""Tests for users API routes"""
import pytest


class TestListUsers:
    def test_list_users(self, client, admin_headers):
        response = client.get("/api/v1/users", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data


class TestCreateUser:
    def test_create_user(self, client, admin_headers):
        response = client.post(
            "/api/v1/users",
            json={"username": "newuser", "password": "pass123", "role": "user"},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["username"] == "newuser"

    def test_create_user_duplicate(self, client, admin_headers):
        client.post("/api/v1/users", json={"username": "dup", "password": "pass123", "role": "user"}, headers=admin_headers)
        resp = client.post("/api/v1/users", json={"username": "dup", "password": "pass123", "role": "user"}, headers=admin_headers)
        assert resp.status_code == 400


class TestToggleActive:
    def test_toggle_active(self, client, admin_headers):
        r = client.post("/api/v1/users", json={"username": "toggle", "password": "pass123", "role": "user"}, headers=admin_headers)
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
