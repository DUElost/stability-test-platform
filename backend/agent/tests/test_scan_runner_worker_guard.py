"""#754：scan 队列 worker 的异常韧性。

原缺陷：`_worker_started` 只在 `_worker_loop` **正常 return**（队列排空）路径复位。
一旦 `_execute_job` 抛出——例如 STP_UNISOC_LOG_SCAN_POLL_SECONDS 被误配成非数字、
`int()` 打穿整条调用链——线程死亡而标志仍为 True，`_ensure_worker` 于是永远认为
worker 还活着，scan 队列**永久停摆直到进程重启**。

这些用例锁定两点不变量：
1. 单次 job 异常不得终止 worker 线程（后续 job 仍被处理）；
2. worker 无论以何种方式退出，`_worker_started` 都必须复位（可被重启）。
"""

from __future__ import annotations

import threading

import pytest

from backend.agent.scan_runner import ScanRunner, _ScanJob


@pytest.fixture(autouse=True)
def _reset_scan_runner():
    ScanRunner._reset_for_tests()
    yield
    ScanRunner._reset_for_tests()


def _job(plan_run_id: int = 1) -> _ScanJob:
    return _ScanJob(
        plan_run_id=plan_run_id,
        host_id="host-a",
        is_final=True,
    )


def test_job_exception_does_not_stop_worker_and_next_job_runs(monkeypatch):
    """单次失败后队列必须继续推进——这是「永久停摆」的直接回归。"""
    processed: list[int] = []
    calls = {"n": 0}

    monkeypatch.setattr(ScanRunner, "_any_scan_runner_configured", classmethod(lambda cls: True))

    def fake_execute(cls, job):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("invalid literal for int() with base 10: 'not-a-number'")
        processed.append(job.plan_run_id)

    monkeypatch.setattr(ScanRunner, "_execute_job", classmethod(fake_execute))

    ScanRunner._pending[1] = _job(1)
    ScanRunner._pending[2] = _job(2)

    t = threading.Thread(target=ScanRunner._worker_loop, daemon=True)
    t.start()
    t.join(timeout=5)

    assert not t.is_alive(), "worker 线程应正常收尾，而不是挂死"
    assert calls["n"] == 2, "首个 job 抛异常后，第二个 job 仍必须被处理"
    assert processed == [2]


def test_worker_started_reset_even_when_execute_raises(monkeypatch):
    """异常退出路径也必须复位 `_worker_started`（否则 `_ensure_worker` 永不重启）。"""
    monkeypatch.setattr(ScanRunner, "_any_scan_runner_configured", classmethod(lambda cls: True))

    def boom(cls, job):
        raise RuntimeError("worker-killing failure")

    monkeypatch.setattr(ScanRunner, "_execute_job", classmethod(boom))

    ScanRunner._pending[1] = _job(1)
    ScanRunner._worker_started = True

    t = threading.Thread(target=ScanRunner._worker_loop, daemon=True)
    t.start()
    t.join(timeout=5)

    assert not t.is_alive()
    assert ScanRunner._worker_started is False, (
        "worker 退出后标志必须复位，否则 scan 队列永久停摆（#754）"
    )


def test_worker_started_reset_on_normal_drain():
    """既有正常路径语义不得回归：排空后标志复位。"""
    ScanRunner._worker_started = True
    ScanRunner._worker_loop()
    assert ScanRunner._worker_started is False


def test_ensure_worker_restarts_after_guard_reset(monkeypatch):
    """复位后可重新拉起 worker——证明「永久停摆」被解除。"""
    monkeypatch.setattr(ScanRunner, "_any_scan_runner_configured", classmethod(lambda cls: True))

    started: list[int] = []

    def boom(cls, job):
        started.append(job.plan_run_id)
        raise RuntimeError("fail once")

    monkeypatch.setattr(ScanRunner, "_execute_job", classmethod(boom))

    # 第一轮：worker 因异常退出，但守卫使其复位
    ScanRunner._pending[1] = _job(1)
    ScanRunner._ensure_worker()
    for _ in range(100):
        if not ScanRunner._worker_started:
            break
        threading.Event().wait(0.05)
    assert ScanRunner._worker_started is False

    # 第二轮：`_ensure_worker` 必须能重新启动（修复前此处是死路）
    ScanRunner._pending[2] = _job(2)
    ScanRunner._ensure_worker()
    for _ in range(100):
        if 2 in started:
            break
        threading.Event().wait(0.05)
    assert 2 in started, "复位后 `_ensure_worker` 必须能重新拉起 worker"
