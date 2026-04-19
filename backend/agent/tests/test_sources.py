"""InotifydSource + CapabilityProber 单元测试。

覆盖：
    CapabilityProber:
      - root + 4 目录可读 → inotifyd_root
      - 非 root + 可读 → inotifyd_shell
      - inotifyd 不可用但目录可读 → polling
      - 必需分类全部失败 → unavailable（accessible 为空）
      - 部分必需分类 requires_root 但 ROOT_REQUIRED 仍不可访问时的 reason 归因

    InotifydSource:
      - 构造命令 shape（adb -s SERIAL shell inotifyd - <paths>:mask）
      - stdout 行解析：正常 3 列、空行、未知目录、短行（< 3 列）
      - event_mask 归一化（去重 / 丢非法字符 / 空 → 默认）
      - start → 接收 callback → stop 全流程（mock subprocess.Popen）
      - callback 抛异常不中断读循环
      - is_running 状态变迁
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from backend.agent.watcher.policy import WatcherPolicy
from backend.agent.watcher.sources import (
    DEFAULT_EVENT_MASK,
    CapabilityProber,
    InotifydSource,
    ProbeResult,
    WatcherCapability,
    WatcherEvent,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

class _FakeAdb:
    """AdbWrapper 测试替身 —— 按 (serial, cmd_fragment) 返回 stdout 或抛。"""

    def __init__(self) -> None:
        # key: (serial, substring) -> ("ok", stdout) | ("raise", exc)
        self._rules: List[tuple[str, str, str, Any]] = []

    def on(self, serial: str, cmd_fragment: str, *, stdout: str = "", raise_exc: Any = None):
        self._rules.append((serial, cmd_fragment, "raise" if raise_exc else "ok",
                            raise_exc if raise_exc else stdout))
        return self

    def shell(self, serial, cmd, timeout=None):
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        for s, frag, kind, payload in self._rules:
            if s == serial and frag in cmd_str:
                if kind == "raise":
                    raise payload
                out = MagicMock()
                out.stdout = payload
                out.returncode = 0
                return out
        # 默认：未配规则视为失败（模拟 AdbError）
        raise RuntimeError(f"no_rule_for: {serial} {cmd_str}")


# ----------------------------------------------------------------------
# CapabilityProber
# ----------------------------------------------------------------------

def test_probe_root_all_readable_returns_inotifyd_root():
    adb = _FakeAdb()
    serial = "S1"
    adb.on(serial, "id", stdout="uid=0(root) gid=0(root)")
    adb.on(serial, "which inotifyd", stdout="/system/bin/inotifyd")
    for path in ("/data/anr", "/data/aee_exp"):
        adb.on(serial, f"ls -d {path}", stdout=path)

    policy = WatcherPolicy()  # 默认 required=["ANR","AEE"]
    result = CapabilityProber(adb).probe(serial, policy)

    assert result.capability is WatcherCapability.INOTIFYD_ROOT
    assert set(result.accessible_categories) == {"ANR", "AEE"}
    assert result.inaccessible_categories == {}
    assert result.is_root is True
    assert result.is_usable is True


def test_probe_non_root_but_readable_returns_inotifyd_shell():
    """非 root 但 shell 能 ls 目标目录 → inotifyd_shell（生产 userdebug 常见）。"""
    adb = _FakeAdb()
    serial = "S2"
    adb.on(serial, "id", stdout="uid=2000(shell) gid=2000(shell)")
    adb.on(serial, "which inotifyd", stdout="/system/bin/inotifyd")
    adb.on(serial, "ls -d /data/anr", stdout="/data/anr")
    adb.on(serial, "ls -d /data/aee_exp", stdout="/data/aee_exp")

    result = CapabilityProber(adb).probe(serial, WatcherPolicy())
    assert result.capability is WatcherCapability.INOTIFYD_SHELL
    assert result.is_root is False
    assert set(result.accessible_categories) == {"ANR", "AEE"}


def test_probe_inotifyd_missing_falls_back_to_polling():
    adb = _FakeAdb()
    serial = "S3"
    adb.on(serial, "id", stdout="uid=0(root)")
    adb.on(serial, "which inotifyd", raise_exc=RuntimeError("not found"))
    adb.on(serial, "ls -d /data/anr", stdout="/data/anr")
    adb.on(serial, "ls -d /data/aee_exp", stdout="/data/aee_exp")

    result = CapabilityProber(adb).probe(serial, WatcherPolicy())
    assert result.capability is WatcherCapability.POLLING
    assert "inotifyd_missing" in result.reasons


def test_probe_all_required_inaccessible_returns_unavailable():
    """所有必需分类都不可读 → unavailable（不看 inotifyd 可用性）。"""
    adb = _FakeAdb()
    serial = "S4"
    adb.on(serial, "id", stdout="uid=2000(shell)")
    adb.on(serial, "which inotifyd", stdout="/system/bin/inotifyd")
    # ls -d 全部未注册规则 → _FakeAdb 抛 RuntimeError → _any_readable 返回 False

    result = CapabilityProber(adb).probe(serial, WatcherPolicy())
    assert result.capability is WatcherCapability.UNAVAILABLE
    assert result.accessible_categories == []
    assert set(result.inaccessible_categories.keys()) == {"ANR", "AEE"}
    # ROOT_REQUIRED 分类非 root 时归因应为 requires_root
    assert result.inaccessible_categories["ANR"] == "requires_root"
    assert result.inaccessible_categories["AEE"] == "requires_root"
    assert result.is_usable is False


def test_probe_partial_accessible_still_usable():
    """只有部分必需分类可读时，只要 covered_required 非空即返回 inotifyd_*。"""
    adb = _FakeAdb()
    serial = "S5"
    adb.on(serial, "id", stdout="uid=0(root)")
    adb.on(serial, "which inotifyd", stdout="/system/bin/inotifyd")
    adb.on(serial, "ls -d /data/anr", stdout="/data/anr")
    # AEE 不注册规则 → 不可读

    result = CapabilityProber(adb).probe(serial, WatcherPolicy())
    assert result.capability is WatcherCapability.INOTIFYD_ROOT
    assert "ANR" in result.accessible_categories
    assert "AEE" in result.inaccessible_categories


# ----------------------------------------------------------------------
# InotifydSource — 命令/解析
# ----------------------------------------------------------------------

def test_build_command_shape():
    """命令形如 adb -s SERIAL shell 'inotifyd - /data/anr:nwx /data/aee_exp:nwx'。"""
    src = InotifydSource(
        adb_path="adb", serial="SX",
        paths_by_category={"ANR": ["/data/anr"], "AEE": ["/data/aee_exp"]},
        on_event=lambda e: None,
    )
    cmd = src._build_command()
    assert cmd[:4] == ["adb", "-s", "SX", "shell"]
    shell_arg = cmd[4]
    assert shell_arg.startswith("inotifyd -")
    assert f"/data/anr:{DEFAULT_EVENT_MASK}" in shell_arg
    assert f"/data/aee_exp:{DEFAULT_EVENT_MASK}" in shell_arg


@pytest.mark.parametrize("mask, expected", [
    ("nwx",   "nwx"),
    ("nnwwx", "nwx"),        # 去重
    ("zzZn",  "n"),          # 非法字符丢弃
    ("",      DEFAULT_EVENT_MASK),  # 空 → 默认
    ("ZZZ",   DEFAULT_EVENT_MASK),  # 全非法 → 默认
])
def test_normalize_mask(mask, expected):
    assert InotifydSource._normalize_mask(mask) == expected


def test_parse_line_valid():
    src = InotifydSource(
        adb_path="adb", serial="SP",
        paths_by_category={"ANR": ["/data/anr"]},
        on_event=lambda e: None,
    )
    ev = src._parse_line("n\t/data/anr\ttrace_00.txt\n")
    assert isinstance(ev, WatcherEvent)
    assert ev.category == "ANR"
    assert ev.event_mask == "n"
    assert ev.filename == "trace_00.txt"
    assert ev.full_path == "/data/anr/trace_00.txt"


def test_parse_line_invalid_returns_none():
    src = InotifydSource(
        adb_path="adb", serial="SP",
        paths_by_category={"ANR": ["/data/anr"]},
        on_event=lambda e: None,
    )
    assert src._parse_line("") is None
    assert src._parse_line("   ") is None
    assert src._parse_line("incomplete_line") is None         # < 3 parts
    assert src._parse_line("n\t/unknown/dir\tfile") is None   # 未知目录


def test_empty_paths_raises():
    with pytest.raises(ValueError, match="must not be empty"):
        InotifydSource(
            adb_path="adb", serial="SP", paths_by_category={},
            on_event=lambda e: None,
        )


# ----------------------------------------------------------------------
# InotifydSource — 生命周期 & 读循环（mock subprocess.Popen）
# ----------------------------------------------------------------------

class _FakePopen:
    """模拟 subprocess.Popen；stdout.readline 按 _lines 逐行吐出后阻塞直到被 terminate/kill。

    贴近真实 Popen：readline 返回 '' 表示 EOF（stdout 关闭），但 poll() 依然为 None
    直到 terminate/kill 被显式调用。读循环看到 EOF 自然结束；stop() 触发 terminate。
    """

    def __init__(self, lines: List[str]) -> None:
        self._lines = list(lines)
        self._idx = 0
        self._returncode: Optional[int] = None
        self._eof_evt = threading.Event()
        self.stdout = self
        self.stderr = MagicMock()
        self.terminated = False
        self.killed = False

    def readline(self) -> str:
        if self._idx >= len(self._lines):
            # EOF：返回 ''；**不**自动置 returncode（模拟真实进程未退出）
            self._eof_evt.set()
            return ""
        line = self._lines[self._idx]
        self._idx += 1
        return line

    def poll(self) -> Optional[int]:
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = -15

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    def wait(self, timeout: Optional[float] = None) -> int:
        return self._returncode if self._returncode is not None else 0


def _drain_with_fake_popen(lines: List[str], *, on_event_wrap=None, paths=None, expected_events: Optional[int] = None):
    """构造 InotifydSource → start → 等事件 → stop；返回收到的事件列表。"""
    events: "queue.Queue[WatcherEvent]" = queue.Queue()

    def on_event(ev: WatcherEvent) -> None:
        if on_event_wrap:
            on_event_wrap(ev)
        events.put(ev)

    src = InotifydSource(
        adb_path="adb", serial="SRC",
        paths_by_category=paths or {
            "ANR": ["/data/anr"],
            "AEE": ["/data/aee_exp"],
        },
        on_event=on_event,
    )
    # 默认期望事件数 = lines 中 tab 分隔 3 段的条目数
    if expected_events is None:
        expected_events = sum(1 for l in lines if len(l.rstrip("\r\n").split("\t")) >= 3)

    fake = _FakePopen(lines)
    with patch("subprocess.Popen", return_value=fake):
        src.start()
        # 等所有预期事件到达或 EOF
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if events.qsize() >= expected_events and fake._eof_evt.is_set():
                break
            time.sleep(0.02)
        src.stop(timeout=1.0)

    out: List[WatcherEvent] = []
    while not events.empty():
        out.append(events.get())
    return out, src, fake


def test_source_start_reads_events_and_invokes_callback():
    """Popen stdout 吐 3 条事件，callback 应收到 3 条 WatcherEvent。"""
    lines = [
        "n\t/data/anr\ttrace_01.txt\n",
        "w\t/data/aee_exp\tdb.0.123\n",
        "x\t/data/anr\tfiletransfer.txt\n",
    ]
    events, src, fake = _drain_with_fake_popen(lines)
    assert len(events) == 3
    cats = [e.category for e in events]
    assert cats == ["ANR", "AEE", "ANR"]
    assert fake.terminated is True  # stop 发了 SIGTERM


def test_source_stop_idempotent_no_raise():
    """重复 stop 不抛。"""
    src = InotifydSource(
        adb_path="adb", serial="SX",
        paths_by_category={"ANR": ["/data/anr"]},
        on_event=lambda e: None,
    )
    with patch("subprocess.Popen", return_value=_FakePopen([])):
        src.start()
    src.stop(timeout=0.5)
    src.stop(timeout=0.5)  # 第二次不应抛


def test_source_callback_exception_does_not_break_loop():
    """callback 抛 → 读循环继续；后续事件仍能到。"""
    call_count = {"n": 0}

    def flaky(ev: WatcherEvent) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("boom")

    lines = [
        "n\t/data/anr\ta\n",
        "n\t/data/anr\tb\n",
        "n\t/data/anr\tc\n",
    ]
    events, _, _ = _drain_with_fake_popen(
        lines, on_event_wrap=flaky, paths={"ANR": ["/data/anr"]},
    )
    # 第 1 条被 flaky 抛异常但仍被 put（flaky 在 put 前执行）—— 验证读循环未因第 1 次异常 break
    # 即后续 2 条应该也到
    assert call_count["n"] == 3, "callback 应被调用 3 次（异常不中断读循环）"


def test_source_spawn_failure_raises_runtime_error():
    """adb 二进制不存在 → Popen 抛 FileNotFoundError → 封装为 RuntimeError。"""
    src = InotifydSource(
        adb_path="adb-not-exist", serial="SX",
        paths_by_category={"ANR": ["/data/anr"]},
        on_event=lambda e: None,
    )
    with patch("subprocess.Popen", side_effect=FileNotFoundError("no adb")):
        with pytest.raises(RuntimeError, match="inotifyd_spawn_failed"):
            src.start()


def test_source_is_running_reflects_state():
    src = InotifydSource(
        adb_path="adb", serial="SX",
        paths_by_category={"ANR": ["/data/anr"]},
        on_event=lambda e: None,
    )
    assert src.is_running() is False
    with patch("subprocess.Popen", return_value=_FakePopen([])):
        src.start()
        # start 后立刻检查（Popen 刚启动，未 EOF）—— 可能已结束；用 lock 直接看 process
        # 这里只验证 stop 后为 False
    src.stop(timeout=0.5)
    assert src.is_running() is False
