"""GPU/PowerCycle/Sleep setup 的资源目录透传（config 规范化丢键修复）。

2026-08-31 实证：gpu_config/powercycle_config/sleep_config 只保留已知
业务键，resources_dir 键被丢弃 → STP_STEP_PARAMS 里传的
gpu_resources_dir 等失效，脚本回落默认路径（host 本地资源缺失报错）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load(name: str, rel: str):
    script_dir = Path(__file__).resolve().parents[2] / rel
    sys.path.insert(0, str(script_dir))
    spec = importlib.util.spec_from_file_location(name, script_dir / "_lib.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


gpu = _load("gpu_lib_v104", "agent/scripts/gpu_setup/v1.0.4")
power = _load("power_lib_v101", "agent/scripts/powercycle_setup/v1.0.1")
sleep = _load("sleep_lib_v101", "agent/scripts/sleep_setup/v1.0.1")


def test_gpu_config_passthrough_resources_dir(monkeypatch):
    monkeypatch.delenv("STP_GPU_RESOURCES_DIR", raising=False)
    cfg = gpu.gpu_config({"project": "gpu10min", "rounds": 5,
                          "gpu_resources_dir": "/mnt/stp-aee/gpu"})
    assert cfg["gpu_resources_dir"] == "/mnt/stp-aee/gpu"
    assert gpu.resources_dir(cfg) == Path("/mnt/stp-aee/gpu/gpu10min")


def test_gpu_config_env_fallback(monkeypatch):
    monkeypatch.setenv("STP_GPU_RESOURCES_DIR", "/nfs/gpu")
    cfg = gpu.gpu_config({"project": "p"})
    assert cfg["gpu_resources_dir"] == "/nfs/gpu"


def test_powercycle_config_passthrough(monkeypatch):
    monkeypatch.delenv("STP_POWER_CYCLE_RESOURCES_DIR", raising=False)
    cfg = power.powercycle_config({"project": "p",
                                   "powercycle_resources_dir": "/mnt/stp-aee/resources/power-cycle"})
    assert cfg["powercycle_resources_dir"] == "/mnt/stp-aee/resources/power-cycle"
    assert power.resources_dir(cfg) == Path("/mnt/stp-aee/resources/power-cycle/p")


def test_sleep_config_passthrough(monkeypatch):
    monkeypatch.delenv("STP_SLEEP_RESOURCES_DIR", raising=False)
    cfg = sleep.sleep_config({"project": "p",
                              "sleep_resources_dir": "/mnt/stp-aee/resources/sleep"})
    assert cfg["sleep_resources_dir"] == "/mnt/stp-aee/resources/sleep"
    assert sleep.resources_dir(cfg) == Path("/mnt/stp-aee/resources/sleep/p")


def test_gpu_check_no_tests_is_failure(monkeypatch):
    """v1.0.3：GPU_RUN_END 但 OK (0 tests) = 空跑显式失败（2026-08-31 实证）。"""
    import importlib.util
    gc_dir = str(Path(__file__).resolve().parents[2] / "agent/scripts/gpu_check/v1.0.3")
    sys.path.insert(0, gc_dir)  # 确保 gpu_check 的 _lib 优先（防 sys.path 污染）
    spec = importlib.util.spec_from_file_location(
        "gpu_check_v103", gc_dir + "/gpu_check.py")
    gc = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(gc)
    # 0 tests 的 test_log
    monkeypatch.setattr(gc, "_read_log_cat",
                        lambda: b"GPU_RUN_START test_id=001 rounds=2\nOK (0 tests)\nGPU_RUN_END rc=0\n")
    assert gc._run_finished() == (True, "no-tests")
    # 真实完成
    monkeypatch.setattr(gc, "_read_log_cat",
                        lambda: b"GPU_RUN_START test_id=001 rounds=2\nOK (1 test)\nGPU_ROUND 1 rc=0\nGPU_RUN_END rc=0\n")
    assert gc._run_finished() == (True, "ok")



def test_gpu_install_apk_stable_uses_push_pm(monkeypatch):
    """v1.0.4：大 APK（378MB Lite）流式安装不稳定——push + pm install 设备本地。"""
    import tempfile
    calls = []

    def fake_adb(*args, timeout=30):
        calls.append(args)
        if args[0] == "push":
            return 0, "1 file pushed", ""
        if args[0] == "shell" and args[1].startswith("pm install"):
            return 0, "Success", ""
        if args[0] == "shell" and args[1].startswith("rm "):
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(gpu, "adb", fake_adb)
    with tempfile.NamedTemporaryFile(suffix=".apk") as f:
        rc, out = gpu._install_apk_stable(Path(f.name))
    assert rc == 0 and "Success" in out
    assert any(c[0] == "push" for c in calls)
    assert any(c[0] == "shell" and "pm install" in c[1] for c in calls)
