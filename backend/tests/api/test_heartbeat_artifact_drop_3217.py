"""#3217：crash artifact 丢失从「只在 Agent 本机可见」变成控制面可查、可告警。

验收的核心是**四条数对得上**：注入数（已知）、Agent ``stats``、控制面 Gauge、``host.extra``
（``GET /hosts/{id}`` 的数据源）。只落 ``host.extra`` 而没有指标 = #1257 型「有账面、无告警」。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from prometheus_client import REGISTRY

from backend.agent.artifact_uploader import ArtifactUploader
from backend.api.routes.heartbeat import _process_heartbeat_with_db
from backend.api.schemas.host import HeartbeatIn
from backend.models.enums import HostStatus
from backend.models.host import Host


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mk_host(db, host_id: str) -> Host:
    host = Host(id=host_id, hostname=host_id, status=HostStatus.ONLINE.value, last_heartbeat=_now(), extra={})
    db.add(host)
    db.commit()
    return host


def _dropped(host_id: str, stage: str):
    return REGISTRY.get_sample_value("stability_agent_artifact_dropped", {"host_id": host_id, "stage": stage})


def _submits(host_id: str):
    return REGISTRY.get_sample_value("stability_agent_artifact_submits", {"host_id": host_id})


def _beat(db, host_id: str, extra: dict) -> Host:
    _process_heartbeat_with_db(HeartbeatIn(host_id=host_id, status="ONLINE", extra=extra), db)
    db.commit()
    host = db.get(Host, host_id)
    db.refresh(host)
    return host


@pytest.mark.parametrize(
    ("capacity", "injected", "expected_dropped"),
    [
        pytest.param(4, 3, 0, id="below-capacity"),
        pytest.param(4, 4, 0, id="exactly-full"),
        pytest.param(4, 7, 3, id="over-capacity"),
    ],
)
def test_flood_tiers_reconcile_four_ways(db_session, capacity, injected, expected_dropped):
    """真实 uploader 注入已知数量 → 心跳 → 控制面：四条数逐档对拍。"""
    host_id = f"art-3217-{capacity}-{injected}"
    _mk_host(db_session, host_id)
    u = ArtifactUploader()
    blocker = threading.Event()
    u._worker_loop = lambda: blocker.wait(5.0)  # 不消费：溢出数确定
    u.configure(api_url="http://x", host_id=host_id, agent_instance_id="agent-3217",
                session=MagicMock(), queue_maxsize=capacity)
    u.start()
    try:
        for i in range(injected):
            u.submit(job_id=1, artifact_type="aee_crash", storage_uri=f"/crash/{i}",
                     fencing_token="1:1", device_serial="S-3217")
        counts = u.heartbeat_counts()
    finally:
        blocker.set()
        u.stop(drain=False, timeout=1.0)

    host = _beat(db_session, host_id, counts)

    # counts 取于 stop 之前（stop(drain=False) 会把残余再计入丢弃，那是另一件事）
    assert counts["artifact_dropped_submit_total"] == expected_dropped          # ① 注入 vs ② Agent stats
    assert _dropped(host_id, "submit") == expected_dropped                      # ③ 控制面 Gauge
    assert host.extra["artifact_dropped_submit_total"] == expected_dropped      # ④ host.extra（GET /hosts/{id}）
    assert _submits(host_id) == injected
    assert host.extra["artifact_submits_total"] == injected


def test_all_three_stages_land_on_their_own_series(db_session):
    _mk_host(db_session, "art-3217-stages")
    _beat(db_session, "art-3217-stages", {
        "artifact_submits_total": 20,
        "artifact_dropped_submit_total": 3,
        "artifact_dropped_promote_total": 2,
        "artifact_dropped_post_total": 1,
    })
    assert _submits("art-3217-stages") == 20
    assert (_dropped("art-3217-stages", "submit"), _dropped("art-3217-stages", "promote"),
            _dropped("art-3217-stages", "post")) == (3, 2, 1)


def test_old_agent_without_keys_writes_no_series(db_session):
    """旧 Agent 不带这些键：**不得**写 0——没量到 ≠ 没发生（升级窗口里旧主机会被读成零丢失）。"""
    _mk_host(db_session, "art-3217-old")
    _beat(db_session, "art-3217-old", {"terminal_outbox_pending": 0})
    assert _submits("art-3217-old") is None
    for stage in ("submit", "promote", "post"):
        assert _dropped("art-3217-old", stage) is None


@pytest.mark.parametrize("bad", ["x", -1, None, True, [3]])
def test_invalid_values_are_skipped_not_coerced(db_session, bad):
    host_id = f"art-3217-bad-{abs(hash(repr(bad))) % 10_000}"
    _mk_host(db_session, host_id)
    _beat(db_session, host_id, {"artifact_dropped_submit_total": bad, "artifact_dropped_post_total": 4})
    assert _dropped(host_id, "submit") is None
    assert _dropped(host_id, "post") == 4, "一项非法不得连累同批的合法项"


def test_retired_host_artifact_children_swept(client, db_session, monkeypatch):
    """#2873 per-host 子项治理同样覆盖新 Gauge：退役后下一次 /metrics 拉取即消失。"""
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _mk_host(db_session, "art-3217-retire")
    _beat(db_session, "art-3217-retire", {"artifact_submits_total": 5, "artifact_dropped_post_total": 2})
    first = client.get("/metrics").text
    assert 'stability_agent_artifact_dropped{host_id="art-3217-retire",stage="post"} 2.0' in first
    assert 'stability_agent_artifact_submits{host_id="art-3217-retire"} 5.0' in first

    host = db_session.get(Host, "art-3217-retire")
    host.retired_at = _now()
    db_session.commit()
    second = client.get("/metrics").text
    assert 'host_id="art-3217-retire"' not in second
