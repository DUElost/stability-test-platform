from backend.services.agent_version_info import (
    build_host_version_view,
    resolve_agent_code_sync_status,
)


DIGEST_OLD = "sha256:" + "a" * 64
DIGEST_NEW = "sha256:" + "b" * 64


# ── ADR-0040 v1.1（#2057）：判据唯一 = code artifact digest ──


def test_resolve_sync_status_matched_by_digest():
    assert (
        resolve_agent_code_sync_status(
            agent_artifact_digest=DIGEST_NEW,
            desired_artifact_digest=DIGEST_NEW,
        )
        == "matched"
    )


def test_resolve_sync_status_drift_by_digest():
    assert (
        resolve_agent_code_sync_status(
            agent_artifact_digest=DIGEST_OLD,
            desired_artifact_digest=DIGEST_NEW,
        )
        == "drift"
    )


def test_resolve_sync_status_unknown_without_reported_digest():
    """#1907 前部署 / 新装未心跳 → unknown 而**非** drift（ADR-0040 v1.1）。"""
    assert (
        resolve_agent_code_sync_status(
            agent_artifact_digest=None,
            desired_artifact_digest=DIGEST_NEW,
        )
        == "unknown"
    )


def test_resolve_sync_status_unknown_without_desired():
    assert (
        resolve_agent_code_sync_status(
            agent_artifact_digest=DIGEST_OLD,
            desired_artifact_digest="",
        )
        == "unknown"
    )


def test_revision_drift_does_not_override_digest_match(monkeypatch):
    """#2057 核心回归：revision 不等但 digest 相等 → matched（revision 只溯源）。"""
    monkeypatch.setattr(
        "backend.services.agent_version_info.get_agent_code_version",
        lambda: "new2222",
    )
    view = build_host_version_view(
        {
            "agent_version": "2.0.0",
            "agent_code_revision": "old1111",
            "agent_code_deployed": "old1111",
            "agent_code_deployed_at": "2026-07-14T05:00:00+00:00",
        },
        agent_artifact_digest=DIGEST_NEW,
        desired_artifact_digest=DIGEST_NEW,
    )
    assert view["agent_code_revision"] == "old1111"
    assert view["expected_code_revision"] == "new2222"
    assert view["agent_code_sync_status"] == "matched"


def test_build_host_version_view_computes_desired_when_absent(monkeypatch):
    monkeypatch.setattr(
        "backend.services.agent_version_info.get_agent_code_version",
        lambda: "1e449c4",
    )
    monkeypatch.setattr(
        "backend.services.agent_version_info.compute_desired_artifact_digest",
        lambda kind=None: DIGEST_NEW,
    )
    view = build_host_version_view({}, agent_artifact_digest=DIGEST_OLD)
    assert view["agent_code_sync_status"] == "drift"


def test_build_host_version_view_from_extra(monkeypatch):
    monkeypatch.setattr(
        "backend.services.agent_version_info.get_agent_code_version",
        lambda: "1e449c4",
    )
    view = build_host_version_view(
        {
            "agent_version": "2.0.0",
            "agent_code_revision": "1e449c4",
            "agent_code_deployed": "1e449c4",
            "agent_code_deployed_at": "2026-07-14T05:00:00+00:00",
        },
        agent_artifact_digest=DIGEST_NEW,
        desired_artifact_digest=DIGEST_NEW,
    )
    assert view["agent_protocol_version"] == "2.0.0"
    assert view["agent_code_revision"] == "1e449c4"
    assert view["expected_code_revision"] == "1e449c4"
    assert view["agent_code_sync_status"] == "matched"


# ── #1907 / ADR-0040 D2/D5/D6：finalize 统一记录通道 ──

import types

import backend.services.agent_version_info as avi


def _make_host(extra=None):
    """flag_modified 需要 SQLAlchemy instrumentation——用真 Host 瞬态实例。"""
    from backend.models.host import Host

    return Host(id="h-1", hostname="h-1", ip="192.0.2.100", extra=extra if extra is not None else {})


def _result(**over):
    base = {
        "ok": True, "converged": False, "reason": "deployed",
        "message": "OK", "duration_ms": 5, "deps_refreshed": False,
        "env_keys_synced": [], "env_paths_missing": {},
        "code_version": "deadbeef", "priv_mode": "wrapper",
        "artifact_digest": "sha256:" + "a" * 64, "phases": {"build": 1},
    }
    base.update(over)
    return base


def _run_finalize(monkeypatch, host, result):
    audits = []
    monkeypatch.setattr(
        avi, "record_audit",
        lambda db, **kw: audits.append(kw),
    )
    db = types.SimpleNamespace(commit=lambda: None)
    avi.finalize_hot_update_outcome(
        db, host, result, entry="ui_api", code_version="deadbeef",
    )
    return audits


def test_finalize_deploys_refresh_deployed_at(monkeypatch):
    host = _make_host()
    _run_finalize(monkeypatch, host, _result())
    assert host.extra["agent_code_deployed"] == "deadbeef"
    assert "agent_code_deployed_at" in host.extra


def test_finalize_noop_keeps_deployed_at(monkeypatch):
    """D2 语义修订：no-op 不刷新 deployed_at（由 digest 匹配状态表达）。"""
    host = _make_host()
    host.extra = {"agent_code_deployed": "oldrev", "agent_code_deployed_at": "old"}
    _run_finalize(monkeypatch, host, _result(converged=True, reason="digest-matched"))
    assert host.extra["agent_code_deployed"] == "oldrev"
    assert host.extra["agent_code_deployed_at"] == "old"


def test_finalize_failure_does_not_touch_deployed_at(monkeypatch):
    host = _make_host()
    _run_finalize(monkeypatch, host, _result(ok=False, reason="ssh_connect_failed"))
    assert "agent_code_deployed" not in host.extra


def test_finalize_audit_carries_digest_and_phases(monkeypatch):
    host = _make_host()
    audits = _run_finalize(monkeypatch, host, _result())
    assert len(audits) == 1
    details = audits[0]["details"]
    assert audits[0]["action"] == "hot_update_result"
    assert details["entry"] == "ui_api"
    assert details["converged"] is False
    assert details["artifact_digest"] == "sha256:" + "a" * 64
    assert details["phases"] == {"build": 1}
