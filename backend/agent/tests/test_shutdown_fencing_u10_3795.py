"""B6 U10: shutdown must report the lease token, never the local worker token."""

import logging
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.agent import graceful_shutdown
from backend.agent.job_runner import JobRunnerState
from backend.agent.lease_renewer import LeaseRenewer
from backend.agent.registry.local_db import LocalDB


WORKER_TOKEN = "worker-42-shutdown-test"


def _shutdown(monkeypatch, tmp_path, registry_token, renewal_token):
    local_db = LocalDB()
    local_db.initialize(str(tmp_path / "shutdown.sqlite"))
    # An unrelated entry must never supply the leftover job's lease identity.
    local_db.save_active_job(99, 2, "other-job-lease")
    if registry_token is not None:
        local_db.save_active_job(42, 1, registry_token)
    state = JobRunnerState(
        active_jobs_lock=threading.RLock(),
        active_job_ids={42},
        active_device_ids={1},
        active_job_tokens={42: WORKER_TOKEN},
        running_worker_tokens={42: WORKER_TOKEN},
        watcher_globally_enabled=False,
        watcher_plan_default=False,
        lock_register=MagicMock(),
        lock_deregister=MagicMock(),
        device_id_register=MagicMock(),
        device_id_deregister=MagicMock(),
    )
    runner = SimpleNamespace(
        _footprint_families={"power-cycle"}, _disarm_results={}, cancel=MagicMock(),
    )
    state.attach_runner(42, WORKER_TOKEN, runner)
    renewer = LeaseRenewer(
        "http://example.invalid", state.active_jobs_lock, state.active_job_ids,
        threading.Event(),
    )
    if renewal_token is not None:
        renewer.set_fencing_token(42, renewal_token, 1, WORKER_TOKEN)
    captured = []
    outbox = MagicMock()

    def capture_drain():
        captured.extend(local_db.get_pending_terminals())
        return len(captured)

    outbox.drain_sync.side_effect = capture_drain
    # Reach the already-elapsed window deterministically; do not sleep or signal.
    wait = MagicMock()
    monkeypatch.setattr(graceful_shutdown, "_await_started_disarm", wait)
    for name in ("LocalDiskMonitor", "LogArchiver", "EventUploader"):
        monkeypatch.setattr(graceful_shutdown, name, MagicMock())
    try:
        graceful_shutdown.shutdown_agent_runtime(
            coordinator=MagicMock(), operation_scheduler=MagicMock(),
            job_runner_state=state, executor=MagicMock(),
            step_trace_uploader=MagicMock(), outbox_drain=outbox,
            log_signal_drainer=None, recovery_sync_stop=MagicMock(),
            recovery_sync_thread=MagicMock(), heartbeat_thread=MagicMock(),
            lease_renewer=renewer, mq_producer=MagicMock(), local_db=local_db,
            sio_client=MagicMock(),
        )
    finally:
        local_db.close()
    wait.assert_called_once_with(state, timeout_seconds=20.0)
    assert state.active_job_tokens[42] == WORKER_TOKEN
    assert state.active_runners[42][0] == WORKER_TOKEN
    return captured


@pytest.mark.parametrize(
    "registry_token,renewal_token,expected",
    [
        ("registry-lease-token", "cached-lease-token", "registry-lease-token"),
        (None, "cached-lease-token", "cached-lease-token"),
    ],
    ids=["registry-first", "renewer-fallback"],
)
def test_shutdown_uses_lease_fencing_token(
    monkeypatch, tmp_path, registry_token, renewal_token, expected,
):
    rows = _shutdown(monkeypatch, tmp_path, registry_token, renewal_token)
    assert len(rows) == 1
    assert rows[0]["job_id"] == 42
    payload = rows[0]["payload"]
    assert payload["fencing_token"] == expected
    assert payload["update"]["status"] == "CANCELED"
    assert payload["disarm_results"]["power-cycle"]["result"] == "incomplete"
    assert payload["disarm_results"]["power-cycle"]["reason"] == (
        "shutdown_disarm_window_elapsed"
    )


def test_shutdown_skips_when_both_lease_token_sources_are_missing(
    monkeypatch, tmp_path, caplog,
):
    caplog.set_level(logging.WARNING, logger=graceful_shutdown.__name__)
    assert _shutdown(monkeypatch, tmp_path, None, None) == []
    assert "shutdown_incomplete_outbox_skip_no_token job=42" in caplog.text
    assert WORKER_TOKEN not in caplog.text
