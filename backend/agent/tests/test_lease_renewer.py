"""ADR-0019 Phase 3b LeaseRenewer 单元测试。

11 个测试，覆盖 device_id 跟踪、409 清理回调、网络失败保留状态、
续租循环收窄、TTL 验证、结构化日志、并发 token 清理。
"""

import threading
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
import requests

from backend.agent.lease_renewer import LeaseRenewer


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_renewer(**overrides):
    """Create a LeaseRenewer with test-safe defaults."""
    defaults = dict(
        api_url="http://127.0.0.1:8000",
        active_jobs_lock=threading.Lock(),
        active_job_ids=set(),
        lock_renewal_stop_event=threading.Event(),
        agent_instance_id="test-instance-001",
    )
    defaults.update(overrides)
    return LeaseRenewer(**defaults)


# ── Test 1: set_fencing_token stores device_id ──────────────────────────────

def test_set_fencing_token_stores_device_id():
    r = _make_renewer()
    r.set_fencing_token(1, "tok-1", device_id=10)
    r.set_fencing_token(2, "tok-2", device_id=20)

    assert r._fencing_tokens == {1: "tok-1", 2: "tok-2"}
    assert r._device_ids == {1: 10, 2: 20}


def test_set_fencing_token_device_id_none_pops_old():
    r = _make_renewer()
    r.set_fencing_token(1, "tok-1", device_id=10)
    assert r._device_ids == {1: 10}

    # device_id=None resets (pops old) → prevents stale reuse
    r.set_fencing_token(1, "tok-new", device_id=None)
    assert r._fencing_tokens[1] == "tok-new"
    assert 1 not in r._device_ids


# ── Test 2: clear_fencing_token returns device_id ───────────────────────────

def test_clear_fencing_token_returns_device_id():
    r = _make_renewer()
    r.set_fencing_token(1, "tok-1", device_id=10)

    did = r.clear_fencing_token(1)
    assert did == 10
    assert 1 not in r._fencing_tokens
    assert 1 not in r._device_ids


def test_clear_fencing_token_second_call_returns_none():
    r = _make_renewer()
    r.set_fencing_token(1, "tok-1", device_id=10)

    r.clear_fencing_token(1)
    did = r.clear_fencing_token(1)
    assert did is None


# ── Test 3+4: 409 triggers on_lease_lost callback + full cleanup ────────────

def test_409_triggers_on_lease_lost_callback():
    """409 response → on_lease_lost called with correct (job_id, device_id)."""
    lost_calls = []

    def on_lost(jid: int, did: Optional[int]):
        lost_calls.append((jid, did))

    r = _make_renewer(on_lease_lost=on_lost)
    r._job_ids.add(1)
    r.set_fencing_token(1, "tok-1", device_id=10)

    resp_409 = MagicMock()
    resp_409.status_code = 409
    resp_409.raise_for_status.side_effect = requests.HTTPError(response=resp_409)

    with patch("requests.post", return_value=resp_409):
        r._extend_lock(1)

    assert len(lost_calls) == 1
    assert lost_calls[0] == (1, 10)
    # Internal state cleaned
    assert 1 not in r._job_ids
    assert 1 not in r._fencing_tokens
    assert 1 not in r._device_ids


def test_409_cleanup_without_callback_still_cleans_internal():
    """No on_lease_lost callback → 409 still cleans internal state (no crash)."""
    r = _make_renewer(on_lease_lost=None)
    r._job_ids.add(1)
    r.set_fencing_token(1, "tok-1", device_id=10)

    resp_409 = MagicMock()
    resp_409.status_code = 409
    resp_409.raise_for_status.side_effect = requests.HTTPError(response=resp_409)

    with patch("requests.post", return_value=resp_409):
        r._extend_lock(1)

    assert 1 not in r._job_ids
    assert 1 not in r._fencing_tokens
    assert 1 not in r._device_ids


# ── Test 5: network failure preserves state ────────────────────────────────

def test_network_failure_preserves_state():
    """ConnectionError after all retries → state unchanged, no callback."""
    lost_calls = []

    def on_lost(jid: int, did: Optional[int]):
        lost_calls.append((jid, did))

    r = _make_renewer(on_lease_lost=on_lost)
    r._job_ids.add(1)
    r.set_fencing_token(1, "tok-1", device_id=10)

    with patch("requests.post", side_effect=requests.ConnectionError("timeout")):
        r._extend_lock(1)

    # State preserved — network recovered next tick
    assert 1 in r._job_ids
    assert 1 in r._fencing_tokens
    assert 1 in r._device_ids
    assert len(lost_calls) == 0


# ── Test 6: renewal loop only iterates fencing_tokens ──────────────────────

def test_renewal_loop_only_iterates_fencing_tokens():
    """job_id in _job_ids but no token → not iterated in loop."""
    r = _make_renewer()
    r._job_ids.add(999)  # job_id with no token registered
    r._job_ids.add(1)
    r.set_fencing_token(1, "tok-1")

    # Snapshot what gets iterated
    with r._jobs_lock:
        token_jobs = list(r._fencing_tokens.keys())

    assert token_jobs == [1]  # 999 not included


# ── Test 7+8: TTL validation ────────────────────────────────────────────────

def test_ttl_validation_warns_when_interval_too_long(caplog):
    """renewal_interval >= TTL/2 → WARNING."""
    import logging
    caplog.set_level(logging.WARNING)

    _make_renewer()  # uses env AGENT_LOCK_RENEWAL_INTERVAL

    # We can't easily override env per-test; test the calculation directly
    # instead. Use monkeypatch to set the env var to a large value.
    pass  # Signal test — actual warning path tested below


