"""#3730 / #3723 §4 U5：powercycle_disarm 模式、回读失败、finish 不写 prefs。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "backend" / "agent" / "scripts"


def _load(mod_name: str, rel: str):
    path = SCRIPTS / rel
    # 先加载同目录 _lib
    lib_path = path.parent / "_lib.py"
    lib_name = f"{mod_name}_lib"
    lib_spec = importlib.util.spec_from_file_location(lib_name, lib_path)
    assert lib_spec and lib_spec.loader
    lib = importlib.util.module_from_spec(lib_spec)
    sys.modules[lib_name] = lib
    lib_spec.loader.exec_module(lib)
    # 入口把 _lib 名绑定为 _lib
    sys.modules["_lib"] = lib
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def disarm_mod():
    return _load("powercycle_disarm_u5", "powercycle_disarm/powercycle_disarm.py")


@pytest.fixture
def finish_mod():
    return _load("powercycle_finish_u5", "powercycle_finish/powercycle_finish.py")


class TestDisarmModes:
    def _stub_identity(self, disarm_mod, monkeypatch):
        monkeypatch.setattr(
            disarm_mod, "_device_identity_evidence",
            lambda: {"permission_model": "root", "autotesttool_apk_sha256": "a" * 64},
        )
        monkeypatch.setattr(
            disarm_mod,
            "adb_shell",
            lambda cmd, timeout=60: "1" if "boot_completed" in str(cmd) else "",
        )

    def test_probe_clear_unverified_category(self, disarm_mod, monkeypatch):
        """probe + clear → failed / success=false（§3.6.1.3 / 第 7 条）。"""
        monkeypatch.setattr(disarm_mod, "device_serial", lambda: "S-PROBE")
        monkeypatch.setattr(disarm_mod, "device_online", lambda: True)
        monkeypatch.setattr(disarm_mod, "_detect_platform", lambda: "MTK")
        monkeypatch.setattr(disarm_mod, "_detect_model", lambda: "MLD_LX2")
        self._stub_identity(disarm_mod, monkeypatch)
        monkeypatch.setattr(
            disarm_mod,
            "_read_prefs_evidence",
            lambda: {
                "readback": "clear",
                "running": False,
                "auto_resume": False,
                "evidence": {"kind": "ok"},
            },
        )
        out = disarm_mod._run({"mode": "probe"})
        assert out["disarm_result"] == "failed"
        assert out["readback"] == "clear"
        assert out["success"] is False
        assert out["disarm_result"] not in {"confirmed", "readback_failed"}
        assert out["evidence"]["permission_model"] == "root"
        assert out["evidence"]["autotesttool_apk_sha256"] == "a" * 64
        assert out["evidence"]["verified_category"] is False

    def test_probe_clear_never_confirmed_even_with_known_model(self, disarm_mod, monkeypatch):
        """§3.6.1.7：任何输入下都不输出 confirmed（清单已外置）。"""
        monkeypatch.setattr(disarm_mod, "device_serial", lambda: "S-PROBE")
        monkeypatch.setattr(disarm_mod, "device_online", lambda: True)
        monkeypatch.setattr(disarm_mod, "_detect_platform", lambda: "MTK")
        monkeypatch.setattr(disarm_mod, "_detect_model", lambda: "MLD_LX2")
        self._stub_identity(disarm_mod, monkeypatch)
        monkeypatch.setattr(
            disarm_mod,
            "_read_prefs_evidence",
            lambda: {
                "readback": "clear",
                "running": False,
                "auto_resume": False,
                "evidence": {"kind": "ok"},
            },
        )
        out = disarm_mod._run({"mode": "probe"})
        assert out["disarm_result"] == "failed"
        assert out["disarm_result"] != "confirmed"

    def test_probe_footprint(self, disarm_mod, monkeypatch):
        monkeypatch.setattr(disarm_mod, "device_serial", lambda: "S-PROBE")
        monkeypatch.setattr(disarm_mod, "device_online", lambda: True)
        monkeypatch.setattr(disarm_mod, "_detect_platform", lambda: "MTK")
        monkeypatch.setattr(disarm_mod, "_detect_model", lambda: "MLD_LX2")
        self._stub_identity(disarm_mod, monkeypatch)
        monkeypatch.setattr(
            disarm_mod,
            "_read_prefs_evidence",
            lambda: {
                "readback": "footprint",
                "running": True,
                "auto_resume": True,
                "evidence": {"kind": "ok"},
            },
        )
        out = disarm_mod._run({"mode": "probe"})
        assert out["disarm_result"] == "failed"
        assert out["success"] is False

    def test_read_failure_keeps_raw_not_clear(self, disarm_mod, monkeypatch):
        """原始读不到 → readback=readback_failed，不得报无足迹。

        未验证类别正式结果不得为 readback_failed（v1.6 §3.6.1）；probe 无可信判据 → failed。
        """
        monkeypatch.setattr(disarm_mod, "device_serial", lambda: "S-PROBE")
        monkeypatch.setattr(disarm_mod, "device_online", lambda: True)
        monkeypatch.setattr(disarm_mod, "_detect_platform", lambda: "MTK")
        monkeypatch.setattr(disarm_mod, "_detect_model", lambda: "MLD_LX3")
        self._stub_identity(disarm_mod, monkeypatch)
        monkeypatch.setattr(
            disarm_mod,
            "_read_prefs_evidence",
            lambda: {
                "readback": "readback_failed",
                "running": None,
                "auto_resume": None,
                "evidence": {"kind": "transient"},
            },
        )
        out = disarm_mod._run({"mode": "probe"})
        assert out["readback"] == "readback_failed"
        assert out["disarm_result"] not in {"confirmed", "readback_failed", "clear"}
        assert out["disarm_result"] == "failed"
        assert out["success"] is False

    def test_read_failure_never_formal_readback_failed(self, disarm_mod, monkeypatch):
        """§3.6.1.7：读取失败正式值不得为 readback_failed（一律未验证规则）。"""
        monkeypatch.setattr(disarm_mod, "device_serial", lambda: "S-PROBE")
        monkeypatch.setattr(disarm_mod, "device_online", lambda: True)
        monkeypatch.setattr(disarm_mod, "_detect_platform", lambda: "MTK")
        monkeypatch.setattr(disarm_mod, "_detect_model", lambda: "MLD_LX3")
        self._stub_identity(disarm_mod, monkeypatch)
        monkeypatch.setattr(
            disarm_mod,
            "_read_prefs_evidence",
            lambda: {
                "readback": "readback_failed",
                "running": None,
                "auto_resume": None,
                "evidence": {"kind": "transient"},
            },
        )
        out = disarm_mod._run({"mode": "probe"})
        assert out["disarm_result"] == "failed"
        assert out["readback"] == "readback_failed"
        assert out["disarm_result"] not in {"confirmed", "readback_failed"}
        assert out["success"] is False

    def test_disarm_calls_stop_then_readback(self, disarm_mod, monkeypatch):
        calls = {"stop": 0}

        monkeypatch.setattr(disarm_mod, "device_serial", lambda: "S-DISARM")
        monkeypatch.setattr(disarm_mod, "_wait_device_online", lambda t: True)
        monkeypatch.setattr(
            disarm_mod, "stop_task", lambda force=True, **kwargs: calls.__setitem__("stop", calls["stop"] + 1)
        )
        monkeypatch.setattr(disarm_mod, "_detect_platform", lambda: "MTK")
        monkeypatch.setattr(disarm_mod, "_detect_model", lambda: "MLD_NX2")
        self._stub_identity(disarm_mod, monkeypatch)
        monkeypatch.setattr(
            disarm_mod,
            "_read_prefs_evidence",
            lambda: {
                "readback": "clear",
                "running": False,
                "auto_resume": False,
                "evidence": {"kind": "ok"},
            },
        )
        out = disarm_mod._run({"mode": "disarm", "wait_device_online_seconds": 30})
        assert calls["stop"] == 1
        assert out["disarm_result"] == "executed_unverified"
        assert out["mode"] == "disarm"

    def test_probe_does_not_stop(self, disarm_mod, monkeypatch):
        calls = {"stop": 0}
        monkeypatch.setattr(disarm_mod, "device_serial", lambda: "S-PROBE")
        monkeypatch.setattr(disarm_mod, "device_online", lambda: True)
        monkeypatch.setattr(
            disarm_mod, "stop_task", lambda force=True, **kwargs: calls.__setitem__("stop", calls["stop"] + 1)
        )
        monkeypatch.setattr(disarm_mod, "_detect_platform", lambda: "MTK")
        monkeypatch.setattr(disarm_mod, "_detect_model", lambda: "MLD_LX2")
        self._stub_identity(disarm_mod, monkeypatch)
        monkeypatch.setattr(
            disarm_mod,
            "_read_prefs_evidence",
            lambda: {
                "readback": "clear",
                "running": False,
                "auto_resume": False,
                "evidence": {"kind": "ok"},
            },
        )
        disarm_mod._run({"mode": "probe"})
        assert calls["stop"] == 0


class TestFinishNoDisarm:
    def test_finish_run_does_not_call_stop_task(self, finish_mod, monkeypatch, tmp_path):
        """§4 U5：powercycle_finish 1.1.0 不改 prefs / 不停服务。"""
        calls = {"stop": 0}
        monkeypatch.setattr(finish_mod, "device_serial", lambda: "S-FIN")
        monkeypatch.setattr(finish_mod, "_wait_device_online", lambda t: True)

        def boom_stop(**kwargs):
            calls["stop"] += 1
            raise AssertionError("finish 不得调用 stop_task")

        # 即便有人再加回 import，也应被本断言抓住
        monkeypatch.setattr(finish_mod, "stop_task", boom_stop, raising=False)

        local = tmp_path / "powercycle_result.txt"
        local.write_bytes(b"cycle 1/10 start\nfinished result=PASS\n")
        monkeypatch.setattr(finish_mod, "_pull_result_file", lambda: local)
        monkeypatch.setattr(finish_mod, "results_dir", lambda project: tmp_path / "r")

        out = finish_mod._run({"collect_attempts": 1, "collect_retry_wait_seconds": 5})
        assert calls["stop"] == 0
        assert out["metrics"]["final_status"] == "PASS"

    def test_finish_source_has_no_stop_task_call(self):
        from tools.dev.source_anchor import SourceGuard

        guard = (
            SourceGuard.of_repo_path(
                "backend/agent/scripts/powercycle_finish/powercycle_finish.py"
            )
            .anchored("v1.1.0")
            .anchored("_collect_with_retry")
        )
        # 允许历史注释提及，禁止可执行调用
        guard.assert_absent(
            "stop_task(",
            why="#3730 §3.6：finish 1.1.0 不得再调用 stop_task",
        )


class TestOnboardedContract:
    def test_onboarded_power_cycle_fields(self):
        from backend.agent.contracts.device_return import ONBOARDED, FAMILY_STEPS

        spec = ONBOARDED["power-cycle"]
        assert spec["disarm_script"] == "powercycle_disarm"
        assert spec["disarm_min_version"] == "1.0.8"
        assert spec["finish_min_version"] == "1.1.0"
        assert "powercycle_disarm" in spec["min_wall_clock_seconds"]
        assert "powercycle_finish" in spec["min_wall_clock_seconds"]
        assert FAMILY_STEPS["power-cycle"]["disarm_onboarded"] == ("powercycle_disarm",)
        # 真机 pending → 不得登记已验证类别
        assert spec["verified_device_classes"] == []

    def test_min_wall_clock_formulas(self):
        from backend.agent.contracts.device_return import ONBOARDED

        mw = ONBOARDED["power-cycle"]["min_wall_clock_seconds"]
        assert mw["powercycle_disarm"]({"wait_device_online_seconds": 600}) == 660
        assert mw["powercycle_finish"]({
            "collect_attempts": 3, "collect_retry_wait_seconds": 120,
        }) == 420
