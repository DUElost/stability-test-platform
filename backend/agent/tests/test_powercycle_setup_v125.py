# -*- coding: utf-8 -*-
"""#3140：powercycle_setup **v1.2.5**——等待预算只计**等待**（不含 push/pm/dumpsys 执行耗时）。

v1.2.4 的两处「等就绪 + 有界重试」（``install_apk`` / ``get_app_uid``）把
``wait_deadline`` 起在循环**之前**：attempt 1 的 ``push``/``pm install``
（各 ``timeout=300``）耗时被计入等待预算 ⇒ 慢安装时 ``max_attempts`` **静默塌缩**、
报文写成 ``attempts=1/3`` 把归因指向设备（审计注 2026-09-22 05:01Z）。

本文件钉住 v1.2.5 的计账语义，并留 **v1.2.4 对照锚点**（同输入下会塌缩）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"

APK = Path("AutoTestTool.apk")


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
def lib_v125():
    return _load("powercycle_lib_v125", "powercycle_setup/_lib.py")


@pytest.fixture(scope="module")
def lib_v124_anchor():
    """对照锚点：v1.2.4 不可变——同一「慢安装」输入下重试会静默塌缩。"""
    return _load("powercycle_lib_v124_anchor", "powercycle_setup/_lib.py")


class _Clock:
    """假时钟：``time.sleep`` 与「模拟执行耗时」都推进 ``time.time()``。"""

    def __init__(self, monkeypatch, mod, start: float = 1_000_000.0):
        self.now = start
        monkeypatch.setattr(mod.time, "time", self.time)
        monkeypatch.setattr(mod.time, "sleep", self.sleep)

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += float(seconds)

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _stub_install_adb(lib, monkeypatch, clock, *, pushes, install_rc=0, install_out="Success"):
    """假 adb：``pushes`` = 逐次 (rc, stderr, 模拟耗时秒)；push 的耗时**计入时钟**。"""
    counts = {"push": 0, "install": 0}

    def fake_adb(*args, timeout=60):
        cmd = args[0]
        if cmd == "uninstall":
            return 0, "", ""
        if cmd == "push":
            idx = min(counts["push"], len(pushes) - 1)
            counts["push"] += 1
            rc, err, busy = pushes[idx]
            clock.advance(busy)
            return rc, "", err
        if cmd == "shell":
            payload = args[1] if len(args) > 1 else ""
            if payload.startswith("pm install"):
                counts["install"] += 1
                return install_rc, install_out, "" if install_rc == 0 else "install failed"
            return 0, "", ""  # rm -f
        raise AssertionError(f"unexpected adb call: {args}")

    monkeypatch.setattr(lib, "adb", fake_adb)
    monkeypatch.setattr(lib, "adb_shell", lambda *a, timeout=30: "")
    return counts


def _ready_true(lib, monkeypatch, clock, *, busy: float = 0.0):
    calls: list[float] = []

    def fake_wait(deadline: float):
        calls.append(deadline)
        clock.advance(busy)
        return True, "ready"

    monkeypatch.setattr(lib, "wait_system_ready", fake_wait)
    return calls


def _ready_consume_to_deadline(lib, monkeypatch, clock):
    """每次就绪等待把假时钟推进到传入 deadline（模拟「一直等到该次等待的上限」）。"""
    calls: list[float] = []

    def fake_wait(deadline: float):
        calls.append(deadline)
        clock.now = max(clock.now, deadline)
        return True, "ready"

    monkeypatch.setattr(lib, "wait_system_ready", fake_wait)
    return calls


class TestV125SlowInstallDoesNotCollapseRetries:
    def test_attempt1_longer_than_budget_still_retries(self, lib_v125, monkeypatch):
        """审计反例：attempt 1 的 push 耗时 ≥ 预算 ⇒ 仍必须尝试第 2 次并成功。"""
        monkeypatch.setenv("STP_ATT_INSTALL_WAIT_BUDGET_SECONDS", "60")
        clock = _Clock(monkeypatch, lib_v125)
        counts = _stub_install_adb(
            lib_v125, monkeypatch, clock,
            pushes=[
                (1, "adb: device 'S-A' not found", 120.0),  # 慢 push（> 预算）
                (0, "", 2.0),                               # 第二次正常
            ],
        )
        _ready_true(lib_v125, monkeypatch, clock)

        lib_v125.install_apk(APK)  # 不得抛错

        assert counts["push"] == 2, "慢安装后重试被静默塌缩（v1.2.4 的缺陷形态）"
        assert counts["install"] == 1

    def test_fast_path_first_attempt_no_wait(self, lib_v125, monkeypatch):
        """正常设备：首试即成功，不做就绪等待、不消耗预算。"""
        clock = _Clock(monkeypatch, lib_v125)
        counts = _stub_install_adb(lib_v125, monkeypatch, clock, pushes=[(0, "", 3.0)])
        waits = _ready_true(lib_v125, monkeypatch, clock)

        lib_v125.install_apk(APK)

        assert counts == {"push": 1, "install": 1}
        assert waits == []

    def test_reboot_window_absorbed_by_ready_and_retry(self, lib_v125, monkeypatch):
        """重启窗：首试 push 失败 → 等就绪（计入等待）→ 第二试成功。"""
        clock = _Clock(monkeypatch, lib_v125)
        counts = _stub_install_adb(
            lib_v125, monkeypatch, clock,
            pushes=[(1, "adb: device 'S-A' not found", 5.0), (0, "", 5.0)],
        )
        waits = _ready_true(lib_v125, monkeypatch, clock, busy=20.0)

        lib_v125.install_apk(APK)

        assert counts["push"] == 2 and len(waits) == 1


class TestV125BudgetAccountingOnlyCountsWaiting:
    def test_wait_exhaustion_message_carries_waited_and_attempts_made(self, lib_v125, monkeypatch):
        """预算被**等待**耗尽：history 标 wait_budget_exhausted(waited=…)，attempts= 为实际尝试数。"""
        monkeypatch.setenv("STP_ATT_INSTALL_WAIT_BUDGET_SECONDS", "60")
        clock = _Clock(monkeypatch, lib_v125)
        _stub_install_adb(
            lib_v125, monkeypatch, clock,
            pushes=[(1, "adb: device 'S-A' not found", 3.0)],  # 执行很快，但一直等不到就绪
        )
        _ready_consume_to_deadline(lib_v125, monkeypatch, clock)

        with pytest.raises(RuntimeError) as exc:
            lib_v125.install_apk(APK)

        msg = str(exc.value)
        assert "attempts=2/3" in msg, msg
        assert "waited=60.0s/60s" in msg, msg
        assert "wait_budget_exhausted(waited=60.0s)" in msg, msg

    def test_waiting_within_budget_keeps_all_attempts(self, lib_v125, monkeypatch):
        """等待在预算内（每次 25s × 2）⇒ 三次尝试全部跑到。"""
        monkeypatch.setenv("STP_ATT_INSTALL_WAIT_BUDGET_SECONDS", "60")
        clock = _Clock(monkeypatch, lib_v125)
        counts = _stub_install_adb(
            lib_v125, monkeypatch, clock,
            pushes=[(1, "adb: device 'S-A' not found", 1.0)],
        )
        _ready_true(lib_v125, monkeypatch, clock, busy=25.0)

        with pytest.raises(RuntimeError) as exc:
            lib_v125.install_apk(APK)

        msg = str(exc.value)
        assert counts["push"] == 3, msg
        assert "attempts=3/3" in msg and "waited=50.0s/60s" in msg, msg

    def test_message_is_bounded(self, lib_v125, monkeypatch):
        monkeypatch.setenv("STP_ATT_INSTALL_WAIT_BUDGET_SECONDS", "60")
        clock = _Clock(monkeypatch, lib_v125)
        _stub_install_adb(
            lib_v125, monkeypatch, clock,
            pushes=[(7, "y" * 5000, 1.0)],
            install_rc=255, install_out="x" * 5000,
        )
        _ready_consume_to_deadline(lib_v125, monkeypatch, clock)

        with pytest.raises(RuntimeError) as exc:
            lib_v125.install_apk(APK)

        assert len(str(exc.value)) < 1200


class TestV125UidBudgetAccounting:
    def test_slow_dumpsys_does_not_collapse_uid_retry(self, lib_v125, monkeypatch):
        """uid 分支同款：首读慢（> 预算）⇒ 仍应尝试第二次并解析成功。"""
        monkeypatch.setenv("STP_PCS_RETRY_WAIT_BUDGET_SECONDS", "30")
        clock = _Clock(monkeypatch, lib_v125)
        seq = [(0, "", ""), (0, "userId=10086", "")]

        def fake_adb(*args, timeout=60):
            clock.advance(60.0)  # 每次 dumpsys 都很慢
            return seq.pop(0)

        monkeypatch.setattr(lib_v125, "adb", fake_adb)
        monkeypatch.setattr(
            lib_v125, "wait_system_ready",
            lambda deadline: (clock.advance(1.0), (True, "ready"))[1],
        )

        assert lib_v125.get_app_uid() == 10086

    def test_uid_exhaustion_message_carries_waited(self, lib_v125, monkeypatch):
        monkeypatch.setenv("STP_PCS_RETRY_WAIT_BUDGET_SECONDS", "30")
        clock = _Clock(monkeypatch, lib_v125)
        monkeypatch.setattr(lib_v125, "adb", lambda *a, timeout=60: (0, "", ""))

        def fake_wait(deadline: float):
            clock.now = max(clock.now, deadline)
            return True, "ready"

        monkeypatch.setattr(lib_v125, "wait_system_ready", fake_wait)

        with pytest.raises(RuntimeError) as exc:
            lib_v125.get_app_uid()

        msg = str(exc.value)
        assert "attempts=2/3" in msg and "waited=30.0s/30s" in msg, msg
        assert "history=[1:no_uid_fields(rc=0)" in msg, msg


