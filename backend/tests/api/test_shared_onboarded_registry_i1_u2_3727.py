"""#3723 §3.3.1.6 I1：Plan loader 必须读取共享 device_return.ONBOARDED。

权威复核反例：向共享 ONBOARDED 注入标记后，_load_onboarded_registry()
须返回同一对象并看见标记（不得只断言两空 dict 相等）。
"""
from __future__ import annotations

import backend.agent.contracts.device_return as device_return
import backend.api.routes.plans as plans_mod
from backend.core.job_timeout_config import DISARM_SCRIPT_NAMES, EXECUTING_DISARM
from backend.services import agent_completion
from backend.services.agent_lease_extend import _VALID_EXECUTION_STATES


def test_plan_loader_reads_shared_onboarded_same_object(monkeypatch):
    """修前红→修后绿：共享登记表标记必须对 Plan loader 可见。"""
    marker = object()
    monkeypatch.setitem(device_return.ONBOARDED, "reviewer-registration", marker)
    loaded = plans_mod._load_onboarded_registry()
    assert loaded is device_return.ONBOARDED
    assert loaded.get("reviewer-registration") is marker


def test_completion_uses_shared_family_steps_object():
    assert agent_completion._FAMILY_STEPS is device_return.FAMILY_STEPS


def test_disarm_script_names_is_union_of_both_roles():
    expected = set()
    for spec in device_return.FAMILY_STEPS.values():
        expected.update(spec.get("disarm") or ())
        expected.update(spec.get("disarm_onboarded") or ())
    assert set(DISARM_SCRIPT_NAMES) == expected
    # ONBOARDED 为空时仍须保留 powercycle_disarm（onboarded 角色）
    assert "powercycle_disarm" in DISARM_SCRIPT_NAMES
    assert "powercycle_finish" in DISARM_SCRIPT_NAMES


def test_executing_disarm_sources_point_at_shared_contract():
    assert EXECUTING_DISARM is device_return.EXECUTING_DISARM
    assert device_return.EXECUTING_DISARM in _VALID_EXECUTION_STATES
