"""#491 — precheck 可达性双通道诊断的行为契约。

背景（真实事故 PlanRun #247）：SocketIO room 键为 ``agent:{HOST_ID}``，与 HTTP
心跳是两条独立通道。HOST_ID 迁移后 Agent 侧未同步时，RPC 恒报 ``agent_offline``
而心跳依旧新鲜，控制面静默 requeue 且**零告警**。本模块把这种失配显式化。

这里的用例只覆盖诊断本身（判定不参与决策，故不重复测 precheck 的失败语义）：
- 心跳新鲜 + ONLINE + 本进程无 sid → conflict（HOST_ID 失配的特征信号）；
- 心跳过期 / host 非 ONLINE / sid 在册 → 不冲突（真离线或正常多实例）；
- 库中不存在的 host id 不得出现在诊断里（诊断不发明 host）；
- 告警只对 conflict 项发，且保持传入顺序。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.models.host import Host
from backend.services.precheck import reachability


def _add_host(db_session, host_id: str, *, status: str = "ONLINE", heartbeat=None) -> Host:
    host = Host(
        id=host_id,
        hostname=f"hostname-{host_id}",
        status=status,
        last_heartbeat=heartbeat,
    )
    db_session.add(host)
    db_session.commit()
    return host


def _fresh_heartbeat() -> datetime:
    return datetime.now(timezone.utc)


def _expired_heartbeat() -> datetime:
    return datetime.now(timezone.utc) - timedelta(
        seconds=reachability.HOST_HEARTBEAT_TIMEOUT_SECONDS + 60
    )


def test_empty_host_ids_returns_empty_diagnostics(db_session):
    assert reachability.diagnose_unreachable_hosts(db_session, []) == {}


def test_unknown_host_id_is_absent(db_session, monkeypatch):
    """诊断不得为不存在的 host 编造条目——调用方据此决定是否改写 blocker。"""
    monkeypatch.setattr(reachability, "_process_local_sid", lambda host_id: None)
    monkeypatch.setattr(reachability, "_socketio_redis_adapter_enabled", lambda: False)

    assert reachability.diagnose_unreachable_hosts(db_session, ["no-such-host"]) == {}


def test_conflict_when_heartbeat_fresh_but_no_local_sid(db_session, monkeypatch):
    """心跳新鲜却在本进程查不到 sid —— HOST_ID 失配的特征，必须标 conflict。"""
    monkeypatch.setattr(reachability, "_process_local_sid", lambda host_id: None)
    monkeypatch.setattr(reachability, "_socketio_redis_adapter_enabled", lambda: False)
    _add_host(db_session, "h-conflict", heartbeat=_fresh_heartbeat())

    diag = reachability.diagnose_unreachable_hosts(db_session, ["h-conflict"])["h-conflict"]

    assert diag["heartbeat_fresh"] is True
    assert diag["sid_registered"] is False
    assert diag["conflict"] is True
    # 未开 Redis adapter 时，Agent 只能连在本进程，故为强信号
    assert diag["confidence"] == "high"


def test_no_conflict_when_sid_registered(db_session, monkeypatch):
    """sid 在册说明 SocketIO 通道是通的，offline 另有原因（如 RPC 超时）。"""
    monkeypatch.setattr(reachability, "_process_local_sid", lambda host_id: "sid-abc")
    monkeypatch.setattr(reachability, "_socketio_redis_adapter_enabled", lambda: False)
    _add_host(db_session, "h-registered", heartbeat=_fresh_heartbeat())

    diag = reachability.diagnose_unreachable_hosts(db_session, ["h-registered"])["h-registered"]

    assert diag["sid_registered"] is True
    assert diag["conflict"] is False


def test_no_conflict_when_heartbeat_expired(db_session, monkeypatch):
    """心跳过期 = 真的离线，不该报通道冲突。"""
    monkeypatch.setattr(reachability, "_process_local_sid", lambda host_id: None)
    monkeypatch.setattr(reachability, "_socketio_redis_adapter_enabled", lambda: False)
    _add_host(db_session, "h-expired", heartbeat=_expired_heartbeat())

    diag = reachability.diagnose_unreachable_hosts(db_session, ["h-expired"])["h-expired"]

    assert diag["heartbeat_fresh"] is False
    assert diag["conflict"] is False


def test_no_conflict_when_host_not_online(db_session, monkeypatch):
    monkeypatch.setattr(reachability, "_process_local_sid", lambda host_id: None)
    monkeypatch.setattr(reachability, "_socketio_redis_adapter_enabled", lambda: False)
    _add_host(db_session, "h-offline", status="OFFLINE", heartbeat=_fresh_heartbeat())

    diag = reachability.diagnose_unreachable_hosts(db_session, ["h-offline"])["h-offline"]

    assert diag["host_status"] == "OFFLINE"
    assert diag["conflict"] is False


def test_confidence_low_when_redis_adapter_enabled(db_session, monkeypatch):
    """开了 Redis adapter，Agent 可能连在别的实例上——「本进程无 sid」只是弱信号。"""
    monkeypatch.setattr(reachability, "_process_local_sid", lambda host_id: None)
    monkeypatch.setattr(reachability, "_socketio_redis_adapter_enabled", lambda: True)
    _add_host(db_session, "h-adapter", heartbeat=_fresh_heartbeat())

    diag = reachability.diagnose_unreachable_hosts(db_session, ["h-adapter"])["h-adapter"]

    assert diag["redis_adapter_enabled"] is True
    assert diag["conflict"] is True
    assert diag["confidence"] == "low"


def test_log_conflicts_only_reports_conflicting_hosts(monkeypatch):
    """告警是 #491 的核心产出：此前这类失配在日志里完全不可见。"""
    errors: list[tuple] = []
    monkeypatch.setattr(
        reachability.logger, "error", lambda *args, **kwargs: errors.append(args),
    )
    diagnostics = {
        "h-conflict": {
            "conflict": True,
            "confidence": "high",
            "sid_registered": False,
            "last_heartbeat": "2026-08-30T10:00:00+00:00",
            "host_status": "ONLINE",
            "redis_adapter_enabled": False,
        },
        "h-quiet": {
            "conflict": False,
            "confidence": "high",
            "sid_registered": True,
            "last_heartbeat": None,
            "host_status": "OFFLINE",
            "redis_adapter_enabled": False,
        },
    }

    conflicts = reachability.log_unreachable_conflicts(diagnostics, plan_run_id=247)

    assert conflicts == ["h-conflict"]
    assert len(errors) == 1
    assert "agent_sid_mismatch" in errors[0][0]
    # 日志里必须带上可定位的上下文，否则等于没告警
    assert "h-conflict" in errors[0]
    assert 247 in errors[0]


def test_log_conflicts_handles_empty_diagnostics(monkeypatch):
    errors: list[tuple] = []
    monkeypatch.setattr(
        reachability.logger, "error", lambda *args, **kwargs: errors.append(args),
    )

    assert reachability.log_unreachable_conflicts({}, plan_run_id=1) == []
    assert reachability.log_unreachable_conflicts(None) == []
    assert errors == []
