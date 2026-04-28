from pathlib import Path

from backend.agent.actions.tool_actions import run_tool_script
from backend.agent.pipeline_engine import StepContext


class _Logger:
    def info(self, message):
        pass

    def error(self, message):
        pass


def test_run_tool_script_supports_pipeline_action_classes(tmp_path: Path):
    script_path = tmp_path / "direct_action.py"
    script_path.write_text(
        "from backend.agent.pipeline_engine import PipelineAction, StepResult\n"
        "class DirectAction(PipelineAction):\n"
        "    def run(self, ctx):\n"
        "        return StepResult(success=True, metrics={'serial': ctx.serial, 'value': ctx.params['value']})\n",
        encoding="utf-8",
    )
    ctx = StepContext(
        adb=None,
        serial="DEVICE-001",
        params={
            "script_path": str(script_path),
            "script_class": "DirectAction",
            "default_params": {"value": 7},
        },
        run_id=123,
        step_id=1,
        logger=_Logger(),
    )

    result = run_tool_script(ctx)

    assert result.success is True
    assert result.metrics == {"serial": "DEVICE-001", "value": 7}


def test_run_tool_script_supports_pipeline_action_classes_from_alternate_imports(tmp_path: Path, monkeypatch):
    script_path = tmp_path / "direct_action.py"
    script_path.write_text(
        "from backend.agent.pipeline_engine import StepResult\n"
        "class PipelineAction:\n"
        "    pass\n"
        "class DirectAction(PipelineAction):\n"
        "    def run(self, ctx):\n"
        "        return StepResult(success=True, metrics={'serial': ctx.serial, 'value': ctx.params['value']})\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOST_ID", "auto")
    ctx = StepContext(
        adb=None,
        serial="DEVICE-001",
        params={
            "script_path": str(script_path),
            "script_class": "DirectAction",
            "default_params": {"value": 7},
        },
        run_id=123,
        step_id=1,
        logger=_Logger(),
    )

    result = run_tool_script(ctx)

    assert result.success is True
    assert result.metrics == {"serial": "DEVICE-001", "value": 7}


def test_run_tool_script_ignores_non_numeric_host_id_for_legacy_tools(tmp_path: Path, monkeypatch):
    script_path = tmp_path / "legacy_tool.py"
    script_path.write_text(
        "class LegacyTool:\n"
        "    def __init__(self, adb_wrapper, api_url, run_id, host_id, device_serial, log_dir):\n"
        "        self.host_id = host_id\n"
        "    def run(self, serial, params):\n"
        "        return self.host_id == 0 and serial == 'DEVICE-001'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOST_ID", "auto")
    ctx = StepContext(
        adb=None,
        serial="DEVICE-001",
        params={
            "script_path": str(script_path),
            "script_class": "LegacyTool",
        },
        run_id=123,
        step_id=1,
        logger=_Logger(),
    )

    result = run_tool_script(ctx)

    assert result.success is True
