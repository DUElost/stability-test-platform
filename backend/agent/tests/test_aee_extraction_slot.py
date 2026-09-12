"""#740 — host-level AEE extraction slot."""

from __future__ import annotations

import threading
import time

import pytest

from backend.agent.aee.extraction_slot import (
    get_extraction_limit,
    host_extraction_slot,
    reset_extraction_slot_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_slot(monkeypatch):
    monkeypatch.delenv("STP_AEE_MAX_CONCURRENT_PULLS", raising=False)
    reset_extraction_slot_for_tests()
    yield
    reset_extraction_slot_for_tests()


def test_default_limit_is_two():
    assert get_extraction_limit() == 2


def test_env_overrides_limit(monkeypatch):
    monkeypatch.setenv("STP_AEE_MAX_CONCURRENT_PULLS", "3")
    reset_extraction_slot_for_tests()
    assert get_extraction_limit() == 3


def test_bad_env_falls_back(monkeypatch):
    monkeypatch.setenv("STP_AEE_MAX_CONCURRENT_PULLS", "nope")
    reset_extraction_slot_for_tests()
    assert get_extraction_limit() == 2


def test_concurrent_pulls_queue_beyond_limit(monkeypatch):
    """第三路必须等前两路释放——证明主机级排队而非无界并发。"""
    monkeypatch.setenv("STP_AEE_MAX_CONCURRENT_PULLS", "2")
    reset_extraction_slot_for_tests()

    gate = threading.Event()
    entered = []
    lock = threading.Lock()
    done = []

    def worker(name: str) -> None:
        with host_extraction_slot(purpose=name):
            with lock:
                entered.append(name)
            gate.wait(timeout=2.0)
        with lock:
            done.append(name)

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b", "c")]
    for th in threads:
        th.start()
    # 给前两路时间抢槽
    time.sleep(0.1)
    with lock:
        assert len(entered) == 2, entered
        assert "c" not in entered
    gate.set()
    for th in threads:
        th.join(timeout=2.0)
    assert sorted(done) == ["a", "b", "c"]
