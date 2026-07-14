"""ADR-0019 Phase 3a Agent Recovery Action Executor unit tests."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

import backend.agent.main as agent_main
from backend.agent.main import (
    execute_recovery_actions_impl,
    run_recovery_sync_if_needed,
    trigger_recovery_sync_on_device_reconnect,
)


class TestRecoveryExecutor:
    def test_cleanup_after_lease_lost_preserves_local_state_for_recovery(self):
        """lease lost cleanup clears in-memory occupancy but keeps local active_job for recovery."""
        local_db = MagicMock()
        active_job_ids = {7}
        active_device_ids = {70}
        active_job_tokens = {7: "70:3"}

        assert hasattr(agent_main, "_cleanup_after_lease_lost")

        agent_main._cleanup_after_lease_lost(
            job_id=7,
            device_id=70,
            active_jobs_lock=threading.Lock(),
            active_job_ids=active_job_ids,
            active_device_ids=active_device_ids,
            active_job_tokens=active_job_tokens,
            local_db=local_db,
        )

        assert 7 not in active_job_ids
        assert 70 not in active_device_ids
        assert 7 not in active_job_tokens
        local_db.delete_active_job.assert_not_called()

    def test_cleanup_after_job_exit_preserves_local_state_when_job_already_inactive(self):
        """worker 退出时若 job 已因 lease lost 被移出活跃集合，不应删除本地 active_job。"""
        local_db = MagicMock()
        lease_renewer = MagicMock()
        lease_renewer.clear_fencing_token_if_current.return_value = None
        active_job_ids = set()
        active_device_ids = set()
        active_job_tokens = {}

        agent_main._cleanup_after_job_exit(
            job_id=26,
            fencing_token="63:6",
            active_jobs_lock=threading.Lock(),
            active_job_ids=active_job_ids,
            active_device_ids=active_device_ids,
            active_job_tokens=active_job_tokens,
            lease_renewer=lease_renewer,
            local_db=local_db,
        )

        local_db.delete_active_job.assert_not_called()
        lease_renewer.clear_fencing_token_if_current.assert_called_once_with(26, "63:6", "63:6")

    def test_cleanup_after_job_exit_deletes_local_state_for_normal_completion(self):
        """正常完成时仍要删除本地 active_job，避免残留恢复记录。"""
        local_db = MagicMock()
        lease_renewer = MagicMock()
        lease_renewer.clear_fencing_token_if_current.return_value = 63
        active_job_ids = {26}
        active_device_ids = {63}
        active_job_tokens = {26: "63:6"}

        agent_main._cleanup_after_job_exit(
            job_id=26,
            fencing_token="63:6",
            active_jobs_lock=threading.Lock(),
            active_job_ids=active_job_ids,
            active_device_ids=active_device_ids,
            active_job_tokens=active_job_tokens,
            lease_renewer=lease_renewer,
            local_db=local_db,
        )

        assert 26 not in active_job_ids
        assert 63 not in active_device_ids
        assert 26 not in active_job_tokens
        local_db.delete_active_job.assert_called_once_with(26)
        lease_renewer.clear_fencing_token_if_current.assert_called_once_with(26, "63:6", "63:6")

    def test_cleanup_after_job_exit_does_not_clear_recovered_replacement(self):
        """旧 worker 退出时，若 job 已被新 fencing_token 接管，不应清掉新活跃状态。"""
        local_db = MagicMock()
        lease_renewer = MagicMock()
        lease_renewer.clear_fencing_token_if_current.return_value = None
        active_job_ids = {26}
        active_device_ids = {63}
        active_job_tokens = {26: "63:7"}

        agent_main._cleanup_after_job_exit(
            job_id=26,
            fencing_token="63:6",
            active_jobs_lock=threading.Lock(),
            active_job_ids=active_job_ids,
            active_device_ids=active_device_ids,
            active_job_tokens=active_job_tokens,
            lease_renewer=lease_renewer,
            local_db=local_db,
        )

        assert active_job_ids == {26}
        assert active_device_ids == {63}
        assert active_job_tokens == {26: "63:7"}
        local_db.delete_active_job.assert_not_called()
        lease_renewer.clear_fencing_token_if_current.assert_called_once_with(26, "63:6", "63:6")

    def test_cleanup_after_job_exit_does_not_clear_same_fencing_replacement(self):
        """旧 worker 退出时，若同 fencing_token 下已被新本地 worker 接管，也不应清掉新状态。"""
        local_db = MagicMock()
        lease_renewer = MagicMock()
        lease_renewer.clear_fencing_token_if_current.return_value = None
        active_job_ids = {26}
        active_device_ids = {63}
        active_job_tokens = {26: "worker-new"}

        agent_main._cleanup_after_job_exit(
            job_id=26,
            fencing_token="63:6",
            local_worker_token="worker-old",
            active_jobs_lock=threading.Lock(),
            active_job_ids=active_job_ids,
            active_device_ids=active_device_ids,
            active_job_tokens=active_job_tokens,
            lease_renewer=lease_renewer,
            local_db=local_db,
        )

        assert active_job_ids == {26}
        assert active_device_ids == {63}
        assert active_job_tokens == {26: "worker-new"}
        local_db.delete_active_job.assert_not_called()
        lease_renewer.clear_fencing_token_if_current.assert_called_once_with(
            26, "63:6", "worker-old",
        )

    def test_execute_resume_registers_token_and_active_id(self):
        """RESUME action → register_active_job called with job_id, token, device_id, device_serial."""
        local_db = MagicMock()
        lease_renewer = MagicMock()
        outbox_drain = MagicMock()
        register_calls = []

        def register(
            jid: int,
            token: str = "",
            device_id: int | None = None,
            device_serial: str = "",
            local_worker_token: str = "",
        ):
            register_calls.append((jid, token, device_id, device_serial, local_worker_token))

        resp = {
            "actions": [
                {"job_id": 1, "device_id": 10, "action": "RESUME", "fencing_token": "tok-1",
                 "job_payload": {"id": 1}},
                {"job_id": 2, "device_id": 20, "action": "RESUME", "fencing_token": "tok-2",
                 "job_payload": {"id": 2}},
            ],
            "outbox_actions": [],
        }

        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id={
                1: {"job_id": 1, "device_id": 10, "device_serial": "SERIAL-1", "fencing_token": "tok-1"},
                2: {"job_id": 2, "device_id": 20, "device_serial": "SERIAL-2", "fencing_token": "tok-2"},
            },
            lease_renewer=lease_renewer,
            local_db=local_db,
            outbox_drain=outbox_drain,
            register_active_job=register,
        )

        assert len(register_calls) == 2
        assert register_calls[0][:4] == (1, "tok-1", 10, "SERIAL-1")
        assert register_calls[0][4].startswith("resume-1-")
        assert register_calls[1][:4] == (2, "tok-2", 20, "SERIAL-2")
        assert register_calls[1][4].startswith("resume-2-")

    def test_execute_resume_prefers_action_device_serial_for_legacy_local_job(self):
        """旧本地 active_job 未持久化 serial 时，使用后端 RESUME 回包里的真实 serial 回填。"""
        local_db = MagicMock()
        lease_renewer = MagicMock()
        outbox_drain = MagicMock()
        register_calls = []

        def register(
            jid: int,
            token: str = "",
            device_id: int | None = None,
            device_serial: str = "",
            local_worker_token: str = "",
        ):
            register_calls.append((jid, token, device_id, device_serial, local_worker_token))

        resp = {
            "actions": [
                {
                    "job_id": 21,
                    "device_id": 62,
                    "action": "RESUME",
                    "fencing_token": "tok-21",
                    "device_serial": "121512542H004524",
                    "job_payload": {"id": 21},
                },
            ],
            "outbox_actions": [],
        }

        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id={
                21: {"job_id": 21, "device_id": 62, "device_serial": "", "fencing_token": "tok-21"},
            },
            lease_renewer=lease_renewer,
            local_db=local_db,
            outbox_drain=outbox_drain,
            register_active_job=register,
        )

        assert len(register_calls) == 1
        assert register_calls[0][:4] == (21, "tok-21", 62, "121512542H004524")
        assert register_calls[0][4].startswith("resume-21-")

    def test_execute_resume_submits_worker_when_job_payload_present(self):
        """RESUME action 携带 job_payload 时，Agent 必须真正触发恢复执行。"""
        local_db = MagicMock()
        lease_renewer = MagicMock()
        outbox_drain = MagicMock()
        register_active_job = MagicMock()
        resume_job = MagicMock()

        resp = {
            "actions": [
                {
                    "job_id": 26,
                    "device_id": 63,
                    "action": "RESUME",
                    "fencing_token": "63:6",
                    "device_serial": "11914404BG102162",
                    "job_payload": {
                        "id": 26,
                        "plan_run_id": 28,
                        "plan_id": 6,
                        "device_id": 63,
                        "device_serial": "11914404BG102162",
                        "host_id": "auto-fdaf1d55e319",
                        "status": "RUNNING",
                        "pipeline_def": {"lifecycle": {"init": [], "teardown": []}},
                        "watcher_policy": None,
                        "fencing_token": "63:6",
                    },
                },
            ],
            "outbox_actions": [],
        }

        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id={
                26: {
                    "job_id": 26,
                    "device_id": 63,
                    "device_serial": "11914404BG102162",
                    "fencing_token": "63:6",
                },
            },
            lease_renewer=lease_renewer,
            local_db=local_db,
            outbox_drain=outbox_drain,
            register_active_job=register_active_job,
            resume_job=resume_job,
        )

        register_args = register_active_job.call_args.args
        assert register_args[:4] == (26, "63:6", 63, "11914404BG102162")
        assert register_args[4].startswith("resume-26-")
        resume_job.assert_called_once()
        resumed_payload = resume_job.call_args.args[0]
        assert resumed_payload["id"] == 26
        assert resumed_payload["device_id"] == 63
        assert resumed_payload["device_serial"] == "11914404BG102162"
        assert resumed_payload["fencing_token"] == "63:6"
        assert resumed_payload["local_worker_token"].startswith("resume-26-")
        # T3: resumed payload carries the catchup marker so the watcher re-attach
        # is observable downstream (job_runner logs watcher_catchup_reattach).
        assert resumed_payload["recovery_resumed"] is True

    def test_execute_resume_without_payload_skips_register_and_resume(self):
        """RESUME 无 job_payload（契约违反 / 旧后端）→ 不登记 active、不触发执行，
        避免产生永不恢复的僵尸 active_job（T2.2 防御）。"""
        local_db = MagicMock()
        lease_renewer = MagicMock()
        outbox_drain = MagicMock()
        register_active_job = MagicMock()
        resume_job = MagicMock()

        resp = {
            "actions": [
                {"job_id": 99, "device_id": 90, "action": "RESUME", "fencing_token": "90:1"},
            ],
            "outbox_actions": [],
        }

        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id={
                99: {"job_id": 99, "device_id": 90, "device_serial": "S-99", "fencing_token": "90:1"},
            },
            lease_renewer=lease_renewer,
            local_db=local_db,
            outbox_drain=outbox_drain,
            register_active_job=register_active_job,
            resume_job=resume_job,
        )

        register_active_job.assert_not_called()
        resume_job.assert_not_called()

    def test_execute_cleanup_clears_local_state(self):
        """CLEANUP action → delete_active_job + clear_fencing_token called."""
        local_db = MagicMock()
        lease_renewer = MagicMock()
        outbox_drain = MagicMock()
        abort_local_job = MagicMock()

        resp = {
            "actions": [
                {"job_id": 5, "device_id": 50, "action": "CLEANUP", "reason": "boot_id_mismatch"},
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
            abort_local_job=abort_local_job,
        )

        abort_local_job.assert_called_once_with(5)
        local_db.delete_active_job.assert_called_once_with(5)
        lease_renewer.clear_fencing_token.assert_called_once_with(5)

    def test_execute_upload_terminal_triggers_outbox_drain(self):
        """UPLOAD_TERMINAL action → drain_sync called, then delete_active_job for acked."""
        local_db = MagicMock()
        lease_renewer = MagicMock()
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
            lease_renewer=lease_renewer,
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
        lease_renewer = MagicMock()
        outbox_drain = MagicMock()
        abort_local_job = MagicMock()

        resp = {
            "actions": [
                {"job_id": 7, "device_id": 70, "action": "ABORT_LOCAL", "reason": "no_active_lease"},
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
            abort_local_job=abort_local_job,
        )

        abort_local_job.assert_called_once_with(7)
        local_db.delete_active_job.assert_called_once_with(7)
        lease_renewer.clear_fencing_token.assert_called_once_with(7)

    def test_noop_without_upload_terminal_still_clears(self):
        """Pure NOOP actions (no UPLOAD_TERMINAL) still clear local_db."""
        local_db = MagicMock()
        lease_renewer = MagicMock()
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
            lease_renewer=lease_renewer,
            local_db=local_db,
            outbox_drain=outbox_drain,
            register_active_job=MagicMock(),
        )

        # drain_sync should NOT be called when there's no UPLOAD_TERMINAL
        outbox_drain.drain_sync.assert_not_called()
        # But NOOP should still clear
        local_db.delete_active_job.assert_called_once_with(10)

    def test_resume_job_not_deleted_by_upload_terminal_outbox_cleanup(self):
        """同一 job 同时收到 RESUME + UPLOAD_TERMINAL 时，不应被 outbox 清理删掉 active_job。"""
        local_db = MagicMock()
        lease_renewer = MagicMock()
        outbox_drain = MagicMock()
        outbox_drain.drain_sync.return_value = 1
        local_db.get_pending_outbox.return_value = []
        register_active_job = MagicMock()

        resp = {
            "actions": [
                {
                    "job_id": 26,
                    "device_id": 63,
                    "action": "RESUME",
                    "fencing_token": "63:6",
                    "device_serial": "11914404BG102162",
                    "job_payload": {"id": 26},
                },
            ],
            "outbox_actions": [
                {"job_id": 26, "action": "UPLOAD_TERMINAL", "reason": "not_terminal_on_backend"},
            ],
        }

        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id={
                26: {
                    "job_id": 26,
                    "device_id": 63,
                    "device_serial": "11914404BG102162",
                    "fencing_token": "63:6",
                },
            },
            lease_renewer=lease_renewer,
            local_db=local_db,
            outbox_drain=outbox_drain,
            register_active_job=register_active_job,
        )

        register_args = register_active_job.call_args.args
        assert register_args[:4] == (26, "63:6", 63, "11914404BG102162")
        assert register_args[4].startswith("resume-26-")
        local_db.delete_active_job.assert_not_called()


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
            {"job_id": 1, "device_id": 10, "device_serial": "SERIAL-1", "fencing_token": "tok-1"},
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
            {"job_id": 1, "device_id": 10, "device_serial": "SERIAL-1", "fencing_token": "tok-1"},
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


class TestReconnectRecoveryTrigger:
    def test_reconnected_device_triggers_recovery_only_for_matching_serial(self):
        local_db = MagicMock()
        local_db.get_active_jobs.return_value = [
            {"job_id": 1, "device_id": 10, "device_serial": "ABC123", "fencing_token": "tok-1"},
            {"job_id": 2, "device_id": 20, "device_serial": "ZZZ999", "fencing_token": "tok-2"},
        ]
        execute_actions = MagicMock()

        with patch("backend.agent.main.run_recovery_sync_if_needed") as mock_run:
            triggered = trigger_recovery_sync_on_device_reconnect(
                reconnected_serials=["ABC123"],
                local_db=local_db,
                api_url="http://x",
                host_id="h1",
                agent_instance_id="inst-1",
                boot_id="boot-1",
                execute_actions=execute_actions,
            )

        assert triggered is True
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["active_jobs"] == [
            {"job_id": 1, "device_id": 10, "device_serial": "ABC123", "fencing_token": "tok-1"},
        ]

    def test_reconnected_device_skips_recovery_without_local_jobs(self):
        local_db = MagicMock()
        local_db.get_active_jobs.return_value = []
        execute_actions = MagicMock()

        with patch("backend.agent.main.run_recovery_sync_if_needed") as mock_run:
            triggered = trigger_recovery_sync_on_device_reconnect(
                reconnected_serials=["ABC123"],
                local_db=local_db,
                api_url="http://x",
                host_id="h1",
                agent_instance_id="inst-1",
                boot_id="boot-1",
                execute_actions=execute_actions,
            )

        assert triggered is False
        mock_run.assert_not_called()

    def test_reconnected_device_skips_recovery_without_matching_serial(self):
        local_db = MagicMock()
        local_db.get_active_jobs.return_value = [
            {"job_id": 1, "device_id": 10, "device_serial": "OLD111", "fencing_token": "tok-1"},
            {"job_id": 2, "device_id": 20, "device_serial": "", "fencing_token": "tok-2"},
        ]
        execute_actions = MagicMock()

        with patch("backend.agent.main.run_recovery_sync_if_needed") as mock_run:
            triggered = trigger_recovery_sync_on_device_reconnect(
                reconnected_serials=["NEW999"],
                local_db=local_db,
                api_url="http://x",
                host_id="h1",
                agent_instance_id="inst-1",
                boot_id="boot-1",
                execute_actions=execute_actions,
            )

        assert triggered is False
        mock_run.assert_not_called()
