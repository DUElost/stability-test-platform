"""#3 — Pipeline subprocess 进程组隔离 + 跨平台 kill 进程树.

覆盖:
- _popen_isolation_kwargs POSIX/Windows 分支
- _terminate_process_tree:
  - proc 已退出 no-op
  - POSIX: SIGTERM 整组 → wait 成功 → 不再 SIGKILL
  - POSIX: SIGTERM → wait TimeoutExpired → SIGKILL 整组
  - POSIX: getpgid / killpg ProcessLookupError 安全 swallow
  - Windows: taskkill /T /F /PID
  - Windows: taskkill 失败 fallback proc.kill
- _run_script_action TimeoutExpired 分支调用 _terminate_process_tree (不再裸 kill)
"""
from __future__ import annotations

import io
import os
import signal
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest


from backend.agent import pipeline_engine
from backend.agent.pipeline_engine import (
    _popen_isolation_kwargs,
    _process_group_alive,
    _remember_process_group,
    _terminate_process_tree,
)


# ── _popen_isolation_kwargs ─────────────────────────────────────────────


def test_isolation_kwargs_posix(monkeypatch):
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", False)
    kw = _popen_isolation_kwargs()
    assert kw == {"start_new_session": True}


def test_isolation_kwargs_windows(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", True)
    monkeypatch.setattr(pipeline_engine, "subprocess", SimpleNamespace(CREATE_NEW_PROCESS_GROUP=0x00000200))
    kw = _popen_isolation_kwargs()
    assert "creationflags" in kw
    assert kw["creationflags"] == 0x00000200


# ── _terminate_process_tree:已退出 no-op ───────────────────────────────


def test_terminate_no_op_when_proc_exited():
    proc = MagicMock()
    proc.poll.return_value = 0  # 已退出
    # 不应调用任何 kill 系列函数
    with patch("backend.agent.pipeline_engine.os.killpg", create=True) as killpg, \
         patch("backend.agent.pipeline_engine.subprocess.run") as srun:
        _terminate_process_tree(proc)
        killpg.assert_not_called()
        srun.assert_not_called()


def test_terminate_no_op_when_group_already_gone(monkeypatch):
    """#1003：父已退出且整组已散 —— 不应再向该 pgid 发任何信号。"""
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", False)
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.pid = 1234
    proc._stp_pgid = 1234  # spawn 时留存的组身份

    with patch("backend.agent.pipeline_engine.os.killpg", create=True) as killpg, \
         patch(
             "backend.agent.pipeline_engine._process_group_alive", return_value=False,
         ) as alive:
        _terminate_process_tree(proc)
        alive.assert_called_once_with(1234)
        killpg.assert_not_called()


# ── POSIX 分支 ──────────────────────────────────────────────────────────


def test_terminate_posix_sigterm_succeeds_no_sigkill(monkeypatch):
    """#1003：SIGTERM 后整组收敛（父已回收 + 组已散）→ 不再 SIGKILL。"""
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", False)
    proc = MagicMock()
    proc.pid = 1234
    proc._stp_pgid = 1234
    # 第一次 poll 在 _resolve_pgid 之前不会发生（走留存路径），其后父已回收
    proc.poll.return_value = 0

    with patch(
        "backend.agent.pipeline_engine.os.getpgid", return_value=1234, create=True,
    ), patch(
        "backend.agent.pipeline_engine.os.killpg", create=True,
    ) as killpg, patch(
        # 入口探测：组还在；收敛探测：组已散
        "backend.agent.pipeline_engine._process_group_alive",
        side_effect=[True, False],
    ):
        _terminate_process_tree(proc, grace_seconds=0.01)
        sigs = [c.args[1] for c in killpg.call_args_list]
        assert sigs == [signal.SIGTERM]


def test_terminate_posix_escalates_when_parent_exited_but_group_alive(monkeypatch):
    """#1003（R07-F02 核心）：父已退出、子孙仍在 → 必须升级到 SIGKILL。"""
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", False)
    proc = MagicMock()
    proc.pid = 1234
    proc._stp_pgid = 1234
    proc.poll.return_value = 0  # 父已退出并被回收

    with patch(
        "backend.agent.pipeline_engine.os.getpgid", return_value=1234, create=True,
    ), patch(
        "backend.agent.pipeline_engine.os.killpg", create=True,
    ) as killpg, patch(
        "backend.agent.pipeline_engine._process_group_alive", return_value=True,
    ):
        _terminate_process_tree(proc, grace_seconds=0.01)
        sigs = [c.args[1] for c in killpg.call_args_list]
        assert sigs == [signal.SIGTERM, pipeline_engine._SIGKILL]


def test_terminate_posix_escalates_to_sigkill_on_wait_timeout(monkeypatch):
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", False)
    proc = MagicMock()
    proc.poll.return_value = None  # 父始终不退出
    proc.pid = 4321

    with patch(
        "backend.agent.pipeline_engine.os.getpgid", return_value=4321, create=True,
    ), patch(
        "backend.agent.pipeline_engine.os.killpg", create=True,
    ) as killpg, patch(
        "backend.agent.pipeline_engine._process_group_alive", return_value=True,
    ):
        _terminate_process_tree(proc, grace_seconds=0.01)
        sigs = [c.args[1] for c in killpg.call_args_list]
        assert sigs == [signal.SIGTERM, pipeline_engine._SIGKILL]


def test_terminate_posix_no_pgid_no_signal(monkeypatch):
    """#1003：父已回收且无留存组身份 —— 拿不到可信 pgid，宁可不动手。"""
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", False)
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.pid = 4242

    with patch(
        "backend.agent.pipeline_engine.os.getpgid", return_value=4242, create=True,
    ) as gp, patch(
        "backend.agent.pipeline_engine.os.killpg", create=True,
    ) as killpg:
        _terminate_process_tree(proc)
        gp.assert_not_called()
        killpg.assert_not_called()


def test_terminate_posix_swallows_getpgid_lookup_error(monkeypatch):
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", False)
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 9999

    with patch(
        "backend.agent.pipeline_engine.os.getpgid",
        side_effect=ProcessLookupError(),
        create=True,
    ), patch(
        "backend.agent.pipeline_engine.os.killpg", create=True,
    ) as killpg:
        _terminate_process_tree(proc)
        killpg.assert_not_called()  # getpgid 失败直接 return,不该 killpg


def test_terminate_posix_swallows_killpg_lookup_error(monkeypatch):
    """proc 在 SIGTERM 那一瞬间退出 → killpg 抛 ProcessLookupError,不应外溢。"""
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", False)
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 7777

    with patch(
        "backend.agent.pipeline_engine.os.getpgid", return_value=7777, create=True,
    ), patch(
        "backend.agent.pipeline_engine.os.killpg",
        side_effect=ProcessLookupError(),
        create=True,
    ):
        _terminate_process_tree(proc)  # 不应抛


# ── Windows 分支 ────────────────────────────────────────────────────────


def test_terminate_windows_taskkill_called(monkeypatch):
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", True)
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 5555

    with patch("backend.agent.pipeline_engine.subprocess.run") as srun:
        _terminate_process_tree(proc)
        args = srun.call_args.args[0]
        assert args[0] == "taskkill"
        assert "/T" in args
        assert "/F" in args
        assert "5555" in args


def test_terminate_windows_taskkill_failure_falls_back_to_proc_kill(monkeypatch):
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", True)
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 6666

    with patch(
        "backend.agent.pipeline_engine.subprocess.run",
        side_effect=OSError("taskkill missing"),
    ):
        _terminate_process_tree(proc)
        proc.kill.assert_called_once()


# ── #1003 真实进程组回归：父退出 ≠ 整组退出 ─────────────────────────────


def _spawn_parent_exits_first(tmp_path):
    """起一个新进程组：sh 立刻退出，后台 python 子孙忽略 SIGTERM 常驻。"""
    child = tmp_path / "ignore_term_child.py"
    child.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True:\n"
        "    time.sleep(0.1)\n"
    )
    return subprocess.Popen(
        ["/bin/sh", "-c", f"{sys.executable} {child} &"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **_popen_isolation_kwargs(),
    )


def test_terminate_reaps_orphaned_descendants_after_parent_exit(tmp_path):
    """父脚本已退出、同组子孙忽略 SIGTERM → 必须按组收敛到 SIGKILL（R07-F02）。

    修复前：proc.poll() 非 None 直接 return / wait 成功即 return —— 子孙漏网，
    Agent 继续上报终态并释放许可，残留进程仍在操作设备。
    """
    if not hasattr(os, "killpg"):
        pytest.skip("POSIX-only")
    proc = _spawn_parent_exits_first(tmp_path)
    _remember_process_group(proc)
    pgid = proc._stp_pgid
    try:
        proc.wait(timeout=10)  # 父 sh 立刻退出，子孙变孤儿但仍在同一进程组
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not _process_group_alive(pgid):
            time.sleep(0.05)
        assert _process_group_alive(pgid), "子孙未存活，用例前提不成立"

        _terminate_process_tree(proc, grace_seconds=0.5)

        assert not _process_group_alive(pgid), "进程组未收敛，残留子孙仍在操作设备"
    finally:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except Exception:
            pass


# ── _run_script_action 超时分支接到 _terminate_process_tree ─────────────


def test_run_script_action_timeout_uses_terminate_process_tree(monkeypatch, tmp_path):
    """timeout 分支不应再直接 proc.kill(),应走 _terminate_process_tree。"""
    # 准备一个最小可执行 ScriptEntry
    entry = MagicMock()
    entry.nfs_path = str(tmp_path / "fake.py")
    entry.script_type = "python"
    (tmp_path / "fake.py").write_text("# noop")

    registry = MagicMock()
    registry.resolve.return_value = entry

    # 用 __new__ 绕过构造函数,只装 _run_script_action 需要的属性
    engine = pipeline_engine.PipelineEngine.__new__(pipeline_engine.PipelineEngine)
    engine._script_registry = registry
    engine._adb_path = "adb"
    engine._nfs_root = "/nfs"
    engine._shared = {}        # _run_script_action 透传 STP_SHARED_METRICS 需要
    engine._local_db = None    # _run_script_action 注入 STP_AGENT_STATE_DB 时探测

    proc = MagicMock()
    # _pump_process 的 reader 线程逐行读取 stdout/stderr；必须给空流，
    # 否则 MagicMock.readline() 永不返回 ""，reader 无限循环把内存打爆（#123）。
    proc.stdout = io.StringIO("")
    proc.stderr = io.StringIO("")
    proc.returncode = -9
    proc.pid = 11111
    proc.poll.return_value = None

    ctx = pipeline_engine.StepContext(
        adb=MagicMock(), serial="S1", params={}, run_id=1, step_id=1,
        logger=MagicMock(), log_dir="/tmp", adb_path="adb", nfs_root="/nfs",
    )
    step = {"action": "script:fake", "version": "v1", "timeout_seconds": 1}

    with patch(
        "backend.agent.pipeline_engine.subprocess.Popen", return_value=proc
    ) as popen, patch(
        "backend.agent.pipeline_engine._terminate_process_tree"
    ) as term:
        result = engine._run_script_action(ctx, step)

    # Popen 拿到了 isolation kwargs
    popen_kwargs = popen.call_args.kwargs
    iso = _popen_isolation_kwargs()
    for k, v in iso.items():
        assert popen_kwargs.get(k) == v, f"Popen 缺 isolation kwarg {k}={v}"

    # 超时分支走 _terminate_process_tree 而不是 proc.kill
    term.assert_called_once_with(proc)
    proc.kill.assert_not_called()

    assert result.success is False
    assert result.exit_code == 124
    # 文案带上是哪个钟、跑了多久（#115 阶段 1）——排查时要能区分总时长钟与停滞钟
    assert result.error_message.startswith("script timeout after ")
