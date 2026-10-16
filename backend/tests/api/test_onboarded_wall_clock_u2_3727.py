"""#3723 U2 / ADR-0061 D3.6 / §3.3.1：ONBOARDED 墙钟必须显式正整数。

反例（修复前红）：省略 timeout 可被控制面 env 放行，远端 Agent 仍落 300。
v1.3：已登记启用步骤省略/null/0/负/低于预算 → 422；控制面 env 永不背书。
"""
from __future__ import annotations


import pytest
from fastapi import HTTPException

from backend.agent.pipeline_engine import _resolve_step_wall_clock
from backend.api.routes.plans import (
    PlanStepIn,
    _validate_onboarded_wall_clock,
)
from backend.services.plan_dispatcher_core import apply_step_timing_fields


def _step(**kwargs) -> PlanStepIn:
    base = dict(
        step_key="teardown_pc",
        script_name="powercycle_finish",
        script_version="1.0.4",
        stage="teardown",
        sort_order=0,
        timeout_seconds=300,
        params={"wait_device_online_seconds": 600},
    )
    base.update(kwargs)
    return PlanStepIn(**base)


def _onboarded_wait_plus_60():
    return {
        "power-cycle": {
            "min_wall_clock_seconds": {
                "powercycle_finish": lambda params: int(
                    params.get("wait_device_online_seconds", 600)
                )
                + 60,
            }
        }
    }


def _assert_rejected(
    steps, *, onboarded, script_defaults=None, expected_timeout, expected_min=660,
):
    with pytest.raises(HTTPException) as ei:
        _validate_onboarded_wall_clock(
            steps,
            onboarded=onboarded,
            script_defaults=script_defaults or {},
        )
    assert ei.value.status_code == 422
    detail = ei.value.detail
    assert detail["code"] == "WALL_CLOCK_BELOW_ONBOARDED_BUDGET"
    assert detail["violations"][0]["min_wall_clock_seconds"] == expected_min
    assert detail["violations"][0]["timeout_seconds"] == expected_timeout


def test_empty_onboarded_does_not_reject():
    _validate_onboarded_wall_clock([_step()], onboarded={})


def test_below_budget_rejected():
    _assert_rejected(
        [_step(timeout_seconds=300)],
        onboarded=_onboarded_wait_plus_60(),
        expected_timeout=300,
    )


@pytest.mark.parametrize("timeout,reported", [
    (None, None),
    (0, 0),
    (-1, -1),
    (659, 659),
])
def test_omit_null_zero_negative_below_rejected(timeout, reported):
    _assert_rejected(
        [_step(timeout_seconds=timeout)],
        onboarded=_onboarded_wait_plus_60(),
        expected_timeout=reported,
    )


@pytest.mark.parametrize("env_value", [None, "660", "10000"])
def test_omit_rejected_regardless_of_control_plane_env(monkeypatch, env_value):
    """改写原 test_omit_timeout_env_at_or_above_budget_ok：env≥预算时省略仍 422。"""
    if env_value is None:
        monkeypatch.delenv("STP_STEP_WALL_CLOCK_SECONDS", raising=False)
    else:
        monkeypatch.setenv("STP_STEP_WALL_CLOCK_SECONDS", env_value)
    _assert_rejected(
        [_step(timeout_seconds=None)],
        onboarded=_onboarded_wait_plus_60(),
        expected_timeout=None,
    )


def test_at_or_above_budget_ok():
    onboarded = {
        "power-cycle": {
            "min_wall_clock_seconds": {"powercycle_finish": 660},
        }
    }
    _validate_onboarded_wall_clock([_step(timeout_seconds=660)], onboarded=onboarded)
    _validate_onboarded_wall_clock([_step(timeout_seconds=700)], onboarded=onboarded)


def test_unregistered_script_skipped():
    onboarded = {
        "power-cycle": {
            "min_wall_clock_seconds": {"powercycle_disarm": 660},
        }
    }
    _validate_onboarded_wall_clock([_step(timeout_seconds=1)], onboarded=onboarded)


def test_disabled_step_skipped():
    onboarded = _onboarded_wait_plus_60()
    _validate_onboarded_wall_clock(
        [_step(timeout_seconds=None, enabled=False)],
        onboarded=onboarded,
    )


def test_budget_uses_default_params_when_step_omits_wait():
    """有效参数 = default_params ∪ step.params；缺省 wait=600 → 预算 660。"""
    defaults = {("powercycle_finish", "1.0.4"): {"wait_device_online_seconds": 600}}
    _assert_rejected(
        [_step(timeout_seconds=300, params=None)],
        onboarded=_onboarded_wait_plus_60(),
        script_defaults=defaults,
        expected_timeout=300,
    )


def test_step_params_override_default_params_for_budget():
    """步骤 params 覆盖 default：wait=100 → 预算 160；显式 160 通过。"""
    defaults = {("powercycle_finish", "1.0.4"): {"wait_device_online_seconds": 600}}
    onboarded = _onboarded_wait_plus_60()
    _validate_onboarded_wall_clock(
        [_step(timeout_seconds=160, params={"wait_device_online_seconds": 100})],
        onboarded=onboarded,
        script_defaults=defaults,
    )
    _assert_rejected(
        [_step(timeout_seconds=159, params={"wait_device_online_seconds": 100})],
        onboarded=onboarded,
        script_defaults=defaults,
        expected_timeout=159,
        expected_min=160,
    )


def test_distinct_hosts_omit_rejected_then_explicit_dispatched(
    monkeypatch,
):
    """异宿主反例：控制面 env=660、Agent 无 env、省略 → 必须 422（修复后绿）。

    若校验误用控制面 env 放行，下发后 Agent 解析为 300 < 660（修复前红证据形态）。
    """
    monkeypatch.setenv("STP_STEP_WALL_CLOCK_SECONDS", "660")
    step = _step(timeout_seconds=None, script_name="powercycle_disarm",
                 script_version="1.0.0", params={"wait_device_online_seconds": 600})
    registry = {
        "power-cycle": {"min_wall_clock_seconds": {"powercycle_disarm": 660}},
    }
    with pytest.raises(HTTPException) as ei:
        _validate_onboarded_wall_clock([step], onboarded=registry)
    assert ei.value.status_code == 422
    assert ei.value.detail["violations"][0]["timeout_seconds"] is None

    # 显式 660：校验过；dispatcher 原样写入；Agent 无 env 仍按 660 执行
    explicit = _step(
        timeout_seconds=660,
        script_name="powercycle_disarm",
        script_version="1.0.0",
        params={"wait_device_online_seconds": 600},
    )
    _validate_onboarded_wall_clock([explicit], onboarded=registry)
    step_def = {
        "step_id": explicit.step_key,
        "action": f"script:{explicit.script_name}",
        "version": explicit.script_version,
        "params": dict(explicit.params or {}),
    }
    apply_step_timing_fields(
        step_def,
        timeout_seconds=explicit.timeout_seconds,
        stall_seconds=explicit.stall_seconds,
    )
    assert step_def["timeout_seconds"] == 660
    monkeypatch.delenv("STP_STEP_WALL_CLOCK_SECONDS", raising=False)
    assert _resolve_step_wall_clock(step_def) == 660.0
