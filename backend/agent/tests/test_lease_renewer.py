"""ADR-0019 Phase 3b LeaseRenewer 单元测试。

覆盖 device_id 跟踪、batch 续租（唯一路径）、逐项 lease-lost 清理、
网络失败保留状态、TTL 验证、结构化日志。
#291：per-job extend_lock 回退已删除——batch 404/405 视为配置错误，
不再回落逐 Job 端点。
"""

import threading
from typing import Optional
from unittest.mock import MagicMock, patch

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


def test_clear_fencing_token_if_current_requires_matching_local_worker_token():
    r = _make_renewer()
    r.set_fencing_token(1, "tok-1", device_id=10, local_worker_token="worker-new")

    did = r.clear_fencing_token_if_current(1, "tok-1", "worker-old")
    assert did is None
    assert r._fencing_tokens[1] == "tok-1"
    assert r._device_ids[1] == 10

    did = r.clear_fencing_token_if_current(1, "tok-1", "worker-new")
    assert did == 10
    assert 1 not in r._fencing_tokens
    assert 1 not in r._device_ids


# ── lease-lost teardown (shared by all renewal paths) ────────────────────────

def test_lease_lost_callback_cleans_internal_state():
    """Definitive loss → on_lease_lost called with correct (job_id, device_id)
    and internal state fully cleaned."""
    lost_calls = []

    def on_lost(jid: int, did: Optional[int]):
        lost_calls.append((jid, did))

    r = _make_renewer(on_lease_lost=on_lost)
    r._job_ids.add(1)
    r.set_fencing_token(1, "tok-1", device_id=10)

    r._handle_lease_lost(1, "tok-1", reason="batch_stale_token", status_code="stale_token")

    assert lost_calls == [(1, 10)]
    # Internal state cleaned
    assert 1 not in r._job_ids
    assert 1 not in r._fencing_tokens
    assert 1 not in r._device_ids


def test_lease_lost_without_callback_still_cleans_internal():
    """No on_lease_lost callback → teardown still cleans internal state."""
    r = _make_renewer(on_lease_lost=None)
    r._job_ids.add(1)
    r.set_fencing_token(1, "tok-1", device_id=10)

    r._handle_lease_lost(1, "tok-1", reason="batch_lease_missing", status_code="lease_missing")

    assert 1 not in r._job_ids
    assert 1 not in r._fencing_tokens
    assert 1 not in r._device_ids


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

    _make_renewer()
    # The warning is logged during __init__ — check caplog
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    ttl_warnings = [r for r in warnings if "lease_renewal_interval_too_long" in r.message]
    assert len(ttl_warnings) >= 1


# ── Test 9: structured logging includes agent_instance_id ───────────────────

def test_structured_logging_includes_agent_instance_id(caplog):
    """Log extra dict carries agent_instance_id (batch renewal path)."""
    import logging
    caplog.set_level(logging.DEBUG)

    r = _make_renewer(agent_instance_id="inst-deadbeef", host_id="h-1")
    r._job_ids.add(1)
    r.set_fencing_token(1, "tok-1")

    resp = _batch_resp([
        {"job_id": 1, "status": "renewed", "expires_at": "2026-01-01T00:00:00Z"},
    ])

    with patch("requests.post", return_value=resp):
        r._extend_batch([1])

    found = False
    for record in caplog.records:
        if getattr(record, "msg", "") == "lease_extended":
            assert getattr(record, "agent_instance_id", None) == "inst-deadbeef"
            assert getattr(record, "job_id", None) == 1
            found = True
            break
    assert found


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


# ── Test 11: token cleared concurrently → excluded from batch request ───────

def test_extend_batch_skips_when_token_cleared_concurrently():
    """Registered token → snapshot taken → token cleared by concurrent cleanup
    before the chunk snapshot → job not included in any HTTP request."""
    r = _make_renewer(host_id="h-1")
    r.set_fencing_token(1, "tok-1", device_id=10)
    # Simulate concurrent cleanup by clearing token before extend
    r.clear_fencing_token(1)

    with patch("requests.post") as mock_post:
        r._extend_batch([1])

    mock_post.assert_not_called()


# ── P0: Host-level batch renewal ───────────────────────────────────────────────


def _batch_resp(results, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"data": {"results": results}}
    return resp


def test_batch_renews_all_and_sends_one_request():
    """host_id set → one POST /leases/extend-batch carries every token."""
    r = _make_renewer(host_id="h-1")
    for jid in (1, 2, 3):
        r._job_ids.add(jid)
        r.set_fencing_token(jid, f"tok-{jid}", device_id=jid * 10)

    resp = _batch_resp([
        {"job_id": 1, "status": "renewed", "expires_at": "2026-01-01T00:00:00Z"},
        {"job_id": 2, "status": "renewed", "expires_at": "2026-01-01T00:00:00Z"},
        {"job_id": 3, "status": "renewed", "expires_at": "2026-01-01T00:00:00Z"},
    ])
    with patch("requests.post", return_value=resp) as mock_post:
        r._extend_batch([1, 2, 3])

    assert mock_post.call_count == 1
    body = mock_post.call_args.kwargs["json"]
    assert body["host_id"] == "h-1"
    assert {item["job_id"] for item in body["leases"]} == {1, 2, 3}
    # All state intact after successful renewal
    assert r._fencing_tokens == {1: "tok-1", 2: "tok-2", 3: "tok-3"}


