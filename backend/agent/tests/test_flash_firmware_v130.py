"""flash_firmware v1.3.0 的门控 / 重试环 / 环境预检。

v1.2.0 已覆盖的路由 / manifest / 版本比对不在此重复；这里聚焦 v1.3.0 新面：
  - sysfs 门控（端口枚举、按 serial 反查、隐藏/恢复、降级路径）
  - 环境预检（硬失败 vs WARNING、strict 升级、ttyACM 写入路径判定）
  - 重试环接线（attempt 计数、cap=4、env 逃生键、attempt>1 重画门控）
sysfs 树注入 tmp_path（_SYSFS_USB_BASE 参数化），udev 规则目录同理。
"""

from __future__ import annotations

import importlib.util
import json
import time as real_time
from pathlib import Path

import pytest


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware" / "v1.3.0"
)

spec = importlib.util.spec_from_file_location(
    "flash_firmware_v130", _SCRIPT_DIR / "flash_firmware.py"
)
ff = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ff)


def _make_usb_dev(
    base: Path, name: str, *, vid: str = "0e8d", pid: str = "2046",
    serial: "str | None" = None, authorized: str = "1",
) -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "idVendor").write_text(vid + "\n", encoding="utf-8")
    (d / "idProduct").write_text(pid + "\n", encoding="utf-8")
    if serial is not None:
        (d / "serial").write_text(serial + "\n", encoding="utf-8")
    (d / "authorized").write_text(authorized + "\n", encoding="utf-8")
    return d


@pytest.fixture
def sysfs(tmp_path: Path) -> Path:
    """仿 .87 hub 树拓扑：目标手机与干扰源都挂在多级 hub 后面。

    1-5.2.3 = 目标（DA 态 201c，有 serial）
    1-5.2.4 / 1-5.2.5 = 其它刷机态 MTK 口（2000 BROM / 2001 preloader）
    1-5.1 = 普通态手机（2046）——门控绝不能碰
    usb1 / 1-0:1.0 / 1-6(非 MTK) = 必须被枚举跳过
    """
    base = tmp_path / "usb"
    base.mkdir()
    (base / "usb1").mkdir()
    (base / "1-0:1.0").mkdir()
    _make_usb_dev(base, "1-5.2.3", pid="201c", serial="TARGETSER")
    _make_usb_dev(base, "1-5.2.4", pid="2000")
    _make_usb_dev(base, "1-5.2.5", pid="2001")
    _make_usb_dev(base, "1-5.1", pid="2046", serial="NORMALSER")
    _make_usb_dev(base, "1-6", vid="18d1", pid="4ee7", serial="GPHONE")
    return base


class TestListMtkPorts:
    def test_hub_tree_devices_are_enumerated(self, sysfs):
        """回归：带点的设备名(1-5.2.x)是 hub 后面的真设备，不能被当接口跳过。"""
        ports = ff._list_mtk_ports(str(sysfs))
        assert set(ports) == {"1-5.2.3", "1-5.2.4", "1-5.2.5", "1-5.1"}

    def test_pid_lowercased_and_auth_read(self, sysfs):
        ports = ff._list_mtk_ports(str(sysfs))
        assert ports["1-5.2.4"]["pid"] == "2000"
        assert ports["1-5.2.4"]["authorized"] == "1"

    def test_missing_base_returns_empty(self, tmp_path):
        assert ff._list_mtk_ports(str(tmp_path / "nope")) == {}


class TestPortForSerial:
    def test_finds_dotted_port(self, sysfs):
        assert ff._port_for_serial("TARGETSER", str(sysfs)) == "1-5.2.3"

    def test_unknown_serial_returns_none(self, sysfs):
        assert ff._port_for_serial("NOPE", str(sysfs)) is None

    def test_empty_serial_returns_none(self, sysfs):
        assert ff._port_for_serial("", str(sysfs)) is None

    def test_missing_base_returns_none(self, tmp_path):
        assert ff._port_for_serial("X", str(tmp_path / "nope")) is None


