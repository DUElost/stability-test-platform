"""#3875 v1.23：影子轮下发授权跟随实际使用的连接。

#4044 收口了本文件的过渡判据：预筛不再看 artifact digest，改看当前心跳显式上报的
能力；「连接必须真的支持新轮次载荷」这一半保持不变，并按实例 id 与心跳证据绑定。
"""

import functools
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import event

from backend.models.host import Host
from backend.realtime import agent_sid_registry, socketio_redis, socketio_server
from backend.services import artifact_digest, shadow_scan_rounds as shadow
from backend.services.agent_capabilities import load_host_capability_evidence
from backend.agent.contracts.capabilities import SCAN_ROUND_V1
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


async def _connect(namespace, host_id, sid, *, capable=False, instance=None):
    auth = {"host_id": host_id, "agent_secret": "test-socket-secret"}
    if capable:
        auth["capabilities"] = [SCAN_ROUND_V1]
    if instance is not None:
        # 能力握手与实例 id 总是一起到达：发送守卫据此把心跳证据绑到当前连接。
        auth["agent_instance_id"] = instance
    await namespace.on_connect(sid, {}, auth)


def _heartbeat(client, host_id, digest, instance, *, capable=True):
    payload = {
        "host_id": host_id,
        "status": "ONLINE",
        "agent_artifact_digest": digest,
        "agent_instance_id": instance,
    }
    if capable:
        payload["capabilities"] = [SCAN_ROUND_V1]
    # capable=False 即旧构建：连 capabilities 键都不带，正是兼容矩阵的第一行。
    response = client.post("/api/v1/heartbeat", json=payload)
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
    assert unit.get("last_dispatched_at")


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
    await _connect(namespace, host_id, "sid-new", capable=True, instance="new-agent")
    shadow.sweep_shadow_scan_rounds()
    row, = _rows(factory, run_id)
    loop = asyncio.get_running_loop()

    real_evaluate = shadow.evaluate_host_capability
    fired = []

    def evaluate_after_host_snapshot(host, **kwargs):
        # 预筛已经读完 host 快照；就在此刻旧构建重连并上报心跳。
        # 快照里的能力证明仍然成立，所以必须靠发送前复核与连接守卫拦住这一发。
        if not fired:
            fired.append(True)
            asyncio.run_coroutine_threadsafe(_connect(namespace, host_id, "sid-old"), loop).result(timeout=5)
            _heartbeat(client, host_id, "", "old-agent", capable=False)
            with factory() as db:
                assert not load_host_capability_evidence(db, host_id).verified
        return real_evaluate(host, **kwargs)

    monkeypatch.setattr(shadow, "evaluate_host_capability", evaluate_after_host_snapshot)
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
        await _connect(namespace, host_id, f"sid-{host_id}", capable=True, instance="new-agent")
    shadow.sweep_shadow_scan_rounds()
    row, = _rows(factory, run_id)

    async def ack(event, data, *, to, **kwargs):
        if to == f"sid-{first}":
            await _connect(namespace, second, "sid-second-old")
            await asyncio.to_thread(
                functools.partial(_heartbeat, client, second, "", "old-agent", capable=False),
            )
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
    await _connect(namespace, host_id, "sid-new", capable=True, instance="new-agent")
    await _connect(namespace, host_id, "sid-old")
    with factory() as db:
        assert load_host_capability_evidence(db, host_id).verified

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
    await _connect(namespace, host_id, "sid-new", capable=True, instance="new-agent")
    clock.now.return_value = now + timedelta(seconds=shadow._SCAN_TASK_SAQ_TIMEOUT)
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


@pytest.mark.asyncio
async def test_missing_local_sid_backs_off_writes_and_retries_after_capable_reconnect(
    shadow_env, shadow_socket, sample_host, client, monkeypatch,
):
    factory, run_id, _, queued, settings = shadow_env
    namespace, send = shadow_socket
    host_id = sample_host.id
    _scan_scope(monkeypatch, host_id)
    desired = artifact_digest.compute_desired_artifact_digest()
    _heartbeat(client, host_id, desired, "remote-agent")
    now = datetime.now(timezone.utc)
    clock = Mock(wraps=datetime)
    clock.now.return_value = now
    monkeypatch.setattr(shadow, "datetime", clock)
    timeout = shadow._SCAN_TASK_SAQ_TIMEOUT
    poll = settings.auto_archive_poll_interval_seconds
    shadow.sweep_shadow_scan_rounds(now=now)
    row, = _rows(factory, run_id)
    updates = []

    def count_coverage_updates(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE scan_round SET ") and "expected_set" in statement.partition(" WHERE ")[0]:
            updates.append(statement)

    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", count_coverage_updates)
    try:
        await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)
        for elapsed in range(poll, timeout, poll):
            clock.now.return_value = now + timedelta(seconds=elapsed)
            queued.reset_mock()
            shadow.sweep_shadow_scan_rounds(now=clock.now.return_value)
            if queued.called:
                queued.assert_called_once()
                assert queued.call_args.args == ("scan_task",)
                assert queued.call_args.kwargs["fact_round_id"] == row.id
                await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)
        assert len(updates) <= 2
        send.assert_not_awaited()
        blocked, = _rows(factory, run_id)
        assert blocked.expected_set[0]["coverage"] == "MISSING"
        assert blocked.expected_set[0]["reason"] == "capability_unverified"
        assert blocked.expected_set[0]["last_dispatched_at"] == now.isoformat()

        await _connect(namespace, host_id, "sid-local", capable=True, instance="remote-agent")
        clock.now.return_value = now + timedelta(seconds=timeout - 1)
        queued.reset_mock()
        shadow.sweep_shadow_scan_rounds(now=clock.now.return_value)
        queued.assert_not_called()
        assert len(updates) <= 2
    finally:
        event.remove(engine, "before_cursor_execute", count_coverage_updates)

    clock.now.return_value = now + timedelta(seconds=timeout)
    shadow.sweep_shadow_scan_rounds(now=clock.now.return_value)
    queued.assert_called_once()
    assert queued.call_args.kwargs["fact_round_id"] == row.id
    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)
    send.assert_awaited_once()
    assert send.await_args.kwargs["to"] == "sid-local"
    retried, = _rows(factory, run_id)
    assert retried.id == row.id
    assert "reason" not in retried.expected_set[0]
    assert retried.expected_set[0]["last_dispatched_at"] == clock.now.return_value.isoformat()
