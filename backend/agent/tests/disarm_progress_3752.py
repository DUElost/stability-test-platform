"""Frozen 1.0.8 progress through the real Engine/pump, without device I/O."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
from types import ModuleType, SimpleNamespace

from backend.agent import pipeline_engine as module
from backend.agent.operation_scheduler import OperationScheduler

STEP = {"step_id": "disarm", "action": "script:powercycle_disarm", "version": "1.0.8",
        "timeout_seconds": 660}


def frozen_progress_lines(tmp_path):
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("u8_progress_packer", root / "tools/dev/package_tool_asset.py")
    assert spec and spec.loader
    packer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packer)
    artifact = tmp_path / "disarm-1.0.8.tar.gz"
    package = packer.build_deterministic_tar_gz(root / "backend/agent/scripts/powercycle_disarm", artifact)
    manifest = json.loads((root / "tool_manifest.json").read_text())
    registered = next(v for v in manifest["tools"]["powercycle_disarm"]["versions"] if v["version"] == "1.0.8")
    assert package["package_sha256"] == registered["package_sha256"] == "98cc16f82fa6c56fe24f37151958f4ad3efd2f5983b1bba36f9340d95cd4a557"
    with tarfile.open(artifact) as archive:
        source = archive.extractfile("_lib.py").read()
    lib = ModuleType("frozen_disarm_progress")
    exec(compile(source, "powercycle_disarm@1.0.8/_lib.py", "exec"), lib.__dict__)
    captured = io.StringIO()
    lib.sys = SimpleNamespace(stderr=captured)
    for seq in (1, 1, 2):
        lib.progress_stamp({"phase": "wait_device_online", "seq": seq})
    lib.enable_exec_progress()
    for _ in range(7):
        lib.exec_progress_tick()
    lib.progress_stamp({"phase": "wait_device_online", "seq": 2})
    lib.progress_stamp({"phase": "wait_device_online", "seq": 1})
    lib.progress_stamp({"phase": "wait_device_online", "seq": 3})
    return captured.getvalue().splitlines(keepends=True)


def real_progress_engine(monkeypatch, tmp_path):
    published = []
    coordinator = SimpleNamespace(disarm_protocol=1, guard_for_job=lambda _: None,
                                  publish_disarm_evidence=lambda _, value: published.append(dict(value)))
    entry = SimpleNamespace(script_type="python", package_sha256="98cc16f82fa6c56fe24f37151958f4ad3efd2f5983b1bba36f9340d95cd4a557")
    engine = module.PipelineEngine(SimpleNamespace(adb_path="test-only"), "test-only", 1,
        log_dir=str(tmp_path), script_registry=SimpleNamespace(resolve=lambda *a: entry),
        nfs_root="", operation_scheduler=OperationScheduler(max_concurrent=1), device_id=1)
    engine._coordinator = coordinator
    engine._footprint_families = {"power-cycle"}
    engine._begin_disarm_tail([STEP], disarm_only=False)
    engine._publish_disarm_phase(STEP, "execute")
    monkeypatch.setattr(engine, "_update_execution_state", lambda *_: None)
    monkeypatch.setattr(module, "resolve_script_path", lambda _: SimpleNamespace(path="test-only.py", cwd=str(tmp_path), source="package"))
    monkeypatch.setattr(module, "inject_required_tools", lambda *a: None)
    monkeypatch.setattr(module, "_remember_process_group", lambda _: None)
    monkeypatch.setattr(module, "_popen_isolation_kwargs", lambda: {})
    monkeypatch.setattr(module, "_IS_WINDOWS", True)  # StringIO, no host pipe or signal
    monkeypatch.setattr(module, "_terminate_process_tree", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected termination")))
    ctx = module.StepContext(adb=None, serial="test-only", params={}, run_id=1, step_id=1,
                             logger=None, phase="teardown")

    def run(lines):
        process = SimpleNamespace(pid=99999999, returncode=0, stdout=io.StringIO('{"success":true}'),
                                  stderr=io.StringIO("".join(lines)), poll=lambda: 0, wait=lambda **_: 0)
        monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: process)
        result = engine._run_script_action(ctx, STEP)
        assert result.success, result.error_message
        return dict(published[-1])
    return run, published
