"""#3723 v1.7.1 §3.6.1.7：脚本一律未验证规则，任何输入不输出 confirmed/readback_failed。

绑定补齐：PR #3747 Planner 核验退回（清单外置 + 证据字段）。
"""
from __future__ import annotations

import json

from backend.agent.contracts.device_return import (
    DISARM_RESULT_EXECUTED_UNVERIFIED,
    DISARM_RESULT_FAILED,
    DISARM_RESULT_READBACK_FAILED,
    map_step_outcome_to_disarm_result,
)
from backend.agent.tests.test_powercycle_disarm_u5_3730 import _load


def _patch_advancing_clock(monkeypatch, mod, *, start: float = 1_000_000.0):
    """sleep 推进假时钟，满足 #3202 守卫（禁止 no-op sleep）。"""
    state = {"now": start}

    def fake_time() -> float:
        return state["now"]

    def fake_sleep(seconds: float) -> None:
        state["now"] += float(seconds)

    monkeypatch.setattr(mod.time, "time", fake_time)
    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
    return state


def _stub_common(mod, monkeypatch):
    monkeypatch.setattr(mod, "device_serial", lambda: "ISOLATED-PLANNER")
    monkeypatch.setattr(mod, "_wait_device_online", lambda seconds: True)
    monkeypatch.setattr(mod, "device_online", lambda: True)
    monkeypatch.setattr(mod, "stop_task", lambda force=True, **kwargs: None)
    _patch_advancing_clock(monkeypatch, mod)
    monkeypatch.setattr(mod, "_detect_platform", lambda: "MTK")
    monkeypatch.setattr(mod, "_detect_model", lambda: "MLD_LX2")
    monkeypatch.setattr(
        mod,
        "adb_shell",
        lambda cmd, timeout=60: "1" if "boot_completed" in str(cmd) else "",
    )
    monkeypatch.setattr(
        mod,
        "_device_identity_evidence",
        lambda: {
            "permission_model": "root",
            "autotesttool_apk_sha256": "b" * 64,
        },
    )


