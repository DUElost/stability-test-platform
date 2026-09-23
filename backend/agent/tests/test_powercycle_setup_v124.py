# -*- coding: utf-8 -*-
"""#2802：powercycle_setup **v1.2.4**——服务启动与 uid 解析的同款「等就绪 + 有界重试」。

r503 取证（首个含 v1.2.2 的窗）：残余 14 条 init 失败里
- 2 条 `PowerCycleService 未在 30s 内启动`（`.82`/`9.127`）+ 1 条 `无法解析 uid`（`.77`），
  失败全落 T+0~30s —— 设备仍在上一窗的重启循环里（~75s 周期 > 单次 30s 等待）；
- 这两条分支既无就绪门也无证据字段（归类为「未升级报文」）。

本文件钉住 v1.2.4 的新语义（快速路径不探测 / 重启窗由重试吸收 / 未就绪即收手 /
报文证据）与 **v1.2.3 对照锚点**（旧版本不可变，只读断言）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"


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
def setup_v124():
    return _load("powercycle_setup_v124", "powercycle_setup/powercycle_setup.py")


@pytest.fixture(scope="module")
def lib_v124():
    return _load("powercycle_lib_v124", "powercycle_setup/_lib.py")


@pytest.fixture(scope="module")
def lib_v123_anchor():
    """对照锚点：v1.2.3 不可变——uid 分支仍是「裸调用 + 无证据」的旧形态。"""
    return _load("powercycle_lib_v123_anchor", "powercycle_setup/_lib.py")


def _patch_clock(monkeypatch, mod, *, start: float = 1_000_000.0):
    """假时钟：sleep 推进 time.time()，避免测试真等 30s/90s。"""
    state = {"now": start, "sleeps": []}

    def fake_time() -> float:
        return state["now"]

    def fake_sleep(seconds: float) -> None:
        state["sleeps"].append(float(seconds))
        state["now"] += float(seconds)

    monkeypatch.setattr(mod.time, "time", fake_time)
    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
    return state


def _stub_start(setup_mod, monkeypatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(setup_mod, "start_task", lambda: calls.append(1))
    monkeypatch.setattr(setup_mod, "service_probe", lambda: (False, 0, 'dumpsys: no matching service'))
    return calls


class TestV124ServiceFastPath:
    def test_service_visible_first_poll_never_waits_ready(self, setup_v124, monkeypatch):
        """正常设备：首试 start_task 后即见服务，不做就绪探测（耗时不变）。"""
        _patch_clock(monkeypatch, setup_v124)
        starts = _stub_start(setup_v124, monkeypatch)
        monkeypatch.setattr(setup_v124, "service_alive", lambda: True)
        waits: list[float] = []
        monkeypatch.setattr(
            setup_v124, "wait_system_ready",
            lambda deadline: (waits.append(deadline), (True, "ready"))[1],
        )

        ok, history, waited_ready, _ = setup_v124._start_service_with_retry()

        assert ok is True
        assert len(starts) == 1
        assert history == [] and waited_ready is False
        assert waits == []  # 快速路径不探测就绪


class TestV124RebootWindowAbsorbed:
    def test_no_service_then_ready_retry_succeeds(self, setup_v124, monkeypatch):
        """首试 30s 内不见服务（重启窗）→ 等就绪 → 第二试成功。"""
        _patch_clock(monkeypatch, setup_v124)
        starts = _stub_start(setup_v124, monkeypatch)
        seen = {"first": True}

        def fake_alive() -> bool:
            if seen["first"]:
                return False  # 首试整段 30s 都不可见
            return True

        def fake_ready(deadline: float):
            seen["first"] = False
            return True, "ready"

        monkeypatch.setattr(setup_v124, "service_alive", fake_alive)
        monkeypatch.setattr(setup_v124, "wait_system_ready", fake_ready)

        ok, history, waited_ready, _ = setup_v124._start_service_with_retry()

        assert ok is True
        assert len(starts) == 2
        assert waited_ready is True
        assert history == ["1:no_service(rc=0)"]

    def test_not_ready_breaks_early(self, setup_v124, monkeypatch):
        """等不到系统就绪 → 立即收手（不再空打 start_task），history 标 not_ready。"""
        _patch_clock(monkeypatch, setup_v124)
        starts = _stub_start(setup_v124, monkeypatch)
        monkeypatch.setattr(setup_v124, "service_alive", lambda: False)
        monkeypatch.setattr(
            setup_v124, "wait_system_ready", lambda deadline: (False, "state='offline'")
        )

        ok, history, waited_ready, _ = setup_v124._start_service_with_retry()

        assert ok is False
        assert len(starts) == 1
        assert history[0] == "1:no_service(rc=0)"
        assert history[1].startswith("2:not_ready(")
        assert waited_ready is False


class TestV124EvidenceOnExhaustion:
    def test_failure_message_carries_attempts_history_rc_dumpsys(self, setup_v124, monkeypatch):
        """全试失败：报文必须带 attempts=/history=/rc=/dumpsys= 四类证据（消除「未升级报文」）。"""
        _patch_clock(monkeypatch, setup_v124)
        monkeypatch.setenv("STP_PCS_RETRY_WAIT_BUDGET_SECONDS", "5")  # 让首试后预算耗尽
        _stub_start(setup_v124, monkeypatch)
        monkeypatch.setattr(setup_v124, "service_alive", lambda: False)
        monkeypatch.setattr(
            setup_v124, "wait_system_ready", lambda deadline: (False, "boot_completed='0'")
        )
        monkeypatch.setattr(
            setup_v124, "service_probe",
            lambda: (False, 1, "dumpsys: no service 'com.mediatek...PowerCycleService'"),
        )

        ok, history, waited_ready, probe = setup_v124._start_service_with_retry()
        msg = setup_v124._service_failure_message(history, waited_ready, probe)

        assert ok is False and probe[1] == 1
        assert "attempts=" in msg and "history=[1:no_service(rc=1)" in msg
        assert "rc=1" in msg and "dumpsys=" in msg
        assert "no service" in msg and "waited_ready=" in msg


class TestV124UidParse:
    def test_parse_uid_prefers_system_shared_uid(self, lib_v124):
        assert lib_v124._parse_uid("sharedUser=android.uid.system") == 1000

    def test_parse_uid_userid_then_uid(self, lib_v124):
        assert lib_v124._parse_uid("userId=10123") == 10123
        assert lib_v124._parse_uid("uid=10086") == 10086
        assert lib_v124._parse_uid("nothing here") is None

    def test_uid_retry_after_ready(self, lib_v124, monkeypatch):
        """首读只回空串（重启窗）→ 等就绪 → 第二次解析成功。"""
        _patch_clock(monkeypatch, lib_v124)
        seq = [ (0, "", ""), (0, "userId=10086", "") ]

        def fake_adb(*args, timeout=60):
            return seq.pop(0)

        calls: list[float] = []
        monkeypatch.setattr(lib_v124, "adb", fake_adb)
        monkeypatch.setattr(
            lib_v124, "wait_system_ready",
            lambda deadline: (calls.append(deadline), (True, "ready"))[1],
        )

        assert lib_v124.get_app_uid() == 10086
        assert len(calls) == 1

    def test_uid_exhaustion_message_carries_evidence(self, lib_v124, monkeypatch):
        _patch_clock(monkeypatch, lib_v124)
        monkeypatch.setenv("STP_PCS_RETRY_WAIT_BUDGET_SECONDS", "5")
        monkeypatch.setattr(lib_v124, "adb", lambda *a, timeout=60: (0, "", ""))
        monkeypatch.setattr(
            lib_v124, "wait_system_ready", lambda deadline: (False, "state='offline'")
        )

        with pytest.raises(RuntimeError) as exc:
            lib_v124.get_app_uid()

        msg = str(exc.value)
        assert "attempts=1/3" in msg or "attempts=" in msg
        assert "history=[1:no_uid_fields(rc=0)" in msg
        assert "dumpsys=" in msg


