"""GPU/PowerCycle/Sleep setup 的资源目录透传（config 规范化丢键修复）。

2026-08-31 实证：gpu_config/powercycle_config/sleep_config 只保留已知
业务键，resources_dir 键被丢弃 → STP_STEP_PARAMS 里传的
gpu_resources_dir 等失效，脚本回落默认路径（host 本地资源缺失报错）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


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


def _load_gpu_check_104():
    import importlib.util
    gc_dir = str(Path(__file__).resolve().parents[2] / "agent/scripts/gpu_check/v1.0.4")
    sys.path.insert(0, gc_dir)
    spec = importlib.util.spec_from_file_location("gpu_check_v104", gc_dir + "/gpu_check.py")
    gc = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(gc)
    return gc


def test_gpu_check_v104_monitor_mode_ok(monkeypatch):
    """v1.0.4：-m monitor 模式只输出 protobuf（无 OK 文本），test_result=true = 正常完成。"""
    gc = _load_gpu_check_104()
    # protobuf 完成：test_result string "true" + testcase_name（2026-09-01 真机实证形态）
    log = (
        b"GPU_RUN_START test_id=002 rounds=10\n"
        b"\x00\x01\x18\x02\"\x01\x01\x0a\x00\x00\x01\x00\x01\x00\x01\x01"
        b"test_result\x12\x05true\x0ftestcase_name\x12\x1etest_StressSpecial_GPUTest_002"
        b"GPU_ROUND 1 rc=0\nGPU_RUN_END rc=0\n"
    )
    monkeypatch.setattr(gc, "_read_log_cat", lambda: log)
    assert gc._run_finished() == (True, "ok")


def test_gpu_check_v104_monitor_mode_crashed(monkeypatch):
    """v1.0.4：protobuf shortMsg Process crashed = instrument 崩溃（2026-09-01 全量实证）。"""
    gc = _load_gpu_check_104()
    log = (
        b"GPU_RUN_START test_id=002 rounds=10\n"
        b"shortMsg\x12\x10Process crashed.\nGPU_ROUND 2 rc=0\nGPU_RUN_END rc=0\n"
    )
    monkeypatch.setattr(gc, "_read_log_cat", lambda: log)
    assert gc._run_finished() == (True, "crashed")


def test_gpu_check_v104_ok_text_still_works(monkeypatch):
    """v1.0.4：非 monitor 文本模式判定不回归（OK (N tests) / OK (0 tests)）。"""
    gc = _load_gpu_check_104()
    monkeypatch.setattr(gc, "_read_log_cat",
                        lambda: b"GPU_RUN_START test_id=001 rounds=2\nOK (1 test)\nGPU_RUN_END rc=0\n")
    assert gc._run_finished() == (True, "ok")
    monkeypatch.setattr(gc, "_read_log_cat",
                        lambda: b"GPU_RUN_START test_id=001 rounds=2\nOK (0 tests)\nGPU_RUN_END rc=0\n")
    assert gc._run_finished() == (True, "no-tests")


def test_gpu_check_v104_running_no_end(monkeypatch):
    gc = _load_gpu_check_104()
    monkeypatch.setattr(gc, "_read_log_cat",
                        lambda: b"GPU_RUN_START test_id=002 rounds=10\n\x00\x01\x18\x02")
    assert gc._run_finished() == (False, "running")



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


def _load_setup_v102(name: str):
    """加载 sleep_setup/powercycle_setup v1.0.2 的 _lib。"""
    import importlib.util
    d = str(Path(__file__).resolve().parents[2] / f"agent/scripts/{name}/v1.0.2")
    sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location(f"{name}_lib_v102", d + "/_lib.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_sleep_setup_v102_install_uses_push_pm(monkeypatch):
    """#775：sleep_setup v1.0.2 AutoTestTool 安装改 push+pm install（流式不稳）。"""
    import tempfile
    lib = _load_setup_v102("sleep_setup")
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

    def fake_shell(*args, timeout=30):
        return 0, "", ""

    monkeypatch.setattr(lib, "adb", fake_adb)
    monkeypatch.setattr(lib, "adb_shell", fake_shell)
    with tempfile.NamedTemporaryFile(suffix=".apk") as f:
        lib.install_apk(Path(f.name))
    assert any(c[0] == "push" for c in calls)
    assert any(c[0] == "shell" and "pm install" in c[1] for c in calls)


