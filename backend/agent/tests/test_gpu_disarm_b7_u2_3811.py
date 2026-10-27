"""B7 U2 finite acceptance: real script processes and Engine, fake device I/O."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.agent import pipeline_engine as engine_module
from backend.agent.contracts.device_return import ONBOARDED
from backend.agent.registry.script_registry import ScriptEntry

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "backend/agent/scripts"
TEMPLATE = ROOT / "backend/schemas/pipeline_templates/gpu.json"

# An executable adb substitute also exercises the script's decoding/return-code
# boundary. Unknown commands fail; the device never receives a real adb call.
FAKE_ADB = r'''
import json, os, pathlib, sys, time
p = pathlib.Path(os.environ["B7_FAKE_DEVICE"])
s = json.loads(p.read_text())
a = sys.argv[3:]
cmd = " ".join(a)
s["calls"].append(cmd)
def done(rc=0, out="", err=""):
    p.write_text(json.dumps(s))
    print(out, end="")
    print(err, file=sys.stderr, end="")
    sys.exit(rc)
if not s["online"]: done(1, err="device offline")
if a == ["get-state"]: done(out="device\n")
if a[0] == "pull":
    if s.get("collect_timeout"):
        p.write_text(json.dumps(s)); time.sleep(30)
    if s.get("collect_failure"): done(1, err="pull failed")
    pathlib.Path(a[2]).write_text("GPU_RUN_START test_id=001 rounds=1\nGPU_ROUND 1 rc=0\nGPU_RUN_END rc=0\n")
    done()
c = a[1] if len(a)>1 else ""
field = None
if c.startswith("pgrep "):
    field = "loop" if "[g]pu_stress_loop" in c else "instrument"
elif c.startswith("if [ -e "): field = "file"
elif c == "settings get global airplane_mode_on": field = "airplane"
elif c == "cmd wifi status": field = "wifi"
if field:
    fault = s.get("read_failure")
    if fault == field: done(1, err="Permission denied")
    if s.get("empty_read") == field: done()
    if field in ("loop", "instrument"): done(0 if s[field] else 1, "4321\n" if s[field] else "")
    if field == "file": done(out="PRESENT\n" if s[field] else "ABSENT\n")
    if field == "airplane": done(out=str(int(s[field]))+"\n")
    if field == "wifi":
        label = s.get("wifi_label", "Wi-Fi")
        done(out=label+(" is enabled\n" if s[field] else " is disabled\n"))
if c == "getprop sys.boot_completed": done(out=s["boot"]+"\n")
if c == "getprop ro.soc.manufacturer": done(out="MediaTek\n")
if c == "getprop ro.board.platform": done(out="mt9999\n")
if c == "getprop ro.product.model": done(out="B7_TEST\n")
if c == "id -u": done(out="0\n" if s["root"] else "2000\n")
if c.startswith("pm path "): done(0 if s["installed"] else 1, "package:/data/app/gpu/base.apk\n" if s["installed"] else "")
if c.startswith("sha256sum "): done(out="a"*64+"  /data/app/gpu/base.apk\n")
if c.startswith("ls -l "): done(out="-rw-rw---- 1 shell sdcard_rw 100 2026-10-27 test_log.txt\n")
if c.startswith("[ -e "): done(out="REMAINS\n" if s["file"] else "CLEAN\n")
if c.startswith("pkill "):
    key = "loop" if "[g]pu_stress_loop" in c else "instrument"
    if s.get("stuck") != key: s[key] = False
    done()
if c.startswith("am force-stop "): done()
if c.startswith("rm -f "):
    assert c.endswith("/sdcard/Auto/gpu_stress_loop.sh"), c
    if s.get("stuck") != "file": s["file"] = False
    done()
if c == "settings put global airplane_mode_on 0":
    if s.get("stuck") != "airplane": s["airplane"] = False
    done()
if c.startswith("am broadcast "): done()
if c == "svc wifi enable":
    if s.get("stuck") != "wifi": s["wifi"] = True
    done()
done(2, err="unexpected fake adb command: "+cmd)
'''


@pytest.fixture
def device(tmp_path, monkeypatch):
    path = tmp_path / "device.json"
    adb = tmp_path / "adb"
    adb.write_text(f"#!{sys.executable}\n" + FAKE_ADB)
    adb.chmod(0o755)
    monkeypatch.setenv("B7_FAKE_DEVICE", str(path))
    monkeypatch.setenv("STP_ADB_PATH", str(adb))
    monkeypatch.setenv("STP_DEVICE_SERIAL", "b7-test-only")
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path / "nfs"))
    state = dict(online=True, boot="1", root=True, installed=True,
                 loop=True, instrument=True, file=True, airplane=True, wifi=False, calls=[])

    def configure(**changes):
        state.update(changes)
        path.write_text(json.dumps(state))

    configure()
    return SimpleNamespace(path=path, adb=adb, configure=configure,
                           read=lambda: json.loads(path.read_text()))


def run_script(device, mode="disarm", *, script="gpu_disarm", **params):
    # The pre-change baseline has no probe: exercise its actual GPU teardown
    # entry, whose missing readbacks make every finite negative case red.
    entry = SCRIPTS / script / f"{script}.py"
    if not entry.exists():
        entry = SCRIPTS / "gpu_finish/gpu_finish.py"
    env = {**os.environ, "STP_STEP_PARAMS": json.dumps({
        "mode": mode, "wait_device_online_seconds": 1, **params})}
    result = subprocess.run([sys.executable, str(entry)], env=env, capture_output=True,
                            text=True, timeout=15)
    return result, json.loads(result.stdout)


@pytest.mark.parametrize("field", ["loop", "instrument", "file", "airplane", "wifi"])
def test_unrestored_footprint_cannot_be_clear(device, field):
    device.configure(stuck=field)
    _, out = run_script(device)
    assert out.get("readback") == "footprint"
    assert out["success"] is False


@pytest.mark.parametrize("field", ["loop", "instrument", "file", "airplane", "wifi"])
@pytest.mark.parametrize("fault", ["read_failure", "empty_read"])
def test_each_failed_or_empty_read_fails_closed(device, field, fault):
    device.configure(**{fault: field})
    _, out = run_script(device)
    assert out.get("readback") == "readback_failed"
    assert out["success"] is False


@pytest.mark.parametrize("mode", ["probe", "disarm"])
@pytest.mark.parametrize("changes", [{"online": False}, {"boot": "0"}])
def test_offline_and_unbooted_never_clear_or_mutate(device, mode, changes):
    device.configure(**changes)
    _, out = run_script(device, mode)
    assert out.get("readback") == "readback_failed"
    assert out["success"] is False
    assert all(c == "get-state" or c == "shell getprop sys.boot_completed"
               for c in device.read()["calls"])


def test_disarm_then_read_only_probe_preserves_tools_and_log(device):
    result, out = run_script(device)
    assert result.returncode == 0
    assert out.get("readback") == "clear"
    assert out["disarm_result"] == "executed_unverified"
    assert out["evidence"]["apk_sha256"] == "a" * 64
    first = device.read()["calls"]
    result, out = run_script(device, "probe")
    assert out["readback"] == "clear" and out["success"] is False
    calls = device.read()["calls"]
    assert not any("pull" in c or "test_log.txt" in c or "uninstall" in c for c in calls)
    assert not any(x in c for c in calls[len(first):]
                   for x in ("pkill", "force-stop", "rm -f", "settings put", "broadcast", "svc wifi"))
    for field in ("boot_completed", "permission_model", "script_name", "script_version", "package_sha256", "kind"):
        assert field in out["evidence"]


def test_file_only_residue_is_visible_to_probe(device):
    device.configure(loop=False, instrument=False, airplane=False, wifi=True)
    _, out = run_script(device, "probe")
    assert out.get("readback") == "footprint"


def test_observed_android_wifi_spelling_is_readable(device):
    device.configure(wifi_label="Wifi")
    _, out = run_script(device)
    assert out["readback"] == "clear"


@pytest.mark.parametrize("root,installed", [(False, True), (True, False), (False, False)])
def test_nonroot_and_missing_tool_still_require_all_four_readbacks(device, root, installed):
    device.configure(root=root, installed=installed)
    _, out = run_script(device)
    assert out.get("readback") == "clear"
    assert out["evidence"]["permission_model"] == ("root" if root else "non-root")
    assert out["evidence"]["apk_sha256"] == ("a" * 64 if installed else None)


def test_template_pins_new_pair_and_registered_budget():
    teardown = json.loads(TEMPLATE.read_text())["lifecycle"]["teardown"]
    assert [(s["action"], s["version"]) for s in teardown] == [
        ("script:gpu_disarm", "1.0.0"), ("script:gpu_finish", "1.1.0")]
    minimum = ONBOARDED["gpu"]["min_wall_clock_seconds"]["gpu_disarm"]
    assert callable(minimum)
    assert teardown[0]["timeout_seconds"] >= minimum(teardown[0]["params"])


@pytest.fixture
def engine(device, tmp_path, monkeypatch):
    # Real package resolver, script subprocess, permit, step/retry, recorder and
    # lifecycle. Only init checks/setup/patrol and external reporting are stubs.
    manifest = json.loads((ROOT / "tool_manifest.json").read_text())["tools"]
    spec = importlib.util.spec_from_file_location("b7_u2_packer", ROOT / "tools/dev/package_tool_asset.py")
    packer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packer)
    entries = {}
    for name in ("gpu_disarm", "gpu_finish"):
        if not (SCRIPTS / name).exists():
            continue
        version = "1.0.0" if name == "gpu_disarm" else "1.1.0"
        archive = tmp_path / "packages" / name / f"{version}.tar.gz"
        archive.parent.mkdir(parents=True)
        packed = packer.build_deterministic_tar_gz(SCRIPTS / name, archive)
        registered = next((v for v in manifest.get(name, {}).get("versions", []) if v["version"] == version), None)
        if registered:
            assert registered["package_sha256"] == packed["package_sha256"]
        entries[name] = ScriptEntry(1, name, version, "python", f"{name}.py", "", packed["package_sha256"])
    monkeypatch.setenv("STP_PACKAGES_ROOT", str(tmp_path / "packages"))
    monkeypatch.setenv("STP_TOOLS_CACHE_ROOT", str(tmp_path / "cache"))
    registry = SimpleNamespace(resolve=lambda name, _version: entries[name])
    engine = engine_module.PipelineEngine(SimpleNamespace(adb_path=str(device.adb)), "b7-test-only", 1,
        log_dir=str(tmp_path / "log"), script_registry=registry, nfs_root="", device_id=1)
    monkeypatch.setattr(engine, "_report_step_trace_mq", MagicMock())
    monkeypatch.setattr(engine, "_report_job_status_mq", MagicMock())
    monkeypatch.setattr(engine, "_update_execution_state", lambda *_: None)
    action = engine._run_script_action
    launched = []

    def run(ctx, step):
        name = step["action"].split(":", 1)[1]
        launched.append(name)
        if name == "gpu_setup":
            return engine_module.StepResult(success=True, exit_code=0)
        return action(ctx, step)

    monkeypatch.setattr(engine, "_run_script_action", run)
    pipeline = deepcopy(json.loads(TEMPLATE.read_text()))
    pipeline["lifecycle"]["init"] = [{"step_id": "setup", "action": "script:gpu_setup", "version": "1.2.4"}]
    for step in pipeline["lifecycle"]["teardown"]:
        step["retry"] = 0
    return engine, pipeline, launched


@pytest.mark.parametrize("timeout", [False, True])
def test_real_engine_collection_failure_cannot_change_disarm(engine, device, timeout, monkeypatch):
    engine, pipeline, launched = engine
    device.configure(**{"collect_timeout" if timeout else "collect_failure": True})
    if timeout:
        pipeline["lifecycle"]["teardown"][-1]["timeout_seconds"] = 1
    monkeypatch.setattr(engine, "_run_patrol_loop", lambda *a, **k: ("completed", ""))
    result = engine.execute(pipeline)
    assert "gpu_disarm" in launched and "gpu_finish" in launched
    assert result.metadata["disarm_results"]["gpu"]["result"] == "executed_unverified"
    assert result.metadata["disarm_results"]["gpu"]["evidence"]["readback"] == "clear"
    assert result.metadata["teardown_status"] == "DEGRADED"
    traces = engine._report_step_trace_mq.call_args_list
    finish = [c for c in traces if c.args[0] == "gpu_finish" and c.args[2] == "FAILED"]
    assert finish and finish[-1].kwargs["exit_code"] == (124 if timeout else 1)


@pytest.mark.parametrize("reason", ["manual_exit", "abort", "platform_termination"])
def test_real_engine_interrupt_runs_only_disarm(engine, reason, monkeypatch):
    engine, pipeline, launched = engine

    def patrol(*args, **kwargs):
        if reason != "manual_exit":
            engine.cancel()
        return ("manual_exit" if reason == "manual_exit" else "abort", "")

    monkeypatch.setattr(engine, "_run_patrol_loop", patrol)
    result = engine.execute(pipeline)
    assert launched == ["gpu_setup", "gpu_disarm"]
    assert result.metadata["disarm_results"]["gpu"]["result"] == "executed_unverified"
