"""#3730 U5 v1.7：job 实际版本角色 helper 与 select_disarm_steps（§3.0.2 / §3.6.2）。"""
from __future__ import annotations

from backend.agent.contracts.device_return import (
    REASON_UNKNOWN_LEGACY_VERSION,
    STEP_ROLE_COLLECT,
    STEP_ROLE_DISARM,
    STEP_ROLE_UNKNOWN_LEGACY_DISARM,
    is_disarm_script,
    select_disarm_steps,
    step_teardown_role,
    validate_onboarded_teardown_shape,
)


class TestStepTeardownRole:
    def test_finish_1_0_x_still_disarm(self):
        assert step_teardown_role("powercycle_finish", "1.0.8") == STEP_ROLE_DISARM
        assert step_teardown_role("powercycle_finish", "1.0.0") == STEP_ROLE_DISARM

    def test_finish_1_1_0_collect_only(self):
        assert step_teardown_role("powercycle_finish", "1.1.0") == STEP_ROLE_COLLECT

    def test_disarm_any_known_version(self):
        assert step_teardown_role("powercycle_disarm", "1.0.0") == STEP_ROLE_DISARM
        assert step_teardown_role("powercycle_disarm", "1.0.5") == STEP_ROLE_DISARM

    def test_missing_or_unknown_version_conservative(self):
        assert step_teardown_role("powercycle_finish", "") == STEP_ROLE_UNKNOWN_LEGACY_DISARM
        assert step_teardown_role("powercycle_disarm", None) == STEP_ROLE_UNKNOWN_LEGACY_DISARM
        assert step_teardown_role("powercycle_finish", "bogus") == STEP_ROLE_UNKNOWN_LEGACY_DISARM


class TestSelectDisarmStepsVersionAware:
    def test_manual_exit_runs_legacy_finish_despite_onboarded(self):
        steps = select_disarm_steps(
            [
                {"step_id": "d", "action": "script:powercycle_disarm", "version": "1.0.5"},
                {"step_id": "f", "action": "script:powercycle_finish", "version": "1.0.8"},
            ],
            {"power-cycle"},
        )
        assert [s["step_id"] for s in steps] == ["d", "f"]

    def test_manual_exit_skips_collect_only_finish(self):
        steps = select_disarm_steps(
            [
                {"step_id": "d", "action": "script:powercycle_disarm", "version": "1.0.5"},
                {"step_id": "f", "action": "script:powercycle_finish", "version": "1.1.0"},
            ],
            {"power-cycle"},
        )
        assert [s["step_id"] for s in steps] == ["d"]

    def test_only_collect_finish_is_not_disarm_script(self):
        assert not is_disarm_script(
            "powercycle_finish",
            footprint_families=["power-cycle"],
            version="1.1.0",
        )
        assert is_disarm_script(
            "powercycle_finish",
            footprint_families=["power-cycle"],
            version="1.0.8",
        )


class TestValidateOnboardedTeardownShape:
    def _footprint(self):
        return {
            "step_key": "init_pc",
            "script_name": "powercycle_setup",
            "script_version": "1.2.9",
            "stage": "init",
            "enabled": True,
        }

    def test_finish_only_disarm_missing(self):
        v = validate_onboarded_teardown_shape([
            self._footprint(),
            {
                "step_key": "td_f",
                "script_name": "powercycle_finish",
                "script_version": "1.1.0",
                "stage": "teardown",
                "enabled": True,
            },
        ])
        assert any(x["reason"] == "disarm_missing" for x in v)

    def test_disarm_disabled(self):
        v = validate_onboarded_teardown_shape([
            self._footprint(),
            {
                "step_key": "td_d",
                "script_name": "powercycle_disarm",
                "script_version": "1.0.5",
                "stage": "teardown",
                "enabled": False,
            },
        ])
        assert any(x["reason"] == "disarm_disabled" for x in v)

    def test_disarm_below_min(self):
        v = validate_onboarded_teardown_shape([
            self._footprint(),
            {
                "step_key": "td_d",
                "script_name": "powercycle_disarm",
                "script_version": "1.0.1",
                "stage": "teardown",
                "enabled": True,
            },
        ])
        assert any(x["reason"] == "disarm_below_min_version" for x in v)

    def test_finish_below_min(self):
        v = validate_onboarded_teardown_shape([
            self._footprint(),
            {
                "step_key": "td_d",
                "script_name": "powercycle_disarm",
                "script_version": "1.0.5",
                "stage": "teardown",
                "enabled": True,
            },
            {
                "step_key": "td_f",
                "script_name": "powercycle_finish",
                "script_version": "1.0.8",
                "stage": "teardown",
                "enabled": True,
            },
        ])
        assert any(x["reason"] == "finish_below_min_version" for x in v)

    def test_positive_split_shape_ok(self):
        v = validate_onboarded_teardown_shape([
            self._footprint(),
            {
                "step_key": "td_d",
                "script_name": "powercycle_disarm",
                "script_version": "1.0.7",
                "stage": "teardown",
                "enabled": True,
            },
            {
                "step_key": "td_f",
                "script_name": "powercycle_finish",
                "script_version": "1.1.0",
                "stage": "teardown",
                "enabled": True,
            },
        ])
        assert v == []

    def test_collect_only_plan_unconstrained(self):
        v = validate_onboarded_teardown_shape([
            {
                "step_key": "td_f",
                "script_name": "powercycle_finish",
                "script_version": "1.1.0",
                "stage": "teardown",
                "enabled": True,
            },
        ])
        assert v == []


# silence unused import lint in some runners
assert REASON_UNKNOWN_LEGACY_VERSION