class TestGateOtherMtk:
    def test_gates_only_flash_stage_others(self, sysfs):
        report = ff._gate_other_mtk("1-5.2.3", str(sysfs))
        assert sorted(report["hidden"]) == ["1-5.2.4", "1-5.2.5"]
        assert report["errors"] == {}
        # 目标口与普通态手机不受影响
        assert (sysfs / "1-5.2.3" / "authorized").read_text().strip() == "1"
        assert (sysfs / "1-5.1" / "authorized").read_text().strip() == "1"
        # 非目标刷机态口已被隐藏
        assert (sysfs / "1-5.2.4" / "authorized").read_text().strip() == "0"

    def test_no_other_flash_stage_devices(self, tmp_path):
        base = tmp_path / "usb"
        base.mkdir()
        # 只有目标口（刷机态）+ 一台普通态手机 → 没有可门控对象
        _make_usb_dev(base, "1-5.2.3", pid="201c", serial="TARGETSER")
        _make_usb_dev(base, "1-5.1", pid="2046")
        report = ff._gate_other_mtk("1-5.2.3", str(base))
        assert report["hidden"] == []
        assert report["skipped_reason"] == \
            "no other flash-stage MTK devices visible"

    def test_no_target_port_skips(self, sysfs):
        report = ff._gate_other_mtk(None, str(sysfs))
        assert report["hidden"] == []
        assert "target port unknown" in report["skipped_reason"]
        # 谨慎语义：不知道目标在哪就一个口都不动
        assert (sysfs / "1-5.2.4" / "authorized").read_text().strip() == "1"

    def test_non_linux_skips(self, sysfs, monkeypatch):
        monkeypatch.setattr(ff, "_is_linux", lambda: False)
        report = ff._gate_other_mtk("1-5.2.3", str(sysfs))
        assert report["skipped_reason"] == "non-linux host"
        assert (sysfs / "1-5.2.4" / "authorized").read_text().strip() == "1"

    def test_all_writes_fail_records_reason(self, sysfs, monkeypatch):
        monkeypatch.setattr(
            ff, "_set_authorized",
            lambda port, value, base=ff._SYSFS_USB_BASE: (False, "no-sudo"))
        report = ff._gate_other_mtk("1-5.2.3", str(sysfs))
        assert report["hidden"] == []
        assert "cannot write authorized" in report["skipped_reason"]
        assert set(report["errors"]) == {"1-5.2.4", "1-5.2.5"}

    def test_restore_gated_rewrites_one(self, sysfs):
        report = ff._gate_other_mtk("1-5.2.3", str(sysfs))
        restore = ff._restore_gated(report, str(sysfs))
        assert sorted(restore["restored"]) == ["1-5.2.4", "1-5.2.5"]
        assert (sysfs / "1-5.2.4" / "authorized").read_text().strip() == "1"

    def test_restore_none_is_noop(self):
        assert ff._restore_gated(None) == {"restored": [], "errors": {}}


class TestSetAuthorized:
    def test_direct_write(self, tmp_path):
        d = _make_usb_dev(tmp_path, "1-1", pid="2000")
        ok, how = ff._set_authorized("1-1", "0", str(tmp_path))
        assert ok is True and how == "direct"
        assert (d / "authorized").read_text().strip() == "0"

    def test_sudo_fallback_when_direct_denied(self, tmp_path, monkeypatch):
        calls: list = []

        def fake_run(argv, **kwargs):
            calls.append(argv)

            class R:
                returncode = 0

            return R()

        monkeypatch.setattr(ff.subprocess, "run", fake_run)
        monkeypatch.setattr(ff, "_sudo_available", lambda: True)
        ok, how = ff._set_authorized("ghost", "0", str(tmp_path / "nope"))
        assert ok is True and how == "sudo"
        argv = calls[0]
        assert argv[:4] == ["sudo", "-n", "sh", "-c"]
        assert str(tmp_path / "nope" / "ghost" / "authorized") in argv[4]
        assert "echo 0 >" in argv[4]

    def test_no_sudo_reports_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ff, "_sudo_available", lambda: False)
        ok, how = ff._set_authorized("ghost", "0", str(tmp_path / "nope"))
        assert ok is False and how == "no-sudo"


