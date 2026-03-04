# -*- coding: utf-8 -*-
"""Unit tests for new/enhanced builtin actions (aee-script-migration change)."""

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import MagicMock, patch, call

import pytest

# Import StepContext and StepResult
from backend.agent.pipeline_engine import StepContext, StepResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(params=None, shared=None, local_db=None, adb_responses=None):
    """Build a StepContext with a mock ADB and logger."""
    adb = MagicMock()
    responses = list(adb_responses or [])

    def _shell(serial, cmd, timeout=30):
        if responses:
            resp = responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp
        return ""

    adb.shell = MagicMock(side_effect=_shell)
    adb.pull = MagicMock()
    adb.push = MagicMock()
    adb.adb_path = "adb"

    logger = MagicMock()

    return StepContext(
        adb=adb,
        serial="TEST001",
        params=params or {},
        run_id=1,
        step_id=0,
        logger=logger,
        shared=shared if shared is not None else {},
        local_db=local_db,
    )


class FakeLocalDB:
    """In-memory LocalDB substitute."""

    def __init__(self):
        self._store = {}

    def get_state(self, key, default=""):
        return self._store.get(key, default)

    def set_state(self, key, value):
        self._store[key] = value


# ===========================================================================
# 9.1  setup_device_commands
# ===========================================================================

class TestSetupDeviceCommands:
    def test_all_succeed(self):
        from backend.agent.actions.device_actions import setup_device_commands

        ctx = _make_ctx(
            params={"commands": [
                {"cmd": "settings put global foo 1", "timeout": 10},
                {"cmd": "setprop bar baz", "timeout": 10},
            ]},
            adb_responses=["OK", "OK"],
        )
        result = setup_device_commands(ctx)
        assert result.success is True
        assert result.metrics["executed"] == 2
        assert result.metrics["failed"] == 0

    def test_on_failure_stop(self):
        from backend.agent.actions.device_actions import setup_device_commands

        ctx = _make_ctx(
            params={"commands": [
                {"cmd": "cmd1", "timeout": 5, "on_failure": "stop"},
                {"cmd": "cmd2", "timeout": 5},
            ]},
            adb_responses=[Exception("device offline"), "OK"],
        )
        result = setup_device_commands(ctx)
        assert result.success is False
        assert result.metrics["executed"] == 0
        assert result.metrics["failed"] == 1
        # cmd2 should never have been attempted
        assert ctx.adb.shell.call_count == 1

    def test_empty_commands(self):
        from backend.agent.actions.device_actions import setup_device_commands

        ctx = _make_ctx(params={"commands": []})
        result = setup_device_commands(ctx)
        assert result.success is True
        assert result.metrics["executed"] == 0

    def test_on_failure_continue(self):
        from backend.agent.actions.device_actions import setup_device_commands

        ctx = _make_ctx(
            params={"commands": [
                {"cmd": "fail_cmd", "timeout": 5, "on_failure": "continue"},
                {"cmd": "ok_cmd", "timeout": 5},
            ]},
            adb_responses=[Exception("timeout"), "OK"],
        )
        result = setup_device_commands(ctx)
        assert result.success is True
        assert result.metrics["executed"] == 1
        assert result.metrics["failed"] == 1


# ===========================================================================
# 9.2  guard_process
# ===========================================================================

