"""Resource pool permission tests."""


def test_resource_pool_endpoints_require_admin(client, admin_headers, auth_headers):
    unauth_list = client.get("/api/v1/resource-pools")
    assert unauth_list.status_code == 401

    operator_list = client.get("/api/v1/resource-pools", headers=auth_headers)
    assert operator_list.status_code == 403

    admin_list = client.get("/api/v1/resource-pools", headers=admin_headers)
    assert admin_list.status_code == 200


# ── #955: available 端点（普通用户可选列表，config 剥密）──────────────────────


def _create_pool_with_secret(client, admin_headers, name="wifi-secret"):
    resp = client.post(
        "/api/v1/resource-pools",
        json={
            "name": name,
            "resource_type": "wifi",
            "config": {"ssid": "Lab-5G", "password": "top-secret", "band": "5g"},
            "max_concurrent_devices": 10,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def test_available_allows_normal_user_without_password(client, admin_headers, auth_headers):
    """验收：普通用户可拉取可选池列表，且 config 不含 password。"""
    pool_id = _create_pool_with_secret(client, admin_headers)

    resp = client.get(
        "/api/v1/resource-pools/available?resource_type=wifi",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    pools = resp.json()["data"]
    assert any(p["id"] == pool_id for p in pools)
    target = next(p for p in pools if p["id"] == pool_id)
    assert "password" not in target["config"]
    assert target["config"]["ssid"] == "Lab-5G"  # 非机密展示字段保留


def test_available_excludes_inactive_pools(client, admin_headers, auth_headers):
    """available 语义=可选：inactive 池不出现。"""
    pool_id = _create_pool_with_secret(client, admin_headers, name="wifi-off")
    client.put(
        f"/api/v1/resource-pools/{pool_id}",
        json={
            "name": "wifi-off",
            "resource_type": "wifi",
            "config": {"ssid": "x", "password": "s"},
            "max_concurrent_devices": 10,
            "is_active": False,
        },
        headers=admin_headers,
    )

    resp = client.get(
        "/api/v1/resource-pools/available?resource_type=wifi",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert all(p["id"] != pool_id for p in resp.json()["data"])


def test_admin_full_list_still_contains_password(client, admin_headers, auth_headers):
    """回归：管理接口保持完整 config（含凭据），仅限 admin。"""
    pool_id = _create_pool_with_secret(client, admin_headers, name="wifi-admin")

    # 普通用户仍不能访问管理 list
    operator_list = client.get("/api/v1/resource-pools", headers=auth_headers)
    assert operator_list.status_code == 403

    admin_list = client.get("/api/v1/resource-pools", headers=admin_headers)
    assert admin_list.status_code == 200
    target = next(p for p in admin_list.json()["data"] if p["id"] == pool_id)
    assert target["config"]["password"] == "top-secret"


def test_loads_includes_public_config_without_password(client, admin_headers):
    """#954：loads 列表含 SSID 等展示字段，不含 password。"""
    pool_id = _create_pool_with_secret(client, admin_headers, name="wifi-loads")

    resp = client.get("/api/v1/resource-pools/loads", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    target = next(p for p in resp.json()["data"] if p["id"] == pool_id)
    assert target["config"]["ssid"] == "Lab-5G"
    assert "password" not in target["config"]