# ---------------------------------------------------------------------------
# 环境预检
# ---------------------------------------------------------------------------


@pytest.fixture
def quiet_precheck(monkeypatch):
    """隔离外部世界：ldd 不真跑、ttyACM/组/udev/PATH 探测可注入。"""
    monkeypatch.setattr(ff, "_ldd_missing_libs", lambda exe, env: [])
    monkeypatch.setattr(ff, "_ttyacm_writable_now", lambda: None)
    # adb 默认「在 PATH 上」；绝对路径仍按真实存在性判断
    monkeypatch.setattr(
        ff, "_shutil_which",
        lambda cmd: None if cmd.startswith("/") else "/usr/bin/" + cmd)
    yield monkeypatch


class TestPrecheckEnvironment:
    def test_all_green(self, tmp_path, quiet_precheck):
        exe = tmp_path / "flash_tool"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
        quiet_precheck.setattr(ff, "_user_in_dialout", lambda: True)
        ok, report = ff._precheck_environment(str(exe), "adb", True, False)
        assert ok is True
        assert report["warnings"] == []
        checks = {it["check"]: it for it in report["items"]}
        assert all(it["ok"] for it in report["items"])
        assert set(checks) == {
            "flash-tool-executable", "shared-libs",
            "adb-present", "ttyacm-write-path",
        }

    def test_non_executable_hard_fails(self, tmp_path, quiet_precheck):
        exe = tmp_path / "flash_tool"
        exe.write_text("x", encoding="utf-8")
        exe.chmod(0o644)
        ok, report = ff._precheck_environment(str(exe), "adb", True, False)
        assert ok is False
        item = next(it for it in report["items"]
                    if it["check"] == "flash-tool-executable")
        assert "chmod +x" in item["detail"]

    def test_ttyacm_unclear_is_warning_not_fatal(self, tmp_path, quiet_precheck):
        exe = tmp_path / "flash_tool"
        exe.write_text("x", encoding="utf-8")
        exe.chmod(0o755)
        quiet_precheck.setattr(ff, "_user_in_dialout", lambda: False)
        quiet_precheck.setattr(ff, "_udev_has_mtk_0666_rule", lambda: False)
        ok, report = ff._precheck_environment(str(exe), "adb", True, False)
        # 默认宽松：ttyACM 路径不明只记 WARNING 不拦刷机（87 实测 0666 也能跑）
        assert ok is True
        assert "ttyacm-write-path" in report["warnings"]

    def test_strict_upgrades_warning_to_failure(self, tmp_path, quiet_precheck):
        exe = tmp_path / "flash_tool"
        exe.write_text("x", encoding="utf-8")
        exe.chmod(0o755)
        quiet_precheck.setattr(ff, "_user_in_dialout", lambda: False)
        quiet_precheck.setattr(ff, "_udev_has_mtk_0666_rule", lambda: False)
        ok, report = ff._precheck_environment(str(exe), "adb", True, True)
        assert ok is False
        assert report["warnings"] == []

    def test_adb_missing_needed_vs_optional(self, tmp_path, quiet_precheck):
        exe = tmp_path / "flash_tool"
        exe.write_text("x", encoding="utf-8")
        exe.chmod(0o755)
        quiet_precheck.setattr(ff, "_user_in_dialout", lambda: True)
        ok_needed, _ = ff._precheck_environment(
            str(exe), "/no/such/adb", True, False)
        ok_optional, _ = ff._precheck_environment(
            str(exe), "/no/such/adb", False, False)
        assert ok_needed is False   # 流程需要 adb（reboot/verify）→ 硬失败
        assert ok_optional is True  # 不需要 → 只记录

    def test_adb_found_via_path_lookup(self, tmp_path, quiet_precheck):
        exe = tmp_path / "flash_tool"
        exe.write_text("x", encoding="utf-8")
        exe.chmod(0o755)
        quiet_precheck.setattr(ff, "_user_in_dialout", lambda: True)
        quiet_precheck.setattr(ff, "_shutil_which",
                               lambda cmd: f"/usr/bin/{cmd}")
        ok, _ = ff._precheck_environment(str(exe), "adb", True, False)
        assert ok is True