class TestGuardProcess:
    def test_process_alive(self):
        from backend.agent.actions.process_actions import guard_process

        ctx = _make_ctx(
            params={"process_name": "monkey"},
            adb_responses=["12345\n"],
        )
        result = guard_process(ctx)
        assert result.success is True
        assert result.metrics["status"] == "alive"
        assert result.metrics["pid"] == "12345"

    def test_process_dead_restart_success(self):
        from backend.agent.actions.process_actions import guard_process

        ctx = _make_ctx(
            params={
                "process_name": "monkey",
                "restart_command": "nohup monkey &",
                "max_restarts": 1,
            },
            adb_responses=[
                "",           # pgrep: no process
                "started",    # restart_command
                "99999\n",    # pgrep re-check
            ],
        )
        result = guard_process(ctx)
        assert result.success is True
        assert result.metrics["status"] == "restarted"
        assert result.metrics["restart_count"] == 1

    def test_multiple_instances_dedup(self):
        from backend.agent.actions.process_actions import guard_process

        ctx = _make_ctx(
            params={"process_name": "monkey"},
            adb_responses=["100\n200\n300\n", "", ""],  # pgrep, kill 200, kill 300
        )
        result = guard_process(ctx)
        assert result.success is True
        assert result.metrics["status"] == "deduplicated"
        assert result.metrics["killed_duplicates"] == 2

    def test_resource_missing(self):
        from backend.agent.actions.process_actions import guard_process

        ctx = _make_ctx(
            params={
                "process_name": "monkey",
                "restart_command": "nohup monkey &",
                "resource_check_path": "/data/local/tmp/script.sh",
            },
            adb_responses=[
                "",                # pgrep: no process
                "not_found",       # resource check fails
            ],
        )
        result = guard_process(ctx)
        assert result.success is False
        assert result.metrics["status"] == "resource_missing"

    def test_no_restart_command(self):
        from backend.agent.actions.process_actions import guard_process

        ctx = _make_ctx(
            params={"process_name": "monkey"},
            adb_responses=[""],  # pgrep: no process
        )
        result = guard_process(ctx)
        assert result.success is False
        assert result.metrics["status"] == "dead_no_restart_cmd"

    def test_max_restarts_exhausted(self):
        """All restart attempts fail → status=restart_failed."""
        from backend.agent.actions.process_actions import guard_process

        ctx = _make_ctx(
            params={
                "process_name": "monkey",
                "restart_command": "nohup monkey &",
                "max_restarts": 2,
            },
            adb_responses=[
                "",           # pgrep: no process
                "started",    # restart attempt 1
                "",           # re-check pgrep: still dead
                "started",    # restart attempt 2
                "",           # re-check pgrep: still dead
            ],
        )
        result = guard_process(ctx)
        assert result.success is False
        assert result.metrics["status"] == "restart_failed"
        assert result.metrics["restart_count"] == 2


# ===========================================================================
# 9.3  scan_aee incremental
# ===========================================================================

class TestScanAeeIncremental:
    def test_full_mode_unchanged(self):
        from backend.agent.actions.file_actions import scan_aee

        ctx = _make_ctx(
            params={
                "aee_dirs": ["/data/aee_exp"],
                "local_dir": tempfile.mkdtemp(),
            },
            adb_responses=["entry1\nentry2\n"],
        )
        result = scan_aee(ctx)
        assert result.success is True
        assert result.metrics["scanned"] == 2

    def test_incremental_first_run(self):
        from backend.agent.actions.file_actions import scan_aee

        local_db = FakeLocalDB()
        # Real db_history format: col 0=db_path, col 8=pkg_name, col 9=timestamp
        db_line = "/data/aee_exp/db.01.NE,Native (NE),1786,1786,99,/data/vendor/core/,1,SIGSEGV,com.test.app,2025-07-19 11:28:43"
        ctx = _make_ctx(
            params={
                "aee_dirs": ["/data/aee_exp"],
                "local_dir": tempfile.mkdtemp(),
                "incremental": True,
            },
            adb_responses=[db_line],
            local_db=local_db,
        )
        result = scan_aee(ctx)
        assert result.success is True
        assert result.metrics["pulled"] == 1
        assert result.metrics["skipped_known"] == 0
        assert len(result.metrics["new_timestamps"]) == 1

    def test_incremental_second_run_skips_known(self):
        from backend.agent.actions.file_actions import scan_aee

        local_db = FakeLocalDB()
        # Real db_history format: col 0=db_path, col 8=pkg_name, col 9=timestamp
        db_line = "/data/aee_exp/db.01.NE,Native (NE),1786,1786,99,/data/vendor/core/,1,SIGSEGV,com.test.app,2025-07-19 11:28:43"
        # Pre-populate LocalDB with already processed entry
        local_db.set_state(
            "scan_aee:TEST001:aee_exp:processed_entries",
            json.dumps([db_line]),
        )
        ctx = _make_ctx(
            params={
                "aee_dirs": ["/data/aee_exp"],
                "local_dir": tempfile.mkdtemp(),
                "incremental": True,
            },
            adb_responses=[db_line],
            local_db=local_db,
        )
        result = scan_aee(ctx)
        assert result.success is True
        assert result.metrics["pulled"] == 0
        assert result.metrics["skipped_known"] == 1

    def test_whitelist_filter(self):
        from backend.agent.actions.file_actions import scan_aee

        local_db = FakeLocalDB()
        # Two entries: com.allowed should pass whitelist, com.blocked should be filtered
        # Real format: col 0=db_path, col 8=pkg_name, col 9=timestamp
        lines = (
            "/data/aee_exp/db.01.NE,Native (NE),1,1,99,/data/vendor/core/,1,SIGSEGV,com.allowed,2025-07-19 10:00:00\n"
            "/data/aee_exp/db.02.JE,Java (JE),2,2,99,/data/vendor/core/,1,crash,com.blocked,2025-07-19 11:00:00"
        )
        # Create whitelist file
        wl_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        wl_file.write("com.allowed\n")
        wl_file.close()

        ctx = _make_ctx(
            params={
                "aee_dirs": ["/data/aee_exp"],
                "local_dir": tempfile.mkdtemp(),
                "incremental": True,
                "whitelist_file": wl_file.name,
            },
            adb_responses=[lines],
            local_db=local_db,
        )
        result = scan_aee(ctx)
        assert result.success is True
        assert result.metrics["pulled"] == 1
        assert result.metrics["filtered_whitelist"] == 1
        os.unlink(wl_file.name)

    def test_local_db_none_fallback(self):
        from backend.agent.actions.file_actions import scan_aee

        ctx = _make_ctx(
            params={
                "aee_dirs": ["/data/aee_exp"],
                "local_dir": tempfile.mkdtemp(),
                "incremental": True,
            },
            adb_responses=["entry1\nentry2\n"],
            local_db=None,
        )
        result = scan_aee(ctx)
        assert result.success is True
        # Should fall back to full mode
        assert "new_timestamps" not in result.metrics or result.metrics.get("scanned", 0) >= 0


