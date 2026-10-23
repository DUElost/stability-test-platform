"""Explicit test-only D2.5 evidence for existing legitimate-confirmation regressions."""
from backend.agent.contracts.device_return import ONBOARDED
from backend.models.host import Device
from backend.models.job import JobInstance
from backend.services.device_return import manifest_package_sha


def trust_test_probe(db, monkeypatch, device_id, job_id=None):
    combo = {
        "platform": "TEST", "model": "TEST_MODEL", "permission_model": "root",
        "autotesttool_apk_sha256": "a"*64, "script_name": "powercycle_disarm", "script_version": "1.0.8",
        "package_sha256": manifest_package_sha("powercycle_disarm", "1.0.8"), "report_ref": "test-only://u8-regression",
    }
    monkeypatch.setitem(ONBOARDED, "power-cycle", {**ONBOARDED["power-cycle"], "verified_device_classes": [combo]})
    device = db.get(Device, device_id)
    device.platform, device.model = "TEST", "TEST_MODEL"
    if job_id is not None:
        job = db.get(JobInstance, job_id)
        job.pipeline_def = {**job.pipeline_def, "lifecycle": {**job.pipeline_def.get("lifecycle", {}),
            "teardown": [{"step_id": "u8-test-disarm", "action": "script:powercycle_disarm", "version": "1.0.8", "timeout_seconds": 660}]}}
    db.commit()
    return {"result": "confirmed", "step_id": "u8-test-disarm", "exit_code": 0,
            "evidence": {**combo, "mode": "disarm", "readback": "clear", "kind": "ok", "success": True, "boot_completed": "1"}}
