import sys
from pathlib import Path

import pytest

try:
    from backend.agent.tools.adapters.legacy_monkey_aee_adapter import (
        LegacyMonkeyAEEAdapter,
        LegacyMonkeyAEEConfig,
    )
    from backend.agent.tools.monkey_aee_stability_test import MonkeyAEEStabilityTest
    from backend.agent.task_executor import TaskExecutor
except ModuleNotFoundError:  # pragma: no cover
    from agent.tools.adapters.legacy_monkey_aee_adapter import (
        LegacyMonkeyAEEAdapter,
        LegacyMonkeyAEEConfig,
    )
    from agent.tools.monkey_aee_stability_test import MonkeyAEEStabilityTest
    from agent.task_executor import TaskExecutor


class DummyAdb:
    def shell(self, serial, cmd):
        class _Result:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return _Result()

    def pull(self, serial, remote_path, local_path):
        return None


def _write_script(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_legacy_adapter_run_success(tmp_path: Path):
    script = tmp_path / "legacy_ok.py"
    _write_script(
        script,
        "import sys, time\n"
        "print('start')\n"
        "print('serial=' + (sys.argv[2] if len(sys.argv) > 2 else 'none'))\n"
        "time.sleep(0.2)\n"
        "print('done')\n",
    )

    config = LegacyMonkeyAEEConfig(
        python_executable=sys.executable,
        script_path=str(script),
        working_dir=str(tmp_path),
        run_timeout_sec=10,
        progress_interval_sec=1,
        poll_interval_sec=0.2,
    )
    adapter = LegacyMonkeyAEEAdapter(config)
    logs = []
    ticks = []

    result = adapter.run(
        serial="SERIAL-001",
        log_dir=str(tmp_path / "logs"),
        on_log=logs.append,
        on_tick=ticks.append,
    )

    assert result.return_code == 0
    assert result.timed_out is False
    assert Path(result.log_path).exists()
    assert any("start" in line for line in logs)


def test_legacy_adapter_timeout(tmp_path: Path):
    script = tmp_path / "legacy_timeout.py"
    _write_script(
        script,
        "import time\n"
        "print('sleeping')\n"
        "time.sleep(3)\n",
    )

    config = LegacyMonkeyAEEConfig(
        python_executable=sys.executable,
        script_path=str(script),
        working_dir=str(tmp_path),
        run_timeout_sec=1,
        progress_interval_sec=1,
        poll_interval_sec=0.2,
    )
    adapter = LegacyMonkeyAEEAdapter(config)

    result = adapter.run(
        serial="SERIAL-002",
        log_dir=str(tmp_path / "logs"),
        on_log=lambda _: None,
        on_tick=None,
    )

    assert result.timed_out is True
    assert result.return_code != 0


def test_monkey_aee_tool_run_success(tmp_path: Path):
    script = tmp_path / "legacy_run.py"
    _write_script(
        script,
        "print('legacy run ok')\n",
    )

    tool = MonkeyAEEStabilityTest(
        adb_wrapper=DummyAdb(),
        run_id=321,
        log_dir=str(tmp_path / "run_logs"),
    )
    params = {
        "python_executable": sys.executable,
        "legacy_script_path": str(script),
        "legacy_working_dir": str(tmp_path),
        "run_timeout_sec": 10,
        "collect_aee_logs": False,
        "pack_artifact": True,
        "pass_serial_arg": False,
    }

    result = tool.run("SERIAL-003", params)

    assert result.status == "FINISHED"
    assert result.exit_code == 0
    assert result.artifact is not None
    assert str(result.artifact["storage_uri"]).startswith("file://")


def test_task_executor_registry_contains_monkey_aee():
    assert "MONKEY_AEE" in TaskExecutor._TEST_CLASS_REGISTRY

