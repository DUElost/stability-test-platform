from backend.services.agent_version_info import (
    build_host_version_view,
    resolve_agent_code_sync_status,
)


def test_resolve_sync_status_matched():
    assert (
        resolve_agent_code_sync_status(
            agent_code_revision="abc1234",
            expected_code_revision="abc1234",
        )
        == "matched"
    )


def test_resolve_sync_status_drift():
    assert (
        resolve_agent_code_sync_status(
            agent_code_revision="old1111",
            expected_code_revision="new2222",
        )
        == "drift"
    )


def test_resolve_sync_status_pending_after_deploy():
    assert (
        resolve_agent_code_sync_status(
            agent_code_revision=None,
            expected_code_revision="new2222",
            agent_code_deployed="new2222",
        )
        == "pending"
    )


def test_resolve_sync_status_unknown_without_signals():
    assert (
        resolve_agent_code_sync_status(
            agent_code_revision=None,
            expected_code_revision="new2222",
        )
        == "unknown"
    )


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
        }
    )
    assert view["agent_protocol_version"] == "2.0.0"
    assert view["agent_code_revision"] == "1e449c4"
    assert view["expected_code_revision"] == "1e449c4"
    assert view["agent_code_sync_status"] == "matched"
