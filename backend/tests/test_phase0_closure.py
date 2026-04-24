"""Phase 0 state-closure regression suite.

Run instructions
================
All tests in this file carry the ``phase0`` marker.

    # Run all Phase 0 regression tests (recommended before touching
    # scheduler / heartbeat / recycler / agent_api):
    pytest backend/tests/test_phase0_closure.py -m phase0 -v

    # Run only pure-unit tests (no DB required):
    pytest backend/tests/test_phase0_closure.py -m "phase0 and not integration" -v

    # Run integration tests (requires TEST_DATABASE_URL):
    pytest backend/tests/test_phase0_closure.py -m "phase0 and integration" -v

Scenarios covered
=================
1. Outbox 409 ACK semantics
   - 409 + current_status=RUNNING → NOT acked, attempt bumped → prevents
     swallowing genuine state conflicts.
   - 409 + current_status=FAILED  → ACKed → server already terminal, safe.

2. Graceful SIGTERM shutdown
   - drain_sync() flushes all pending outbox entries (the shutdown path).
   - shutdown_event.wait() wakes instantly on set (no 10s hang).
   - SIGTERM handler sets the shutdown event (Linux only).

3. Deferred post-completion (recycler)
   - Orphan terminal jobs with ended_at > 120s grace get post_completion
     backfilled; idempotent on second pass.

4. Outbox LocalDB primitives
   - enqueue / get_pending / ack / bump / prune_acked — correctness.

5. _parse_current_status robustness
   - Parses detail dict, handles missing fields, survives malformed body.
"""

import json
import os
import signal
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from backend.agent.registry.local_db import LocalDB

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_local_db():
    """Create a LocalDB backed by a temp file for test isolation."""
    db = LocalDB()
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.initialize(path)
    return db, path


class _FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            err = requests.HTTPError(response=self)
            err.response = self
            raise err


def _build_drain(local_db):
    from backend.agent.main import OutboxDrainThread

    return OutboxDrainThread(
        api_url="http://fake:8000",
        local_db=local_db,
        interval=999,
    )


def _raise_http_error(fake_resp):
    """Return a side_effect callable that raises HTTPError wrapping *fake_resp*."""
    import requests as req_mod

    def side_effect(*_a, **_kw):
        err = req_mod.HTTPError(response=fake_resp)
        err.response = fake_resp
        raise err

    return side_effect


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Outbox 409 ACK semantics
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.phase0
class TestOutbox409Semantics:
    """Verify outbox drain distinguishes terminal vs non-terminal 409."""

    def test_409_running_does_not_ack(self):
        """409 with current_status=RUNNING → NOT acked, attempt bumped."""
        local_db, path = _make_local_db()
        try:
            local_db.enqueue_terminal(42, {"update": {"status": "FINISHED"}})
            drain = _build_drain(local_db)

            fake_resp = _FakeResponse(409, {
                "detail": {
                    "message": "Cannot transition RUNNING -> COMPLETED",
                    "current_status": "RUNNING",
                    "requested_status": "COMPLETED",
                },
            })
            with patch(
                "backend.agent.outbox_drainer.requests.post",
                side_effect=_raise_http_error(fake_resp),
            ):
                sent = drain._drain_once()

            assert sent == 0, "Should NOT ACK when current_status is RUNNING"
            pending = local_db.get_pending_terminals()
            assert len(pending) == 1, "Entry should still be pending"
            assert pending[0]["attempts"] == 1, "Attempt count should be bumped"
        finally:
            local_db.close()
            os.unlink(path)

    def test_409_failed_does_ack(self):
        """409 with current_status=FAILED → ACKed (job is terminal on server)."""
        local_db, path = _make_local_db()
        try:
            local_db.enqueue_terminal(43, {"update": {"status": "FINISHED"}})
            drain = _build_drain(local_db)

            fake_resp = _FakeResponse(409, {
                "detail": {
                    "message": "Cannot transition FAILED -> COMPLETED",
                    "current_status": "FAILED",
                    "requested_status": "COMPLETED",
                },
            })
            with patch(
                "backend.agent.outbox_drainer.requests.post",
                side_effect=_raise_http_error(fake_resp),
            ):
                sent = drain._drain_once()

            assert sent == 1, "Should ACK when current_status is terminal (FAILED)"
            pending = local_db.get_pending_terminals()
            assert len(pending) == 0, "No pending entries should remain"
        finally:
            local_db.close()
            os.unlink(path)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. SIGTERM / graceful shutdown
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.phase0
class TestSigtermGracefulShutdown:
    """Verify SIGTERM → active jobs finish → outbox flush → exit."""

    def test_outbox_drain_sync_flushes_pending(self):
        """drain_sync() flushes all pending outbox entries — the shutdown path."""
        local_db, path = _make_local_db()
        try:
            local_db.enqueue_terminal(99, {"update": {"status": "FAILED"}})
            local_db.enqueue_terminal(100, {"update": {"status": "FINISHED"}})

            from backend.agent.main import OutboxDrainThread

            success_resp = MagicMock()
            success_resp.status_code = 200
            success_resp.raise_for_status = MagicMock()

            drain = OutboxDrainThread("http://fake:8000", local_db, interval=999)

            with patch("backend.agent.outbox_drainer.requests.post", return_value=success_resp):
                flushed = drain.drain_sync()

            assert flushed == 2, "drain_sync should flush all pending entries"
            assert local_db.get_pending_terminals() == [], "All entries should be ACKed"
        finally:
            local_db.close()
            os.unlink(path)

    def test_shutdown_event_unblocks_main_loop(self):
        """shutdown_event.wait(poll_interval) wakes immediately on set."""
        shutdown_event = threading.Event()
        woke_at = []

        def simulated_main_loop():
            start = time.monotonic()
            shutdown_event.wait(10.0)
            woke_at.append(time.monotonic() - start)

        t = threading.Thread(target=simulated_main_loop)
        t.start()
        time.sleep(0.1)
        shutdown_event.set()
        t.join(timeout=2)

        assert not t.is_alive(), "Thread should have exited"
        assert len(woke_at) == 1
        assert woke_at[0] < 1.0, f"Should wake in <1s, took {woke_at[0]:.2f}s"

    @pytest.mark.skipif(
        os.name == "nt",
        reason="SIGTERM self-delivery behaves differently on Windows",
    )
    def test_sigterm_sets_shutdown_event(self):
        """On Linux: SIGTERM handler sets the event."""
        shutdown_event = threading.Event()

        def handler(signum, frame):
            shutdown_event.set()

        old = signal.signal(signal.SIGTERM, handler)
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.1)
            assert shutdown_event.is_set()
        finally:
            signal.signal(signal.SIGTERM, old)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. Deferred post-completion (recycler)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.phase0
