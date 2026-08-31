# -*- coding: utf-8 -*-
"""GPU 脚本侧单元测试（backend/agent/scripts/gpu_*，issue #462 P0c）。

加载方式：importlib + sys.path 注入（对齐 test_sleep_scripts.py 先例，
同样在加载前后清 ``sys.modules['_lib']`` 缓存，避免与 mtbf/sleep/powercycle 家族串库）。
golden fixture：fixtures/gpu/test_log.txt（含 instrument 原文 + 平台标记行）。
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gpu"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("_lib", None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_lib", None)
        sys.path.remove(str(path.parent))


@pytest.fixture(scope="module")
def lib():
    return _load("gpu_lib", "gpu_setup/v1.0.0/_lib.py")


@pytest.fixture(scope="module")
def lib_v101():
    """gpu v1.0.1：进程检测改 pgrep -f + bracket 防自匹配（冒烟发现 ④）。"""
    return _load("gpu_lib_v101", "gpu_setup/v1.0.1/_lib.py")


@pytest.fixture(scope="module")
def setup_mod():
    return _load("gpu_setup_mod", "gpu_setup/v1.0.0/gpu_setup.py")


@pytest.fixture(scope="module")
def check_mod():
    return _load("gpu_check_mod", "gpu_check/v1.0.0/gpu_check.py")


@pytest.fixture(scope="module")
def check_mod_v102():
    """gpu_check v1.0.2：bytes 读取二进制日志（冒烟发现 ⑤）。"""
    return _load("gpu_check_mod_v102", "gpu_check/v1.0.2/gpu_check.py")


@pytest.fixture(scope="module")
def finish_mod():
    return _load("gpu_finish_mod", "gpu_finish/v1.0.0/gpu_finish.py")


@pytest.fixture()
def golden_log() -> str:
    return (_FIXTURES / "test_log.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# test_log.txt 标记解析（G15 D1：平台自产标记行；instrument 输出原文备查）
# ---------------------------------------------------------------------------


class TestParseGpuLog:
    def test_golden_summary(self, lib, golden_log):
        parsed = lib.parse_gpu_log(golden_log)
        assert parsed["started"] is True
        assert parsed["test_id"] == "002"
        assert parsed["expected_rounds"] == 3
        assert parsed["rounds_done"] == 3
        assert parsed["failed_rounds"] == 1        # rc=-3 那一轮
        assert parsed["end_rc"] == 0

    def test_golden_rounds(self, lib, golden_log):
        parsed = lib.parse_gpu_log(golden_log)
        assert parsed["rounds"] == [
            {"round": 1, "rc": 0},
            {"round": 2, "rc": 0},
            {"round": 3, "rc": -3},
        ]

    def test_instrument_lines_ignored(self, lib, golden_log):
        """原文行（INSTRUMENTATION_STATUS 等）不进入 rounds，只解析标记行。"""
        parsed = lib.parse_gpu_log(golden_log)
        assert len(parsed["rounds"]) == 3

    def test_incomplete_no_end(self, lib):
        content = "GPU_RUN_START test_id=001 rounds=5\nGPU_ROUND 1 rc=0\nGPU_ROUND 2 rc=0\n"
        parsed = lib.parse_gpu_log(content)
        assert parsed["rounds_done"] == 2
        assert parsed["expected_rounds"] == 5
        assert parsed["end_rc"] is None
        assert parsed["failed_rounds"] == 0

    def test_empty(self, lib):
        parsed = lib.parse_gpu_log("")
        assert parsed["started"] is False
        assert parsed["rounds_done"] == 0
        assert parsed["end_rc"] is None

    def test_marker_prefix_must_be_line_start(self, lib):
        """'GPU_ROUND' 作为行首锚定——instrument 输出若含该词不被误算。"""
        content = "GPU_RUN_START test_id=001 rounds=1\nxGPU_ROUND 1 rc=0\nGPU_ROUND 1 rc=0\n"
        parsed = lib.parse_gpu_log(content)
        assert parsed["rounds_done"] == 1


# ---------------------------------------------------------------------------
# RAM 分版（runAll----20260228.bat 直移）
# ---------------------------------------------------------------------------


class TestRamDetection:
    def test_ddrsize_g(self, lib, monkeypatch):
        monkeypatch.setattr(lib, "adb_shell", lambda cmd, timeout=15: "8G\n" if "ddrsize" in cmd else "")
        assert lib.detect_ram_gb() == 8.0

    def test_ddrsize_m(self, lib, monkeypatch):
        monkeypatch.setattr(lib, "adb_shell", lambda cmd, timeout=15: "4096M\n" if "ddrsize" in cmd else "")
        assert lib.detect_ram_gb() == 4.0

    def test_meminfo_fallback(self, lib, monkeypatch):
        def fake_shell(cmd, timeout=15):
            if "ddrsize" in cmd:
                return ""
            return "MemTotal:        16777216 kB\n"
        monkeypatch.setattr(lib, "adb_shell", fake_shell)
        # (16777216 + 524288) // 1048576 = 16.5 → 16
        assert lib.detect_ram_gb() == 16.0

    def test_unreadable_returns_none(self, lib, monkeypatch):
        monkeypatch.setattr(lib, "adb_shell", lambda cmd, timeout=15: "")
        assert lib.detect_ram_gb() is None


class TestSelectVariant:
    def test_under_threshold_lite(self, lib):
        variant, meta = lib.select_variant(8.0, 8)
        assert variant == "Antutu_v10_Lite"
        assert meta["test_id"] == "002"
        assert meta["antutu_pkg"] == "com.antutu.benchmark.full.lite"

    def test_above_threshold_full(self, lib):
        variant, meta = lib.select_variant(16.0, 8)
        assert variant == "Antutu_v10"
        assert meta["test_id"] == "001"
        assert meta["antutu_pkg"] == "com.antutu.benchmark.full"

    def test_none_raises(self, lib):
        with pytest.raises(RuntimeError) as ei:
            lib.select_variant(None, 8)
        assert "RAM" in str(ei.value)


# ---------------------------------------------------------------------------
# 配置层级 + ini 解析
# ---------------------------------------------------------------------------


class TestGpuConfig:
    def test_ini_parse(self, lib):
        assert lib.parse_ini("; comment\nlite_max_gb=8\n") == {"lite_max_gb": "8"}

    def test_defaults(self, lib, monkeypatch):
        monkeypatch.setattr(lib, "read_ini", lambda project: {})
        for key in ("STP_GPU_LITE_MAX_GB", "STP_GPU_ROUNDS", "STP_GPU_INSTALL_APKS", "STP_GPU_PROJECT"):
            monkeypatch.delenv(key, raising=False)
        cfg = lib.gpu_config({})
        assert cfg["lite_max_gb"] == 8
        assert cfg["rounds"] == 700
        assert cfg["install_apks"] is True
        assert cfg["project"] == "legacy"

    def test_params_win(self, lib, monkeypatch):
        monkeypatch.setattr(lib, "read_ini", lambda project: {"lite_max_gb": "16"})
        monkeypatch.setenv("STP_GPU_LITE_MAX_GB", "12")
        assert lib.gpu_config({"lite_max_gb": 4})["lite_max_gb"] == 4

    def test_env_over_ini(self, lib, monkeypatch):
        monkeypatch.setattr(lib, "read_ini", lambda project: {"lite_max_gb": "16"})
        monkeypatch.setenv("STP_GPU_LITE_MAX_GB", "12")
        assert lib.gpu_config({})["lite_max_gb"] == 12

    def test_ini_fallback(self, lib, monkeypatch):
        monkeypatch.setattr(lib, "read_ini", lambda project: {"lite_max_gb": "16"})
        monkeypatch.delenv("STP_GPU_LITE_MAX_GB", raising=False)
        assert lib.gpu_config({})["lite_max_gb"] == 16


# ---------------------------------------------------------------------------
# gpu_setup：fail-fast（variant 目录缺失）
# ---------------------------------------------------------------------------


class TestSetupFailFast:
    def test_missing_variant_dir_raises(self, setup_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(setup_mod, "gpu_config", lambda cfg: {"project": "legacy", "lite_max_gb": 8})
        monkeypatch.setattr(setup_mod, "resources_dir", lambda cfg: tmp_path)
        monkeypatch.setattr(setup_mod, "detect_ram_gb", lambda: 16.0)
        monkeypatch.setattr(setup_mod, "select_variant", lambda ram, lite: ("Antutu_v10", {"test_id": "001"}))
        with pytest.raises(FileNotFoundError) as ei:
            setup_mod._run({})
        assert "Antutu_v10" in str(ei.value)

    def test_ram_unresolvable_raises(self, setup_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(setup_mod, "gpu_config", lambda cfg: {"project": "legacy", "lite_max_gb": 8})
        monkeypatch.setattr(setup_mod, "resources_dir", lambda cfg: tmp_path)
        monkeypatch.setattr(setup_mod, "detect_ram_gb", lambda: None)
        with pytest.raises(RuntimeError) as ei:
            setup_mod._run({})
        assert "RAM" in str(ei.value)


# ---------------------------------------------------------------------------
# gpu_check：存活/进度/自然收尾
# ---------------------------------------------------------------------------


class TestCheckV102BinaryLog:
    def test_run_finished_binary_tolerant(self, check_mod_v102, monkeypatch):
        """test_log.txt 含二进制 protobuf 输出——bytes 模式读取不抛解码错误。"""
        monkeypatch.setattr(check_mod_v102, "device_serial", lambda: "S1")

        class FakeResult:
            stdout = b"\xf9\x01\x02\x00\x0c\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00GPU_RUN_START test_id=002 rounds=2\nGPU_RUN_END rc=0\n"

        monkeypatch.setattr(check_mod_v102.subprocess, "run", lambda *a, **k: FakeResult())
        assert check_mod_v102._run_finished() is True

    def test_run_finished_binary_without_marker(self, check_mod_v102, monkeypatch):
        monkeypatch.setattr(check_mod_v102, "device_serial", lambda: "S1")

        class FakeResult:
            stdout = b"\xf9\x01\x02\x00\x0c\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00GPU_RUN_START test_id=002 rounds=2\n"

        monkeypatch.setattr(check_mod_v102.subprocess, "run", lambda *a, **k: FakeResult())
        assert check_mod_v102._run_finished() is False


class TestCheck:
    def _patch_device_io(self, mod, monkeypatch, tmp_path, alive=True, done=3, log_bytes=4096,
                         finished=False):
        monkeypatch.setattr(mod, "device_serial", lambda: "S1")
        monkeypatch.setattr(mod, "_state_file", lambda: tmp_path / "state.json")
        monkeypatch.setattr(mod, "instrument_alive", lambda: alive)
        monkeypatch.setattr(mod, "_grep_rounds_done", lambda: done)
        monkeypatch.setattr(mod, "result_log_bytes", lambda: log_bytes)
        monkeypatch.setattr(mod, "_run_finished", lambda: finished)
        monkeypatch.setattr(mod, "progress_stamp", lambda payload: None)

    def test_running_progress(self, check_mod, monkeypatch, tmp_path):
        self._patch_device_io(check_mod, monkeypatch, tmp_path)
        r = check_mod._run({})
        assert r["success"] is True
        assert r["progress"]["rounds_done"] == 3
        assert r["progress"]["instrument_alive"] is True
        assert r["progress"]["run_finished"] is False

    def test_finished_reports_completion(self, check_mod, monkeypatch, tmp_path):
        self._patch_device_io(check_mod, monkeypatch, tmp_path, alive=False, finished=True)
        r = check_mod._run({})
        assert r["success"] is True
        assert r["progress"]["run_finished"] is True

    def test_dead_streak_grace(self, check_mod, monkeypatch, tmp_path):
        self._patch_device_io(check_mod, monkeypatch, tmp_path, alive=False)
        r1 = check_mod._run({"dead_grace_cycles": 2})
        assert r1["success"] is True
        r2 = check_mod._run({"dead_grace_cycles": 2})
        assert r2["success"] is False
        assert "连续 2 个周期" in r2["error_message"]

    def test_injected_expected_rounds(self, check_mod, monkeypatch, tmp_path):
        self._patch_device_io(check_mod, monkeypatch, tmp_path)
        r = check_mod._run({"expected_rounds": 2000})
        assert r["progress"]["expected_rounds"] == 2000


# ---------------------------------------------------------------------------
# gpu v1.0.1 进程检测（冒烟发现 ④：ps -A 截断 args + pkill/pgrep 自匹配）
# ---------------------------------------------------------------------------


class TestV101ProcessDetection:
    def test_pattern_does_not_self_match(self, lib_v101):
        """bracket 技巧：pattern 文本不匹配自身（adb shell 命令行含 pattern 串，
        不排除会恒真/自杀）。"""
        cmdline = f"sh -c pgrep -f '{lib_v101._INSTRUMENT_PGREP_PATTERN}'"
        assert re.search(lib_v101._INSTRUMENT_PGREP_PATTERN, cmdline) is None

    def test_instrument_alive_with_pids(self, lib_v101, monkeypatch):
        monkeypatch.setattr(lib_v101, "adb_shell", lambda cmd, timeout=30: "7382\n7386\n")
        assert lib_v101.instrument_alive() is True

    def test_instrument_alive_empty(self, lib_v101, monkeypatch):
        monkeypatch.setattr(lib_v101, "adb_shell", lambda cmd, timeout=30: "")
        assert lib_v101.instrument_alive() is False

    def test_stop_stress_uses_bracket_pattern(self, lib_v101, monkeypatch):
        """v1.0.0 的 pkill -f 会杀掉自身 shell（命令行含 pattern），后续 force-stop 不执行。"""
        calls = []
        monkeypatch.setattr(lib_v101, "adb_shell", lambda cmd, timeout=30: calls.append(cmd) or "")
        lib_v101.stop_stress()
        pkills = [c for c in calls if c.startswith("pkill")]
        assert len(pkills) == 1
        assert "[g]pu_stress_loop" in pkills[0]
        assert "[A]ndroidJUnitRunner" in pkills[0]
        # force-stop 顺序执行（v1.0.0 会在 pkill 处自杀导致后面的 force-stop 丢失）
        assert calls[0].startswith("am force-stop")
        assert sum(1 for c in calls if c.startswith("am force-stop")) == 4


# ---------------------------------------------------------------------------
# gpu_finish v1.0.1：run_id 设备维度（验收发现⑨）
# ---------------------------------------------------------------------------


class TestFinishV101RunId:
    def test_run_id_has_serial(self, monkeypatch, tmp_path):
        mod = _load("gpu_finish_mod_v102", "gpu_finish/v1.0.2/gpu_finish.py")
        monkeypatch.setattr(mod, "device_serial", lambda: "GPU-S9")
        monkeypatch.setattr(mod, "stop_stress", lambda: None)
        monkeypatch.setattr(mod.time, "sleep", lambda _: None)

        def fake_pull():
            local = tmp_path / "test_log.txt"
            local.write_text("GPU_RUN_START test_id=001 rounds=1\nGPU_ROUND 1 rc=0\n", encoding="utf-8")
            return local

        monkeypatch.setattr(mod, "_pull_result_log", fake_pull)
        monkeypatch.setattr(mod, "results_dir", lambda project: tmp_path / "r")
        out = mod._run({})
        assert out["metrics"]["run_id"].endswith("_GPU-S9")


# ---------------------------------------------------------------------------
# gpu_finish：停止 + 拉取 + 解析 + 落盘
# ---------------------------------------------------------------------------


class TestFinish:
    def test_run_writes_detail_json(self, finish_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(finish_mod, "device_serial", lambda: "GPU-S1")
        monkeypatch.setattr(finish_mod, "stop_stress", lambda: None)
        monkeypatch.setattr(finish_mod.time, "sleep", lambda _: None)

        def fake_pull():
            local = tmp_path / "test_log.txt"
            local.write_text(
                "GPU_RUN_START test_id=001 rounds=2\n"
                "GPU_ROUND 1 rc=0\n"
                "GPU_ROUND 2 rc=0\n"
                "GPU_RUN_END rc=0\n",
                encoding="utf-8",
            )
            return local

        monkeypatch.setattr(finish_mod, "_pull_result_log", fake_pull)
        results = tmp_path / "nfs" / "gpu" / "legacy" / "results"
        monkeypatch.setattr(finish_mod, "results_dir", lambda project: results)

        out = finish_mod._run({"project": "legacy"})
        assert out["metrics"]["rounds_done"] == 2
        assert out["metrics"]["expected_rounds"] == 2
        assert out["metrics"]["failed_rounds"] == 0
        assert out["metrics"]["end_rc"] == 0
        assert out["metrics"]["final_status"] == "COMPLETED"
        detail = results / f"{out['metrics']['run_id']}.json"
        assert detail.is_file()
        body = json.loads(detail.read_text(encoding="utf-8"))
        assert body["metrics"]["final_status"] == "COMPLETED"
        assert body["rounds"][0] == {"round": 1, "rc": 0}

    def test_run_incomplete_marked(self, finish_mod, monkeypatch, tmp_path):
        """无 GPU_RUN_END → final_status=INCOMPLETE。"""
        monkeypatch.setattr(finish_mod, "device_serial", lambda: "GPU-S2")
        monkeypatch.setattr(finish_mod, "stop_stress", lambda: None)
        monkeypatch.setattr(finish_mod.time, "sleep", lambda _: None)

        def fake_pull():
            local = tmp_path / "test_log.txt"
            local.write_text("GPU_RUN_START test_id=001 rounds=5\nGPU_ROUND 1 rc=0\n", encoding="utf-8")
            return local

        monkeypatch.setattr(finish_mod, "_pull_result_log", fake_pull)
        monkeypatch.setattr(finish_mod, "results_dir", lambda project: tmp_path / "r")
        out = finish_mod._run({})
        assert out["metrics"]["final_status"] == "INCOMPLETE"
        assert out["metrics"]["end_rc"] is None

    def test_pull_missing_raises(self, finish_mod, monkeypatch):
        monkeypatch.setattr(finish_mod, "result_log_bytes", lambda: 0)
        with pytest.raises(RuntimeError) as ei:
            finish_mod._pull_result_log()
        assert "test_log.txt" in str(ei.value)
