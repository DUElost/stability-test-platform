"""#1706：ScanRunner 空闲退出与入队交错窗口的丢单回归。

原缺陷：worker 在「队列已空」判定后**先释放锁**，`_worker_started` 的复位发生在
`finally`。入队线程若恰好落在「判定空」与「复位」之间，`_ensure_worker` 见标志
仍为 True 直接返回——留下「有 job、无 worker、标志 False」的队列，直到下一次
scan_now 或控制面重试才有人再拉起。

用例不用 sleep 竞跑：用一把「释放后回调」的锁把入队精确钉在该间隙。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

import pytest

from backend.agent.scan_runner import ScanRunner, _ScanJob


class _ReleaseHookLock:
    """包装 Lock：底层锁释放后触发钩子（回调时锁已让出，回调内可再取锁）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.on_release: Optional[Callable[[], None]] = None

    def __enter__(self) -> "_ReleaseHookLock":
        self._lock.acquire()
        return self

    def __exit__(self, *_exc) -> bool:
        self._lock.release()
        hook = self.on_release
        self.on_release = None
        if hook is not None:
            hook()
        return False


@pytest.fixture(autouse=True)
def _reset_scan_runner():
    ScanRunner._reset_for_tests()
    yield
    ScanRunner._reset_for_tests()


def _job(plan_run_id: int = 1) -> _ScanJob:
    return _ScanJob(plan_run_id=plan_run_id, host_id="host-a", is_final=True)


def _patch_worker_deps(monkeypatch, hook_lock: _ReleaseHookLock, execute) -> None:
    monkeypatch.setattr(ScanRunner, "_any_scan_runner_configured", classmethod(lambda cls: True))
    monkeypatch.setattr(ScanRunner, "_execute_job", classmethod(execute))
    monkeypatch.setattr(ScanRunner, "_worker_lock", hook_lock)


def test_enqueue_in_idle_exit_window_is_not_lost(monkeypatch):
    """「判定空 → 复位」间隙入队的 job 必须仍被消费（修复前无人拉起新 worker）。"""
    consumed: list[int] = []
    job2_done = threading.Event()
    hook_armed = threading.Event()

    def fake_execute(cls, job):
        consumed.append(job.plan_run_id)
        if job.plan_run_id == 1:
            assert hook_armed.wait(5), "前置：job1 执行须等到钩子就位"
        else:
            job2_done.set()

    hook_lock = _ReleaseHookLock()
    _patch_worker_deps(monkeypatch, hook_lock, fake_execute)

    ScanRunner._pending[1] = _job(1)
    ScanRunner._ensure_worker()  # 测试线程也会释放 worker 锁，故其返回后再装钩子
    hook_lock.on_release = lambda: ScanRunner.enqueue_scan_now(2, "host-a", is_final=True)
    hook_armed.set()

    assert job2_done.wait(5), (
        "空闲退出间隙入队的 job 未被消费：入队线程见到陈旧 `_worker_started=True`，"
        "而 worker 复位后无人再拉起（#1706）"
    )
    assert consumed == [1, 2]


def test_old_worker_exit_does_not_clear_restarted_worker_flag(monkeypatch):
    """旧 worker 收尾不得清掉已重启 worker 的标志（否则下次入队会再拉起一条线程）。"""
    job2_entered = threading.Event()
    job2_gate = threading.Event()
    hook_armed = threading.Event()
    exiting_workers: list[threading.Thread] = []

    def fake_execute(cls, job):
        if job.plan_run_id == 1:
            assert hook_armed.wait(5), "前置：job1 执行须等到钩子就位"
        else:
            job2_entered.set()
            assert job2_gate.wait(5), "job2 须保持运行，以观察重启 worker 的标志"

    hook_lock = _ReleaseHookLock()
    _patch_worker_deps(monkeypatch, hook_lock, fake_execute)

    def hook() -> None:
        # 钩子在旧 worker 线程上触发：记下它，随后 join 以确保其 finally 已跑完
        exiting_workers.append(threading.current_thread())
        ScanRunner.enqueue_scan_now(2, "host-a", is_final=True)

    ScanRunner._pending[1] = _job(1)
    ScanRunner._ensure_worker()
    hook_lock.on_release = hook
    hook_armed.set()

    assert job2_entered.wait(5), "新 worker 必须被拉起并执行 job2"
    assert len(exiting_workers) == 1
    exiting_workers[0].join(timeout=5)
    assert not exiting_workers[0].is_alive()

    try:
        assert ScanRunner._worker_started is True, (
            "旧 worker 的收尾复位覆盖了新 worker 的标志：标志已假死，"
            "下一次入队会重复拉起 worker 线程（#1706）"
        )
    finally:
        job2_gate.set()
