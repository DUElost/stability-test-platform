"""Resource pool permission tests."""


def test_resource_pool_endpoints_require_admin(client, admin_headers, auth_headers):
    unauth_list = client.get("/api/v1/resource-pools")
    assert unauth_list.status_code == 401

    operator_list = client.get("/api/v1/resource-pools", headers=auth_headers)
    assert operator_list.status_code == 403

    admin_list = client.get("/api/v1/resource-pools", headers=admin_headers)
    assert admin_list.status_code == 200