def test_batch_per_item_loss_isolates_from_survivors():
    """One stale_token item tears down only that job; others keep renewing."""
    lost_calls = []
    r = _make_renewer(host_id="h-1", on_lease_lost=lambda j, d: lost_calls.append((j, d)))
    for jid in (1, 2, 3):
        r._job_ids.add(jid)
        r.set_fencing_token(jid, f"tok-{jid}", device_id=jid * 10)

    resp = _batch_resp([
        {"job_id": 1, "status": "renewed", "expires_at": "2026-01-01T00:00:00Z"},
        {"job_id": 2, "status": "stale_token"},
        {"job_id": 3, "status": "job_not_running"},
    ])
    with patch("requests.post", return_value=resp):
        r._extend_batch([1, 2, 3])

    # Job 1 survives; 2 and 3 torn down with their device_ids
    assert 1 in r._fencing_tokens
    assert 2 not in r._fencing_tokens and 3 not in r._fencing_tokens
    assert set(lost_calls) == {(2, 20), (3, 30)}


def test_batch_lease_missing_triggers_teardown():
    """lease_missing means the reconciler reclaimed it → stop renewing."""
    lost_calls = []
    r = _make_renewer(host_id="h-1", on_lease_lost=lambda j, d: lost_calls.append((j, d)))
    r._job_ids.add(1)
    r.set_fencing_token(1, "tok-1", device_id=10)

    resp = _batch_resp([{"job_id": 1, "status": "lease_missing"}])
    with patch("requests.post", return_value=resp):
        r._extend_batch([1])

    assert lost_calls == [(1, 10)]
    assert 1 not in r._fencing_tokens


def test_batch_stale_token_guard_preserves_reclaimed_job():
    """If job_id was re-registered under a new token between snapshot and result,
    the loss for the OLD token must not tear down the freshly reclaimed job."""
    lost_calls = []
    r = _make_renewer(host_id="h-1", on_lease_lost=lambda j, d: lost_calls.append((j, d)))
    r._job_ids.add(1)
    r.set_fencing_token(1, "tok-old", device_id=10)

    def _post(*args, **kwargs):
        # Simulate concurrent re-claim: token rotated after the request was built.
        r.set_fencing_token(1, "tok-new", device_id=10)
        return _batch_resp([{"job_id": 1, "status": "stale_token"}])

    with patch("requests.post", side_effect=_post):
        r._extend_batch([1])

    # New token preserved — the stale loss was ignored
    assert r._fencing_tokens.get(1) == "tok-new"
    assert lost_calls == []


def test_batch_network_error_preserves_all_state():
    """Transport failure leaves every token intact for next-tick retry."""
    lost_calls = []
    r = _make_renewer(host_id="h-1", on_lease_lost=lambda j, d: lost_calls.append((j, d)))
    for jid in (1, 2):
        r._job_ids.add(jid)
        r.set_fencing_token(jid, f"tok-{jid}", device_id=jid * 10)

    with patch("requests.post", side_effect=requests.ConnectionError("down")):
        r._extend_batch([1, 2])

    assert r._fencing_tokens == {1: "tok-1", 2: "tok-2"}
    assert lost_calls == []


def test_batch_404_is_config_error_no_fallback(caplog):
    """#291: batch 404/405 → config error, state intact, NO per-job fallback."""
    import logging
    caplog.set_level(logging.ERROR)

    r = _make_renewer(host_id="h-1")
    r._job_ids.add(1)
    r.set_fencing_token(1, "tok-1", device_id=10)

    resp_404 = MagicMock()
    resp_404.status_code = 404

    with patch("requests.post", return_value=resp_404) as mock_post:
        r._extend_batch([1])

    # Exactly one batch attempt; no per-job retry calls
    assert mock_post.call_count == 1
    errors = [rec for rec in caplog.records if rec.msg == "lease_extend_batch_endpoint_missing"]
    assert len(errors) == 1
    # Lease state intact — next tick retries
    assert r._fencing_tokens == {1: "tok-1"}
    assert not hasattr(r, "_batch_supported")


def test_batch_chunks_large_host(monkeypatch):
    """Batch splits into chunks bounded by AGENT_LEASE_EXTEND_BATCH_CHUNK."""
    monkeypatch.setenv("AGENT_LEASE_EXTEND_BATCH_CHUNK", "2")
    r = _make_renewer(host_id="h-1")
    for jid in (1, 2, 3, 4, 5):
        r._job_ids.add(jid)
        r.set_fencing_token(jid, f"tok-{jid}", device_id=jid)

    def _post(url, **kwargs):
        sent = kwargs["json"]["leases"]
        return _batch_resp([
            {"job_id": it["job_id"], "status": "renewed", "expires_at": "x"}
            for it in sent
        ])

    with patch("requests.post", side_effect=_post) as mock_post:
        r._extend_batch([1, 2, 3, 4, 5])

    # 5 jobs / chunk 2 → 3 requests
    assert mock_post.call_count == 3
