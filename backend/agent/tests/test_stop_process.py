# -*- coding: utf-8 -*-
"""Unit tests for stop_process process_name enhancement."""

from unittest.mock import MagicMock

import pytest

from backend.agent.pipeline_engine import StepContext, StepResult


def _make_ctx(params=None, shared=None, adb_responses=None):
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
    logger = MagicMock()

    return StepContext(
        adb=adb,
        serial="FAKE001",
        params=params or {},
        run_id=1,
        step_id=0,
        logger=logger,
        shared=shared or {},
    )


class TestStopProcess:
    """Tests for backend.agent.actions.process_actions.stop_process."""

    def test_pid_from_step_priority(self):
        """pid_from_step takes priority over process_name."""
        from backend.agent.actions.process_actions import stop_process

        ctx = _make_ctx(
            params={"pid_from_step": "start_monkey", "process_name": "com.monkey"},
            shared={"start_monkey": {"pid": 12345}},
            adb_responses=[""],  # kill response
        )
        result = stop_process(ctx)
        assert result.success is True
        # Should have called kill with PID, not pgrep
        ctx.adb.shell.assert_called_once()
        call_cmd = ctx.adb.shell.call_args[0][1]
        assert "kill -9 12345" in call_cmd
        assert "pgrep" not in call_cmd

    def test_process_name_fallback(self):
        """When pid_from_step is not available, use process_name with pgrep -f."""
        from backend.agent.actions.process_actions import stop_process

        ctx = _make_ctx(
            params={"process_name": "com.android.commands.monkey.transsion"},
            adb_responses=[
                MagicMock(stdout="1234\n5678\n"),  # pgrep response
                "",  # kill 1234
                "",  # kill 5678
            ],
        )
        result = stop_process(ctx)
        assert result.success is True
        assert ctx.adb.shell.call_count == 3  # pgrep + 2 kills
        ctx.logger.info.assert_called()
        log_msg = ctx.logger.info.call_args[0][0]
        assert "2 process(es)" in log_msg

    def test_process_name_no_match(self):
        """When pgrep finds no matching process, return success (no-op)."""
        from backend.agent.actions.process_actions import stop_process

        ctx = _make_ctx(
            params={"process_name": "nonexistent.process"},
            adb_responses=[MagicMock(stdout="")],  # pgrep returns empty
        )
        result = stop_process(ctx)
        assert result.success is True
        assert ctx.adb.shell.call_count == 1  # only pgrep, no kill

    def test_no_pid_no_process_name(self):
        """When neither pid nor process_name is provided, return success (no-op)."""
        from backend.agent.actions.process_actions import stop_process

        ctx = _make_ctx(params={})
        result = stop_process(ctx)
        assert result.success is True
        ctx.adb.shell.assert_not_called()
        ctx.logger.info.assert_called_once()
        assert "No PID or process_name" in ctx.logger.info.call_args[0][0]

    def test_process_name_pgrep_exception(self):
        """When pgrep raises an exception, treat as no match and succeed."""
        from backend.agent.actions.process_actions import stop_process

        ctx = _make_ctx(
            params={"process_name": "com.test.app"},
            adb_responses=[Exception("adb connection lost")],
        )
        result = stop_process(ctx)
        assert result.success is True

    def test_process_name_shell_injection_rejected(self):
        """process_name with shell metacharacters is rejected (security)."""
        from backend.agent.actions.process_actions import stop_process

        for malicious_name in [
            "com.app'; rm -rf /; '",
            "com.app$(whoami)",
            "com.app`id`",
            "com.app & echo pwned",
            'com.app"; cat /etc/passwd',
            "-u0",           # pgrep option injection (leading dash)
            "--full",        # pgrep long option injection
        ]:
            ctx = _make_ctx(params={"process_name": malicious_name})
            result = stop_process(ctx)
            assert result.success is False, f"Should reject: {malicious_name}"
            assert "unsafe characters" in result.error_message
            ctx.adb.shell.assert_not_called()

    def test_process_name_valid_patterns_accepted(self):
        """Valid process name patterns pass the safety check."""
        from backend.agent.actions.process_actions import stop_process

        for valid_name in [
            "com.android.commands.monkey.transsion",
            "com.example.app",
            "/data/local/tmp/aimwd",
            "my-test-process",
            "process_v2.0",
        ]:
            ctx = _make_ctx(
                params={"process_name": valid_name},
                adb_responses=[MagicMock(stdout="")],  # pgrep returns empty
            )
            result = stop_process(ctx)
            assert result.success is True, f"Should accept: {valid_name}"