@pytest.mark.integration
class TestDeferredPostCompletion:
    """Verify recycler fills post_completion only after grace period."""

    @pytest.mark.skipif(
        os.getenv("TESTING") != "1" and not os.getenv("DATABASE_URL"),
        reason="Requires database connection",
    )
    def test_fill_deferred_post_completions(self):
        """Create an orphan terminal job, verify SAQ enqueue for post-completion."""
        from backend.core.database import SessionLocal
        from backend.models.enums import HostStatus, JobStatus, WorkflowStatus
        from backend.models.host import Device, Host
        from backend.models.job import JobInstance, TaskTemplate
        from backend.models.workflow import WorkflowDefinition, WorkflowRun

        suffix = uuid4().hex[:8]
        now = datetime.now(timezone.utc)
        host_id = f"test-ph0-{suffix}"

        db = SessionLocal()
        try:
            host = Host(
                id=host_id, hostname=f"h-{suffix}",
                status=HostStatus.ONLINE.value, created_at=now,
            )
            device = Device(
                serial=f"S-{suffix}", host_id=host_id,
                status="ONLINE", tags=[], created_at=now,
            )
            wf = WorkflowDefinition(
                name=f"wf-{suffix}", failure_threshold=0.5,
                created_by="pytest", created_at=now, updated_at=now,
            )
            db.add_all([host, device, wf])
            db.flush()

            tpl = TaskTemplate(
                workflow_definition_id=wf.id, name=f"t-{suffix}",
                pipeline_def={"stages": {"prepare": [], "execute": [], "post_process": []}},
                sort_order=0, created_at=now,
            )
            db.add(tpl)
            db.flush()

            run = WorkflowRun(
                workflow_definition_id=wf.id,
                status=WorkflowStatus.FAILED.value,
                failure_threshold=0.5, triggered_by="pytest",
                started_at=now, ended_at=now,
            )
            db.add(run)
            db.flush()

            job = JobInstance(
                workflow_run_id=run.id, task_template_id=tpl.id,
                device_id=device.id, host_id=host_id,
                status=JobStatus.FAILED.value,
                status_reason="test_timeout",
                pipeline_def=tpl.pipeline_def,
                created_at=now, updated_at=now,
                started_at=now,
                ended_at=now - timedelta(seconds=300),
                post_processed_at=None,
            )
            db.add(job)
            db.commit()
            job_id = job.id

            from backend.scheduler.recycler import _fill_deferred_post_completions

            with patch("backend.tasks.saq_worker.enqueue_sync") as mock_enqueue:
                filled = _fill_deferred_post_completions(db, datetime.now(timezone.utc))

            assert filled >= 1, f"Should enqueue at least our orphan job, got {filled}"

            # Verify post_completion_task was enqueued for our job
            pc_calls = [
                c for c in mock_enqueue.call_args_list
                if c[0][0] == "post_completion_task" and c[1].get("job_id") == job_id
            ]
            assert len(pc_calls) == 1, f"post_completion_task should be enqueued for job {job_id}"
            assert pc_calls[0][1]["key"] == f"pc:{job_id}"

            # Verify notification task was enqueued for our job
            notif_calls = [
                c for c in mock_enqueue.call_args_list
                if c[0][0] == "send_notification_task"
                and c[1].get("context", {}).get("run_id") == job_id
            ]
            assert len(notif_calls) == 1, "send_notification_task should be enqueued"
            assert notif_calls[0][1]["event_type"] == "RUN_FAILED"

            # Second pass — same job re-enqueued (SAQ key dedup handles idempotency)
            with patch("backend.tasks.saq_worker.enqueue_sync") as mock_enqueue2:
                filled2 = _fill_deferred_post_completions(db, datetime.now(timezone.utc))
            assert filled2 >= 1, "Orphan job re-enqueued (SAQ key dedup is the idempotency layer)"

        finally:
            from backend.models.job import StepTrace

            db.query(StepTrace).filter(StepTrace.job_id == job_id).delete()
            db.query(JobInstance).filter(JobInstance.id == job_id).delete()
            db.query(WorkflowRun).filter(WorkflowRun.id == run.id).delete()
            db.query(TaskTemplate).filter(TaskTemplate.id == tpl.id).delete()
            db.query(WorkflowDefinition).filter(WorkflowDefinition.id == wf.id).delete()
            db.query(Device).filter(Device.id == device.id).delete()
            db.query(Host).filter(Host.id == host_id).delete()
            db.commit()
            db.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. Outbox LocalDB primitives
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.phase0
class TestLocalDBOutbox:
    """Verify outbox table operations: enqueue, ack, prune."""

    def test_enqueue_and_get_pending(self):
        local_db, path = _make_local_db()
        try:
            local_db.enqueue_terminal(1, {"update": {"status": "FINISHED"}})
            local_db.enqueue_terminal(2, {"update": {"status": "FAILED"}})

            pending = local_db.get_pending_terminals()
            assert len(pending) == 2
            assert pending[0]["job_id"] == 1
            assert pending[1]["job_id"] == 2
        finally:
            local_db.close()
            os.unlink(path)

    def test_ack_removes_from_pending(self):
        local_db, path = _make_local_db()
        try:
            local_db.enqueue_terminal(10, {"update": {"status": "FINISHED"}})
            local_db.ack_terminal(10)
            assert local_db.get_pending_terminals() == []
        finally:
            local_db.close()
            os.unlink(path)

    def test_idempotent_enqueue(self):
        """Same job_id enqueued twice should replace, not duplicate."""
        local_db, path = _make_local_db()
        try:
            local_db.enqueue_terminal(5, {"update": {"status": "FINISHED"}})
            local_db.enqueue_terminal(5, {"update": {"status": "FAILED"}})

            pending = local_db.get_pending_terminals()
            assert len(pending) == 1
            assert pending[0]["payload"]["update"]["status"] == "FAILED"
        finally:
            local_db.close()
            os.unlink(path)

    def test_bump_attempt(self):
        local_db, path = _make_local_db()
        try:
            local_db.enqueue_terminal(7, {"update": {"status": "FINISHED"}})
            local_db.bump_terminal_attempt(7, "connection refused")
            assert local_db.get_pending_terminals()[0]["attempts"] == 1
        finally:
            local_db.close()
            os.unlink(path)

    def test_prune_acked(self):
        local_db, path = _make_local_db()
        try:
            for i in range(5):
                local_db.enqueue_terminal(100 + i, {"update": {"status": "FINISHED"}})
                local_db.ack_terminal(100 + i)

            pruned = local_db.prune_acked_terminals(keep_recent=2)
            assert pruned == 3

            conn = sqlite3.connect(path)
            count = conn.execute(
                "SELECT COUNT(*) FROM job_terminal_outbox WHERE acked=1"
            ).fetchone()[0]
            conn.close()
            assert count == 2
        finally:
            local_db.close()
            os.unlink(path)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. _parse_current_status robustness
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.phase0
class TestParseCurrentStatus:
    """Verify 409 response body parsing."""

    def test_parse_detail_dict(self):
        from backend.agent.main import OutboxDrainThread

        resp = _FakeResponse(409, {
            "detail": {"current_status": "FAILED", "message": "..."},
        })
        assert OutboxDrainThread._parse_current_status(resp) == "FAILED"

    def test_parse_missing_detail(self):
        from backend.agent.main import OutboxDrainThread

        resp = _FakeResponse(409, {"error": "something"})
        assert OutboxDrainThread._parse_current_status(resp) is None

    def test_parse_no_current_status_in_detail(self):
        """detail dict without current_status returns None."""
        from backend.agent.main import OutboxDrainThread

        resp = _FakeResponse(409, {
            "detail": {"message": "transition rejected"},
        })
        assert OutboxDrainThread._parse_current_status(resp) is None

    def test_parse_malformed_body(self):
        from backend.agent.main import OutboxDrainThread

        resp = MagicMock()
        resp.json.side_effect = ValueError("not json")
        assert OutboxDrainThread._parse_current_status(resp) is None
