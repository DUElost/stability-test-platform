"""#3723 v1.6 §3.6.1 / Planner 核验：未验证类别不得输出正式 readback_failed/confirmed。

绑定补齐：PR #3747 comment（Planner 独立 probe 修前红 → 本文件回归）。
验证脚本结构化输出；另见同文件 Agent 汇总路径。
"""
from __future__ import annotations

import json

import pytest

from backend.agent.contracts.device_return import (
    DISARM_RESULT_EXECUTED_UNVERIFIED,
    DISARM_RESULT_FAILED,
    DISARM_RESULT_READBACK_FAILED,
    map_step_outcome_to_disarm_result,
)
from backend.agent.tests.test_powercycle_disarm_u5_3730 import _load


def _stub_common(mod, monkeypatch, *, verified_classes):
    monkeypatch.setattr(mod, "device_serial", lambda: "ISOLATED-PLANNER")
    monkeypatch.setattr(mod, "_wait_device_online", lambda seconds: True)
    monkeypatch.setattr(mod, "device_online", lambda: True)
    monkeypatch.setattr(mod, "stop_task", lambda force=True: None)
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(mod, "_detect_platform", lambda: "MTK")
    monkeypatch.setattr(mod, "_detect_model", lambda: "MLD_LX2")
    monkeypatch.setattr(mod, "_load_verified_classes", lambda: list(verified_classes))


class TestUnverifiedCategorySemantics:
    def test_unverified_disarm_completed_read_fail_is_executed_unverified(
        self, monkeypatch
    ):
        """Planner probe：未验证 + 解除成功 + 原始读取失败 → 不得正式 readback_failed。"""
        mod = _load("planner_u5_category", "powercycle_disarm/powercycle_disarm.py")
        _stub_common(mod, monkeypatch, verified_classes=[])
        monkeypatch.setattr(
            mod,
            "_read_prefs_evidence",
            lambda: {
                "readback": "readback_failed",
                "running": None,
                "auto_resume": None,
                "evidence": {"kind": "permission-denied"},
            },
        )
        output = mod._run({"mode": "disarm"})
        assert output["evidence"]["verified_category"] is False
        assert output["readback"] == "readback_failed"
        assert output["disarm_result"] not in {"confirmed", "readback_failed"}, output
        assert output["disarm_result"] == "executed_unverified"
        assert output["success"] is True

    def test_unverified_probe_read_fail_is_not_formal_readback_failed(
        self, monkeypatch
    ):
        """未验证 probe 无可信判据：不得正式 readback_failed，且不通过。"""
        mod = _load("planner_u5_probe_fail", "powercycle_disarm/powercycle_disarm.py")
        _stub_common(mod, monkeypatch, verified_classes=[])
        monkeypatch.setattr(
            mod,
            "_read_prefs_evidence",
            lambda: {
                "readback": "readback_failed",
                "running": None,
                "auto_resume": None,
                "evidence": {"kind": "permission-denied"},
            },
        )
        output = mod._run({"mode": "probe"})
        assert output["evidence"]["verified_category"] is False
        assert output["readback"] == "readback_failed"
        assert output["disarm_result"] not in {"confirmed", "readback_failed"}
        assert output["disarm_result"] == "failed"
        assert output["success"] is False

    def test_verified_disarm_read_fail_is_formal_readback_failed(self, monkeypatch):
        mod = _load("planner_u5_verified_rf", "powercycle_disarm/powercycle_disarm.py")
        _stub_common(
            mod,
            monkeypatch,
            verified_classes=[{"platform": "MTK", "model": "MLD_LX2"}],
        )
        monkeypatch.setattr(
            mod,
            "_read_prefs_evidence",
            lambda: {
                "readback": "readback_failed",
                "running": None,
                "auto_resume": None,
                "evidence": {"kind": "permission-denied"},
            },
        )
        output = mod._run({"mode": "disarm"})
        assert output["evidence"]["verified_category"] is True
        assert output["readback"] == "readback_failed"
        assert output["disarm_result"] == "readback_failed"
        assert output["success"] is False

    def test_verified_clear_is_confirmed(self, monkeypatch):
        mod = _load("planner_u5_verified_clear", "powercycle_disarm/powercycle_disarm.py")
        _stub_common(
            mod,
            monkeypatch,
            verified_classes=[{"platform": "MTK", "model": "MLD_LX2"}],
        )
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
        output = mod._run({"mode": "disarm"})
        assert output["disarm_result"] == "confirmed"
        assert output["success"] is True

    def test_unverified_stop_error_without_clear_is_failed(self, monkeypatch):
        """动作失败且未取得 clear：不得借未验证规则报成 executed_unverified。"""
        mod = _load("planner_u5_stop_err", "powercycle_disarm/powercycle_disarm.py")
        _stub_common(mod, monkeypatch, verified_classes=[])

        def boom_stop(force=True):
            raise RuntimeError("stop exploded")

        monkeypatch.setattr(mod, "stop_task", boom_stop)
        monkeypatch.setattr(
            mod,
            "_read_prefs_evidence",
            lambda: {
                "readback": "readback_failed",
                "running": None,
                "auto_resume": None,
                "evidence": {"kind": "transient"},
            },
        )
        output = mod._run({"mode": "disarm"})
        assert output["disarm_result"] == "failed"
        assert output["success"] is False
        assert output["readback"] == "readback_failed"

    def test_unverified_wait_offline_is_failed_not_readback_failed(self, monkeypatch):
        mod = _load("planner_u5_offline", "powercycle_disarm/powercycle_disarm.py")
        monkeypatch.setattr(mod, "device_serial", lambda: "ISOLATED-OFFLINE")
        monkeypatch.setattr(mod, "_wait_device_online", lambda seconds: False)
        monkeypatch.setattr(mod, "_load_verified_classes", lambda: [])
        output = mod._run({"mode": "disarm", "wait_device_online_seconds": 1})
        assert output["readback"] == "readback_failed"
        assert output["disarm_result"] == "failed"
        assert output["evidence"]["verified_category"] is False
        assert output["success"] is False


