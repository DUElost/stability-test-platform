"""#3723 v1.6 §3.6.1.3 / Planner 反例：未验证 probe + 原始 clear 不得通过。

修前（3de9c7c695）：disarm_result=executed_unverified / success=True → 红。
修后：failed / success=False。
"""
from __future__ import annotations

from backend.agent.tests.test_planner_u5_category_contract import _stub_common
from backend.agent.tests.test_powercycle_disarm_u5_3730 import _load


def test_unverified_probe_raw_clear_is_not_a_trusted_pass(monkeypatch):
    mod = _load("planner_unverified_clear", "powercycle_disarm/powercycle_disarm.py")
    _stub_common(mod, monkeypatch)
    monkeypatch.setattr(
        mod,
        "_read_prefs_evidence",
        lambda: {
            "readback": "clear",
            "running": False,
            "auto_resume": False,
            "evidence": {"kind": "ok"},
        },
    )
    output = mod._run({"mode": "probe"})
    assert output["evidence"]["verified_category"] is False
    assert output["readback"] == "clear"
    assert output["disarm_result"] == "failed", output
    assert output["success"] is False, output
    assert output["disarm_result"] not in {"confirmed", "readback_failed"}
