"""升级门禁 API（#1249）：agent 门禁端点 + UI 热更新路由的拒绝/释放语义。

UI 路由的同类断言在重构共享服务后必须保持——它们锁的是「所有升级入口
一条协议」；agent 端点断言锁的是 Ansible 等外部入口的可用性。
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.services.host_maintenance import in_maintenance_window


GATE_URL = "/api/v1/agent/hosts/{host_id}/upgrade-gate"
RELEASE_URL = "/api/v1/agent/hosts/{host_id}/upgrade-gate/release"


def _acquire(client, host_id: str, **body):
    return client.post(GATE_URL.format(host_id=host_id), json=body)


def _release(client, host_id: str, holder: str):
    return client.post(RELEASE_URL.format(host_id=host_id), json={"holder": holder})


def test_agent_gate_acquires_and_releases_window(client, db_session, sample_host):
    resp = _acquire(client, sample_host.id, holder="ansible:test:1")

    assert resp.status_code == 200, resp.text
    payload = resp.json()["data"]
    assert payload["holder"] == "ansible:test:1"
    assert payload["active_jobs"] == []
    db_session.refresh(sample_host)
    assert in_maintenance_window(sample_host.maintenance_until)

    release = _release(client, sample_host.id, "ansible:test:1")
    assert release.status_code == 200, release.text
    db_session.refresh(sample_host)
    assert not in_maintenance_window(sample_host.maintenance_until)


def test_agent_gate_rejects_active_jobs_by_default(client, sample_host, sample_job_instance):
    resp = _acquire(client, sample_host.id)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "HOST_HAS_ACTIVE_JOBS"
    assert [j["id"] for j in detail["active_jobs"]] == [sample_job_instance.id]
    assert not (sample_host.maintenance_until)


def test_agent_gate_aborts_running_jobs_when_requested(client, db_session, sample_host, sample_job_instance):
    resp = _acquire(client, sample_host.id, abort_running_jobs=True, holder="ansible:test:2")

    assert resp.status_code == 200, resp.text
    assert sample_job_instance.id in resp.json()["data"]["aborted_summary"]["aborted_jobs"]
    db_session.refresh(sample_job_instance)
    assert sample_job_instance.status == "ABORTED"


def test_agent_gate_conflicts_with_existing_window(client, db_session, sample_host):
    assert _acquire(client, sample_host.id, holder="ansible:test:3").status_code == 200

    resp = _acquire(client, sample_host.id, holder="ansible:test:4")

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "HOST_IN_MAINTENANCE"


def test_agent_gate_unknown_host_returns_404(client):
    resp = _acquire(client, "no-such-host")

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "HOST_NOT_FOUND"


def test_agent_gate_rejects_retired_host_with_409(client, db_session, sample_host):
    """#2638：退役主机申请升级窗口要 409 `HOST_RETIRED`，不是框架层 500。

    409 分支在 `raise_upgrade_gate_http` 里早就写好了（ADR-0038 D5），但调用方的
    `except` 元组漏了 `HostRetiredError` ⇒ 分支**从唯一入参路径不可达**，异常原样上抛。
    这条走真实端点（不 mock 映射函数），所以「分支再次变成不可达」会直接红在这里，
    而不是红在一个只测映射函数的用例上。
    """
    # ADR-0038 D1：退役不改写 status，只置 retired_at——门禁必须活读它
    sample_host.retired_at = datetime.now(timezone.utc)
    db_session.commit()

    resp = _acquire(client, sample_host.id, holder="ansible:retired")

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "HOST_RETIRED"
    # 拒绝在拿窗口之前抛出：拒绝路径不得占用维护窗口（与活跃 Job 拒绝同一约定）
    db_session.expire_all()
    assert not in_maintenance_window(sample_host.maintenance_until)


def test_agent_gate_rejects_wrong_secret(client, sample_host, monkeypatch):
    monkeypatch.setenv("AGENT_SECRET", "expected-secret")

    resp = client.post(
        GATE_URL.format(host_id=sample_host.id),
        json={},
        headers={"X-Agent-Secret": "wrong-secret"},
    )

    assert resp.status_code == 401


def _patch_hot_update(monkeypatch, *, ok: bool = True):
    from backend.api.routes import hosts as hosts_routes

    class _Creds:
        user = "android"
        password = "secret"
        key_path = ""
        known_hosts_path = ""

    monkeypatch.setattr(
        hosts_routes,
        "resolve_host_ssh_credentials",
        lambda host, inventory_lookup=None: (_Creds(), False),
    )
    monkeypatch.setattr(
        hosts_routes,
        "execute_hot_update",
        lambda **kwargs: {
            "ok": ok,
            "message": "OK" if ok else "boom",
            "deps_refreshed": False,
            "env_keys_synced": [],
            "env_paths_missing": {},
            "code_version": "test-sha",
        },
    )


def test_hot_update_route_rejects_active_jobs(client, admin_headers, sample_host, sample_job_instance):
    resp = client.post(f"/api/v1/hosts/{sample_host.id}/hot-update", headers=admin_headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "HOST_HAS_ACTIVE_JOBS"


def test_hot_update_route_releases_window_on_success(
    monkeypatch, client, db_session, admin_headers, sample_host
):
    _patch_hot_update(monkeypatch, ok=True)

    resp = client.post(f"/api/v1/hosts/{sample_host.id}/hot-update", headers=admin_headers)

    assert resp.status_code == 200, resp.text
    db_session.refresh(sample_host)
    assert not in_maintenance_window(sample_host.maintenance_until)


def test_hot_update_route_releases_window_on_failure(
    monkeypatch, client, db_session, admin_headers, sample_host
):
    _patch_hot_update(monkeypatch, ok=False)

    resp = client.post(f"/api/v1/hosts/{sample_host.id}/hot-update", headers=admin_headers)

    assert resp.status_code == 502
    db_session.refresh(sample_host)
    assert not in_maintenance_window(sample_host.maintenance_until)