class TestUdevRuleDetection:
    PROD_RULE = 'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", MODE="0666"\n'

    def test_production_rule_matches(self, tmp_path):
        rules = tmp_path / "rules.d"
        rules.mkdir()
        (rules / "98-ttyacm-mtk.rules").write_text(self.PROD_RULE,
                                                   encoding="utf-8")
        assert ff._udev_has_mtk_0666_rule(str(rules)) is True

    def test_attrs_only_variant_matches(self, tmp_path):
        rules = tmp_path / "rules.d"
        rules.mkdir()
        (rules / "99-mtk.rules").write_text(
            'ATTRS{idVendor}=="0e8d", MODE:="0666"\n', encoding="utf-8")
        assert ff._udev_has_mtk_0666_rule(str(rules)) is True

    def test_wrong_mode_does_not_match(self, tmp_path):
        rules = tmp_path / "rules.d"
        rules.mkdir()
        (rules / "98-ttyacm-mtk.rules").write_text(
            'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", MODE="0600"\n',
            encoding="utf-8")
        assert ff._udev_has_mtk_0666_rule(str(rules)) is False

    def test_unrelated_0666_rule_does_not_match(self, tmp_path):
        rules = tmp_path / "rules.d"
        rules.mkdir()
        (rules / "50-default.rules").write_text(
            'SUBSYSTEM=="usb", MODE="0666"\n', encoding="utf-8")
        assert ff._udev_has_mtk_0666_rule(str(rules)) is False

    def test_missing_dir_returns_false(self, tmp_path):
        assert ff._udev_has_mtk_0666_rule(str(tmp_path / "nope")) is False


# ---------------------------------------------------------------------------
# main() 接线：重试环 / 门控时序 / 预检短路
# ---------------------------------------------------------------------------


class _FakeTime:
    """替换模块内 time：sleep 只记账不走钟，monotonic/time 直通。"""

    def __init__(self):
        self.sleeps: list = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def time(self):
        return real_time.time()

    def monotonic(self):
        return real_time.monotonic()


def _fw_dir(tmp_path: Path) -> Path:
    ver = tmp_path / "fw" / "MLD" / "9.0.0.1"
    ver.mkdir(parents=True)
    (ver / "scatter.txt").write_text("s", encoding="utf-8")
    (ver / "da.bin").write_text("d", encoding="utf-8")
    (ver / "manifest.json").write_text(json.dumps({
        "family": "MLD", "version": "9.0.0.1",
        "scatter_file": "scatter.txt", "da_file": "da.bin",
    }), encoding="utf-8")
    return ver


