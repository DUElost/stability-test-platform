"""JobSession 单元测试。

覆盖场景（K3 feature flag 默认关闭时仍然要求契约正确）：
  1. 正常启动：watcher 注册 + summary 字段正确填写
  2. DEGRADED 策略：watcher 启动失败不抛异常，capability=unavailable
  3. FAIL 策略：watcher 启动失败立即释放锁 + 抛 JobStartupError
  4. Phase 1 stop 异常不阻塞 Phase 2 锁释放
  5. payload 契约违反 → JobStartupError(reason_code=payload_contract_violation)
  6. summary.to_complete_payload() 字段形状

测试通过 monkeypatch LogWatcherManager.start/stop 制造各种场景，
避免真实 adb 依赖。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

from backend.agent.job_session import JobSession, JobStartupError, JobSessionSummary
from backend.agent.watcher import LogWatcherManager, WatcherStartError
from backend.agent.watcher.manager import WatcherHandle
from backend.agent.watcher.policy import OnUnavailableAction, WatcherPolicy


# ----------------------------------------------------------------------
# Fixtures & helpers
# ----------------------------------------------------------------------

def _make_payload(watcher_policy: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """最小合法 claim payload（契约必需字段齐全）。"""
    payload: Dict[str, Any] = {
        "id": 101,
        "device_id": 42,
        "device_serial": "SERIAL-ABC",
        "host_id": "host-unittest",
        "pipeline_def": {"stages": {"prepare": [], "execute": [], "post_process": []}},
    }
    if watcher_policy is not None:
        payload["watcher_policy"] = watcher_policy
    return payload


class _FakeManager:
    """可编程 Manager —— 替代 LogWatcherManager.instance() 单例。

    通过 mode 参数决定 start/stop 行为：
      - "ok"             : 正常启动，返回 stub capability
      - "fail_unavail"   : start 抛 WatcherStartError(code=probe_failed)
      - "fail_unexpected": start 抛普通 Exception
      - "stop_raises"    : start 正常，stop 抛异常（模拟 Phase 1 异常）
    """

    def __init__(self, mode: str = "ok", *, capability: str = "stub"):
        self.mode = mode
        self.capability = capability
        self.started: List[Dict[str, Any]] = []
        self.stopped: List[str] = []

    def start(self, *, host_id: str, serial: str, job_id: int, log_dir: str, policy: WatcherPolicy) -> WatcherHandle:
        self.started.append({
            "host_id": host_id, "serial": serial, "job_id": job_id,
            "log_dir": log_dir,
        })
        if self.mode == "fail_unavail":
            raise WatcherStartError("probe failed all categories", code="probe_failed")
        if self.mode == "fail_unexpected":
            raise RuntimeError("unexpected infrastructure bug")
        handle = WatcherHandle(
            watcher_id=f"wch-{job_id}",
            host_id=host_id,
            serial=serial,
            job_id=job_id,
            log_dir=log_dir,
            policy=policy,
            capability=self.capability,
            started_at=datetime(2026, 4, 18, 10, 0, 0, tzinfo=timezone.utc),
        )
        # 模拟运行期累计了一些信号
        handle.stats["signals_emitted"] = 3
        return handle

    def stop(self, watcher_id: str, *, drain: bool = True, timeout: float = 5.0):
        self.stopped.append(watcher_id)
        if self.mode == "stop_raises":
            raise RuntimeError("watcher stop failure simulated")
        return WatcherHandle(
            watcher_id=watcher_id,
            host_id="host-unittest",
            serial="SERIAL-ABC",
            job_id=101,
            log_dir="/tmp/unittest",
            policy=WatcherPolicy(),
            capability=self.capability,
            started_at=datetime(2026, 4, 18, 10, 0, 0, tzinfo=timezone.utc),
            stopped_at=datetime(2026, 4, 18, 10, 0, 5, tzinfo=timezone.utc),
        )


@pytest.fixture
def lock_tracker():
    """追踪 lock_register/deregister 调用顺序（验证 Phase 2 必定执行）。"""
    class Tracker:
        def __init__(self):
            self.active_jobs: set[int] = set()
            self.active_devices: set[int] = set()
            self.events: List[str] = []

        def reg_job(self, jid: int):
            self.active_jobs.add(jid)
            self.events.append(f"reg_job:{jid}")

        def dereg_job(self, jid: int):
            self.active_jobs.discard(jid)
            self.events.append(f"dereg_job:{jid}")

        def reg_dev(self, did: int):
            self.active_devices.add(did)
            self.events.append(f"reg_dev:{did}")

        def dereg_dev(self, did: int):
            self.active_devices.discard(did)
            self.events.append(f"dereg_dev:{did}")

    return Tracker()


@pytest.fixture
def patch_manager(monkeypatch):
    """替换 LogWatcherManager.instance() 的返回值。"""
    def _patch(fake: _FakeManager):
        monkeypatch.setattr(
            "backend.agent.job_session.LogWatcherManager",
            type("MockLWM", (), {"instance": staticmethod(lambda: fake)}),
        )
        return fake
    return _patch


# ----------------------------------------------------------------------
# 测试用例
# ----------------------------------------------------------------------

def test_enter_starts_watcher_and_records_summary(lock_tracker, patch_manager):
    """正常路径：enter 启动 watcher，summary 填入 watcher_id/started_at/capability。"""
    fake = patch_manager(_FakeManager(mode="ok", capability="stub"))

    session = JobSession(
        job_payload=_make_payload(),
        host_id="host-unittest",
        log_dir="/tmp/jobs/101",
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
        device_id_register=lock_tracker.reg_dev,
        device_id_deregister=lock_tracker.dereg_dev,
    )
    session.__enter__()

    # 锁已注册
    assert 101 in lock_tracker.active_jobs
    assert 42 in lock_tracker.active_devices
    # Manager.start 被调用，关键参数正确
    assert len(fake.started) == 1
    assert fake.started[0]["job_id"] == 101
    assert fake.started[0]["serial"] == "SERIAL-ABC"
    # Summary 反映启动结果
    assert session.summary.watcher_id == "wch-101"
    assert session.summary.watcher_capability == "stub"
    assert session.summary.watcher_started_at is not None

    # 正常 exit：Phase 1 stop + Phase 2 释放锁
    session.__exit__(None, None, None)
    assert 101 not in lock_tracker.active_jobs
    assert 42 not in lock_tracker.active_devices
    assert "wch-101" in fake.stopped


def test_enter_watcher_fail_with_degraded_continues(lock_tracker, patch_manager):
    """DEGRADED（首发默认）：watcher 启动失败不抛异常，capability=unavailable，锁保留。"""
    fake = patch_manager(_FakeManager(mode="fail_unavail"))

    session = JobSession(
        job_payload=_make_payload(watcher_policy={"on_unavailable": "degraded"}),
        host_id="host-unittest",
        log_dir="/tmp/jobs/101",
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
        device_id_register=lock_tracker.reg_dev,
        device_id_deregister=lock_tracker.dereg_dev,
    )
    # 不应抛异常
    session.__enter__()

    assert session.policy.on_unavailable == OnUnavailableAction.DEGRADED
    assert session.summary.watcher_capability == "unavailable"
    # 锁仍保留（DEGRADED 下 Job 照常执行）
    assert 101 in lock_tracker.active_jobs
    assert 42 in lock_tracker.active_devices

    # exit 时即使 handle 为 None，Phase 2 仍释放锁
    session.__exit__(None, None, None)
    assert 101 not in lock_tracker.active_jobs
    assert 42 not in lock_tracker.active_devices


def test_enter_watcher_fail_with_fail_raises_jobstartuperror(lock_tracker, patch_manager):
    """FAIL 策略：启动失败立刻释放锁 + 抛 JobStartupError(reason_code=watcher_probe_failed)。"""
    patch_manager(_FakeManager(mode="fail_unavail"))

    session = JobSession(
        job_payload=_make_payload(watcher_policy={"on_unavailable": "fail"}),
        host_id="host-unittest",
        log_dir="/tmp/jobs/101",
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
        device_id_register=lock_tracker.reg_dev,
        device_id_deregister=lock_tracker.dereg_dev,
    )

    with pytest.raises(JobStartupError) as excinfo:
        session.__enter__()

    assert excinfo.value.reason_code == "watcher_probe_failed"
    # 锁已释放（FAIL 路径必须立即释放，避免资源泄漏）
    assert 101 not in lock_tracker.active_jobs
    assert 42 not in lock_tracker.active_devices


def test_enter_unexpected_error_releases_lock_and_raises(lock_tracker, patch_manager):
    """未知异常 → 等同 start failure，释放锁 + JobStartupError(reason_code=watcher_start_unexpected)。"""
    patch_manager(_FakeManager(mode="fail_unexpected"))

    session = JobSession(
        job_payload=_make_payload(),  # 默认 DEGRADED 也不影响未知异常路径
        host_id="host-unittest",
        log_dir="/tmp/jobs/101",
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
    )

    with pytest.raises(JobStartupError) as excinfo:
        session.__enter__()

    assert excinfo.value.reason_code == "watcher_start_unexpected"
    assert 101 not in lock_tracker.active_jobs


def test_exit_phase1_exception_does_not_block_phase2(lock_tracker, patch_manager):
    """Phase 1 stop 抛异常时，Phase 2 锁释放仍必须执行（JobSession 的核心不变量）。"""
    fake = patch_manager(_FakeManager(mode="stop_raises"))

    session = JobSession(
        job_payload=_make_payload(),
        host_id="host-unittest",
        log_dir="/tmp/jobs/101",
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
        device_id_register=lock_tracker.reg_dev,
        device_id_deregister=lock_tracker.dereg_dev,
    )
    session.__enter__()
    assert 101 in lock_tracker.active_jobs

    # exit 不应向调用方抛异常（Phase 1 异常被吞）
    session.__exit__(None, None, None)

    # Phase 2 必定执行
    assert 101 not in lock_tracker.active_jobs
    assert 42 not in lock_tracker.active_devices
    # manager.stop 确实被调用过
    assert "wch-101" in fake.stopped


def test_payload_contract_violation_raises(lock_tracker, patch_manager):
    """缺 device_serial → JobStartupError(reason_code=payload_contract_violation)。"""
    patch_manager(_FakeManager(mode="ok"))

    bad_payload = _make_payload()
    del bad_payload["device_serial"]

    with pytest.raises(JobStartupError) as excinfo:
        JobSession(
            job_payload=bad_payload,
            host_id="host-unittest",
            log_dir="/tmp/jobs/101",
            lock_register=lock_tracker.reg_job,
            lock_deregister=lock_tracker.dereg_job,
        )
    assert excinfo.value.reason_code == "payload_contract_violation"
    # fail-fast：契约违反时锁根本不应注册
    assert 101 not in lock_tracker.active_jobs


def test_to_complete_payload_shape(lock_tracker, patch_manager):
    """summary.to_complete_payload 字段完整 + 可 JSON 序列化。"""
    import json

    fake = patch_manager(_FakeManager(mode="ok", capability="stub"))

    session = JobSession(
        job_payload=_make_payload(),
        host_id="host-unittest",
        log_dir="/tmp/jobs/101",
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
    )
    session.__enter__()
    session.__exit__(None, None, None)

    payload = session.summary.to_complete_payload()
    # 契约字段全部存在
    expected_keys = {
        "watcher_id", "watcher_started_at", "watcher_stopped_at",
        "watcher_capability", "log_signal_count", "watcher_stats",
    }
    assert set(payload.keys()) == expected_keys

    # 时间字段为 ISO8601 字符串
    assert isinstance(payload["watcher_started_at"], str)
    assert isinstance(payload["watcher_stopped_at"], str)
    assert payload["watcher_capability"] == "stub"
    # JSON 序列化不崩（契约第 13 行要求）
    json.dumps(payload)


def test_summary_to_payload_when_watcher_never_started(lock_tracker, patch_manager):
    """DEGRADED 路径下 handle 为 None，to_complete_payload 仍可安全调用。"""
    patch_manager(_FakeManager(mode="fail_unavail"))

    session = JobSession(
        job_payload=_make_payload(watcher_policy={"on_unavailable": "degraded"}),
        host_id="host-unittest",
        log_dir="/tmp/jobs/101",
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
    )
    session.__enter__()
    session.__exit__(None, None, None)

    payload = session.summary.to_complete_payload()
    assert payload["watcher_id"] is None
    assert payload["watcher_capability"] == "unavailable"
    assert payload["log_signal_count"] == 0
    assert payload["watcher_started_at"] is None
    assert payload["watcher_stopped_at"] is None
