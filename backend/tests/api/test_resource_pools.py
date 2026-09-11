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


def test_delete_pool_with_allocations_is_409(
    client, db_session, admin_headers, gate_chain,
):
    """#937: 有分配记录的资源池硬删除返回 409（不裸 500）。"""
    from datetime import datetime, timezone

    from backend.models.job import JobInstance
    from backend.models.plan_run import PlanRun
    from backend.models.resource_pool import ResourceAllocation

    pool_id = _create_pool_with_secret(client, admin_headers, name="del-pool")
    run = PlanRun(
        plan_id=gate_chain["plan"].id, status="RUNNING", failure_threshold=0.1,
        plan_snapshot={"name": "x", "plan_id": gate_chain["plan"].id},
        run_type="MANUAL", started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.flush()
    job = JobInstance(
        plan_run_id=run.id, plan_id=gate_chain["plan"].id,
        device_id=gate_chain["device_a"].id, host_id="h-A",
        status="RUNNING",
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(ResourceAllocation(
        job_instance_id=job.id, resource_pool_id=pool_id,
        device_id=gate_chain["device_a"].id,
    ))
    db_session.commit()

    resp = client.delete(
        f"/api/v1/resource-pools/{pool_id}", headers=admin_headers,
    )
    assert resp.status_code == 409, resp.text
    assert "分配" in resp.json()["detail"]


def test_delete_pool_without_allocations_succeeds(client, admin_headers):
    pool_id = _create_pool_with_secret(client, admin_headers, name="del-clean-pool")
    resp = client.delete(
        f"/api/v1/resource-pools/{pool_id}", headers=admin_headers,
    )
    assert resp.status_code == 204, resp.text


def test_loads_includes_public_config_without_password(client, admin_headers):
    """#954：loads 列表含 SSID 等展示字段，不含 password。"""
    pool_id = _create_pool_with_secret(client, admin_headers, name="wifi-loads")

    resp = client.get("/api/v1/resource-pools/loads", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    target = next(p for p in resp.json()["data"] if p["id"] == pool_id)
    assert target["config"]["ssid"] == "Lab-5G"
    assert "password" not in target["config"]