def _wire_happy_path(monkeypatch, fw_ver: Path, outcomes: list, *,
                     events: "list | None" = None):
    """把 flash_tool / 锁 / adb / verify 全部 stub 掉。

    outcomes: 每次 _run_flash_tool_with_progress 返回 (output, rc)，弹尽后报错。
    events:   可选列表，按序记录 gate/reboot/regate 调用时序。
    """

    def fake_gate(target_port, base=ff._SYSFS_USB_BASE):
        # 镜像真实现的分支语义（分支本身已在 TestGateOtherMtk 单测覆盖）
        if events is not None:
            events.append(("gate", target_port))
        if not ff._is_linux():
            return {"hidden": [], "errors": {},
                    "skipped_reason": "non-linux host",
                    "target_port": target_port}
        if not target_port:
            return {"hidden": [], "errors": {},
                    "skipped_reason": "target port unknown",
                    "target_port": None}
        return {"hidden": ["1-5.2.4"], "errors": {},
                "skipped_reason": None, "target_port": target_port}

    def fake_restore(gated, base=ff._SYSFS_USB_BASE):
        if events is not None:
            events.append(("restore", list((gated or {}).get("hidden", []))))
        return {"restored": (gated or {}).get("hidden", []), "errors": {}}

    def fake_reboot(serial, target, adb_path, wait_seconds):
        if events is not None:
            events.append(("reboot", serial))
        return {"attempted": True}

    def fake_run(cmd, cwd, env, timeout, on_stage, on_percent):
        if not outcomes:
            raise AssertionError("unexpected extra flash_tool invocation")
        return outcomes.pop(0)

    lock_token = object()
    monkeypatch.setattr(ff, "_precheck_environment",
                        lambda exe, adb, need_adb, strict:
                        (True, {"items": [], "warnings": []}))
    monkeypatch.setattr(ff, "_pick_flash_tool_exe",
                        lambda tool_dir: str(fw_ver / "flash_tool"))
    monkeypatch.setattr(ff, "_is_linux", lambda: True)
    monkeypatch.setattr(ff, "_port_for_serial",
                        lambda serial, base=ff._SYSFS_USB_BASE: "1-5.2.3")
    monkeypatch.setattr(ff, "_gate_other_mtk", fake_gate)
    monkeypatch.setattr(ff, "_restore_gated", fake_restore)
    monkeypatch.setattr(ff, "_acquire_host_lock",
                        lambda on_wait_tick=None: lock_token)
    monkeypatch.setattr(ff, "_release_host_lock", lambda fd: None)
    monkeypatch.setattr(ff, "_reboot_into_flash_mode", fake_reboot)
    monkeypatch.setattr(ff, "_run_flash_tool_with_progress", fake_run)
    monkeypatch.setattr(ff, "_wait_device_back",
                        lambda serial, adb_path, timeout, on_tick: True)
    monkeypatch.setattr(ff, "_verify_after_flash",
                        lambda route, serial, adb, wait, on_tick:
                        (True, {"current": route.get("version")}))
    fake_time = _FakeTime()
    monkeypatch.setattr(ff, "time", fake_time)
    return fake_time


def _params_env(monkeypatch, fw_ver: Path, **params):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TARGETSER")
    monkeypatch.setenv("STP_ADB_PATH", "adb")
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
        "firmware_dir": str(fw_ver),
        "flash_tool_dir": str(fw_ver),  # 仅需存在；exe 由 stub 提供
        **params,
    }))


