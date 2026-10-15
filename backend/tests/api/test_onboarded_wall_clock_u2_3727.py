"""#3723 U2 / ADR-0061 D3.6：ONBOARDED 墙钟下限。

反例（修复前红）：墙钟低于预算的开关机步骤仍被接受。
U5 合入前生产 ONBOARDED 为空 → 现有步骤不拒（本文件用 fixture 注入）。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.api.routes.plans import (
    PlanStepIn,
    _validate_onboarded_wall_clock,
)


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


def test_empty_onboarded_does_not_reject():
    _validate_onboarded_wall_clock([_step()], onboarded={})


def test_below_budget_rejected():
    onboarded = {
        "power-cycle": {
            "min_wall_clock_seconds": {
                "powercycle_finish": lambda params: int(params.get("wait_device_online_seconds", 600)) + 60,
            }
        }
    }
    with pytest.raises(HTTPException) as ei:
        _validate_onboarded_wall_clock([_step(timeout_seconds=300)], onboarded=onboarded)
    assert ei.value.status_code == 422
    detail = ei.value.detail
    assert detail["code"] == "WALL_CLOCK_BELOW_ONBOARDED_BUDGET"
    assert detail["violations"][0]["min_wall_clock_seconds"] == 660
    assert detail["violations"][0]["timeout_seconds"] == 300


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
    # powercycle_finish 未登记 → 不拒
    _validate_onboarded_wall_clock([_step(timeout_seconds=1)], onboarded=onboarded)
