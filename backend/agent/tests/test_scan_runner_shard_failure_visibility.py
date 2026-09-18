"""#739 面② / #2188 D 步（#2474 残余）：分片登记失败的 worker 可见性。

原缺陷：`upload_manager` 按 #2474 的写侧契约对「文件已落 dedup/、清单未写
_meta」的半交付 raise，但 `ScanRunner._worker_loop` 的 catch-all 只落
`scan_queue_job_failed` 日志——typed raise 被吞成普通失败，控制面无从区分、
无计数、不可告警，#2474 想消除的静默半交付在端到端上依旧成立。

锁定三点不变量：
1. ShardRegistrationError 走专用日志标记 + 计数器（与普通 scan 失败区分）；
2. 计数器经 `shard_register_failure_total()` 暴露（main 心跳上报数据源）；
3. worker 存活语义不回归（#754/#1706：单 job 失败不带走队列）。
"""

from __future__ import annotations

import logging
import threading

import pytest

from backend.agent.scan_runner import ScanRunner, _ScanJob
from backend.agent.upload_manager import ShardRegistrationError


@pytest.fixture(autouse=True)
def _reset_scan_runner():
    ScanRunner._reset_for_tests()
    yield
    ScanRunner._reset_for_tests()


def _job(plan_run_id: int) -> _ScanJob:
    return _ScanJob(
        plan_run_id=plan_run_id,
        host_id="host-a",
        is_final=True,
    )


def _run_worker_until_drained() -> None:
    t = threading.Thread(target=ScanRunner._worker_loop, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "worker 线程应正常收尾"


def test_shard_failure_counted_with_distinct_marker(monkeypatch, caplog):
    """typed 半交付失败：专用标记 + 计数器 +1，且 worker 继续处理后续 job。"""
    monkeypatch.setattr(ScanRunner, "_any_scan_runner_configured", classmethod(lambda cls: True))
    processed: list[int] = []
    calls = {"n": 0}

    def fake_execute(cls, job):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ShardRegistrationError(
                f"shard register failed plan_run={job.plan_run_id} host={job.host_id}"
            )
        processed.append(job.plan_run_id)

    monkeypatch.setattr(ScanRunner, "_execute_job", classmethod(fake_execute))

    ScanRunner._pending[1] = _job(1)
    ScanRunner._pending[2] = _job(2)

    with caplog.at_level(logging.ERROR, logger="backend.agent.scan_runner"):
        _run_worker_until_drained()

    assert calls["n"] == 2, "半交付失败后，后续 job 仍必须被处理（#754 语义）"
    assert processed == [2]
    assert ScanRunner.shard_register_failure_total() == 1
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "scan_shard_register_failed" in joined
    # 不得再混用普通失败标记（区分是本次修复的核心交付）
    assert "scan_queue_job_failed" not in joined


def test_generic_failure_not_counted_as_shard_failure(monkeypatch, caplog):
    """普通异常维持既有口径：scan_queue_job_failed，计数器不动。"""
    monkeypatch.setattr(ScanRunner, "_any_scan_runner_configured", classmethod(lambda cls: True))

    def boom(cls, job):
        raise ValueError("scan tool crashed")

    monkeypatch.setattr(ScanRunner, "_execute_job", classmethod(boom))

    ScanRunner._pending[1] = _job(1)

    with caplog.at_level(logging.ERROR, logger="backend.agent.scan_runner"):
        _run_worker_until_drained()

    assert ScanRunner.shard_register_failure_total() == 0
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "scan_queue_job_failed" in joined
    assert "scan_shard_register_failed" not in joined


def test_counter_accumulates_and_resets_for_tests(monkeypatch):
    monkeypatch.setattr(ScanRunner, "_any_scan_runner_configured", classmethod(lambda cls: True))

    def boom(cls, job):
        raise ShardRegistrationError("shard register failed")

    monkeypatch.setattr(ScanRunner, "_execute_job", classmethod(boom))

    ScanRunner._pending[1] = _job(1)
    ScanRunner._pending[2] = _job(2)
    _run_worker_until_drained()
    assert ScanRunner.shard_register_failure_total() == 2

    ScanRunner._reset_for_tests()
    assert ScanRunner.shard_register_failure_total() == 0