class TestMainRetryLoop:
    def test_first_attempt_success_single_try(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        _params_env(monkeypatch, fw_ver)
        _wire_happy_path(monkeypatch, fw_ver, [("All command exec done", 0)])
        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert payload["metrics"]["attempt_count"] == 1
        assert payload["metrics"]["attempts"][0]["outcome"] == "ok"
        assert payload["metrics"]["gating"]["hidden"] == ["1-5.2.4"]

    def test_verdict_failure_retries_then_succeeds(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        _params_env(monkeypatch, fw_ver, retry_backoff_seconds=7)
        events: list = []
        fake_time = _wire_happy_path(
            monkeypatch, fw_ver,
            [("S_FT_DOWNLOAD_FAIL blah", 1), ("All command exec done", 0)],
            events=events,
        )
        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert payload["metrics"]["attempt_count"] == 2
        assert [a["outcome"] for a in payload["metrics"]["attempts"]] == \
            ["verdict-failed", "ok"]
        assert fake_time.sleeps == [7]  # 只在 attempt>1 前退避一次
        # 时序：初次门控 → reboot → (retry) 再门控 → 再 reboot → restore
        kinds = [e[0] for e in events]
        assert kinds == ["gate", "reboot", "gate", "reboot", "restore"]

    def test_attempts_capped_at_four(self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        _params_env(monkeypatch, fw_ver, max_attempts=99, retry_backoff_seconds=0)
        _wire_happy_path(
            monkeypatch, fw_ver,
            [("S_FT_DOWNLOAD_FAIL", 1)] * 10)
        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is False
        assert payload["metrics"]["attempt_count"] == 4
        assert "after 4 attempt(s)" in payload["error_message"]

    def test_env_max_attempts_escape_hatch(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        _params_env(monkeypatch, fw_ver)
        monkeypatch.setenv("STP_FLASH_MAX_ATTEMPTS", "3")
        _wire_happy_path(monkeypatch, fw_ver, [("S_FT_DOWNLOAD_FAIL", 1)] * 10)
        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is False
        assert payload["metrics"]["attempt_count"] == 3

    def test_bad_param_values_fall_back_safely(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        _params_env(monkeypatch, fw_ver, max_attempts="abc",
                    retry_backoff_seconds=-5)
        _wire_happy_path(
            monkeypatch, fw_ver,
            [("S_FT_DOWNLOAD_FAIL", 1)] * 10)
        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        # max_attempts 回落 2；负退避被钳到 0（time.sleep(-5) 会抛 ValueError）
        assert payload["metrics"]["attempt_count"] == 2

    def test_launch_failure_stops_retrying_but_restores(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        _params_env(monkeypatch, fw_ver, max_attempts=4)
        events: list = []

        def fake_gate(target_port, base=ff._SYSFS_USB_BASE):
            events.append("gate")
            return {"hidden": ["1-5.2.4"], "errors": {},
                    "skipped_reason": None, "target_port": target_port}

        def boom(cmd, cwd, env, timeout, on_stage, on_percent):
            raise RuntimeError("cannot execute binary")

        monkeypatch.setattr(ff, "_precheck_environment",
                            lambda exe, adb, need_adb, strict:
                            (True, {"items": [], "warnings": []}))
        monkeypatch.setattr(ff, "_pick_flash_tool_exe",
                            lambda tool_dir: str(fw_ver / "flash_tool"))
        monkeypatch.setattr(ff, "_is_linux", lambda: True)
        monkeypatch.setattr(ff, "_port_for_serial",
                            lambda serial, base=ff._SYSFS_USB_BASE: "1-5.2.3")
        monkeypatch.setattr(ff, "_gate_other_mtk", fake_gate)
        monkeypatch.setattr(ff, "_restore_gated",
                            lambda gated, base=ff._SYSFS_USB_BASE:
                            events.append("restore") or
                            {"restored": [], "errors": {}})
        monkeypatch.setattr(ff, "_acquire_host_lock",
                            lambda on_wait_tick=None: object())
        monkeypatch.setattr(ff, "_release_host_lock", lambda fd: None)
        monkeypatch.setattr(ff, "_run_flash_tool_with_progress", boom)
        monkeypatch.setattr(ff, "time", _FakeTime())

        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is False
        assert "launch failed" in payload["error_message"]
        # 环境性问题不重试（gate 一次），但恢复必须执行
        assert events.count("gate") == 1
        assert events.count("restore") == 1

    def test_timeout_counts_as_failed_attempt(
            self, tmp_path, monkeypatch, capsys):
        import subprocess as real_subprocess

        fw_ver = _fw_dir(tmp_path)
        _params_env(monkeypatch, fw_ver, retry_backoff_seconds=0)
        attempts_seen: list = []

        def fake_run(cmd, cwd, env, timeout, on_stage, on_percent):
            attempts_seen.append(timeout)
            raise real_subprocess.TimeoutExpired([str(cmd)], timeout)

        _wire_happy_path(monkeypatch, fw_ver, [])
        monkeypatch.setattr(ff, "_run_flash_tool_with_progress", fake_run)
        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is False
        assert payload["metrics"]["attempts"][0]["outcome"] == "timeout"
        assert payload["metrics"]["attempt_count"] == 2  # 默认 max_attempts


class TestMainGatingWiring:
    def test_gating_disabled_by_params(self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        _params_env(monkeypatch, fw_ver, gate_other_mtk=False)
        called: list = []

        def unexpected(*a, **kw):
            called.append(a)

        _wire_happy_path(monkeypatch, fw_ver, [("All command exec done", 0)])
        monkeypatch.setattr(ff, "_gate_other_mtk", unexpected)
        monkeypatch.setattr(ff, "_port_for_serial", unexpected)
        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert called == []
        assert payload["metrics"]["gating"]["skipped_reason"] == \
            "disabled by params"

    def test_windows_silently_skips_gating(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        _params_env(monkeypatch, fw_ver)
        _wire_happy_path(monkeypatch, fw_ver, [("All command exec done", 0)])
        monkeypatch.setattr(ff, "_is_linux", lambda: False)
        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert payload["metrics"]["gating"]["skipped_reason"] == "non-linux host"
        assert payload["metrics"]["gating"]["restore"]["restored"] == []

    def test_no_serial_skips_port_resolution(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        monkeypatch.delenv("STP_DEVICE_SERIAL", raising=False)
        monkeypatch.setenv("STP_ADB_PATH", "adb")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
            "firmware_dir": str(fw_ver),
            # 显式给出：默认路径指向 git 外的 flashtool 部署目录，
            # CI 检出没有，缺了它会提前死在 tool_dir 校验上
            "flash_tool_dir": str(fw_ver),
            "skip_if_current": False,
            "verify_version": False,
        }))
        _wire_happy_path(monkeypatch, fw_ver, [("All command exec done", 0)])
        resolved: list = []

        def spy(serial, base=ff._SYSFS_USB_BASE):
            resolved.append(serial)
            return None

        monkeypatch.setattr(ff, "_port_for_serial", spy)
        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        # 反查只发生一次、发生在任何 reboot 之前（BROM 态无 serial 可查）
        assert resolved == [""]
        # 端口未知 → 门控降级为跳过，且没有口被隐藏/恢复
        assert payload["metrics"]["gating"]["target_port"] is None
        assert payload["metrics"]["gating"]["skipped_reason"]
        assert payload["metrics"]["gating"]["hidden"] == []

    def test_legacy_metric_keys_preserved(
            self, tmp_path, monkeypatch, capsys):
        """v1.2.0 输出契约：旧顶层 metrics 键一个都不能少。"""
        fw_ver = _fw_dir(tmp_path)
        _params_env(monkeypatch, fw_ver)
        _wire_happy_path(monkeypatch, fw_ver, [("All command exec done", 0)])
        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        metrics = payload["metrics"]
        for key in ("duration_seconds", "lock_wait_seconds",
                    "device_reenumerated", "exit_code", "command_argv",
                    "da_file", "scatter_file", "firmware_dir", "route",
                    "version_check", "post_flash_verify", "pre_reboot",
                    "stdout_tail", "stderr_tail"):
            assert key in metrics, key
        for key in ("env_precheck", "gating", "attempts", "attempt_count"):
            assert key in metrics, key


class TestMainPrecheckShortCircuit:
    def test_precheck_failure_short_circuits_before_lock(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        _params_env(monkeypatch, fw_ver)

        def boom(*a, **kw):
            raise AssertionError("must not reach lock/gate after precheck fail")

        monkeypatch.setattr(ff, "_pick_flash_tool_exe",
                            lambda tool_dir: str(tmp_path / "missing_exe"))
        monkeypatch.setattr(ff, "_precheck_environment",
                            lambda exe, adb, need_adb, strict:
                            (False, {"items": [
                                {"check": "shared-libs", "ok": False,
                                 "detail": "missing: libQt5Core.so"}],
                                "warnings": []}))
        monkeypatch.setattr(ff, "_acquire_host_lock", boom)
        monkeypatch.setattr(ff, "_gate_other_mtk", boom)
        monkeypatch.setattr(ff, "time", _FakeTime())

        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is False
        assert "environment precheck failed" in payload["error_message"]
        assert "libQt5Core.so" in payload["error_message"]
        assert payload["metrics"]["env_precheck"]["warnings"] == []
