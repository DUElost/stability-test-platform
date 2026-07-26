"""#73: SoC 平台判定与归一化。

实测样本(2026-07-26,生产 20 台 host 逐机型采样)作为回归基线:
  Z2581/Z2582  → Spreadtrum / ums9230  → UNISOC(无 /data/aee_exp)
  DAM-M500     → Mediatek   / mt6768   → MTK
  ELA-LX2/LX3  → Mediatek   / mt6768   → MTK
  MLD-LX3      → Mediatek   / mt6768   → MTK
"""
import subprocess
from unittest.mock import patch

import pytest

from backend.agent.device_platform import (
    PLATFORM_MTK,
    PLATFORM_QCOM,
    PLATFORM_UNISOC,
    PLATFORM_UNKNOWN,
    clear_platform_cache,
    detect_device_platform,
    normalize_platform,
    parse_platform_props,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_platform_cache()
    yield
    clear_platform_cache()


class TestNormalizePlatform:
    @pytest.mark.parametrize("soc,expected", [
        ("Spreadtrum", PLATFORM_UNISOC),
        ("UNISOC", PLATFORM_UNISOC),
        ("unisoc", PLATFORM_UNISOC),
        ("Mediatek", PLATFORM_MTK),
        ("MediaTek Inc.", PLATFORM_MTK),
        ("MTK", PLATFORM_MTK),
        ("Qualcomm", PLATFORM_QCOM),
        ("QTI", PLATFORM_QCOM),
    ])
    def test_soc_manufacturer_wins(self, soc, expected):
        # board 给一个矛盾值,验证 soc.manufacturer 优先级更高
        assert normalize_platform(soc_manufacturer=soc, board_platform="mt6768") == expected

    @pytest.mark.parametrize("board,expected", [
        ("ums9230", PLATFORM_UNISOC),
        ("sc9863a", PLATFORM_UNISOC),
        ("mt6768", PLATFORM_MTK),
        ("mt6789", PLATFORM_MTK),
        ("msm8998", PLATFORM_QCOM),
        ("sm8550", PLATFORM_QCOM),
        ("kona", PLATFORM_QCOM),
        ("lahaina", PLATFORM_QCOM),
    ])
    def test_board_prefix_fallback(self, board, expected):
        assert normalize_platform(board_platform=board) == expected

    def test_hardware_fallback_when_board_empty(self):
        assert normalize_platform(board_platform="", hardware="ums9230_6h10") == PLATFORM_UNISOC

    @pytest.mark.parametrize("args", [
        {},
        {"soc_manufacturer": ""},
        {"soc_manufacturer": "   "},
        {"board_platform": "exynos9820"},   # 三星:当前设备池无实例,判不出来
        {"soc_manufacturer": None, "board_platform": None, "hardware": None},
    ])
    def test_unknown_never_none(self, args):
        """判不出来必须是 UNKNOWN 而不是 None — 否则无法区分「没采集」。"""
        assert normalize_platform(**args) == PLATFORM_UNKNOWN


class TestParsePlatformProps:
    def test_parses_three_line_output(self):
        assert parse_platform_props("Spreadtrum\nums9230\nums9230_6h10\n") == PLATFORM_UNISOC

    def test_parses_mtk_sample(self):
        assert parse_platform_props("Mediatek\nmt6768\nmt6768\n") == PLATFORM_MTK

    def test_ignores_leading_shell_banner(self):
        noisy = "WARNING: linker: something\nMediatek\nmt6768\nmt6768\n"
        assert parse_platform_props(noisy) == PLATFORM_MTK

    def test_partial_output_does_not_crash(self):
        assert parse_platform_props("Mediatek\n") == PLATFORM_MTK
        assert parse_platform_props("") == PLATFORM_UNKNOWN


class TestDetectDevicePlatform:
    def _run_result(self, stdout, returncode=0):
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")

    def test_single_adb_call_for_all_props(self):
        with patch("backend.agent.device_platform.subprocess.run") as run:
            run.return_value = self._run_result("Mediatek\nmt6768\nmt6768\n")
            assert detect_device_platform("adb", "SERIAL1") == PLATFORM_MTK
            assert run.call_count == 1
            cmd = run.call_args[0][0]
            assert cmd[:4] == ["adb", "-s", "SERIAL1", "shell"]
            # 三个 getprop 合并进一条 shell 命令
            assert cmd[4].count("getprop") == 3

    def test_result_is_cached_per_serial(self):
        with patch("backend.agent.device_platform.subprocess.run") as run:
            run.return_value = self._run_result("Spreadtrum\nums9230\nums9230_6h10\n")
            assert detect_device_platform("adb", "SERIAL2") == PLATFORM_UNISOC
            assert detect_device_platform("adb", "SERIAL2") == PLATFORM_UNISOC
            assert run.call_count == 1

    def test_unknown_is_not_cached(self):
        """探测失败不该被缓存 — 否则一次 adb 抖动永久钉死为 UNKNOWN。"""
        with patch("backend.agent.device_platform.subprocess.run") as run:
            run.return_value = self._run_result("", returncode=1)
            assert detect_device_platform("adb", "SERIAL3") == PLATFORM_UNKNOWN
            run.return_value = self._run_result("Mediatek\nmt6768\nmt6768\n")
            assert detect_device_platform("adb", "SERIAL3") == PLATFORM_MTK

    def test_timeout_returns_unknown_not_raise(self):
        with patch("backend.agent.device_platform.subprocess.run") as run:
            run.side_effect = subprocess.TimeoutExpired(cmd="adb", timeout=5)
            assert detect_device_platform("adb", "SERIAL4") == PLATFORM_UNKNOWN

    def test_generic_exception_returns_unknown(self):
        with patch("backend.agent.device_platform.subprocess.run") as run:
            run.side_effect = OSError("adb not found")
            assert detect_device_platform("adb", "SERIAL5") == PLATFORM_UNKNOWN