def test_powercycle_setup_v102_install_uses_push_pm(monkeypatch):
    """#775：powercycle_setup v1.0.2 同款 push+pm install。"""
    import tempfile
    lib = _load_setup_v102("powercycle_setup")
    calls = []

    def fake_adb(*args, timeout=30):
        calls.append(args)
        if args[0] == "push":
            return 0, "pushed", ""
        if args[0] == "shell" and args[1].startswith("pm install"):
            return 0, "Success", ""
        if args[0] == "shell" and args[1].startswith("rm "):
            return 0, "", ""
        return 0, "", ""

    def fake_shell(*args, timeout=30):
        return 0, "", ""

    monkeypatch.setattr(lib, "adb", fake_adb)
    monkeypatch.setattr(lib, "adb_shell", fake_shell)
    with tempfile.NamedTemporaryFile(suffix=".apk") as f:
        lib.install_apk(Path(f.name))
    assert any(c[0] == "push" for c in calls)
    assert any(c[0] == "shell" and "pm install" in c[1] for c in calls)


def _load_lib(name: str, ver: str) -> ModuleType:
    """加载任意脚本版本的 _lib（用于 finish/setup 单测）。"""
    import importlib.util
    d = str(Path(__file__).resolve().parents[2] / f"agent/scripts/{name}/v{ver}")
    sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location(f"{name}_v{ver.replace('.', '')}", d + "/_lib.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_powercycle_finish_v103_verifies_stop_flags(monkeypatch):
    """#894：powercycle_finish v1.0.3 停测后回读验证 running=false——残留则重试并 raise。"""
    lib = _load_lib("powercycle_finish", "1.0.3")
    calls = {"set_stop_flags": 0}

    def fake_get_prefs():
        # 第一次回读残留 true（模拟写失败），重试后 false
        calls["set_stop_flags"] += 0
        return 'name="running" value="true"' if calls["set_stop_flags"] < 1 else 'name="running" value="false"'

    def fake_set_stop_flags():
        calls["set_stop_flags"] += 1

    monkeypatch.setattr(lib, "get_prefs_xml", fake_get_prefs)
    monkeypatch.setattr(lib, "set_stop_flags", fake_set_stop_flags)
    lib._verify_stop_flags()  # 重试一次后通过
    assert calls["set_stop_flags"] >= 1


def test_powercycle_finish_v103_raises_if_still_residual(monkeypatch):
    """残留无法清除（两次仍 true）→ raise（finish 报错而非假成功）。"""
    lib = _load_lib("powercycle_finish", "1.0.3")
    monkeypatch.setattr(lib, "get_prefs_xml",
                        lambda: 'name="running" value="true"')
    monkeypatch.setattr(lib, "set_stop_flags", lambda: None)
    import pytest
    with pytest.raises(RuntimeError, match="running 未置 false"):
        lib._verify_stop_flags()


def test_sleep_finish_v101_verifies_stop_flags(monkeypatch):
    lib = _load_lib("sleep_finish", "1.0.2")
    monkeypatch.setattr(lib, "get_prefs_xml",
                        lambda: 'name="running" value="false"')
    lib._verify_stop_flags()  # 直接通过


def test_monkey_setup_v236_has_att_clean_step():
    """#894：monkey_setup v2.3.6 默认 steps 含 att_clean。"""
    import importlib.util
    d = str(Path(__file__).resolve().parents[2] / "agent/scripts/monkey_setup/v2.3.6")
    sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location("monkey_setup_v236", d + "/monkey_setup.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    assert "att_clean" in mod.STEPS
    assert "att_clean" in mod.main.__defaults__[0] if mod.main.__defaults__ else True


def test_gpu_setup_v105_pre_reboot_config():
    """#774：gpu_config 透传 pre_reboot（默认 true——setup 前重启清 UiAutomation 残留）。"""
    lib = _load_lib("gpu_setup", "1.0.5")
    assert lib.gpu_config({"project": "chain"})["pre_reboot"] is True
    assert lib.gpu_config({"project": "chain", "pre_reboot": "false"})["pre_reboot"] is False


def test_gpu_check_v105_failures_verdict(monkeypatch):
    """#774 run 353 实证：JUnit FAILURES（antutu app 启动失败）→ failed 归因（非空跑）。"""
    gc = _load_gpu_check_105()
    log = (
        b"GPU_RUN_START test_id=002 rounds=10\n"
        b"1) test_StressSpecial_GPUTest_002(...)\n"
        b"java.lang.AssertionError: antutu app\n start test | restart\n"
        b"FAILURES!!!\nTests run: 1,  Failures: 1\n"
        b"GPU_ROUND 1 rc=0\nGPU_RUN_END rc=0\n"
    )
    monkeypatch.setattr(gc, "_read_log_cat", lambda: log)
    assert gc._run_finished() == (True, "failed")


def test_gpu_check_v105_crashed_still_works(monkeypatch):
    gc = _load_gpu_check_105()
    monkeypatch.setattr(gc, "_read_log_cat",
                        lambda: b"GPU_RUN_START test_id=002 rounds=1\nshortMsg Process crashed.\nGPU_RUN_END rc=0\n")
    assert gc._run_finished() == (True, "crashed")


def _load_gpu_check_105():
    import importlib.util
    d = str(Path(__file__).resolve().parents[2] / "agent/scripts/gpu_check/v1.0.5")
    sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location("gpu_check_v105", d + "/gpu_check.py")
    gc = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(gc)
    return gc


def test_gpu_setup_v106_has_settle(monkeypatch):
    """v1.0.6：reboot 后 settle（boot_completed=1 后等待 60s 默认）。"""
    import importlib.util
    d = str(Path(__file__).resolve().parents[2] / "agent/scripts/gpu_setup/v1.0.6")
    sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location("gpu_v106", d + "/gpu_setup.py")
    g = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(g)
    import inspect
    src = inspect.getsource(g._pre_reboot_device)
    assert "STP_GPU_REBOOT_SETTLE_SECONDS" in src


def test_gpu_setup_v107_install_push_fail_no_nameerror(monkeypatch):
    """#755：push 全失败时返回 (rc, msg) 而非 NameError（run 355 实证 2 台）。"""
    import tempfile
    d = str(Path(__file__).resolve().parents[2] / "agent/scripts/gpu_setup/v1.0.7")
    sys.path.insert(0, d)
    import importlib.util
    spec = importlib.util.spec_from_file_location("gpu_lib_v107", d + "/_lib.py")
    lib = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(lib)

    def fake_adb(*args, timeout=30):
        if args[0] == "push":
            return 1, "", "push failed"     # 两次 push 都失败
        return 0, "", ""

    monkeypatch.setattr(lib, "adb", fake_adb)
    with tempfile.NamedTemporaryFile(suffix=".apk") as f:
        rc, out = lib._install_apk_stable(Path(f.name))   # 不应抛 NameError
    assert rc != 0
    assert "push failed" in out


def test_gpu_setup_v107_wait_timeout_caught(monkeypatch):
    """v1.0.7：wait-for-device 超时被捕获（不抛 init 失败）。run 355 实证 2 台。"""
    import inspect
    d = str(Path(__file__).resolve().parents[2] / "agent/scripts/gpu_setup/v1.0.7")
    sys.path.insert(0, d)
    import importlib.util
    spec = importlib.util.spec_from_file_location("gpu_v107", d + "/gpu_setup.py")
    g = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(g)
    src = inspect.getsource(g._pre_reboot_device)
    assert "TimeoutExpired" in src


def test_gpu_setup_v108_dismiss_dialogs_wired():
    """#774 run 356/357 根因：v1.0.8 prepare_device 后清 Antutu 首启弹窗。"""
    d = Path(__file__).resolve().parents[2] / "agent/scripts/gpu_setup/v1.0.8"
    setup_src = (d / "gpu_setup.py").read_text(encoding="utf-8")
    lib_src = (d / "_lib.py").read_text(encoding="utf-8")
    assert "dismiss_antutu_dialogs(meta" in setup_src          # 接线
    assert "def dismiss_antutu_dialogs" in lib_src             # 实现
    assert "uiautomator dump" in lib_src and "input tap" in lib_src  # 通用清弹窗


def test_gpu_finish_v103_junit_failures_counted():
    """#774：rc=0 但 JUnit FAILURES = 假成功——v1.0.2 计入 junit_failed_rounds。"""
    d = Path(__file__).resolve().parents[2] / "agent/scripts/gpu_finish/v1.0.3"
    sys.path.insert(0, str(d))
    import importlib.util
    spec = importlib.util.spec_from_file_location("gpu_finish_lib_v103", str(d / "_lib.py"))
    lib = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(lib)
    log = (
        "GPU_RUN_START test_id=002 rounds=3\n"
        "FAILURES!!!\nTests run: 1, Failures: 1\nGPU_ROUND 1 rc=0\n"
        "FAILURES!!!\nTests run: 1, Failures: 1\nGPU_ROUND 2 rc=0\n"
        "OK (1 test)\nGPU_ROUND 3 rc=0\n"
        "GPU_RUN_END rc=0\n"
    )
    p = lib.parse_gpu_log(log)
    assert p["rounds_done"] == 3
    assert p["failed_rounds"] == 0
    assert p["junit_failed_rounds"] == 2
