"""PipelineEngine script action tests."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from backend.agent.pipeline_engine import PipelineEngine


class FakeScriptRegistry:
    def __init__(self, path: str, script_type: str = "python"):
        self.path = path
        self.script_type = script_type

    def resolve(self, name: str, version: str):
        assert name
        assert version
        return SimpleNamespace(
            script_id=1,
            name=name,
            version=version,
            script_type=self.script_type,
            nfs_path=self.path,
            content_sha256="c" * 64,
        )


class FakeMQ:
    connected = True

    def __init__(self):
        self.traces = []

    def send_step_trace(self, **kwargs):
        self.traces.append(kwargs)


def _write_script(path, source: str) -> str:
    path.write_text(source, encoding="utf-8")
    return str(path)


def test_pipeline_engine_executes_python_script_action(tmp_path):
    script = _write_script(
        tmp_path / "echo_params.py",
        """
import json
import os
params = json.loads(os.environ["STP_STEP_PARAMS"])
print(json.dumps({"metrics": {"value": params["value"], "serial": os.environ["STP_DEVICE_SERIAL"]}}))
""".strip(),
    )
    engine = PipelineEngine(
        adb=SimpleNamespace(adb_path="adb"),
        serial="SERIAL001",
        run_id=42,
        script_registry=FakeScriptRegistry(script),
    )

    result = engine.execute({
        "stages": {
            "prepare": [
                {
                    "step_id": "echo",
                    "action": "script:echo_params",
                    "version": "1.0.0",
                    "params": {"value": 7},
                    "timeout_seconds": 5,
                }
            ]
        }
    })

    assert result.success is True
    assert engine._shared["echo"] == {"value": 7, "serial": "SERIAL001"}


def test_pipeline_engine_reports_script_stdout_and_stderr(tmp_path):
    script = _write_script(
        tmp_path / "stdout_stderr.py",
        """
import sys
print("hello stdout")
print("debug stderr", file=sys.stderr)
""".strip(),
    )
    mq = FakeMQ()
    engine = PipelineEngine(
        adb=SimpleNamespace(adb_path="adb"),
        serial="SERIAL001",
        run_id=42,
        mq_producer=mq,
        script_registry=FakeScriptRegistry(script),
    )

    result = engine._execute_step_stages(
        "execute",
        {
            "step_id": "stdout_stderr",
            "action": "script:stdout_stderr",
            "version": "1.0.0",
            "params": {},
            "timeout_seconds": 5,
        },
    )

    assert result.success is True
    assert mq.traces[-1]["status"] == "COMPLETED"
    assert "hello stdout" in mq.traces[-1]["output"]
    assert "debug stderr" in mq.traces[-1]["output"]


def test_pipeline_engine_reports_skipped_script_without_retry(tmp_path):
    script = _write_script(
        tmp_path / "skip.py",
        'import json; print(json.dumps({"skipped": True, "skip_reason": "already done"}))',
    )
    mq = FakeMQ()
    engine = PipelineEngine(
        adb=SimpleNamespace(adb_path="adb"),
        serial="SERIAL001",
        run_id=42,
        mq_producer=mq,
        script_registry=FakeScriptRegistry(script),
    )

    result = engine._execute_step_stages(
        "prepare",
        {
            "step_id": "skip",
            "action": "script:skip",
            "version": "1.0.0",
            "params": {},
            "timeout_seconds": 5,
            "retry": 2,
        },
    )

    assert result.success is True
    assert result.skipped is True
    assert mq.traces[-1]["status"] == "SKIPPED"
    assert mq.traces[-1]["output"] == "already done"


def test_pipeline_engine_skips_disabled_step_without_resolving_action():
    mq = FakeMQ()
    engine = PipelineEngine(
        adb=SimpleNamespace(adb_path="adb"),
        serial="SERIAL001",
        run_id=42,
        mq_producer=mq,
    )

    result = engine._execute_step_stages(
        "prepare",
        {
            "step_id": "disabled",
            "action": "builtin:missing_action",
            "params": {},
            "timeout_seconds": 5,
            "enabled": False,
        },
    )

    assert result.success is True
    assert result.skipped is True
    assert result.skip_reason == "step disabled"
    assert len(mq.traces) == 1
    assert mq.traces[0]["step_id"] == "disabled"
    assert mq.traces[0]["status"] == "SKIPPED"
    assert mq.traces[0]["output"] == "step disabled"


def test_pipeline_engine_script_timeout_returns_124(tmp_path):
    script = _write_script(
        tmp_path / "sleep.py",
        "import time\ntime.sleep(5)\n",
    )
    engine = PipelineEngine(
        adb=SimpleNamespace(adb_path=sys.executable),
        serial="SERIAL001",
        run_id=42,
        script_registry=FakeScriptRegistry(script),
    )

    result = engine._execute_step_stages(
        "prepare",
        {
            "step_id": "sleep",
            "action": "script:sleep",
            "version": "1.0.0",
            "params": {},
            "timeout_seconds": 1,
        },
    )

    assert result.success is False
    assert result.exit_code == 124
