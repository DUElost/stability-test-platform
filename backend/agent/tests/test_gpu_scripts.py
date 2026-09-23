# -*- coding: utf-8 -*-
"""GPU 脚本侧单元测试（backend/agent/scripts/gpu_*，issue #462 P0c）。

加载方式：importlib + sys.path 注入（对齐 test_sleep_scripts.py 先例，
同样在加载前后清 ``sys.modules['_lib']`` 缓存，避免与 mtbf/sleep/powercycle 家族串库）。
golden fixture：fixtures/gpu/test_log.txt（含 instrument 原文 + 平台标记行）。
"""
from __future__ import annotations

import importlib.util
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
    return _load("gpu_lib", "gpu_setup/_lib.py")


@pytest.fixture(scope="module")
def lib_v101():
    """gpu v1.0.1：进程检测改 pgrep -f + bracket 防自匹配（冒烟发现 ④）。"""
    return _load("gpu_lib_v101", "gpu_setup/_lib.py")


@pytest.fixture(scope="module")
def setup_mod():
    return _load("gpu_setup_mod", "gpu_setup/gpu_setup.py")


@pytest.fixture(scope="module")
def check_mod():
    return _load("gpu_check_mod", "gpu_check/gpu_check.py")


@pytest.fixture(scope="module")
def check_mod_v102():
    """gpu_check v1.0.2：bytes 读取二进制日志（冒烟发现 ⑤）。"""
    return _load("gpu_check_mod_v102", "gpu_check/gpu_check.py")


@pytest.fixture(scope="module")
def finish_mod():
    return _load("gpu_finish_mod", "gpu_finish/gpu_finish.py")


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




# ---------------------------------------------------------------------------
# gpu_check：存活/进度/自然收尾
# ---------------------------------------------------------------------------






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




# ---------------------------------------------------------------------------
# gpu_finish：停止 + 拉取 + 解析 + 落盘
# ---------------------------------------------------------------------------


class TestFinish:


    def test_pull_missing_raises(self, finish_mod, monkeypatch):
        monkeypatch.setattr(finish_mod, "result_log_bytes", lambda: 0)
        with pytest.raises(RuntimeError) as ei:
            finish_mod._pull_result_log()
        assert "test_log.txt" in str(ei.value)


# ── v1.2.1 _install_apk_stable：push 输出保留 + 退避重试（#2756） ─────────────


class TestInstallApkStableV121:
    """run 431 同族取证：push 失败输出被丢弃 + 立即重试仍在风暴内。v1.2.1 修复。"""

    @pytest.fixture()
    def v121(self, monkeypatch):
        mod = _load("gpu_lib_v121", "gpu_setup/_lib.py")
        import contextlib

        @contextlib.contextmanager
        def _no_heartbeat(phase, *, interval=None):
            yield

        monkeypatch.setattr(mod, "progress_heartbeat", _no_heartbeat)
        return mod

    @staticmethod
    def _patch_adb(monkeypatch, mod, calls, results):
        def fake_adb(*args, timeout=60):
            calls.append(list(args))
            return results[min(len(calls) - 1, len(results) - 1)]

        monkeypatch.setattr(mod, "adb", fake_adb)
        monkeypatch.setattr(mod, "adb_shell", lambda *a, timeout=60: "")

    def test_push_failure_reason_preserved(self, v121, monkeypatch):
        calls: list = []
        self._patch_adb(monkeypatch, v121, calls, [(255, "", "device offline")])
        sleeps: list = []
        monkeypatch.setattr(v121.time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.delenv("STP_GPU_INSTALL_RETRY_BACKOFF_SECONDS", raising=False)

        rc, out = v121._install_apk_stable(Path("/res/GpuLite.apk"))

        assert rc == 255
        assert "push failed (rc=255)" in out
        assert "device offline" in out
        assert ["wait-for-device"] in calls
        assert sleeps == [10.0]

    def test_transient_failure_recovers(self, v121, monkeypatch):
        calls: list = []
        self._patch_adb(
            monkeypatch, v121, calls,
            [(1, "", "error"), (0, "", ""), (0, "Success", "")],
        )
        monkeypatch.setattr(v121.time, "sleep", lambda s: None)

        rc, out = v121._install_apk_stable(Path("/res/GpuLite.apk"))
        assert rc == 0 and "Success" in out
