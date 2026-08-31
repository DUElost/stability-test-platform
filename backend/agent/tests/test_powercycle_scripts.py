# -*- coding: utf-8 -*-
"""PowerCycle 脚本侧单元测试（backend/agent/scripts/powercycle_*，issue #462 P0b）。

加载方式：importlib + sys.path 注入（对齐 test_sleep_scripts.py 先例，
同样在加载前后清 ``sys.modules['_lib']`` 缓存，避免与 mtbf/sleep 家族串库）。
golden fixture：fixtures/powercycle/powercycle_result.txt（设备端行格式样本）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "powercycle"
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
    return _load("powercycle_lib", "powercycle_setup/v1.0.0/_lib.py")


@pytest.fixture(scope="module")
def setup_mod():
    return _load("powercycle_setup_mod", "powercycle_setup/v1.0.0/powercycle_setup.py")


@pytest.fixture(scope="module")
def check_mod():
    return _load("powercycle_check_mod", "powercycle_check/v1.0.0/powercycle_check.py")


@pytest.fixture(scope="module")
def check_mod_v101():
    """powercycle_check v1.0.1：完成检测（同 sleep_check 冒烟发现）。"""
    return _load("powercycle_check_mod_v101", "powercycle_check/v1.0.1/powercycle_check.py")


@pytest.fixture(scope="module")
def check_mod_v102():
    """powercycle_check v1.0.2：定时收取窗口（方案 A）+ boot 转换清零（发现⑥）。"""
    return _load("powercycle_check_mod_v102", "powercycle_check/v1.0.2/powercycle_check.py")


@pytest.fixture(scope="module")
def check_mod_v103():
    """powercycle_check v1.0.3：窗口判定固定东八区（主机时区各异，实测 PDT）。"""
    return _load("powercycle_check_mod_v103", "powercycle_check/v1.0.3/powercycle_check.py")


@pytest.fixture(scope="module")
def finish_mod():
    return _load("powercycle_finish_mod", "powercycle_finish/v1.0.0/powercycle_finish.py")


@pytest.fixture(scope="module")
def finish_mod_v101():
    """powercycle_finish v1.0.1：收取前置等待设备上线（验收发现⑦）+ run_id 设备维度（⑨）。"""
    return _load("powercycle_finish_mod_v101", "powercycle_finish/v1.0.1/powercycle_finish.py")


@pytest.fixture()
def golden_result() -> bytes:
    return (_FIXTURES / "powercycle_result.txt").read_bytes()


# ---------------------------------------------------------------------------
# powercycle_result.txt 解析（纯文本行；join 键 = cycle 分子/分母）
# ---------------------------------------------------------------------------


class TestParsePowercycleResult:
    def test_golden_summary(self, lib, golden_result):
        parsed = lib.parse_powercycle_result(golden_result)
        assert parsed["cycles_done"] == 3
        assert parsed["expected_cycles"] == 100
        assert parsed["reboot_failures"] == 1
        assert parsed["final_status"] == "PASS"

    def test_golden_entries(self, lib, golden_result):
        parsed = lib.parse_powercycle_result(golden_result)
        kinds = [e["kind"] for e in parsed["entries"]]
        assert kinds.count("cycle") == 3
        assert kinds.count("reboot_failed") == 1
        assert kinds.count("finished") == 1
        failed = [e for e in parsed["entries"] if e["kind"] == "reboot_failed"][0]
        assert failed["message"] == "power off timeout"

    def test_no_timestamp_prefix(self, lib):
        content = b"cycle 1/50 start\ncycle 2/50 start\n"
        parsed = lib.parse_powercycle_result(content)
        assert parsed["cycles_done"] == 2
        assert parsed["expected_cycles"] == 50

    def test_crlf_tolerant(self, lib):
        content = b"cycle 1/10 start\r\nreboot failed: x\r\n"
        parsed = lib.parse_powercycle_result(content)
        assert parsed["cycles_done"] == 1
        assert parsed["reboot_failures"] == 1

    def test_incomplete_no_finished_line(self, lib):
        parsed = lib.parse_powercycle_result(b"cycle 1/100 start\nstopped by user\n")
        assert parsed["final_status"] is None
        kinds = [e["kind"] for e in parsed["entries"]]
        assert kinds[-1] == "stopped"

    def test_empty_content(self, lib):
        parsed = lib.parse_powercycle_result(b"")
        assert parsed["cycles_done"] == 0
        assert parsed["final_status"] is None
        assert parsed["entries"] == []


# ---------------------------------------------------------------------------
# 配置层级（params > env > properties > 默认）+ P0 边界校验
# ---------------------------------------------------------------------------


class TestPowerCycleConfig:
    def _patch_env_props(self, lib, monkeypatch, props: dict, envs: dict):
        monkeypatch.setattr(lib, "read_properties", lambda project: props)
        for key in ("STP_POWER_CYCLE_TEST_TIMES", "STP_POWER_CYCLE_MODE", "STP_POWER_CYCLE_POWER_OFF_MINUTES",
                    "STP_POWER_CYCLE_WAIT_SECONDS", "STP_POWER_CYCLE_TESTER", "STP_POWER_CYCLE_AUTO_RESUME",
                    "STP_POWER_CYCLE_INSTALL_APKS", "STP_POWER_CYCLE_RESET_COUNT", "STP_POWER_CYCLE_PROJECT"):
            monkeypatch.delenv(key, raising=False)
        for key, value in envs.items():
            monkeypatch.setenv(key, value)

    def test_code_defaults(self, lib, monkeypatch):
        self._patch_env_props(lib, monkeypatch, {}, {})
        cfg = lib.powercycle_config({})
        assert cfg["test_times"] == 100
        assert cfg["mode"] == "reboot"
        assert cfg["power_off_minutes"] == 1
        assert cfg["wait_seconds"] == 3
        assert cfg["tester"] == "tester"
        assert cfg["auto_resume"] is True
        assert cfg["project"] == "legacy"

    def test_params_win(self, lib, monkeypatch):
        self._patch_env_props(lib, monkeypatch, {"test.times": "300"}, {"STP_POWER_CYCLE_TEST_TIMES": "200"})
        assert lib.powercycle_config({"test_times": 7})["test_times"] == 7

    def test_env_over_props(self, lib, monkeypatch):
        self._patch_env_props(lib, monkeypatch, {"test.times": "300"}, {"STP_POWER_CYCLE_TEST_TIMES": "200"})
        assert lib.powercycle_config({})["test_times"] == 200

    def test_props_fallback(self, lib, monkeypatch):
        self._patch_env_props(lib, monkeypatch, {"test.times": "300"}, {})
        assert lib.powercycle_config({})["test_times"] == 300

    def test_poweroff_mode_rejected(self, lib, monkeypatch):
        """G15 D4：P0 只做 reboot，poweroff 配置校验失败。"""
        self._patch_env_props(lib, monkeypatch, {}, {})
        with pytest.raises(ValueError) as ei:
            lib.powercycle_config({"mode": "poweroff"})
        assert "poweroff" in str(ei.value)
        assert "reboot" in str(ei.value)

    def test_backend_key_ignored(self, lib, monkeypatch):
        """G15 D3：固定 autotesttool，backend 键不读取（MSSV 延后）。"""
        self._patch_env_props(lib, monkeypatch, {}, {})
        cfg = lib.powercycle_config({"backend": "mssv"})
        assert "backend" not in cfg


# ---------------------------------------------------------------------------
# prefs XML（lib.ps1:Set-PowerCyclePrefs 同款）
# ---------------------------------------------------------------------------


class TestPrefsXml:
    def test_build_full_map(self, lib):
        xml = lib.build_prefs_xml(100, "reboot", 1, 3, "tester", True, current_count=7)
        assert 'name="test_times" value="100"' in xml
        assert 'name="current_count" value="7"' in xml
        assert '<string name="mode">reboot</string>' in xml
        assert 'name="power_off_minutes" value="1"' in xml
        assert 'name="wait_seconds" value="3"' in xml
        assert 'name="auto_resume" value="true"' in xml
        assert 'name="running" value="false"' in xml

    def test_set_prefs_reset_count_false_preserves(self, lib, monkeypatch):
        monkeypatch.setattr(lib, "repair_prefs_ownership", lambda: None)
        pushed = {}

        def fake_push(content):
            pushed["xml"] = content

        monkeypatch.setattr(lib, "push_prefs_xml", fake_push)
        monkeypatch.setattr(
            lib, "get_prefs_xml",
            lambda: '<?xml version="1.0"?><map><int name="current_count" value="42"/></map>',
        )
        cfg = {"test_times": 100, "mode": "reboot", "power_off_minutes": 1, "wait_seconds": 3,
               "tester": "tester", "auto_resume": True, "reset_count": False}
        assert lib.set_prefs(cfg) == 42
        assert 'name="current_count" value="42"' in pushed["xml"]


# ---------------------------------------------------------------------------
# REBOOT 权限前置（G15 D3：固定 autotesttool，无 MSSV 兜底 → fail-fast）
# ---------------------------------------------------------------------------


class TestRebootPermission:
    def test_granted(self, lib, monkeypatch):
        monkeypatch.setattr(
            lib, "adb_shell",
            lambda cmd, timeout=30: "android.permission.REBOOT: granted=true"
            if "dumpsys" in cmd else "",
        )
        assert lib.check_reboot_permission() == "granted"

    def test_su_fallback(self, lib, monkeypatch):
        def fake_shell(cmd, timeout=30):
            if "dumpsys" in cmd:
                return "android.permission.REBOOT: granted=false"
            if cmd == "which su":
                return "/system/bin/su\n"
            return ""
        monkeypatch.setattr(lib, "adb_shell", fake_shell)
        assert lib.check_reboot_permission() == "su"

    def test_neither_raises_in_setup(self, lib, monkeypatch):
        def fake_shell(cmd, timeout=30):
            if "dumpsys" in cmd:
                return "android.permission.REBOOT: granted=false"
            return ""
        monkeypatch.setattr(lib, "adb_shell", fake_shell)
        assert lib.check_reboot_permission() is None


class TestSetupFailFast:
    def test_missing_apk_raises(self, setup_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(setup_mod, "powercycle_config", lambda cfg: {"project": "legacy"})
        monkeypatch.setattr(setup_mod, "resources_dir", lambda cfg: tmp_path)
        with pytest.raises(FileNotFoundError) as ei:
            setup_mod._run({})
        assert "AutoTestTool.apk" in str(ei.value)


# ---------------------------------------------------------------------------
# powercycle_check：设备离线不判死（reboot 模式固有）+ 进度/存活
# ---------------------------------------------------------------------------


class TestCollectWindow:
    def _patch_env(self, monkeypatch):
        for key in ("STP_POWER_CYCLE_COLLECT_WINDOW_START", "STP_POWER_CYCLE_COLLECT_WINDOW_MINUTES"):
            monkeypatch.delenv(key, raising=False)

    def test_cn_tz_default_now(self, check_mod_v103, monkeypatch):
        """v1.0.3：不传 now 时按东八区判定（主机时区不影响窗口时刻）。"""
        self._patch_env(monkeypatch)
        # 直接验证：带 tzinfo 的 now 正常判定
        from datetime import timezone, timedelta
        now_cn = datetime(2026, 8, 31, 0, 15, tzinfo=timezone(timedelta(hours=8)))
        assert check_mod_v103._in_collect_window(
            {"collect_window_start": "00:00", "collect_window_minutes": 30}, now_cn) is True
        now_pdt = datetime(2026, 8, 31, 9, 15, tzinfo=timezone(timedelta(hours=-7)))
        # 同一实际时刻（UTC 09:15）：东八区 17:15 → 不在 00:00 窗口
        assert check_mod_v103._in_collect_window(
            {"collect_window_start": "00:00", "collect_window_minutes": 30}, now_pdt) is False

    def test_disabled_when_no_start(self, check_mod_v102, monkeypatch):
        self._patch_env(monkeypatch)
        assert check_mod_v102._in_collect_window({}) is False

    def test_inside_window(self, check_mod_v102, monkeypatch):
        self._patch_env(monkeypatch)
        now = datetime(2026, 8, 31, 0, 15)
        assert check_mod_v102._in_collect_window(
            {"collect_window_start": "00:00", "collect_window_minutes": 30}, now) is True

    def test_outside_window(self, check_mod_v102, monkeypatch):
        self._patch_env(monkeypatch)
        now = datetime(2026, 8, 31, 1, 0)
        assert check_mod_v102._in_collect_window(
            {"collect_window_start": "00:00", "collect_window_minutes": 30}, now) is False

    def test_cross_midnight(self, check_mod_v102, monkeypatch):
        """窗口 23:50 + 30min → 次日 00:20 前仍属窗口（跨天覆盖）。"""
        self._patch_env(monkeypatch)
        now = datetime(2026, 8, 31, 0, 10)
        assert check_mod_v102._in_collect_window(
            {"collect_window_start": "23:50", "collect_window_minutes": 30}, now) is True
        later = datetime(2026, 8, 31, 0, 30)
        assert check_mod_v102._in_collect_window(
            {"collect_window_start": "23:50", "collect_window_minutes": 30}, later) is False

    def test_invalid_start_returns_false(self, check_mod_v102, monkeypatch):
        self._patch_env(monkeypatch)
        now = datetime(2026, 8, 31, 0, 15)
        assert check_mod_v102._in_collect_window({"collect_window_start": "abc"}, now) is False
        assert check_mod_v102._in_collect_window({"collect_window_start": "25:99"}, now) is False


class TestV102WindowFlow:
    def _patch(self, mod, monkeypatch, tmp_path, in_window=True):
        monkeypatch.setattr(mod, "device_serial", lambda: "S1")
        monkeypatch.setattr(mod, "_state_file", lambda: tmp_path / "state.json")
        monkeypatch.setattr(mod, "device_online", lambda: True)
        monkeypatch.setattr(mod, "_in_collect_window", lambda cfg, now=None: in_window)
        monkeypatch.setattr(mod, "_wait_online_short", lambda timeout_s=120: True)
        monkeypatch.setattr(mod, "progress_stamp", lambda payload: None)
        return {"pause": [], "resume": [], "collect": []}

    def test_window_collects_once_then_idles(self, check_mod_v102, monkeypatch, tmp_path):
        calls = self._patch(check_mod_v102, monkeypatch, tmp_path)
        monkeypatch.setattr(check_mod_v102, "pause_task", lambda: calls["pause"].append(1))
        monkeypatch.setattr(check_mod_v102, "resume_task", lambda: calls["resume"].append(1))
        monkeypatch.setattr(check_mod_v102, "collect_powercycle_result",
                            lambda project: calls["collect"].append(project)
                            or {"run_id": "powercycle_x_S1", "cycles_done": 3})
        r1 = check_mod_v102._run({"collect_window_start": "00:00", "project": "smoke"})
        assert r1["success"] is True
        assert r1["progress"]["phase"] == "collecting"
        assert r1["progress"]["last_collected_run_id"] == "powercycle_x_S1"
        assert len(calls["pause"]) == 1 and len(calls["collect"]) == 1 and len(calls["resume"]) == 1
        # 窗口内后续周期不重复收取
        r2 = check_mod_v102._run({"collect_window_start": "00:00", "project": "smoke"})
        assert r2["success"] is True
        assert len(calls["collect"]) == 1

    def test_window_collect_failure_retries_next_cycle(self, check_mod_v102, monkeypatch, tmp_path):
        calls = self._patch(check_mod_v102, monkeypatch, tmp_path)
        monkeypatch.setattr(check_mod_v102, "pause_task", lambda: None)
        monkeypatch.setattr(check_mod_v102, "resume_task", lambda: None)

        def fake_collect(project):
            calls["collect"].append(project)
            if len(calls["collect"]) == 1:
                raise RuntimeError("设备 120s 未上线")
            return {"run_id": "powercycle_y_S1", "cycles_done": 4}

        monkeypatch.setattr(check_mod_v102, "collect_powercycle_result", fake_collect)
        r1 = check_mod_v102._run({"collect_window_start": "00:00", "project": "smoke"})
        assert r1["success"] is True          # 收取失败不判死
        assert "未上线" in r1["progress"]["collect_error"]
        r2 = check_mod_v102._run({"collect_window_start": "00:00", "project": "smoke"})
        assert r2["progress"]["last_collected_run_id"] == "powercycle_y_S1"
        assert r2["progress"].get("collect_error") is None

    def test_window_disabled_normal_patrol(self, check_mod_v102, monkeypatch, tmp_path):
        """未配置窗口 → 正常 patrol 语义（无 phase 字段）。"""
        self._patch(check_mod_v102, monkeypatch, tmp_path, in_window=False)
        monkeypatch.setattr(check_mod_v102, "service_alive", lambda: True)
        monkeypatch.setattr(check_mod_v102, "_read_prefs_progress", lambda: (3, 100))
        monkeypatch.setattr(check_mod_v102, "_grep_cycle_count", lambda: 0)
        monkeypatch.setattr(check_mod_v102, "_result_bytes", lambda: 200)
        monkeypatch.setattr(check_mod_v102, "_run_finished", lambda: False)
        r = check_mod_v102._run({})
        assert r["success"] is True
        assert "phase" not in r["progress"]
        assert r["progress"]["cycles_done"] == 3


class TestV102BootTransition:
    """⑥ 修复：offline→online 转换（boot 窗口）不累计 dead_streak。"""

    def _patch(self, mod, monkeypatch, tmp_path):
        monkeypatch.setattr(mod, "device_serial", lambda: "S1")
        monkeypatch.setattr(mod, "_state_file", lambda: tmp_path / "state.json")
        monkeypatch.setattr(mod, "_in_collect_window", lambda cfg, now=None: False)
        monkeypatch.setattr(mod, "_read_prefs_progress", lambda: (5, 100))
        monkeypatch.setattr(mod, "_grep_cycle_count", lambda: 0)
        monkeypatch.setattr(mod, "_result_bytes", lambda: 200)
        monkeypatch.setattr(mod, "_run_finished", lambda: False)
        monkeypatch.setattr(mod, "progress_stamp", lambda payload: None)
        return {"online": True, "alive": False}

    def test_boot_window_not_counted_then_dead_detected(self, check_mod_v102, monkeypatch, tmp_path):
        st = self._patch(check_mod_v102, monkeypatch, tmp_path)
        monkeypatch.setattr(check_mod_v102, "device_online", lambda: st["online"])
        monkeypatch.setattr(check_mod_v102, "service_alive", lambda: st["alive"])

        # 周期1：设备离线（重启中）
        st["online"] = False
        r1 = check_mod_v102._run({"dead_grace_cycles": 2})
        assert r1["success"] is True and r1["progress"]["device_online"] is False

        # 周期2：刚上线（boot 窗口），服务未起 → 不累计
        st["online"] = True
        r2 = check_mod_v102._run({"dead_grace_cycles": 2})
        assert r2["success"] is True

        # 周期3：仍在线服务死 → dead_streak=1
        r3 = check_mod_v102._run({"dead_grace_cycles": 2})
        assert r3["success"] is True

        # 周期4：dead_streak=2 → 判死（正常语义保留）
        r4 = check_mod_v102._run({"dead_grace_cycles": 2})
        assert r4["success"] is False
        assert "连续 2 个周期" in r4["error_message"]


class TestCheck:
    def _patch_device_io(self, mod, monkeypatch, tmp_path, online=True, alive=True, prefs=(3, 100),
                         result_bytes=2048):
        monkeypatch.setattr(mod, "device_serial", lambda: "S1")
        monkeypatch.setattr(mod, "_state_file", lambda: tmp_path / "state.json")
        monkeypatch.setattr(mod, "device_online", lambda: online)
        monkeypatch.setattr(mod, "service_alive", lambda: alive)
        monkeypatch.setattr(mod, "_read_prefs_progress", lambda: prefs)
        monkeypatch.setattr(mod, "_grep_cycle_count", lambda: 0)
        monkeypatch.setattr(mod, "_result_bytes", lambda: result_bytes)
        monkeypatch.setattr(mod, "progress_stamp", lambda payload: None)

    def test_offline_is_not_dead(self, check_mod, monkeypatch, tmp_path):
        """重启周期设备离线：success 且 device_online=False，不累计 dead_streak。"""
        self._patch_device_io(check_mod, monkeypatch, tmp_path, online=False)
        r1 = check_mod._run({})
        assert r1["success"] is True
        assert r1["progress"]["device_online"] is False
        # 连续 10 个周期离线仍 success（平台心跳链路负责判 UNKNOWN）
        for _ in range(9):
            r = check_mod._run({})
            assert r["success"] is True

    def test_online_service_dead_streak_grace(self, check_mod, monkeypatch, tmp_path):
        self._patch_device_io(check_mod, monkeypatch, tmp_path, alive=False)
        r1 = check_mod._run({"dead_grace_cycles": 2})
        assert r1["success"] is True
        assert r1["progress"]["service_alive"] is False
        r2 = check_mod._run({"dead_grace_cycles": 2})
        assert r2["success"] is False
        assert "连续 2 个周期" in r2["error_message"]

    def test_online_alive_resets_streak(self, check_mod, monkeypatch, tmp_path):
        self._patch_device_io(check_mod, monkeypatch, tmp_path, alive=False)
        check_mod._run({})
        self._patch_device_io(check_mod, monkeypatch, tmp_path, alive=True)
        r2 = check_mod._run({})
        assert r2["success"] is True
        assert r2["progress"]["seq"] == 2

    def test_prefs_progress_used(self, check_mod, monkeypatch, tmp_path):
        self._patch_device_io(check_mod, monkeypatch, tmp_path)
        r = check_mod._run({})
        assert r["progress"]["cycles_done"] == 3
        assert r["progress"]["expected_cycles"] == 100

    def test_injected_expected_wins(self, check_mod, monkeypatch, tmp_path):
        self._patch_device_io(check_mod, monkeypatch, tmp_path)
        r = check_mod._run({"expected_cycles": 130})
        assert r["progress"]["expected_cycles"] == 130

    def test_prefs_unavailable_falls_back_to_grep(self, check_mod, monkeypatch, tmp_path):
        self._patch_device_io(check_mod, monkeypatch, tmp_path, prefs=None)
        monkeypatch.setattr(check_mod, "_grep_cycle_count", lambda: 9)
        r = check_mod._run({})
        assert r["progress"]["cycles_done"] == 9
        assert r["progress"]["expected_cycles"] == 0


# ---------------------------------------------------------------------------
# powercycle_check v1.0.1 完成检测（服务完成 test_times 后自停 → 报完成）
# ---------------------------------------------------------------------------


class TestCheckV101Completion:
    def _patch_device_io(self, mod, monkeypatch, tmp_path, online=True, finished=True, alive=False,
                         prefs=(2, 2)):
        monkeypatch.setattr(mod, "device_serial", lambda: "S1")
        monkeypatch.setattr(mod, "_state_file", lambda: tmp_path / "state.json")
        monkeypatch.setattr(mod, "device_online", lambda: online)
        monkeypatch.setattr(mod, "service_alive", lambda: alive)
        monkeypatch.setattr(mod, "_read_prefs_progress", lambda: prefs)
        monkeypatch.setattr(mod, "_grep_cycle_count", lambda: 0)
        monkeypatch.setattr(mod, "_result_bytes", lambda: 383)
        monkeypatch.setattr(mod, "_run_finished", lambda: finished)
        monkeypatch.setattr(mod, "progress_stamp", lambda payload: None)

    def test_finished_reports_completion(self, check_mod_v101, monkeypatch, tmp_path):
        self._patch_device_io(check_mod_v101, monkeypatch, tmp_path, finished=True, alive=False)
        r = check_mod_v101._run({})
        assert r["success"] is True
        assert r["progress"]["run_finished"] is True
        assert r["progress"]["cycles_done"] == 2

    def test_finished_never_accumulates_dead_streak(self, check_mod_v101, monkeypatch, tmp_path):
        self._patch_device_io(check_mod_v101, monkeypatch, tmp_path, finished=True, alive=False)
        for _ in range(5):
            assert check_mod_v101._run({"dead_grace_cycles": 2})["success"] is True

    def test_not_finished_keeps_v100_dead_streak(self, check_mod_v101, monkeypatch, tmp_path):
        self._patch_device_io(check_mod_v101, monkeypatch, tmp_path, finished=False, alive=False)
        r1 = check_mod_v101._run({"dead_grace_cycles": 2})
        assert r1["success"] is True
        r2 = check_mod_v101._run({"dead_grace_cycles": 2})
        assert r2["success"] is False

    def test_offline_still_takes_precedence(self, check_mod_v101, monkeypatch, tmp_path):
        """设备离线（重启周期）仍先报离线——此时结果文件读不到，finished 检测天然跳过。"""
        self._patch_device_io(check_mod_v101, monkeypatch, tmp_path, online=False, finished=False)
        r = check_mod_v101._run({})
        assert r["success"] is True
        assert r["progress"]["device_online"] is False
        assert "run_finished" not in r["progress"]


# ---------------------------------------------------------------------------
# powercycle_finish：拉取 + 解析 + 落盘
# ---------------------------------------------------------------------------


class TestWaitDeviceOnline:
    def test_online_immediately(self, finish_mod_v101, monkeypatch):
        monkeypatch.setattr(finish_mod_v101, "device_online", lambda: True)
        assert finish_mod_v101._wait_device_online(600) is True

    def test_offline_until_timeout(self, finish_mod_v101, monkeypatch):
        monkeypatch.setattr(finish_mod_v101, "device_online", lambda: False)
        monkeypatch.setattr(finish_mod_v101.time, "sleep", lambda _: None)
        assert finish_mod_v101._wait_device_online(10) is False

    def test_comes_online_after_retries(self, finish_mod_v101, monkeypatch):
        calls = {"n": 0}

        def fake_online():
            calls["n"] += 1
            return calls["n"] >= 2

        monkeypatch.setattr(finish_mod_v101, "device_online", fake_online)
        monkeypatch.setattr(finish_mod_v101.time, "sleep", lambda _: None)
        assert finish_mod_v101._wait_device_online(600) is True

    def test_run_waits_online_before_stop(self, finish_mod_v101, monkeypatch, tmp_path):
        """收取前置：设备离线时先等待（验收发现⑦——teardown 撞 reboot 窗口）。"""
        order = []
        monkeypatch.setattr(finish_mod_v101, "device_serial", lambda: "PC-S3")
        monkeypatch.setattr(finish_mod_v101, "device_online", lambda: True)
        monkeypatch.setattr(finish_mod_v101, "stop_task", lambda force=True: order.append("stop"))
        monkeypatch.setattr(finish_mod_v101.time, "sleep", lambda _: None)
        monkeypatch.setattr(finish_mod_v101, "adb_shell", lambda cmd, timeout=30: "")

        def fake_pull():
            local = tmp_path / "powercycle_result.txt"
            local.write_bytes(b"cycle 1/10 start\n")
            return local

        monkeypatch.setattr(finish_mod_v101, "_pull_result_file", fake_pull)
        monkeypatch.setattr(finish_mod_v101, "results_dir", lambda project: tmp_path / "r")
        out = finish_mod_v101._run({})
        assert out["metrics"]["final_status"] == "INCOMPLETE"
        assert out["metrics"]["run_id"].endswith("_PC-S3")

    def test_run_offline_timeout_raises(self, finish_mod_v101, monkeypatch):
        """等待超时仍离线 → 明确报错（不静默丢结果）。"""
        monkeypatch.setattr(finish_mod_v101, "device_serial", lambda: "PC-S4")
        monkeypatch.setattr(finish_mod_v101, "device_online", lambda: False)
        monkeypatch.setattr(finish_mod_v101.time, "sleep", lambda _: None)
        with pytest.raises(RuntimeError) as ei:
            finish_mod_v101._run({"wait_device_online_seconds": 10})
        assert "未上线" in str(ei.value)


class TestFinish:
    def test_run_writes_detail_json(self, finish_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(finish_mod, "device_serial", lambda: "PC-S1")
        monkeypatch.setattr(finish_mod, "stop_task", lambda force=True: None)
        monkeypatch.setattr(finish_mod.time, "sleep", lambda _: None)
        monkeypatch.setattr(finish_mod, "adb_shell", lambda cmd, timeout=30: "")

        def fake_pull():
            local = tmp_path / "powercycle_result.txt"
            local.write_bytes(
                b"cycle 1/10 start\nreboot failed: x\nfinished result=PASS\n"
            )
            return local

        monkeypatch.setattr(finish_mod, "_pull_result_file", fake_pull)
        results = tmp_path / "nfs" / "power-cycle" / "legacy" / "results"
        monkeypatch.setattr(finish_mod, "results_dir", lambda project: results)

        out = finish_mod._run({"project": "legacy"})
        assert out["metrics"]["cycles_done"] == 1
        assert out["metrics"]["expected_cycles"] == 10
        assert out["metrics"]["reboot_failures"] == 1
        assert out["metrics"]["final_status"] == "PASS"
        detail = results / f"{out['metrics']['run_id']}.json"
        assert detail.is_file()
        body = json.loads(detail.read_text(encoding="utf-8"))
        assert body["metrics"]["final_status"] == "PASS"
        assert body["entries"][0]["kind"] == "cycle"

    def test_run_incomplete_marked(self, finish_mod, monkeypatch, tmp_path):
        """无 finished 行 → final_status=INCOMPLETE（测试未收尾）。"""
        monkeypatch.setattr(finish_mod, "device_serial", lambda: "PC-S2")
        monkeypatch.setattr(finish_mod, "stop_task", lambda force=True: None)
        monkeypatch.setattr(finish_mod.time, "sleep", lambda _: None)
        monkeypatch.setattr(finish_mod, "adb_shell", lambda cmd, timeout=30: "")

        def fake_pull():
            local = tmp_path / "powercycle_result.txt"
            local.write_bytes(b"cycle 1/10 start\nstopped by user\n")
            return local

        monkeypatch.setattr(finish_mod, "_pull_result_file", fake_pull)
        monkeypatch.setattr(finish_mod, "results_dir", lambda project: tmp_path / "r")
        out = finish_mod._run({})
        assert out["metrics"]["final_status"] == "INCOMPLETE"

    def test_pull_missing_raises(self, finish_mod, monkeypatch):
        monkeypatch.setattr(finish_mod, "result_paths", lambda: ("/sdcard/x/powercycle_result.txt",))
        monkeypatch.setattr(
            finish_mod, "adb_shell",
            lambda cmd, timeout=30: "No such file or directory",
        )
        with pytest.raises(RuntimeError) as ei:
            finish_mod._pull_result_file()
        assert "powercycle_result.txt" in str(ei.value)
