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

import signal
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from backend.agent import pipeline_engine
from backend.agent.pipeline_engine import (
    _popen_isolation_kwargs,
    _terminate_process_tree,
)


# ── _popen_isolation_kwargs ─────────────────────────────────────────────


def test_isolation_kwargs_posix(monkeypatch):
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", False)
    kw = _popen_isolation_kwargs()
    assert kw == {"start_new_session": True}


def test_isolation_kwargs_windows(monkeypatch):
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", True)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
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


# ── POSIX 分支 ──────────────────────────────────────────────────────────


def test_terminate_posix_sigterm_succeeds_no_sigkill(monkeypatch):
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", False)
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 1234
    # wait 在 grace 内成功
    proc.wait.return_value = 0

    with patch(
        "backend.agent.pipeline_engine.os.getpgid", return_value=1234, create=True,
    ) as gp, patch(
        "backend.agent.pipeline_engine.os.killpg", create=True,
    ) as killpg:
        _terminate_process_tree(proc, grace_seconds=0.01)
        gp.assert_called_once_with(1234)
        # 只发了 SIGTERM,没发 SIGKILL
        assert killpg.call_count == 1
        sig_used = killpg.call_args.args[1]
        assert sig_used == signal.SIGTERM


def test_terminate_posix_escalates_to_sigkill_on_wait_timeout(monkeypatch):
    monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", False)
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4321
    proc.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=0.01)

    with patch(
        "backend.agent.pipeline_engine.os.getpgid", return_value=4321, create=True,
    ), patch(
        "backend.agent.pipeline_engine.os.killpg", create=True,
    ) as killpg:
        _terminate_process_tree(proc, grace_seconds=0.01)
        sigs = [c.args[1] for c in killpg.call_args_list]
        assert sigs == [signal.SIGTERM, pipeline_engine._SIGKILL]


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
    # communicate 第一次抛超时,第二次返回 ("", "")
    proc.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd="x", timeout=1),
        ("", ""),
    ]
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
    assert result.error_message == "script timeout"