class TestUnverifiedCategorySemantics:
    def test_disarm_read_fail_is_failed_after_v176(self, monkeypatch):
        """§3.6.6 第 5 条：读取被拒/未知时 disarm 正式值为 failed。"""
        mod = _load("planner_u5_category", "powercycle_disarm/powercycle_disarm.py")
        _stub_common(mod, monkeypatch)
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
        assert output["evidence"]["permission_model"] == "root"
        assert output["evidence"]["autotesttool_apk_sha256"] == "b" * 64
        assert output["readback"] == "readback_failed"
        assert output["disarm_result"] not in {"confirmed", "readback_failed"}, output
        assert output["disarm_result"] == "failed"
        assert output["success"] is False

    def test_probe_read_fail_is_not_formal_readback_failed(self, monkeypatch):
        mod = _load("planner_u5_probe_fail", "powercycle_disarm/powercycle_disarm.py")
        _stub_common(mod, monkeypatch)
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

    def test_any_input_never_outputs_confirmed_or_formal_readback_failed(self, monkeypatch):
        """矩阵：clear/footprint/readback_failed × probe/disarm → 正式值不含 confirmed/readback_failed。"""
        mod = _load("planner_u5_matrix", "powercycle_disarm/powercycle_disarm.py")
        _stub_common(mod, monkeypatch)
        cases = [
            ("probe", "clear"),
            ("probe", "footprint"),
            ("probe", "readback_failed"),
            ("disarm", "clear"),
            ("disarm", "footprint"),
            ("disarm", "readback_failed"),
        ]
        for mode, readback in cases:
            monkeypatch.setattr(
                mod,
                "_read_prefs_evidence",
                lambda rb=readback: {
                    "readback": rb,
                    "running": rb == "footprint",
                    "auto_resume": rb == "footprint",
                    "evidence": {"kind": "ok" if rb != "readback_failed" else "transient"},
                },
            )
            output = mod._run({"mode": mode})
            assert output["disarm_result"] not in {"confirmed", "readback_failed"}, (
                mode, readback, output["disarm_result"]
            )
            assert "permission_model" in output["evidence"]
            assert "autotesttool_apk_sha256" in output["evidence"]
            assert output["evidence"]["verified_category"] is False

    def test_no_builtin_verified_class_list(self):
        mod = _load("planner_u5_no_list", "powercycle_disarm/powercycle_disarm.py")
        assert not hasattr(mod, "_VERIFIED_DEVICE_CLASSES")
        assert not hasattr(mod, "_load_verified_classes")
        assert not hasattr(mod, "_category_verified")

    def test_stop_error_without_clear_is_failed(self, monkeypatch):
        mod = _load("planner_u5_stop_err", "powercycle_disarm/powercycle_disarm.py")
        _stub_common(mod, monkeypatch)

        def boom_stop(force=True, **kwargs):
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
        assert output["disarm_result"] not in {"confirmed", "readback_failed"}

    def test_wait_offline_is_failed_not_readback_failed(self, monkeypatch):
        mod = _load("planner_u5_offline", "powercycle_disarm/powercycle_disarm.py")
        monkeypatch.setattr(mod, "device_serial", lambda: "ISOLATED-OFFLINE")
        monkeypatch.setattr(mod, "_wait_device_online", lambda seconds: False)
        # 离线路径不得再 adb 取证（§3.6.1.7）；identity stub 若被调用即失败
        monkeypatch.setattr(
            mod,
            "_device_identity_evidence",
            lambda: (_ for _ in ()).throw(AssertionError("offline must not probe identity")),
        )
        output = mod._run({"mode": "disarm", "wait_device_online_seconds": 30})
        assert output["evidence"]["verified_category"] is False
        assert output["evidence"]["permission_model"] == "unknown"
        assert output["evidence"]["autotesttool_apk_sha256"] is None
        assert output["disarm_result"] == "failed"
        assert output["disarm_result"] not in {"confirmed", "readback_failed"}
        assert output["readback"] == "readback_failed"
        assert output["success"] is False

    def test_probe_clear_is_failed(self, monkeypatch):
        mod = _load("planner_u5_probe_clear", "powercycle_disarm/powercycle_disarm.py")
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
        assert output["disarm_result"] == "failed"
        assert output["success"] is False

    def test_disarm_clear_keeps_executed_unverified(self, monkeypatch):
        mod = _load("planner_u5_disarm_clear", "powercycle_disarm/powercycle_disarm.py")
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
        output = mod._run({"mode": "disarm"})
        assert output["disarm_result"] == "executed_unverified"
        assert output["success"] is True
        assert output["readback"] == "clear"
        assert output["disarm_result"] != "confirmed"


class TestAgentAggregationPath:

    def test_structured_executed_unverified_survives_mapper_and_payload(self):
        structured = "executed_unverified"
        value = map_step_outcome_to_disarm_result(
            started=True,
            success=True,
            readback="readback_failed",
            onboarded_family=True,
            structured_result=structured,
        )
        assert value == DISARM_RESULT_EXECUTED_UNVERIFIED

        parsed = {
            "disarm_result": "executed_unverified",
            "readback": "readback_failed",
            "evidence": {
                "verified_category": False,
                "kind": "permission-denied",
                "permission_model": "root",
                "autotesttool_apk_sha256": "c" * 64,
            },
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
        # U1 六值：未传 category_verified 时保持 readback_failed（控制面/U8 路径）
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

    def test_structured_failed_probe_survives_mapper_and_payload(self):
        structured = "failed"
        mapped = map_step_outcome_to_disarm_result(
            started=True,
            success=False,
            readback="clear",
            onboarded_family=True,
            structured_result=structured,
            category_verified=False,
        )
        assert mapped == DISARM_RESULT_FAILED

        payload = {
            "disarm_result": "failed",
            "readback": "clear",
            "evidence": {
                "verified_category": False,
                "kind": "ok",
                "permission_model": "non-root",
                "autotesttool_apk_sha256": None,
            },
            "success": False,
            "mode": "probe",
        }
        loaded = json.loads(json.dumps(payload))
        assert loaded["disarm_result"] == "failed"
        assert loaded["success"] is False
        remapped = map_step_outcome_to_disarm_result(
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
        assert remapped == DISARM_RESULT_FAILED