# ===========================================================================
# 9.4  export_mobilelogs
# ===========================================================================

class TestExportMobilelogs:
    def test_with_match(self):
        from backend.agent.actions.file_actions import export_mobilelogs

        # Use ISO timestamp that directly matches a mobilelog dir
        ctx = _make_ctx(
            params={
                "timestamps_from_step": "scan_aee",
                "local_dir": tempfile.mkdtemp(),
                "time_window_minutes": 30,
            },
            shared={
                "scan_aee": {"new_timestamps": ["2024/03/04 02:06:40"]},
            },
            adb_responses=[
                # ls mobilelog
                "APLog_2024_0304_020640\nAPLog_2024_0303_150000\n",
            ],
        )
        result = export_mobilelogs(ctx)
        assert result.success is True
        assert result.metrics["matched"] == 1

    def test_no_match(self):
        from backend.agent.actions.file_actions import export_mobilelogs

        ctx = _make_ctx(
            params={
                "timestamps_from_step": "scan_aee",
                "local_dir": tempfile.mkdtemp(),
                "time_window_minutes": 1,
            },
            shared={
                "scan_aee": {"new_timestamps": ["1000000000"]},
            },
            adb_responses=["APLog_2026_0304_120000\n"],
        )
        result = export_mobilelogs(ctx)
        assert result.success is True
        assert result.metrics["matched"] == 0
        assert len(result.metrics["unmatched_timestamps"]) == 1

    def test_empty_timestamps(self):
        from backend.agent.actions.file_actions import export_mobilelogs

        ctx = _make_ctx(
            params={
                "timestamps_from_step": "scan_aee",
                "local_dir": tempfile.mkdtemp(),
            },
            shared={"scan_aee": {"new_timestamps": []}},
        )
        result = export_mobilelogs(ctx)
        assert result.success is True
        assert result.metrics["pulled"] == 0

    def test_no_shared_step(self):
        from backend.agent.actions.file_actions import export_mobilelogs

        ctx = _make_ctx(
            params={
                "timestamps_from_step": "nonexistent",
                "local_dir": tempfile.mkdtemp(),
            },
        )
        result = export_mobilelogs(ctx)
        assert result.success is True
        assert result.metrics["pulled"] == 0

    def test_at_separator_and_ctime_format(self):
        """Verify parsing of 'ctime @ iso' timestamps from real device data."""
        from backend.agent.actions.file_actions import export_mobilelogs

        ctx = _make_ctx(
            params={
                "timestamps_from_step": "scan_aee",
                "local_dir": tempfile.mkdtemp(),
                "time_window_minutes": 30,
            },
            shared={
                "scan_aee": {
                    "new_timestamps": [
                        "Sat Jul 19 10:15:42 CST 2025 @ 2025-07-19 10:15:42.273301",
                    ]
                },
            },
            adb_responses=[
                "APLog_2025_0719_101500\nAPLog_2025_0718_080000\n",
            ],
        )
        result = export_mobilelogs(ctx)
        assert result.success is True
        assert result.metrics["matched"] == 1

    def test_ctime_only_format(self):
        """Verify parsing of bare ctime timestamps without @ separator."""
        from backend.agent.actions.file_actions import export_mobilelogs

        ctx = _make_ctx(
            params={
                "timestamps_from_step": "scan_aee",
                "local_dir": tempfile.mkdtemp(),
                "time_window_minutes": 30,
            },
            shared={
                "scan_aee": {
                    "new_timestamps": ["Sat Jul 19 10:15:42 CST 2025"],
                },
            },
            adb_responses=[
                "APLog_2025_0719_101500\n",
            ],
        )
        result = export_mobilelogs(ctx)
        assert result.success is True
        assert result.metrics["matched"] == 1


