"""#4044 验收 2：从 Agent 心跳 → 控制面接收 → 轮次预筛 → Socket.IO 实际发送的反例矩阵。

判据落在**实际发送次数**上：预筛判得对不代表没发出去，所以每条断言都挂在
Socket.IO 的发送调用点上，并要求轮次台账留下 `capability_unverified`。

覆盖的场景：旧 Agent、当前心跳漏 capabilities 字段、显式空列表、迟到心跳、
预筛后重连（换旧构建 / 换实例）、已固定 DEDUP_FIRST 后能力失效与恢复、
带缺口 settled 的审计线索，以及现行 scan_now 不受守卫影响。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.agent.contracts.capabilities import SCAN_ROUND_V1
from backend.models.host import Host
from backend.realtime import agent_sid_registry, socketio_server
from backend.services import issue_fact_store as facts
from backend.services import shadow_scan_rounds as shadow
from backend.tasks import saq_tasks
from backend.tests.realtime.test_agent_rpc import patched_namespace  # noqa: F401
from backend.tests.services.test_shadow_scan_rounds import (
    _receipt, _rows, shadow_env as shadow_env,
)


@pytest.fixture
def shadow_socket(client, monkeypatch, patched_namespace):
    send = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(socketio_server, "_sio", SimpleNamespace(call=send))
    monkeypatch.setattr(socketio_server, "_agent_ns", patched_namespace)
    monkeypatch.setattr(socketio_server, "require_agent_secret", lambda: "test-socket-secret")
    monkeypatch.setattr(agent_sid_registry, "register_agent_owner", AsyncMock())
    monkeypatch.setattr(agent_sid_registry, "unregister_agent_owner", AsyncMock())
    return patched_namespace, send


def _beat(client, host_id, *, instance="agent-new", capabilities=None, status="ONLINE"):
    """只走真实心跳入口：能力证据必须由这一拍产生，测试不直接写判据列。"""
    payload = {"host_id": host_id, "status": status, "agent_instance_id": instance}
    if capabilities is not None:
        payload["capabilities"] = list(capabilities)
    response = client.post("/api/v1/heartbeat", json=payload)
    assert response.status_code == 200, response.text


async def _connect(namespace, host_id, sid, *, capable, instance="agent-new"):
    auth = {"host_id": host_id, "agent_secret": "test-socket-secret"}
    if capable:
        auth["capabilities"] = [SCAN_ROUND_V1]
        auth["agent_instance_id"] = instance
    await namespace.on_connect(sid, {}, auth)


def _scope(monkeypatch, *host_ids):
    monkeypatch.setattr(shadow, "_expected_units", lambda *_: [
        {"host_id": host_id, "platform": "mtk"} for host_id in host_ids])
    monkeypatch.setattr(shadow, "iter_plan_run_scan_hosts", lambda *_: [
        (host_id, "ONLINE", False) for host_id in host_ids])


def _stub_payload(monkeypatch):
    monkeypatch.setattr(shadow, "build_scan_now_payload",
                        lambda _db, _run, _host, **kwargs: {"device_serials": ["a"], **kwargs})


def _unit(factory, run_id, host_id):
    row, = _rows(factory, run_id)
    return row, next(u for u in row.expected_set if u["host_id"] == host_id)


def _assert_gap_registered(factory, run_id, host_id):
    """缺口必须以 capability_unverified 落在轮次台账上，否则生产无从追认。"""
    _row, unit = _unit(factory, run_id, host_id)
    assert unit["coverage"] == shadow.COVERAGE_MISSING
    assert unit["reason"] == "capability_unverified"


def _clear_reservation(factory, round_id, host_id):
    """模拟「距上次下发已超过 scan_task 超时」，让 host 重新进入可下发窗口。"""
    with factory() as db:
        coverage = [dict(u) for u in facts.get_scan_round(db, round_id=round_id).expected_set]
        for unit in coverage:
            if unit["host_id"] == host_id:
                unit.pop("last_dispatched_at", None)
        facts.record_scan_round_coverage(db, round_id=round_id, coverage=coverage)
        db.commit()


@pytest.mark.asyncio
async def test_old_agent_build_sends_zero_new_round_commands(
    shadow_env, shadow_socket, sample_host, client, monkeypatch,
):
    """兼容矩阵「旧 Agent」：握手与心跳都不声明能力 → 新轮次一条也发不出去。"""
    factory, run_id, _root, _queued, _settings = shadow_env
    namespace, send = shadow_socket
    _scope(monkeypatch, sample_host.id)
    _stub_payload(monkeypatch)
    _beat(client, sample_host.id, instance="legacy-agent")
    await _connect(namespace, sample_host.id, "sid-legacy", capable=False)

    shadow.sweep_shadow_scan_rounds()
    row, = _rows(factory, run_id)
    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)

    send.assert_not_awaited()
    _assert_gap_registered(factory, run_id, sample_host.id)


@pytest.mark.asyncio
async def test_current_beat_without_capability_field_sends_zero(
    shadow_env, shadow_socket, sample_host, client, monkeypatch,
):
    """心跳漏字段：连接侧仍然声明能力，也必须因为「这一拍没作证」而拒绝下发。"""
    factory, run_id, _root, _queued, _settings = shadow_env
    namespace, send = shadow_socket
    _scope(monkeypatch, sample_host.id)
    _stub_payload(monkeypatch)
    _beat(client, sample_host.id, instance="agent-partial")
    await _connect(namespace, sample_host.id, "sid-capable", capable=True,
                   instance="agent-partial")

    shadow.sweep_shadow_scan_rounds()
    row, = _rows(factory, run_id)
    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)

    send.assert_not_awaited()
    _assert_gap_registered(factory, run_id, sample_host.id)


@pytest.mark.asyncio
async def test_explicit_empty_capability_list_sends_zero(
    shadow_env, shadow_socket, sample_host, client, monkeypatch,
):
    """显式空列表＝本构建自陈无能力：与缺字段同等拒绝，但与「从未上报」原因不同。"""
    factory, run_id, _root, _queued, _settings = shadow_env
    namespace, send = shadow_socket
    _scope(monkeypatch, sample_host.id)
    _stub_payload(monkeypatch)
    _beat(client, sample_host.id, capabilities=[])
    await _connect(namespace, sample_host.id, "sid-empty", capable=True)

    shadow.sweep_shadow_scan_rounds()
    row, = _rows(factory, run_id)
    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)

    send.assert_not_awaited()
    _assert_gap_registered(factory, run_id, sample_host.id)


@pytest.mark.asyncio
async def test_late_heartbeat_withdrawing_capability_stops_the_send(
    shadow_env, shadow_socket, sample_host, client, monkeypatch,
):
    """迟到心跳：预筛时证据仍然成立，发送前被撤回 → 实际发送 0。"""
    factory, run_id, _root, _queued, _settings = shadow_env
    namespace, send = shadow_socket
    _scope(monkeypatch, sample_host.id)
    _stub_payload(monkeypatch)
    _beat(client, sample_host.id, capabilities=[SCAN_ROUND_V1])
    await _connect(namespace, sample_host.id, "sid-new", capable=True)
    shadow.sweep_shadow_scan_rounds()
    row, = _rows(factory, run_id)

    real_evaluate = shadow.evaluate_host_capability
    fired = []

    def evaluate_then_withdraw(host, **kwargs):
        # host 快照已读出；就在这一刻旧构建接管同一 host 并上报心跳。
        if not fired:
            fired.append(True)
            _beat(client, sample_host.id, instance="agent-rollback")
        return real_evaluate(host, **kwargs)

    monkeypatch.setattr(shadow, "evaluate_host_capability", evaluate_then_withdraw)
    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)

    send.assert_not_awaited()
    _assert_gap_registered(factory, run_id, sample_host.id)


@pytest.mark.asyncio
async def test_reconnect_to_old_build_after_prescreen_stops_the_send(
    shadow_env, shadow_socket, sample_host, client, monkeypatch,
):
    """预筛后重连成旧构建：心跳证据此刻仍成立，仅连接守卫这一道就必须拦住。"""
    factory, run_id, _root, _queued, _settings = shadow_env
    namespace, send = shadow_socket
    _scope(monkeypatch, sample_host.id)
    _stub_payload(monkeypatch)
    _beat(client, sample_host.id, capabilities=[SCAN_ROUND_V1])
    await _connect(namespace, sample_host.id, "sid-new", capable=True)
    shadow.sweep_shadow_scan_rounds()
    row, = _rows(factory, run_id)
    await namespace.on_disconnect("sid-new")
    await _connect(namespace, sample_host.id, "sid-legacy", capable=False)

    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)

    send.assert_not_awaited()
    _assert_gap_registered(factory, run_id, sample_host.id)


@pytest.mark.asyncio
async def test_reconnect_to_other_instance_after_prescreen_stops_the_send(
    shadow_env, shadow_socket, sample_host, client, monkeypatch,
):
    """预筛后换实例：两个实例都声明 scan_round_v1，证明却属于别的进程实例。"""
    factory, run_id, _root, _queued, _settings = shadow_env
    namespace, send = shadow_socket
    _scope(monkeypatch, sample_host.id)
    _stub_payload(monkeypatch)
    _beat(client, sample_host.id, instance="agent-A", capabilities=[SCAN_ROUND_V1])
    await _connect(namespace, sample_host.id, "sid-A", capable=True, instance="agent-A")
    shadow.sweep_shadow_scan_rounds()
    row, = _rows(factory, run_id)
    await namespace.on_disconnect("sid-A")
    await _connect(namespace, sample_host.id, "sid-B", capable=True, instance="agent-B")

    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)
    send.assert_not_awaited()
    _assert_gap_registered(factory, run_id, sample_host.id)

    # 当前心跳改由 B 作证后，同一轮内即可恢复下发（不必等新轮次）。
    _beat(client, sample_host.id, instance="agent-B", capabilities=[SCAN_ROUND_V1])
    _clear_reservation(factory, row.id, sample_host.id)
    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)
    assert [call.kwargs["to"] for call in send.await_args_list] == ["sid-B"]


@pytest.mark.asyncio
async def test_malformed_handshake_shape_fails_closed(shadow_socket, sample_host):
    """畸形握手形状（capabilities 不是数组）按无能力处理——fail closed 不放宽。"""
    namespace, send = shadow_socket
    await namespace.on_connect("sid-bad", {}, {
        "host_id": sample_host.id, "agent_secret": "test-socket-secret",
        "capabilities": SCAN_ROUND_V1,
    })
    assert SCAN_ROUND_V1 not in namespace.get_capabilities("sid-bad")
    assert await socketio_server.call_shadow_scan_control(
        sample_host.id, payload={}, required_instance_id="agent-new") is None
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_bypassed_prescreen_still_cannot_send(
    shadow_env, shadow_socket, sample_host, client, monkeypatch,
):
    """变异探针：预筛被绕过（dispatch 凭空出现）时，发送前的复核必须独立拦住误下发。"""
    factory, run_id, _root, _queued, _settings = shadow_env
    namespace, send = shadow_socket
    _scope(monkeypatch, sample_host.id)
    _beat(client, sample_host.id, capabilities=[SCAN_ROUND_V1])
    await _connect(namespace, sample_host.id, "sid-new", capable=True)
    shadow.sweep_shadow_scan_rounds()
    row, = _rows(factory, run_id)

    monkeypatch.setattr(shadow, "prepare_shadow_scan", lambda *_: [
        shadow.ShadowScanDispatch(sample_host.id, {"fact_round_id": row.id}, "forged"),
    ])
    _beat(client, sample_host.id, instance="agent-rollback")

    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_beat_payload_carries_fact_round_and_existing_fields(
    shadow_env, shadow_socket, sample_host, sample_device, sample_job_instance,
    client, monkeypatch,
):
    """验收 1：能力经接收→保存→读取后可用，实际载荷含正确 fact_round_id 与既有字段。"""
    factory, run_id, _root, _queued, _settings = shadow_env
    namespace, send = shadow_socket
    assert sample_job_instance.plan_run_id == run_id
    assert sample_job_instance.host_id == sample_host.id
    _scope(monkeypatch, sample_host.id)
    _beat(client, sample_host.id, instance="agent-new", capabilities=[SCAN_ROUND_V1])
    await _connect(namespace, sample_host.id, "sid-new", capable=True, instance="agent-new")

    shadow.sweep_shadow_scan_rounds()
    row, = _rows(factory, run_id)
    await saq_tasks.scan_task({}, plan_run_id=run_id, fact_round_id=row.id)

    event, message = send.await_args.args
    assert (event, message["command"]) == ("control", "scan_now")
    payload = message["payload"]
    assert payload["plan_run_id"] == run_id
    assert payload["fact_round_id"] == row.id
    assert payload["device_serials"] == [sample_device.serial]
    assert set(payload) == {
        "plan_run_id", "is_final", "device_serials", "run_date_stamps",
        "fact_round_id", "spill_inputs",
    }
    assert send.await_args.kwargs["to"] == "sid-new"
    assert send.await_args.kwargs["namespace"] == "/agent"
    _row, unit = _unit(factory, run_id, sample_host.id)
    assert "reason" not in unit
    assert unit["last_dispatched_at"]


@pytest.mark.asyncio
async def test_current_scan_now_path_never_enters_the_capability_guard(
    sample_host, client, monkeypatch,
):
    """现行 scan_now 沿既有路径：旧 Agent（无能力）照样收到命令，且不读能力证据。"""
    from backend.tests.tasks.test_saq_scan_pipeline import _scan_task_env

    tripwire = Mock(side_effect=AssertionError("current scan entered the capability guard"))
    monkeypatch.setattr(shadow, "prepare_shadow_scan", tripwire)
    monkeypatch.setattr(shadow, "verify_shadow_scan_dispatch", tripwire)
    monkeypatch.setattr(shadow, "evaluate_host_capability", tripwire)
    _beat(client, sample_host.id, instance="legacy-agent")

    scan_sync = Mock(return_value="1")
    completeness = Mock(return_value=1)
    queue = _queue_stub()

    async def to_thread(fn, *args, **kwargs):
        if fn is scan_sync:
            return "1"
        if fn is completeness:
            return 1
        return fn(*args, **kwargs)

    with _scan_task_env(saq_tasks, monkeypatch, [(sample_host.id, "ONLINE")],
                        to_thread=AsyncMock(side_effect=to_thread), scan_sync=scan_sync,
                        completeness=completeness, record_archive=Mock(), queue=queue):
        with patch("backend.realtime.socketio_server.call_agent_control",
                   new=AsyncMock(return_value=True)) as control:
            await saq_tasks.scan_task({}, plan_run_id=42)
            control.assert_awaited_once()
    tripwire.assert_not_called()


def _queue_stub():
    queue = Mock()
    queue.enqueue = AsyncMock()
    return queue


def _dedup_mode(factory, run_id):
    """只固定分区模式，不预置轮次：用于「运行中」失效的场景。"""
    with factory() as db:
        facts.record_partition_mode(db, plan_run_id=run_id, platform="mtk",
                                    mode=facts.PARTITION_MODE_DEDUP_FIRST)
        db.commit()


def _dedup_final_round_with_gap(factory, run_id, host_id, *, now):
    """构造「已固定 DEDUP_FIRST + 终态最后一轮已关闭且带能力缺口」的既有事实。"""
    with factory() as db:
        facts.record_partition_mode(db, plan_run_id=run_id, platform="mtk",
                                    mode=facts.PARTITION_MODE_DEDUP_FIRST)
        row = facts.record_scan_round(
            db, plan_run_id=run_id, platform="mtk", round_no=1,
            kind=facts.SCAN_ROUND_KIND_FINAL,
            expected_set=[{"host_id": host_id, "platform": "mtk",
                           "coverage": shadow.COVERAGE_MISSING,
                           "reason": "capability_unverified"}],
            input_cutoff_at=now - timedelta(seconds=1),
        )
        facts.set_scan_round_status(db, round_id=row.id,
                                    status=facts.SCAN_ROUND_STATUS_CLOSED, closed_at=now)
        db.get(shadow.PlanRun, run_id).status = "SUCCESS"
        db.commit()
        return row.id


def _partition(factory, run_id):
    with factory() as db:
        return facts.get_partition_mode(db, plan_run_id=run_id, platform="mtk")


def test_dedup_first_capability_loss_keeps_mode_and_registers_gap(
    shadow_env, monkeypatch,
):
    """Owner 裁定 6975368043：固定 DEDUP_FIRST 后能力失效 → 模式不回退，只登记缺口。"""
    factory, run_id, _root, _queued, _settings = shadow_env
    now = datetime.now(timezone.utc)
    _scope(monkeypatch, "host-a")
    _stub_payload(monkeypatch)
    with factory() as db:
        db.add(Host(id="host-a", hostname="dedup-loss", status="ONLINE", last_heartbeat=now,
                    agent_capabilities=[SCAN_ROUND_V1],
                    agent_capabilities_reported_at=now,
                    agent_capabilities_instance_id="agent-a"))
        db.commit()
    _dedup_mode(factory, run_id)

    shadow.sweep_shadow_scan_rounds(now=now)
    row = _rows(factory, run_id)[0]
    assert len(shadow.prepare_shadow_scan(run_id, row.id)) == 1

    with factory() as db:
        host = db.get(Host, "host-a")
        host.agent_capabilities = []
        host.agent_capabilities_reported_at = host.last_heartbeat = now + timedelta(seconds=10)
        db.commit()
    _clear_reservation(factory, row.id, "host-a")
    assert shadow.prepare_shadow_scan(run_id, row.id) == []
    _assert_gap_registered(factory, run_id, "host-a")
    assert _partition(factory, run_id).mode == facts.PARTITION_MODE_DEDUP_FIRST


def test_dedup_first_capability_recovery_uses_a_supplement_round(
    shadow_env, monkeypatch,
):
    """能力恢复后按既有补充轮处理：只补那台缺口 host，模式不变、不 settled。"""
    factory, run_id, root, _queued, settings = shadow_env
    now = datetime.now(timezone.utc)
    clock = Mock(wraps=datetime)
    clock.now.return_value = now
    monkeypatch.setattr(shadow, "datetime", clock)
    _scope(monkeypatch, "host-a")
    _stub_payload(monkeypatch)
    with factory() as db:
        db.add(Host(id="host-a", hostname="dedup-recovery", status="ONLINE",
                    last_heartbeat=now, agent_capabilities=[],
                    agent_capabilities_reported_at=now,
                    agent_capabilities_instance_id="agent-old"))
        db.commit()
    final_id = _dedup_final_round_with_gap(factory, run_id, "host-a", now=now)

    # 安静期内恢复能力：补充轮只带这台 host，期望集不回放已覆盖结果。
    recovered_at = now + timedelta(seconds=1)
    clock.now.return_value = recovered_at
    with factory() as db:
        host = db.get(Host, "host-a")
        host.agent_capabilities = [SCAN_ROUND_V1]
        host.agent_capabilities_reported_at = host.last_heartbeat = recovered_at
        host.agent_capabilities_instance_id = "agent-new"
        db.commit()
    shadow.sweep_shadow_scan_rounds(now=recovered_at)

    rounds = _rows(factory, run_id)
    assert [r.kind for r in rounds] == [facts.SCAN_ROUND_KIND_FINAL,
                                        facts.SCAN_ROUND_KIND_SUPPLEMENT]
    supplement = rounds[-1]
    assert [u["host_id"] for u in supplement.expected_set] == ["host-a"]
    assert len(shadow.prepare_shadow_scan(run_id, supplement.id)) == 1
    partition = _partition(factory, run_id)
    assert partition.mode == facts.PARTITION_MODE_DEDUP_FIRST
    assert partition.shadow_settled_at is None
    final = [r for r in _rows(factory, run_id) if r.id == final_id][0]
    assert final.expected_set[0]["coverage"] == shadow.COVERAGE_MISSING

    # 补充轮拿到报表后关闭；安静期从最后一轮关闭时起算，之后才 settled。
    wait = settings.stp_scan_round_report_wait_seconds
    _receipt(root, run_id, supplement.id, "host-a", empty=False)
    closed_at = recovered_at + timedelta(seconds=wait + 1)
    clock.now.return_value = closed_at
    shadow.sweep_shadow_scan_rounds(now=closed_at)
    assert _rows(factory, run_id)[-1].status == facts.SCAN_ROUND_STATUS_CLOSED
    assert _partition(factory, run_id).shadow_settled_at is None
    settled_at = closed_at + timedelta(seconds=wait + 1)
    clock.now.return_value = settled_at
    shadow.sweep_shadow_scan_rounds(now=settled_at)
    assert _partition(factory, run_id).shadow_settled_at == settled_at


def test_settling_a_dedup_partition_with_an_open_gap_is_auditable(
    shadow_env, monkeypatch, caplog,
):
    """缺口没等到恢复就进入 settled：必须留下可核验的终态证据，不能静默结案。"""
    factory, run_id, _root, _queued, settings = shadow_env
    now = datetime.now(timezone.utc)
    clock = Mock(wraps=datetime)
    clock.now.return_value = now
    monkeypatch.setattr(shadow, "datetime", clock)
    _scope(monkeypatch, "host-a")
    _stub_payload(monkeypatch)
    with factory() as db:
        db.add(Host(id="host-a", hostname="dedup-settled-gap", status="ONLINE",
                    last_heartbeat=now, agent_capabilities=[],
                    agent_capabilities_reported_at=now,
                    agent_capabilities_instance_id="agent-old"))
        db.commit()
    final_id = _dedup_final_round_with_gap(factory, run_id, "host-a", now=now)

    wait = settings.stp_scan_round_report_wait_seconds
    caplog.set_level("INFO")
    settled_at = now + timedelta(seconds=wait + 1)
    shadow.sweep_shadow_scan_rounds(now=settled_at)

    partition = _partition(factory, run_id)
    assert partition.shadow_settled_at == settled_at
    assert partition.mode == facts.PARTITION_MODE_DEDUP_FIRST
    assert (f"shadow_scan_settled_with_capability_gap plan_run={run_id} "
            f"fact_round_id={final_id} platform=mtk hosts=host-a") in caplog.text
    assert "shadow_scan_partition_settled" in caplog.text
