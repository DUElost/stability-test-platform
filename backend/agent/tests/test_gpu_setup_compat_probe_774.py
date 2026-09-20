"""#774：gpu_setup v1.2.2 兼容边界探测（compat_probe）。

长循环前跑一轮 instrument 冒烟；命中 UiAutomation / BaseTestCase NPE 等已知签名
→ init 快速失败，避免 273/276 台把整窗烧在 APK 框架崩溃上。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
V122 = SCRIPTS / "gpu_setup" / "v1.2.2"


def _load_lib():
    sys.path.insert(0, str(V122))
    spec = importlib.util.spec_from_file_location("gpu_lib_v122_compat", V122 / "_lib.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_gpu_setup_v122_compat_probe_config_default_true():
    lib = _load_lib()
    assert lib.gpu_config({"project": "chain"})["compat_probe"] is True
    assert lib.gpu_config({"project": "chain", "compat_probe": "false"})["compat_probe"] is False
    assert lib.gpu_config({"project": "chain", "compat_probe": "true"})["compat_probe"] is True


def test_gpu_setup_v122_classify_compat_signatures():
    lib = _load_lib()
    assert (
        lib.classify_compat_failure(
            "TestRunner: java.lang.IllegalStateException: UiAutomationService already registered!"
        )
        == "uiautomation_already_registered"
    )
    assert (
        lib.classify_compat_failure(
            "java.lang.NullPointerException: Attempt to invoke virtual method "
            "'boolean androidx.test.uiautomator.UiDevice.isScreenOn()' on a null object reference\n"
            "    at com.transsion.common.BaseTestCase.tearDown(BaseTestCase.java:951)"
        )
        == "basedtestcase_uidevice_npe"
    )
    assert (
        lib.classify_compat_failure("at com.transsion.common.BaseTestCase.tearDown(BaseTestCase.java:951)")
        == "basedtestcase_teardown_npe"
    )
    assert lib.classify_compat_failure("OK (1 test)\n") is None
    assert lib.classify_compat_failure("") is None


def test_gpu_setup_v122_run_compat_probe_raises_on_signature(monkeypatch):
    lib = _load_lib()
    calls: list[str] = []

    def fake_shell(cmd: str, timeout: int = 60) -> str:
        calls.append(cmd)
        if cmd.startswith("cat "):
            return (
                "INSTRUMENTATION_STATUS: shortMsg=Process crashed.\n"
                "TestRunner: java.lang.IllegalStateException: "
                "UiAutomationService already registered!\n"
                "__STP_PROBE_RC=255\n"
            )
        return ""

    monkeypatch.setattr(lib, "adb_shell", fake_shell)
    with pytest.raises(RuntimeError, match="signature=uiautomation_already_registered"):
        lib.run_compat_probe("002", timeout_s=30)
    assert any("am force-stop" in c for c in calls)
    assert any("am instrument" in c for c in calls)
    assert any("test_StressSpecial_GPUTest_002" in c for c in calls)


def test_gpu_setup_v122_run_compat_probe_ok_passthrough(monkeypatch):
    lib = _load_lib()

    def fake_shell(cmd: str, timeout: int = 60) -> str:
        if cmd.startswith("cat "):
            return "OK (1 test)\n__STP_PROBE_RC=0\n"
        return ""

    monkeypatch.setattr(lib, "adb_shell", fake_shell)
    lib.run_compat_probe("001")  # 不抛


def test_gpu_setup_v122_wiring_in_setup_script():
    setup_src = (V122 / "gpu_setup.py").read_text(encoding="utf-8")
    lib_src = (V122 / "_lib.py").read_text(encoding="utf-8")
    assert "run_compat_probe" in setup_src
    assert "compat_probe" in setup_src
    assert "def run_compat_probe" in lib_src
    assert "STP_GPU_COMPAT_PROBE" in lib_src