class TestAgentAggregationPath:
    def test_structured_executed_unverified_survives_mapper_and_payload(self):
        """脚本 → map_step_outcome（structured）→ 终态条目，不只测 helper。"""
        structured = "executed_unverified"
        value = map_step_outcome_to_disarm_result(
            started=True,
            success=True,
            readback="readback_failed",
            onboarded_family=True,
            structured_result=structured,
        )
        assert value == DISARM_RESULT_EXECUTED_UNVERIFIED

        # 模拟引擎解析 stdout JSON 后的终态条目形状
        parsed = {
            "disarm_result": "executed_unverified",
            "readback": "readback_failed",
            "evidence": {"verified_category": False, "kind": "permission-denied"},
            "success": True,
        }
        raw = json.dumps(parsed)
        loaded = json.loads(raw)
        assert loaded["disarm_result"] == "executed_unverified"
        mapped = map_step_outcome_to_disarm_result(
            started=True,
            success=bool(loaded.get("success")),
            readback=loaded.get("readback"),
            onboarded_family=True,
            structured_result=loaded.get("disarm_result"),
            category_verified=bool(
                (loaded.get("evidence") or {}).get("verified_category")
            )
            if isinstance(loaded.get("evidence"), dict)
            and "verified_category" in (loaded.get("evidence") or {})
            else None,
        )
        assert mapped == DISARM_RESULT_EXECUTED_UNVERIFIED
        # 安全网：无 structured 但明确未验证时，不得正式 readback_failed
        assert (
            map_step_outcome_to_disarm_result(
                started=True,
                success=True,
                readback="readback_failed",
                onboarded_family=True,
                category_verified=False,
            )
            == DISARM_RESULT_EXECUTED_UNVERIFIED
        )
        # U1 六值：未传 category_verified 时保持 readback_failed
        assert (
            map_step_outcome_to_disarm_result(
                started=True, success=True, readback="readback_failed"
            )
            == DISARM_RESULT_READBACK_FAILED
        )
        assert (
            map_step_outcome_to_disarm_result(
                started=True,
                success=False,
                readback="readback_failed",
                category_verified=False,
            )
            == DISARM_RESULT_FAILED
        )
