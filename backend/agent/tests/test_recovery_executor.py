"""ADR-0019 Phase 3a Agent Recovery Action Executor unit tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.agent.main import execute_recovery_actions_impl, run_recovery_sync_if_needed


class TestRecoveryExecutor:
    def test_execute_resume_registers_token_and_active_id(self):
        """RESUME action → register_active_job called with job_id, token, device_id."""
        local_db = MagicMock()
        lock_manager = MagicMock()
        outbox_drain = MagicMock()
        register_calls = []

        def register(jid: int, token: str = "", device_id: int | None = None):
            register_calls.append((jid, token, device_id))

        resp = {
            "actions": [
                {"job_id": 1, "device_id": 10, "action": "RESUME", "fencing_token": "tok-1"},
                {"job_id": 2, "device_id": 20, "action": "RESUME", "fencing_token": "tok-2"},
            ],
            "outbox_actions": [],
        }

        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id={},
            lock_manager=lock_manager,
            local_db=local_db,
            outbox_drain=outbox_drain,
            register_active_job=register,
        )

        assert len(register_calls) == 2
        assert register_calls[0] == (1, "tok-1", 10)
        assert register_calls[1] == (2, "tok-2", 20)

    def test_execute_cleanup_clears_local_state(self):
        """CLEANUP action → delete_active_job + clear_fencing_token called."""
        local_db = MagicMock()
        lock_manager = MagicMock()
        outbox_drain = MagicMock()

        resp = {
            "actions": [
                {"job_id": 5, "device_id": 50, "action": "CLEANUP", "reason": "boot_id_mismatch"},
            ],
            "outbox_actions": [],
        }

        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id={},
            lock_manager=lock_manager,
            local_db=local_db,
            outbox_drain=outbox_drain,
            register_active_job=MagicMock(),
        )

        local_db.delete_active_job.assert_called_once_with(5)
        lock_manager.clear_fencing_token.assert_called_once_with(5)

    def test_execute_upload_terminal_triggers_outbox_drain(self):
        """UPLOAD_TERMINAL action → drain_sync called, then delete_active_job for acked."""
        local_db = MagicMock()
        lock_manager = MagicMock()
        outbox_drain = MagicMock()
        outbox_drain.drain_sync.return_value = 2

        # drain_sync flushes the outbox → pending_outbox should be empty after
        local_db.get_pending_outbox.return_value = []

        resp = {
            "actions": [],
            "outbox_actions": [
                {"job_id": 3, "action": "UPLOAD_TERMINAL", "reason": "not_terminal_on_backend"},
                {"job_id": 4, "action": "NOOP", "reason": "already_terminal"},
            ],
        }

        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id={},
            lock_manager=lock_manager,
            local_db=local_db,
            outbox_drain=outbox_drain,
            register_active_job=MagicMock(),
        )

        outbox_drain.drain_sync.assert_called_once()
        assert local_db.delete_active_job.call_count == 2
        local_db.delete_active_job.assert_any_call(3)
        local_db.delete_active_job.assert_any_call(4)

    def test_abort_local_clears_local_state(self):
        """ABORT_LOCAL action → delete_active_job + clear_fencing_token."""
        local_db = MagicMock()
        lock_manager = MagicMock()
        outbox_drain = MagicMock()

        resp = {
            "actions": [
                {"job_id": 7, "device_id": 70, "action": "ABORT_LOCAL", "reason": "no_active_lease"},
            ],
            "outbox_actions": [],
        }

        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id={},
            lock_manager=lock_manager,
            local_db=local_db,
            outbox_drain=outbox_drain,
            register_active_job=MagicMock(),
        )

        local_db.delete_active_job.assert_called_once_with(7)
        lock_manager.clear_fencing_token.assert_called_once_with(7)

    def test_noop_without_upload_terminal_still_clears(self):
        """Pure NOOP actions (no UPLOAD_TERMINAL) still clear local_db."""
        local_db = MagicMock()
        lock_manager = MagicMock()
        outbox_drain = MagicMock()
        local_db.get_pending_outbox.return_value = []

        resp = {
            "actions": [],
            "outbox_actions": [
                {"job_id": 10, "action": "NOOP", "reason": "already_terminal"},
            ],
        }

        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id={},
            lock_manager=lock_manager,
            local_db=local_db,
            outbox_drain=outbox_drain,
            register_active_job=MagicMock(),
        )

        # drain_sync should NOT be called when there's no UPLOAD_TERMINAL
        outbox_drain.drain_sync.assert_not_called()
        # But NOOP should still clear
        local_db.delete_active_job.assert_called_once_with(10)


# ── Startup smoke tests (run_recovery_sync_if_needed) ──


class TestRecoverySyncStartup:
    """Verify the three recovery startup paths."""

    def test_clean_install_skip_recovery(self):
        """No persisted state → skip recovery, no sync_recovery call."""
        local_db = MagicMock()
        local_db.get_active_jobs.return_value = []
        local_db.get_pending_outbox.return_value = []
        execute_actions = MagicMock()

        with patch("backend.agent.main.sync_recovery") as mock_sync:
            run_recovery_sync_if_needed(
                local_db=local_db,
                api_url="http://x",
                host_id="h1",
                agent_instance_id="inst-1",
                boot_id="boot-1",
                execute_actions=execute_actions,
            )

        mock_sync.assert_not_called()
        execute_actions.assert_not_called()

    def test_active_jobs_triggers_resume(self):
        """Persisted active_jobs → sync_recovery called → execute_actions called."""
        local_db = MagicMock()
        local_db.get_active_jobs.return_value = [
            {"job_id": 1, "device_id": 10, "fencing_token": "tok-1"},
        ]
        local_db.get_pending_outbox.return_value = []
        execute_actions = MagicMock()

        with patch("backend.agent.main.sync_recovery") as mock_sync:
            mock_sync.return_value = {
                "actions": [{"job_id": 1, "device_id": 10, "action": "RESUME", "fencing_token": "tok-1"}],
                "outbox_actions": [],
            }
            run_recovery_sync_if_needed(
                local_db=local_db,
                api_url="http://x",
                host_id="h1",
                agent_instance_id="inst-1",
                boot_id="boot-1",
                execute_actions=execute_actions,
            )

        mock_sync.assert_called_once()
        execute_actions.assert_called_once()

    def test_pending_outbox_triggers_upload_terminal(self):
        """Persisted pending_outbox → sync_recovery called with outbox entries."""
        local_db = MagicMock()
        local_db.get_active_jobs.return_value = []
        local_db.get_pending_outbox.return_value = [
            {"job_id": 2, "event_type": "RUN_COMPLETED"},
        ]
        execute_actions = MagicMock()

        with patch("backend.agent.main.sync_recovery") as mock_sync:
            mock_sync.return_value = {
                "actions": [],
                "outbox_actions": [{"job_id": 2, "action": "UPLOAD_TERMINAL", "reason": "not_terminal"}],
            }
            run_recovery_sync_if_needed(
                local_db=local_db,
                api_url="http://x",
                host_id="h1",
                agent_instance_id="inst-1",
                boot_id="boot-1",
                execute_actions=execute_actions,
            )

        mock_sync.assert_called_once()
        # Verify outbox was passed to sync_recovery
        call_kwargs = mock_sync.call_args.kwargs
        assert call_kwargs["pending_outbox"] == [{"job_id": 2, "event_type": "RUN_COMPLETED"}]
        execute_actions.assert_called_once()

    def test_sync_recovery_network_failure_does_not_crash(self):
        """sync_recovery raises → log exception, continue (no crash)."""
        local_db = MagicMock()
        local_db.get_active_jobs.return_value = [
            {"job_id": 1, "device_id": 10, "fencing_token": "tok-1"},
        ]
        local_db.get_pending_outbox.return_value = []
        execute_actions = MagicMock()

        with patch("backend.agent.main.sync_recovery") as mock_sync:
            mock_sync.side_effect = ConnectionError("network down")
            # Must not raise
            run_recovery_sync_if_needed(
                local_db=local_db,
                api_url="http://x",
                host_id="h1",
                agent_instance_id="inst-1",
                boot_id="boot-1",
                execute_actions=execute_actions,
            )

        mock_sync.assert_called_once()
        execute_actions.assert_not_called()  # recovery skipped on failure
