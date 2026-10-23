"""#3723 v1.7.16: init accepts nonprivileged values; privileged claims stay gated."""

import pytest

from backend.core.database import SessionLocal
from backend.models.host import Device
from backend.services.device_return import check_init_probe_result, evaluate_probe_entry
from backend.tests.api.test_disarm_policy_u8_3752 import (
    combination, result_entry, seed, signals_are_forbidden,  # noqa: F401
)


@pytest.mark.parametrize("result", ["executed_unverified", "failed", "incomplete", "not_executed"])
def test_init_accepts_nonprivileged_result_from_unverified_combination(seed, combination, result):
    entry = result_entry(combination, "wrong_apk")
    entry["result"] = result
    if result == "failed":
        entry["exit_code"] = 1
        entry["evidence"].update(success=False, readback="readback_failed", kind="denied")
    elif result == "incomplete":
        entry["exit_code"] = 124
    elif result == "not_executed":
        entry["exit_code"] = None
        entry["evidence"].update(success=False, readback=None)
    with SessionLocal() as db:
        device = db.get(Device, seed["device_id"])
        policy = evaluate_probe_entry(device, "power-cycle", entry, token_valid=True)
        assert policy["trusted"] is False
        decision = check_init_probe_result(db, device, "power-cycle", entry, token_valid=True)
        assert decision["status_code"] == 200, decision


def test_init_confirmed_422_can_be_retried_as_executed_unverified(seed, combination):
    entry = result_entry(combination, "wrong_apk")
    with SessionLocal() as db:
        device = db.get(Device, seed["device_id"])
        assert check_init_probe_result(db, device, "power-cycle", entry, token_valid=True)["status_code"] == 422
        entry["result"] = "executed_unverified"
        decision = check_init_probe_result(db, device, "power-cycle", entry, token_valid=True)
        assert decision["status_code"] == 200, decision


@pytest.mark.parametrize("result", ["confirmed", "readback_failed"])
def test_untrusted_privileged_init_result_still_rejected(seed, combination, result):
    entry = {**result_entry(combination, "wrong_apk"), "result": result}
    with SessionLocal() as db:
        device = db.get(Device, seed["device_id"])
        decision = check_init_probe_result(db, device, "power-cycle", entry, token_valid=True)
        assert decision["status_code"] == 422
        assert decision["code"] == "UNVERIFIED_PROBE_RESULT"


def test_controls_trusted_confirmed_and_invalid_token(seed, combination):
    with SessionLocal() as db:
        device = db.get(Device, seed["device_id"])
        entry = result_entry(combination)
        assert check_init_probe_result(db, device, "power-cycle", entry, token_valid=True)["status_code"] == 200
        assert check_init_probe_result(db, device, "power-cycle", entry, token_valid=False)["status_code"] == 409
