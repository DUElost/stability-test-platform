"""flash_firmware v1.3.13 超时逃生契约（#811）。

守什么：超时后必须 SIGTERM→宽限→SIGKILL 兜底，并用 poll() 确认 flash_tool
已死；仍存活则 fail-closed（RuntimeError），不得进入下一次尝试与未死实例并发
抢刷。

脚本以文件方式加载（与部署形态一致），仅依赖 stdlib，无包副作用。
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = (
    _REPO_ROOT
    / "backend/agent/scripts/flash_firmware/v1.3.13/flash_firmware.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("flash_firmware_v1313", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeStream:
    def __iter__(self):
        return iter(())

    def close(self):
        pass


class _FakeProc:
    """poll() 直到 terminate 后才返回退出码；可选地在 SIGKILL 后仍不死。"""

    def __init__(self, *, dies_on_term=True):
        self.pid = 4242
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self._alive = True
        self._dies_on_term = dies_on_term
        self.signals: list[int] = []

    def poll(self):
        return None if self._alive else 0

    def signal(self, sig: int):
        self.signals.append(sig)
        if not self._dies_on_term:
            return  # 顽固进程：SIGTERM/SIGKILL 都不死
        self._alive = False

    def wait(self, timeout=None):
        if self._alive:
            raise subprocess.TimeoutExpired(cmd=["flash_tool"], timeout=timeout or 0)
        return 0

    def kill(self):
        self.signal(9)


def _patch(monkeypatch, module, proc):
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: proc)
    killed: list[int] = []

    def fake_killpg(pgid, sig):
        killed.append(sig)
        proc.signal(sig)

    monkeypatch.setattr(module.os, "getpgid", lambda pid: pid, raising=True)
    monkeypatch.setattr(module.os, "killpg", fake_killpg, raising=True)
    return killed


def test_timeout_terminates_with_sigterm_then_sigkill_and_raises_timeoutexpired(monkeypatch):
    module = _load()
    proc = _FakeProc(dies_on_term=True)
    killed = _patch(monkeypatch, module, proc)

    with pytest.raises(subprocess.TimeoutExpired):
        module._run_flash_tool_with_progress(
            cmd=["flash_tool"], cwd=".", env={}, timeout=0,
            on_stage=lambda *_: None, on_percent=lambda *_: None,
        )

    assert killed[0] == 15, f"应先发 SIGTERM，实际 {killed}"
    assert proc.poll() is not None  # 进入下一次尝试前已确认死亡


def test_stubborn_process_after_sigkill_fails_closed(monkeypatch):
    module = _load()
    proc = _FakeProc(dies_on_term=False)  # 连 SIGKILL 都不死
    killed = _patch(monkeypatch, module, proc)

    with pytest.raises(RuntimeError, match="SIGKILL"):
        module._run_flash_tool_with_progress(
            cmd=["flash_tool"], cwd=".", env={}, timeout=0,
            on_stage=lambda *_: None, on_percent=lambda *_: None,
        )

    assert 9 in killed, f"宽限未退后应补 SIGKILL，实际 {killed}"


def test_new_version_directory_exists_and_is_immutable_target():
    assert _SCRIPT.is_file(), "v1.3.13 脚本目录缺失"
    # 旧版本仍在（不可原地改，新行为以新版本表达）
    old = _REPO_ROOT / "backend/agent/scripts/flash_firmware/v1.3.12/flash_firmware.py"
    assert old.is_file()
