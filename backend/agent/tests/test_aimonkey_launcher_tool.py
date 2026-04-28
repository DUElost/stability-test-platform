from pathlib import Path

from backend.agent.actions.tool_actions import run_tool_script
from backend.agent.pipeline_engine import StepContext


class _Logger:
    def __init__(self):
        self.lines = []

    def info(self, message):
        self.lines.append(str(message))

    def error(self, message):
        self.lines.append(str(message))


class _Context:
    def __init__(self, serial, params):
        self.serial = serial
        self.params = params
        self.logger = _Logger()


def test_aimonkey_launcher_invokes_monkeytest_for_current_serial(tmp_path: Path):
    from backend.agent.tools.aimonkey_launcher import AIMonkeyLauncherAction

    launcher_dir = tmp_path / "AIMonkeyTest_20260317"
    launcher_dir.mkdir()
    marker = launcher_dir / "called.txt"
    (launcher_dir / "MonkeyTest.py").write_text(
        "import os\n"
        "class MonkeyTest:\n"
        "    def __init__(self, need_nohup, to_push_res, is_sleep, is_blacklist):\n"
        "        self.values = [need_nohup, to_push_res, is_sleep, is_blacklist]\n"
        "    def startTest(self, serial):\n"
        f"        open({str(marker)!r}, 'w', encoding='utf-8').write(serial + '|' + os.getcwd() + '|' + repr(self.values))\n",
        encoding="utf-8",
    )

    ctx = _Context(
        "DEVICE-001",
        {
            "launcher_dir": str(launcher_dir),
            "need_nohup": True,
            "push_resources": True,
            "sleep_mode": False,
            "blacklist": True,
        },
    )

    result = AIMonkeyLauncherAction().run(ctx)

    assert result.success is True
    assert marker.read_text(encoding="utf-8") == (
        f"DEVICE-001|{launcher_dir}|[True, True, False, True]"
    )


def test_aimonkey_launcher_reports_missing_launcher(tmp_path: Path):
    from backend.agent.tools.aimonkey_launcher import AIMonkeyLauncherAction

    ctx = _Context("DEVICE-001", {"launcher_dir": str(tmp_path / "missing")})

    result = AIMonkeyLauncherAction().run(ctx)

    assert result.success is False
    assert "MonkeyTest.py not found" in result.error_message


def test_aimonkey_launcher_can_be_loaded_by_run_tool_script(tmp_path: Path):
    launcher_dir = tmp_path / "AIMonkeyTest_20260317"
    launcher_dir.mkdir()
    marker = launcher_dir / "called.txt"
    (launcher_dir / "MonkeyTest.py").write_text(
        "class MonkeyTest:\n"
        "    def __init__(self, need_nohup, to_push_res, is_sleep, is_blacklist):\n"
        "        self.values = [need_nohup, to_push_res, is_sleep, is_blacklist]\n"
        "    def startTest(self, serial):\n"
        f"        open({str(marker)!r}, 'w', encoding='utf-8').write(serial + '|' + repr(self.values))\n",
        encoding="utf-8",
    )
    action_path = Path(__file__).resolve().parents[1] / "tools" / "aimonkey_launcher.py"
    ctx = StepContext(
        adb=None,
        serial="DEVICE-001",
        params={
            "script_path": str(action_path),
            "script_class": "AIMonkeyLauncherAction",
            "default_params": {
                "launcher_dir": str(launcher_dir),
                "need_nohup": True,
                "push_resources": True,
                "sleep_mode": False,
                "blacklist": True,
            },
        },
        run_id=123,
        step_id=1,
        logger=_Logger(),
    )

    result = run_tool_script(ctx)

    assert result.success is True
    assert marker.read_text(encoding="utf-8") == "DEVICE-001|[True, True, False, True]"
