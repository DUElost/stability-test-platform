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
        "lifecycle": {
            "init": [
                {
                    "step_id": "echo",
                    "action": "script:echo_params",
                    "version": "1.0.0",
                    "params": {"value": 7},
                    "timeout_seconds": 5,
                }
            ],
            "teardown": [],
        }
    })

    assert result.success is True
    assert engine._shared["echo"] == {"value": 7, "serial": "SERIAL001"}


def test_pipeline_engine_rejects_top_level_stages_format():
    engine = PipelineEngine(
        adb=SimpleNamespace(adb_path="adb"),
        serial="SERIAL001",
        run_id=42,
    )

    result = engine.execute({
        "stages": {
            "execute": [
                {
                    "step_id": "echo",
                    "action": "script:echo_params",
                    "version": "1.0.0",
                    "params": {},
                    "timeout_seconds": 5,
                }
            ]
        }
    })

    assert result.success is False
    assert "lifecycle" in result.error_message


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

    result = engine._execute_step(
        "init",
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

    result = engine._execute_step(
        "init",
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

    result = engine._execute_step(
        "init",
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

    result = engine._execute_step(
        "init",
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


def test_pipeline_engine_script_stall_returns_125(tmp_path):
    """#141: 停滞超时用独立退出码 125，与 wall_clock 的 124 可区分。"""
    script = _write_script(
        tmp_path / "silent_sleep.py",
        "import time\ntime.sleep(30)\n",
    )
    mq = FakeMQ()
    engine = PipelineEngine(
        adb=SimpleNamespace(adb_path=sys.executable),
        serial="SERIAL001",
        run_id=42,
        mq_producer=mq,
        script_registry=FakeScriptRegistry(script),
    )

    result = engine._execute_step(
        "init",
        {
            "step_id": "silent_sleep",
            "action": "script:silent_sleep",
            "version": "1.0.0",
            "params": {},
            "timeout_seconds": 30,
            "stall_seconds": 1,
        },
    )

    assert result.success is False
    assert result.exit_code == 125
    assert "stalled" in result.error_message
    assert result.metadata == {"timeout_kind": "stall"}
    failed_traces = [
        t for t in mq.traces
        if t.get("event_type") == "FAILED"
    ]
    assert failed_traces
    assert failed_traces[-1]["exit_code"] == 125
    assert failed_traces[-1]["metadata"] == {"timeout_kind": "stall"}


def test_pipeline_engine_rejects_windows_batch_script_action():
    engine = PipelineEngine(
        adb=SimpleNamespace(adb_path="adb"),
        serial="SERIAL001",
        run_id=42,
        script_registry=FakeScriptRegistry("/tmp/legacy_windows.bat", script_type="bat"),
    )

    result = engine._execute_step(
        "init",
        {
            "step_id": "legacy_windows",
            "action": "script:legacy_windows",
            "version": "1.0.0",
            "params": {},
            "timeout_seconds": 5,
        },
    )

    assert result.success is False
    assert result.exit_code == 1
    assert "Unsupported script_type: bat" in result.error_message


# ---- ADR-0051 Phase 2b：按包身份执行（DB 权威 → tools_cache） ----

def _make_site_package(tmp_path, name: str, version: str, body: str) -> tuple[str, dict]:
    """写 packages/{name}/{version}.tar.gz + manifest.json；返回 (sha, agent env 增量)。"""
    import hashlib
    import io
    import json
    import tarfile

    packages_root = tmp_path / "packages"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = body.encode("utf-8")
        info = tarfile.TarInfo(f"{name}.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    blob = buf.getvalue()
    sha = hashlib.sha256(blob).hexdigest()
    (packages_root / name).mkdir(parents=True)
    (packages_root / name / f"{version}.tar.gz").write_bytes(blob)
    (packages_root / "manifest.json").write_text(json.dumps({"schema_version": 1, "tools": {name: {"versions": [
        {"version": version, "package_sha256": sha, "artifact": f"packages/{name}/{version}.tar.gz",
         "python": None, "script": f"{name}.py", "retired": False}]}}}), encoding="utf-8")
    return sha, {"STP_PACKAGES_ROOT": str(packages_root), "STP_TOOLS_CACHE_ROOT": str(tmp_path / "tools_cache")}


class PackagedRegistry(FakeScriptRegistry):
    def __init__(self, path: str, package_sha256):
        super().__init__(path)
        self.package_sha256 = package_sha256

    def resolve(self, name: str, version: str):
        entry = super().resolve(name, version)
        entry.package_sha256 = self.package_sha256
        return entry


def _package_pipeline():
    return {"lifecycle": {"init": [{"step_id": "who", "action": "script:who", "version": "1.0.0",
                                     "params": {}, "timeout_seconds": 5}], "teardown": []}}


def test_script_executes_from_package_when_switch_on(tmp_path, monkeypatch):
    """树上的入口与包内入口内容不同：开关 on 时跑的是包内那份，cwd = 包根。"""
    tree = _write_script(tmp_path / "who.py", "print('{\"metrics\": {\"src\": \"tree\"}}')")
    sha, env = _make_site_package(
        tmp_path, "who", "1.0.0",
        "import os, json; print(json.dumps({'metrics': {'src': 'package', 'cwd': os.getcwd(), "
        "'source_env': os.environ.get('STP_SCRIPT_SOURCE'), 'pp': os.environ.get('PYTHONPATH', '')}}))\n",
    )
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("STP_SCRIPT_PACKAGES", "on")
    engine = PipelineEngine(adb=SimpleNamespace(adb_path="adb"), serial="S", run_id=1,
                            script_registry=PackagedRegistry(tree, sha))
    assert engine.execute(_package_pipeline()).success is True
    m = engine._shared["who"]
    assert m["src"] == "package" and m["source_env"] == "package"
    assert m["cwd"] == str(tmp_path / "tools_cache" / "who" / "1.0.0")
    # PYTHONPATH 注入的是 agent 目录本身，不再由 nfs_path 深度推导
    from backend.agent import pipeline_engine as pe
    assert m["pp"].split(":")[0] == str(__import__("pathlib").Path(pe.__file__).resolve().parent)


def test_script_executes_from_tree_when_switch_off(tmp_path, monkeypatch):
    tree = _write_script(tmp_path / "who.py", "import os, json; print(json.dumps({'metrics': {'src': 'tree', 'source_env': os.environ.get('STP_SCRIPT_SOURCE')}}))")
    sha, env = _make_site_package(tmp_path, "who", "1.0.0", "print('{\"metrics\": {\"src\": \"package\"}}')\n")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("STP_SCRIPT_PACKAGES", raising=False)
    engine = PipelineEngine(adb=SimpleNamespace(adb_path="adb"), serial="S", run_id=1,
                            script_registry=PackagedRegistry(tree, sha))
    assert engine.execute(_package_pipeline()).success is True
    assert engine._shared["who"] == {"src": "tree", "source_env": "tree"}
    assert not (tmp_path / "tools_cache").exists()


def test_script_falls_back_to_tree_when_package_missing(tmp_path, monkeypatch):
    tree = _write_script(tmp_path / "who.py", "print('{\"metrics\": {\"src\": \"tree\"}}')")
    _, env = _make_site_package(tmp_path, "who", "1.0.0", "print('x')\n")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("STP_SCRIPT_PACKAGES", "on")
    engine = PipelineEngine(adb=SimpleNamespace(adb_path="adb"), serial="S", run_id=1,
                            script_registry=PackagedRegistry(tree, "0" * 64))
    assert engine.execute(_package_pipeline()).success is True
    assert engine._shared["who"] == {"src": "tree"}


def test_strict_mode_fails_step_with_exit_2_when_package_missing(tmp_path, monkeypatch):
    tree = _write_script(tmp_path / "who.py", "print('{\"metrics\": {}}')")
    _, env = _make_site_package(tmp_path, "who", "1.0.0", "print('x')\n")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("STP_SCRIPT_PACKAGES", "strict")
    engine = PipelineEngine(adb=SimpleNamespace(adb_path="adb"), serial="S", run_id=1,
                            script_registry=PackagedRegistry(tree, "0" * 64))
    result = engine._execute_step("init", _package_pipeline()["lifecycle"]["init"][0])
    assert result.success is False and result.exit_code == 2
    assert "script package unavailable" in (result.error_message or "")
    assert "package_unavailable" in (result.error_message or "")