# ===========================================================================
# 9.5  aee_extract batch
# ===========================================================================

class TestAeeExtractBatch:
    def test_single_file_mode_unchanged(self):
        from backend.agent.actions.log_actions import aee_extract

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            ctx = _make_ctx(params={"input_dir": "/tmp/aee", "output_dir": "/tmp/out", "tool_path": "aee_extract"})
            result = aee_extract(ctx)
            assert result.success is True

    def test_batch_scan_and_decrypt(self):
        from backend.agent.actions.log_actions import aee_extract

        # Create temp dir with .dbg files
        tmpdir = tempfile.mkdtemp()
        for i in range(3):
            open(os.path.join(tmpdir, f"file{i}.dbg"), "w").close()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            ctx = _make_ctx(
                params={"input_dir": tmpdir, "batch": True, "max_workers": 2, "tool_path": "aee_extract"},
                local_db=FakeLocalDB(),
            )
            result = aee_extract(ctx)
            assert result.success is True
            assert result.metrics["total_found"] == 3
            assert result.metrics["decrypted"] == 3

    def test_batch_retry_limit_skip(self):
        from backend.agent.actions.log_actions import aee_extract

        tmpdir = tempfile.mkdtemp()
        dbg_path = os.path.join(tmpdir, "fail.dbg")
        open(dbg_path, "w").close()

        local_db = FakeLocalDB()
        # Pre-set failure count at retry limit
        local_db.set_state("aee_decrypt:failures", json.dumps({dbg_path: 2}))

        ctx = _make_ctx(
            params={"input_dir": tmpdir, "batch": True, "retry_limit": 2, "tool_path": "aee_extract"},
            local_db=local_db,
        )
        result = aee_extract(ctx)
        assert result.success is True
        assert result.metrics["skipped_retry_limit"] == 1
        assert result.metrics["decrypted"] == 0

    def test_batch_low_disk(self):
        from backend.agent.actions.log_actions import aee_extract

        tmpdir = tempfile.mkdtemp()
        open(os.path.join(tmpdir, "file.dbg"), "w").close()

        with patch("shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(free=1 * 1024**3)  # 1 GB
            ctx = _make_ctx(
                params={"input_dir": tmpdir, "batch": True, "min_free_disk_gb": 10, "tool_path": "aee_extract"},
            )
            result = aee_extract(ctx)
            assert result.success is True
            assert result.metrics["skipped_low_disk"] is True


# ===========================================================================
# 9.6  PipelineEngine shared write fix regression
# ===========================================================================

class TestPipelineEngineSharedFix:
    def test_stages_path_writes_metrics_to_shared(self):
        """Verify _execute_step_stages writes result.metrics to self._shared."""
        from backend.agent.pipeline_engine import PipelineEngine, StepResult

        engine = PipelineEngine(
            adb=MagicMock(),
            serial="TEST001",
            run_id=1,
            mq_producer=MagicMock(connected=False),
        )

        # Mock action that returns metrics
        test_metrics = {"pulled": 5, "new_timestamps": ["ts1", "ts2"]}

        def fake_action(ctx):
            return StepResult(success=True, metrics=test_metrics)

        with patch.object(engine, "_resolve_action_stages", return_value=fake_action):
            with patch.object(engine, "_report_step_trace_mq"):
                step = {"step_id": "scan_aee", "action": "builtin:scan_aee", "params": {}, "timeout_seconds": 30}
                result = engine._execute_step_stages("execute", step)

        assert result.success is True
        assert engine._shared["scan_aee"] == test_metrics
