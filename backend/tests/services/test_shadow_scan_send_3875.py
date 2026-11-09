"""#3875 v1.23: shadow scan authorization follows the connection used to send."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.models.host import Host
from backend.realtime import agent_sid_registry, socketio_redis, socketio_server
from backend.services import artifact_digest, shadow_scan_rounds as shadow
from backend.tasks import saq_tasks
from backend.tests.realtime.test_agent_rpc import patched_namespace as patched_namespace
from backend.tests.services.test_shadow_scan_rounds import _rows, shadow_env as shadow_env


@pytest.fixture
def shadow_socket(client, monkeypatch, patched_namespace):
    send = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(socketio_server, "_sio", SimpleNamespace(call=send))
    monkeypatch.setattr(socketio_server, "_agent_ns", patched_namespace)
    monkeypatch.setattr(socketio_server, "require_agent_secret", lambda: "test-socket-secret")
    monkeypatch.setattr(agent_sid_registry, "register_agent_owner", AsyncMock())
    monkeypatch.setattr(agent_sid_registry, "unregister_agent_owner", AsyncMock())
    return patched_namespace, send


async def _connect(namespace, host_id, sid, *, capable=False):
    auth = {"host_id": host_id, "agent_secret": "test-socket-secret"}
    if capable:
        auth["capabilities"] = ["scan_round_v1"]
    await namespace.on_connect(sid, {}, auth)


def _heartbeat(client, host_id, digest, instance):
    response = client.post("/api/v1/heartbeat", json={
        "host_id": host_id,
        "status": "ONLINE",
        "agent_artifact_digest": digest,
        "agent_instance_id": instance,
    })
    assert response.status_code == 200


def _scan_scope(monkeypatch, *host_ids):
    monkeypatch.setattr(shadow, "_expected_units", lambda *_: [
        {"host_id": host_id, "platform": "mtk"} for host_id in host_ids
    ])
    monkeypatch.setattr(shadow, "iter_plan_run_scan_hosts", lambda *_: [
        (host_id, "ONLINE", False) for host_id in host_ids
    ])
    monkeypatch.setattr(shadow, "build_scan_now_payload", lambda db, run_id, host_id, **kwargs: {
        "plan_run_id": run_id,
        "device_serials": ["test-serial"],
        "fact_round_id": kwargs["fact_round_id"],
    })


def _assert_unverified(factory, run_id, host_id):
    row, = _rows(factory, run_id)
    unit = next(unit for unit in row.expected_set if unit["host_id"] == host_id)
    assert unit["coverage"] == "MISSING"
    assert unit["reason"] == "capability_unverified"
    assert "last_dispatched_at" not in unit


@pytest.mark.asyncio
async def test_empty_heartbeat_after_host_snapshot_must_stop_scan_now(
    shadow_env, shadow_socket, sample_host, client, monkeypatch,
):
    factory, run_id, _, _, _ = shadow_env
    namespace, send = shadow_socket
    host_id = sample_host.id
    _scan_scope(monkeypatch, host_id)
    desired = artifact_digest.compute_desired_artifact_digest()
    _heartbeat(client, host_id, desired, "new-agent")
    await _connect(namespace, host_id, "sid-new", capable=True)
    shadow.sweep_shadow_scan_rounds()
    row, = _rows(factory, run_id)
    loop = asyncio.get_running_loop()

    def desired_after_host_snapshot(*, kind):
        assert kind == artifact_digest.ARTIFACT_KIND_CODE
        asyncio.run_coroutine_threadsafe(_connect(namespace, host_id, "sid-old"), loop).result(timeout=5)
        _heartbeat(client, host_id, "", "old-agent")
        with factory() as db:
            host = db.get(Host, host_id)
            assert host.agent_artifact_digest_reported_at < host.last_heartbeat
        return desired

    monkeypatch.setattr(shadow, "compute_desired_artifact_digest", desired_after_host_snapshot)
    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)
    send.assert_not_awaited()
    _assert_unverified(factory, run_id, host_id)


@pytest.mark.asyncio
async def test_empty_heartbeat_while_waiting_for_other_host_ack_must_stop_next_scan_now(
    shadow_env, shadow_socket, sample_host, client, monkeypatch,
):
    factory, run_id, _, _, _ = shadow_env
    namespace, send = shadow_socket
    first, second = sample_host.id, "shadow-second"
    with factory() as db:
        db.add(Host(id=second, hostname=second, status="ONLINE"))
        db.commit()
    _scan_scope(monkeypatch, first, second)
    desired = artifact_digest.compute_desired_artifact_digest()
    for host_id in (first, second):
        _heartbeat(client, host_id, desired, "new-agent")
        await _connect(namespace, host_id, f"sid-{host_id}", capable=True)
    shadow.sweep_shadow_scan_rounds()
    row, = _rows(factory, run_id)

    async def ack(event, data, *, to, **kwargs):
        if to == f"sid-{first}":
            await _connect(namespace, second, "sid-second-old")
            await asyncio.to_thread(_heartbeat, client, second, "", "old-agent")
        return {"ok": True}

    send.side_effect = ack
    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)
    assert [call.kwargs["to"] for call in send.await_args_list] == [f"sid-{first}"]
    _assert_unverified(factory, run_id, second)


@pytest.mark.asyncio
async def test_old_agent_connects_before_heartbeat_and_cannot_receive_shadow_scan(
    shadow_env, shadow_socket, sample_host, client, monkeypatch, caplog,
):
    factory, run_id, _, _, _ = shadow_env
    namespace, send = shadow_socket
    host_id = sample_host.id
    _scan_scope(monkeypatch, host_id)
    desired = artifact_digest.compute_desired_artifact_digest()
    _heartbeat(client, host_id, desired, "new-agent")
    await _connect(namespace, host_id, "sid-new", capable=True)
    await _connect(namespace, host_id, "sid-old")
    with factory() as db:
        assert shadow._shadow_capability_verified(db.get(Host, host_id), desired)

    shadow.sweep_shadow_scan_rounds()
    row, = _rows(factory, run_id)
    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)
    send.assert_not_awaited()
    _assert_unverified(factory, run_id, host_id)
    assert (
        f"shadow_scan_capability_unverified_at_send plan_run={run_id} fact_round_id={row.id} "
        f"host={host_id} platform=mtk"
    ) in caplog.messages


@pytest.mark.asyncio
async def test_capable_reconnect_dispatches_again_within_same_round(
    shadow_env, shadow_socket, sample_host, client, monkeypatch,
):
    factory, run_id, _, queued, _ = shadow_env
    namespace, send = shadow_socket
    host_id = sample_host.id
    _scan_scope(monkeypatch, host_id)
    desired = artifact_digest.compute_desired_artifact_digest()
    _heartbeat(client, host_id, desired, "new-agent")
    await _connect(namespace, host_id, "sid-old")
    now = datetime.now(timezone.utc)
    clock = Mock(wraps=datetime)
    clock.now.return_value = now
    monkeypatch.setattr(shadow, "datetime", clock)

    shadow.sweep_shadow_scan_rounds(now=now)
    row, = _rows(factory, run_id)
    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)
    send.assert_not_awaited()
    _assert_unverified(factory, run_id, host_id)

    await namespace.on_disconnect("sid-old")
    await _connect(namespace, host_id, "sid-new", capable=True)
    clock.now.return_value = now + timedelta(seconds=120)
    queued.reset_mock()
    shadow.sweep_shadow_scan_rounds(now=clock.now.return_value)
    queued.assert_called_once()
    assert queued.call_args.kwargs["fact_round_id"] == row.id

    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)
    send.assert_awaited_once_with(
        "control",
        {"command": "scan_now", "payload": {
            "plan_run_id": run_id, "fact_round_id": row.id, "device_serials": ["test-serial"],
        }},
        namespace="/agent", timeout=3.0, to="sid-new", ignore_queue=True,
    )
    only, = _rows(factory, run_id)
    assert only.id == row.id
    assert "reason" not in only.expected_set[0]
    assert only.expected_set[0]["last_dispatched_at"] == clock.now.return_value.isoformat()


@pytest.mark.asyncio
async def test_shadow_requires_local_sid_but_current_scan_can_use_room(
    shadow_env, shadow_socket, sample_host, client, monkeypatch,
):
    factory, run_id, _, _, _ = shadow_env
    _, send = shadow_socket
    host_id = sample_host.id
    _scan_scope(monkeypatch, host_id)
    desired = artifact_digest.compute_desired_artifact_digest()
    _heartbeat(client, host_id, desired, "remote-agent")
    monkeypatch.setattr(socketio_redis, "socketio_redis_adapter_enabled", lambda: True)

    shadow.sweep_shadow_scan_rounds()
    row, = _rows(factory, run_id)
    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)
    send.assert_not_awaited()
    _assert_unverified(factory, run_id, host_id)

    payload = {"plan_run_id": run_id, "is_final": False, "device_serials": [], "run_date_stamps": []}
    assert await socketio_server.call_agent_control(host_id, "scan_now", payload=payload)
    send.assert_awaited_once_with(
        "control", {"command": "scan_now", "payload": payload},
        namespace="/agent", timeout=3.0, room=f"agent:{host_id}",
    )
