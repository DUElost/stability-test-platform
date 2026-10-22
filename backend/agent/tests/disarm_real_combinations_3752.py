"""#3723 §3.8.5: real 1.0.8 script output, with device I/O stubbed.

Values come from #3747/6312583571 and #3730/6320738260, confirmed
by the Planner in #3730/6331107188. No device calls or host signals.
"""
import json
from types import SimpleNamespace

from backend.agent.tests.test_powercycle_disarm_u5_3730 import _load
from backend.agent.tests.test_device_return_u1_3726 import _bare_engine

APK = {"MLD_LX2": "f275e1f233e3ac7485469406b3e35a2cf58be0fec0d7e9d60184f180db8a2929",
       "MLD_LX3": "582964aff036cb8785248cd3a149cbf73f13f88bddc51a80c8605b458399e977"}
PACKAGE = "98cc16f82fa6c56fe24f37151958f4ad3efd2f5983b1bba36f9340d95cd4a557"
COMBINATIONS = [(m, p) for m in APK for p in ("root", "non-root")]
CHANGES = [None, "platform", "model", "permission_model", "autotesttool_apk_sha256", "package_sha256", "boot_completed"]
STEP = {"step_id": "disarm", "action": "script:powercycle_disarm", "version": "1.0.8", "timeout_seconds": 660}


def aggregate_script_output(monkeypatch, model, permission, changed=None):
    module = _load("powercycle_u8_combinations", "powercycle_disarm/powercycle_disarm.py")
    monkeypatch.setattr(module, "device_serial", lambda: "test-only")
    monkeypatch.setattr(module, "device_online", lambda: True)
    monkeypatch.setattr(module, "_wait_device_online", lambda *a, **k: True)
    monkeypatch.setattr(module, "_detect_platform", lambda: "MTK")
    monkeypatch.setattr(module, "_detect_model", lambda: model.replace("_", "-"))
    monkeypatch.setattr(module, "adb_shell", lambda *a, **k: "1")
    monkeypatch.setattr(module, "stop_task", lambda **k: {})
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module, "_device_identity_evidence", lambda: {
        "permission_model": permission, "autotesttool_apk_sha256": APK[model]})
    monkeypatch.setattr(module, "_read_prefs_evidence", lambda: {
        "readback": "clear", "running": False, "auto_resume": False,
        "evidence": {"kind": "ok", "autotesttool_installed": True}})
    output = module._run({"mode": "disarm"})
    assert output["success"] is True and output["disarm_result"] == "executed_unverified"
    assert output["readback"] == "clear"
    if changed in ("permission_model", "autotesttool_apk_sha256", "boot_completed"):
        output["evidence"][changed] = "unknown" if changed == "permission_model" else "different"
    engine = _bare_engine()
    sha = "c"*64 if changed == "package_sha256" else PACKAGE
    engine._script_registry = SimpleNamespace(resolve=lambda *a: SimpleNamespace(package_sha256=sha))
    platform = "OTHER" if changed == "platform" else "MTK"
    observed_model = "MLD-LX2" if changed == "model" else model
    monkeypatch.setattr(engine, "_disarm_device_identity", lambda: (platform, observed_model))
    engine._record_disarm_for_step(STEP, started=True, success=True, exit_code=0, output=json.dumps(output))
    return engine._disarm_results["power-cycle"], (platform, observed_model)
