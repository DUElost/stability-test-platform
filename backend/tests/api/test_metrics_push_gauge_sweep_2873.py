"""#2873：推式 per-host gauge 的退役 child 差集清理（拉取端 sweep）。

锁三件事：① live host 的值照常出现；② host 退役后**下一次 /metrics 拉取**其
全部 label child 消失（含多 label 组：outbox 的 type、present 的 platform）；
③ 清理只影响被摘 host，别的 host 数值不动。#2791 的同族判据，写侧在 core。
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.core.metrics import (
    record_agent_outbox_pending,
    record_host_operation_concurrency,
    set_reconciler_unresolved_dirs,
)
from backend.models.enums import HostStatus
from backend.models.host import Host


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mk_host(db, host_id: str) -> Host:
    octet = (sum(ord(ch) for ch in host_id) % 200) + 10
    host = Host(
        id=host_id, hostname=host_id, status=HostStatus.ONLINE.value,
        ip=f"10.45.0.{octet}", ssh_user="root", ssh_port=22, extra={},
        last_heartbeat=_now(), created_at=_now(),
    )
    db.add(host)
    return host


def test_retired_host_children_swept_from_push_gauges(client, db_session, monkeypatch):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _mk_host(db_session, "push-a")
    _mk_host(db_session, "push-b")
    db_session.commit()

    record_agent_outbox_pending("push-a", "terminal", 7)
    record_agent_outbox_pending("push-a", "log_signal", 3)
    record_host_operation_concurrency("push-a", held=2, max_slots=4, waiting=1)
    set_reconciler_unresolved_dirs("push-a", 9)
    record_agent_outbox_pending("push-b", "terminal", 1)
    record_host_operation_concurrency("push-b", held=0, max_slots=4, waiting=0)

    first = client.get("/metrics").text
    assert 'stability_agent_outbox_pending{host_id="push-a",type="terminal"} 7.0' in first
    assert 'stability_agent_outbox_pending{host_id="push-a",type="log_signal"} 3.0' in first
    assert 'stability_host_operation_slots_held{host_id="push-a"} 2.0' in first
    assert 'stability_reconciler_unresolved_dirs{host_id="push-a"} 9.0' in first
    assert 'stability_agent_outbox_pending{host_id="push-b",type="terminal"} 1.0' in first

    host = db_session.get(Host, "push-a")
    host.retired_at = _now()
    db_session.commit()

    second = client.get("/metrics").text
    for frag in (
        'stability_agent_outbox_pending{host_id="push-a"',
        'stability_host_operation_slots_held{host_id="push-a"}',
        'stability_reconciler_unresolved_dirs{host_id="push-a"}',
    ):
        assert frag not in second, f"{frag} 仍冻结在 registry（退役 host 不再是容量，ADR-0038 D5）"
    # B 不受波及（差集只摘退役者）
    assert 'stability_agent_outbox_pending{host_id="push-b",type="terminal"} 1.0' in second


def test_live_host_restarts_reporting_after_sweep(client, db_session, monkeypatch):
    """退役→取消退役→新心跳：值能回来（note 的 remove 语义不吃掉后续 set）。"""
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    host = _mk_host(db_session, "push-c")
    db_session.commit()

    set_reconciler_unresolved_dirs("push-c", 5)
    assert 'stability_reconciler_unresolved_dirs{host_id="push-c"} 5.0' in client.get("/metrics").text

    host.retired_at = _now()
    db_session.commit()
    assert 'host_id="push-c"' not in client.get("/metrics").text

    host.retired_at = None
    db_session.commit()
    set_reconciler_unresolved_dirs("push-c", 2)
    body = client.get("/metrics").text
    assert 'stability_reconciler_unresolved_dirs{host_id="push-c"} 2.0' in body
