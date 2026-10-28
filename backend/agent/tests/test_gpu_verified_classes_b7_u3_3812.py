"""#3809 v1.5 §4 U3: approved registrations and bounded negative cases."""

import pytest

from backend.agent.contracts import device_return as contract


APK_SHA256 = "47a059398943e8dc0943269c99a30264f70c7175ae943a69c48ec733ea84e8c5"
PACKAGE_SHA256 = "c464779d695402473bf5afb173bc743d68eac307a680c46bfec2ebbf4299ead7"
REPORT_REFS = [
    "https://github.com/DUElost/stability-test-platform/issues/3811#issuecomment-6441554440",
    "docs/notes/bug-fix/2026-10-27-b7-u2-gpu-disarm-3811-evidence.json",
    "docs/notes/bug-fix/2026-10-27-b7-u2-gpu-disarm-3811.md",
    "https://github.com/DUElost/stability-test-platform/issues/3809",
]
APPROVED = [
    {
        "platform": "MTK", "model": model, "permission_model": permission,
        "apk_sha256": APK_SHA256,
        "script_name": "gpu_disarm", "script_version": "1.0.0",
        "package_sha256": PACKAGE_SHA256, "report_ref": REPORT_REFS,
    }
    for model in ("MLD_LX2", "MLD_LX3", "MOD_LX3")
    for permission in ("root", "non-root")
]


def _policy(observed, *, onboarded=None):
    evidence = {
        **observed, "mode": "disarm", "kind": "ok", "readback": "clear",
        "success": True, "boot_completed": "1",
    }
    return contract.probe_result_policy(
        "gpu", {"exit_code": 0, "evidence": evidence},
        platform=observed["platform"], model=observed["model"],
        script_name=observed["script_name"], script_version=observed["script_version"],
        package_sha256=observed["package_sha256"], token_valid=True,
        onboarded=onboarded,
    )


def test_gpu_registered_fields_exactly_match_planner_approved_six():
    assert contract.ONBOARDED["gpu"]["verified_device_classes"] == APPROVED


@pytest.mark.parametrize("combination", APPROVED)
def test_gpu_approved_combination_is_confirmed(combination):
    result = _policy(combination)
    assert result["result"] == "confirmed"
    assert result["trusted"] is True
    assert result["reason"] == ""
    assert result["report_ref"] == REPORT_REFS


@pytest.mark.parametrize("combination", APPROVED)
@pytest.mark.parametrize("field,value", [
    ("apk_sha256", "0" * 64),
    ("package_sha256", "0" * 64),
    ("model", "UNREGISTERED_MODEL"),
])
def test_gpu_changed_identity_is_unverified_in_full_registry(combination, field, value):
    # A changed package identity reaches both caller and evidence; this tests
    # combination admission rather than the separate package_mismatch policy.
    result = _policy({**combination, field: value})
    assert result["result"] == "executed_unverified"
    assert result["trusted"] is False
    assert result["reason"] == "combination_unverified"


@pytest.mark.parametrize("combination", APPROVED)
def test_gpu_permission_mismatch_is_unverified_in_single_entry_registry(combination):
    opposite = "non-root" if combination["permission_model"] == "root" else "root"
    result = _policy(
        {**combination, "permission_model": opposite},
        onboarded={"gpu": {"verified_device_classes": [combination]}},
    )
    assert result["result"] == "executed_unverified"
    assert result["trusted"] is False
    assert result["reason"] == "combination_unverified"


@pytest.mark.parametrize("combination", APPROVED)
def test_gpu_permission_swap_is_confirmed_in_full_registry(combination):
    opposite = "non-root" if combination["permission_model"] == "root" else "root"
    result = _policy({**combination, "permission_model": opposite})
    assert result["result"] == "confirmed"
    assert result["trusted"] is True
    assert result["reason"] == ""


@pytest.mark.parametrize("combination", APPROVED)
def test_gpu_unknown_permission_keeps_existing_reason(combination):
    result = _policy({**combination, "permission_model": "unknown"})
    assert result["result"] == "executed_unverified"
    assert result["trusted"] is False
    assert result["reason"] == "permission_unknown"


@pytest.mark.parametrize("combination", APPROVED)
def test_gpu_missing_apk_identity_is_not_confirmed(combination):
    result = _policy({**combination, "apk_sha256": ""})
    assert result["result"] == "executed_unverified"
    assert result["trusted"] is False
    assert result["reason"] == "identity_missing"