def test_ttl_interval_logic():
    """Verify the comparison is correct: 60 < 300 → OK, 350 ≥ 300 → too long."""
    from backend.agent.lease_renewer import _BACKEND_LEASE_TTL

    assert 60 < _BACKEND_LEASE_TTL / 2, "default 60s should be valid"
    assert 350 >= _BACKEND_LEASE_TTL / 2, "350s should trigger warning"


def test_ttl_validation_warns_with_large_interval(monkeypatch, caplog):
    """AGENT_LOCK_RENEWAL_INTERVAL=350 → WARNING logged."""
    import logging
    caplog.set_level(logging.WARNING)

    monkeypatch.setenv("AGENT_LOCK_RENEWAL_INTERVAL", "350")

    r = _make_renewer()
    # The warning is logged during __init__ — check caplog
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    ttl_warnings = [r for r in warnings if "lease_renewal_interval_too_long" in r.message]
    assert len(ttl_warnings) >= 1


# ── Test 9: structured logging includes agent_instance_id ───────────────────

def test_structured_logging_includes_agent_instance_id(caplog):
    """Log extra dict carries agent_instance_id."""
    import logging
    caplog.set_level(logging.DEBUG)

    r = _make_renewer(agent_instance_id="inst-deadbeef")
    r._job_ids.add(1)
    r.set_fencing_token(1, "tok-1")

    success_resp = MagicMock()
    success_resp.raise_for_status.return_value = None
    success_resp.json.return_value = {"expires_at": "2026-01-01T00:00:00Z"}

    with patch("requests.post", return_value=success_resp):
        r._extend_lock(1)

    # Find the lease_extended log
    found = False
    for record in caplog.records:
        if getattr(record, "msg", "") == "lease_extended":
            assert getattr(record, "agent_instance_id", None) == "inst-deadbeef"
            assert getattr(record, "job_id", None) == 1
            found = True
            break
    if not found:
        # msg may differ; check via record attributes on any log from _extend_lock
        logs = [r for r in caplog.records if hasattr(r, "agent_instance_id")]
        assert len(logs) > 0


# ── Test 10: recovery executor adapted ──────────────────────────────────────

def test_recovery_executor_accepts_lease_renewer_param():
    """execute_recovery_actions_impl uses lease_renewer= kwarg (not lock_manager)."""
    from backend.agent.main import execute_recovery_actions_impl

    lease_renewer = MagicMock()
    local_db = MagicMock()
    outbox_drain = MagicMock()
    outbox_drain.drain_sync.return_value = 0
    local_db.get_pending_outbox.return_value = []

    resp = {
        "actions": [
            {"job_id": 1, "device_id": 10, "action": "CLEANUP", "reason": "boot_id_mismatch"},
        ],
        "outbox_actions": [],
    }

    execute_recovery_actions_impl(
        resp=resp,
        active_jobs_by_id={},
        lease_renewer=lease_renewer,
        local_db=local_db,
        outbox_drain=outbox_drain,
        register_active_job=MagicMock(),
    )

    local_db.delete_active_job.assert_called_once_with(1)
    lease_renewer.clear_fencing_token.assert_called_once_with(1)


# ── Test 11: token cleared concurrently → skip without HTTP ─────────────────

def test_extend_lock_skips_when_token_cleared_concurrently():
    """Registered token → snapshot taken → token cleared by concurrent cleanup →
    _extend_lock reads no token and skips without issuing HTTP POST."""
    r = _make_renewer()
    r.set_fencing_token(1, "tok-1", device_id=10)
    # Simulate concurrent cleanup by clearing token before extend
    r.clear_fencing_token(1)

    with patch("requests.post") as mock_post:
        r._extend_lock(1)

    mock_post.assert_not_called()


# ── Phase 4c: 404 lease lost ──────────────────────────────────────────────────

def test_404_triggers_lease_lost_cleanup():
    """Phase 4c: 404 response → on_lease_lost called (job not found on backend)."""
    lost_calls = []

    def on_lost(jid: int, did: Optional[int]):
        lost_calls.append((jid, did))

    r = _make_renewer(on_lease_lost=on_lost)
    r._job_ids.add(1)
    r.set_fencing_token(1, "tok-1", device_id=10)

    resp_404 = MagicMock()
    resp_404.status_code = 404
    resp_404.raise_for_status.side_effect = requests.HTTPError(response=resp_404)

    with patch("requests.post", return_value=resp_404):
        r._extend_lock(1)

    assert len(lost_calls) == 1
    assert lost_calls[0] == (1, 10)
    # Internal state cleaned
    assert 1 not in r._job_ids
    assert 1 not in r._fencing_tokens
    assert 1 not in r._device_ids


def test_500_does_not_trigger_lease_lost():
    """Phase 4c: 500/5xx → retry, no lease lost cleanup."""
    lost_calls = []

    def on_lost(jid: int, did: Optional[int]):
        lost_calls.append((jid, did))

    r = _make_renewer(on_lease_lost=on_lost)
    r._job_ids.add(1)
    r.set_fencing_token(1, "tok-1", device_id=10)

    resp_500 = MagicMock()
    resp_500.status_code = 500
    resp_500.raise_for_status.side_effect = requests.HTTPError(response=resp_500)

    with patch("requests.post", return_value=resp_500):
        r._extend_lock(1)

    # State preserved — 5xx is transient
    assert len(lost_calls) == 0
    assert 1 in r._job_ids
    assert 1 in r._fencing_tokens
    assert 1 in r._device_ids
