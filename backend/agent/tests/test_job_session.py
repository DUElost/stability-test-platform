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

from backend.agent.job_session import JobSession, JobStartupError
from backend.agent.watcher import WatcherStartError
from backend.agent.watcher.manager import WatcherHandle
from backend.agent.watcher.policy import OnUnavailableAction, WatcherPolicy
from backend.agent.aee.reconciler import ReconcilerStats


# ----------------------------------------------------------------------
# Fixtures & helpers
# ----------------------------------------------------------------------

def _make_payload(
    watcher_policy: Dict[str, Any] | None = None,
    started_at: datetime | None = None,
) -> Dict[str, Any]:
    """最小合法 claim payload（契约必需字段齐全）。"""
    payload: Dict[str, Any] = {
        "id": 101,
        "device_id": 42,
        "device_serial": "SERIAL-ABC",
        "host_id": "host-unittest",
        "pipeline_def": {"stages": {"prepare": [], "execute": [], "post_process": []}},
        "fencing_token": "101:1",
    }
    if watcher_policy is not None:
        payload["watcher_policy"] = watcher_policy
    if started_at is not None:
        payload["started_at"] = started_at.isoformat()
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

    def start(
        self,
        *,
        host_id: str,
        serial: str,
        job_id: int,
        log_dir: str,
        policy: WatcherPolicy,
        fencing_token: str = "",
        plan_run_id: Optional[int] = None,
    ) -> WatcherHandle:
        self.started.append({
            "host_id": host_id, "serial": serial, "job_id": job_id,
            "log_dir": log_dir, "plan_run_id": plan_run_id,
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
    patch_manager(_FakeManager(mode="fail_unavail"))

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

    patch_manager(_FakeManager(mode="ok", capability="stub"))

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
        "reconciler_stats",
        # #96：per-source 诊断拆分（log_signal_count = watcher + reconciler），
        # 控制面 watcher_summary 是 Dict[str,Any]，未消费这两个键，仅为运维可观测。
        "watcher_signal_count", "reconciler_signal_count",
    }
    assert set(payload.keys()) == expected_keys
    # M0/Task2: reconciler_stats 默认空 dict(未灰度开启 reconciler 时)
    assert payload["reconciler_stats"] == {}
    # #96: 未启动 reconciler 时 per-source 拆分仍存在且为 0
    assert payload["watcher_signal_count"] == 0
    assert payload["reconciler_signal_count"] == 0

    # 时间字段为 ISO8601 字符串
    assert isinstance(payload["watcher_started_at"], str)
    assert isinstance(payload["watcher_stopped_at"], str)
    assert payload["watcher_capability"] == "stub"
    # JSON 序列化不崩（契约第 13 行要求）
    json.dumps(payload)


def test_reconciler_start_failure_restores_direct_emit(lock_tracker, patch_manager, monkeypatch):
    """C-3: reconciler.start() 抛异常 → 回滚 watcher 的 emit 抑制,AEE 仍能 emit。

    场景:灰度开启 reconciler + watcher 已 active(capability ok) + reconciler.start 崩。
    期望:_reconciler 置 None,且 watcher.impl.set_aee_reconciler_active(False) 被调用,
    使 inotifyd 路径恢复直接 emit AEE/VENDOR_AEE(否则信号静默丢失)。
    """
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "1")
    monkeypatch.delenv("STP_WATCHER_AEE_RECONCILE_HOSTS", raising=False)

    class _FakeImpl:
        def __init__(self):
            # 初始 active=True 模拟 manager.start 时按灰度开启把 emit 抑制打开
            self._aee_reconciler_active = True
            self.emitter = object()

        def set_aee_reconciler_active(self, active: bool) -> None:
            self._aee_reconciler_active = bool(active)

    impl = _FakeImpl()

    class _MgrWithDeps(_FakeManager):
        def get_dep(self, key, default=None):
            return {
                "nfs_base_dir": "",
                "local_db": object(),
                "adb_path": "adb",
            }.get(key, default)

    fake = _MgrWithDeps(mode="ok", capability="inotifyd_root")
    patch_manager(fake)

    class _BoomReconciler:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("reconciler boom")

    monkeypatch.setattr(
        "backend.agent.aee.reconciler.AeeDbHistoryReconciler", _BoomReconciler,
    )

    session = JobSession(
        job_payload=_make_payload(),
        host_id="host-unittest",
        log_dir="/tmp/jobs/101",
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
    )
    session.__enter__()
    # __enter__ 时 handle.impl 仍是 None(FakeManager 返回 impl=None) → 首轮跳过;
    # 注入真实 impl 后手动再驱动一次启动判定,逼出 reconciler.start 失败回滚路径。
    session._handle.impl = impl
    session._maybe_start_aee_reconciler()

    assert session._reconciler is None, "reconciler.start 失败后应清空引用"
    assert impl._aee_reconciler_active is False, (
        "回滚后 watcher 必须恢复直接 emit(set_aee_reconciler_active(False))"
    )

    session.__exit__(None, None, None)
    assert 101 not in lock_tracker.active_jobs


def test_reconciler_uses_get_aee_local_root(lock_tracker, patch_manager, monkeypatch, tmp_path):
    """Reconciler local_root 来自 get_aee_local_root()，不使用 nfs_base_dir。"""
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "1")
    monkeypatch.delenv("STP_WATCHER_AEE_RECONCILE_HOSTS", raising=False)

    hdd = tmp_path / "hdd"
    hdd.mkdir()
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(hdd))

    class _FakeImpl:
        def __init__(self):
            self._aee_reconciler_active = False
            self.emitter = object()

        def set_aee_reconciler_active(self, active: bool) -> None:
            self._aee_reconciler_active = bool(active)

    impl = _FakeImpl()
    captured: dict = {}

    class _CaptureReconciler:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def start(self):
            # #78 子任务 2:AeeDbHistoryReconciler.start() 现返回 bool(True=已启动)。
            # preflight 失败会返回 False → 触发 RuntimeError 回滚;这里默认成功启动。
            return True

    class _MgrWithDeps(_FakeManager):
        def get_dep(self, key, default=None):
            return {
                "nfs_base_dir": "/mnt/cifs/should-not-use",
                "local_db": object(),
                "adb_path": "adb",
            }.get(key, default)

    patch_manager(_MgrWithDeps(mode="ok", capability="inotifyd_root"))
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.AeeDbHistoryReconciler", _CaptureReconciler,
    )

    session = JobSession(
        job_payload=_make_payload(),
        host_id="host-unittest",
        log_dir="/tmp/jobs/101",
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
    )
    session.__enter__()
    session._handle.impl = impl
    session._maybe_start_aee_reconciler()

    assert session._reconciler is not None
    assert captured.get("local_root") == hdd

    session.__exit__(None, None, None)


def test_reconciler_gets_run_date_stamp_from_payload_started_at(
    lock_tracker, patch_manager, monkeypatch, tmp_path,
):
    """JobSession 用 claim payload 的 started_at 派生 Shanghai MMDD 传入 reconciler。"""
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "1")
    monkeypatch.delenv("STP_WATCHER_AEE_RECONCILE_HOSTS", raising=False)

    hdd = tmp_path / "hdd"
    hdd.mkdir()
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(hdd))

    class _FakeImpl:
        def __init__(self):
            self._aee_reconciler_active = False
            self.emitter = object()

        def set_aee_reconciler_active(self, active: bool) -> None:
            self._aee_reconciler_active = bool(active)

    impl = _FakeImpl()
    captured: dict = {}

    class _CaptureReconciler:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def start(self):
            return True

    class _MgrWithDeps(_FakeManager):
        def get_dep(self, key, default=None):
            return {
                "nfs_base_dir": "/mnt/cifs/should-not-use",
                "local_db": object(),
                "adb_path": "adb",
            }.get(key, default)

    patch_manager(_MgrWithDeps(mode="ok", capability="inotifyd_root"))
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.AeeDbHistoryReconciler", _CaptureReconciler,
    )

    # 2026-08-08 16:30 UTC = 2026-08-09 00:30 Asia/Shanghai → MMDD 0809
    session = JobSession(
        job_payload=_make_payload(
            started_at=datetime(2026, 8, 8, 16, 30, tzinfo=timezone.utc),
        ),
        host_id="host-unittest",
        log_dir="/tmp/jobs/101",
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
    )
    session.__enter__()
    session._handle.impl = impl
    session._maybe_start_aee_reconciler()

    assert session._reconciler is not None
    assert captured.get("run_date_stamp") == "0809"

    session.__exit__(None, None, None)


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


# ---------------------------------------------------------------------------
# #73: AEE Reconciler 平台门禁
# ---------------------------------------------------------------------------


class _PlatformImpl:
    def __init__(self):
        self._aee_reconciler_active = False
        self.emitter = object()

    def set_aee_reconciler_active(self, active: bool) -> None:
        self._aee_reconciler_active = bool(active)


class _MgrWithAdb(_FakeManager):
    def get_dep(self, key, default=None):
        return {
            "nfs_base_dir": "",
            "local_db": object(),
            "adb_path": "adb",
        }.get(key, default)


def _platform_session(lock_tracker, patch_manager, monkeypatch, platform, reconciler_cls):
    """构造一个 watcher 已 active 的 session,把平台探测固定为 `platform`。"""
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "1")
    monkeypatch.delenv("STP_WATCHER_AEE_RECONCILE_HOSTS", raising=False)
    monkeypatch.setattr(
        "backend.agent.device_platform.detect_device_platform",
        lambda *a, **k: platform,
    )
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.AeeDbHistoryReconciler", reconciler_cls,
    )
    patch_manager(_MgrWithAdb(mode="ok", capability="inotifyd_root"))

    session = JobSession(
        job_payload=_make_payload(),
        host_id="host-unittest",
        log_dir="/tmp/jobs/101",
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
    )
    session.__enter__()
    session._handle.impl = _PlatformImpl()
    return session


class _OkReconciler:
    def __init__(self, **kwargs):
        pass

    def start(self):
        return True


def test_reconciler_skipped_on_unisoc_platform(lock_tracker, patch_manager, monkeypatch):
    """#73: 展锐机型无 /data/aee_exp — reconciler 不该启动。"""
    session = _platform_session(
        lock_tracker, patch_manager, monkeypatch, "UNISOC", _OkReconciler,
    )
    session._maybe_start_aee_reconciler()

    assert session._reconciler is None, "展锐平台必须跳过 reconciler"
    session.__exit__(None, None, None)


def test_reconciler_skipped_on_qcom_platform(lock_tracker, patch_manager, monkeypatch):
    session = _platform_session(
        lock_tracker, patch_manager, monkeypatch, "QCOM", _OkReconciler,
    )
    session._maybe_start_aee_reconciler()

    assert session._reconciler is None, "高通平台必须跳过 reconciler"
    session.__exit__(None, None, None)


def test_reconciler_starts_on_mtk_platform(lock_tracker, patch_manager, monkeypatch):
    """#73: MTK 是 AEE 的目标平台 — 门禁不能误伤。"""
    session = _platform_session(
        lock_tracker, patch_manager, monkeypatch, "MTK", _OkReconciler,
    )
    session._maybe_start_aee_reconciler()

    assert session._reconciler is not None, "MTK 平台必须正常启动 reconciler"
    session.__exit__(None, None, None)


def test_reconciler_starts_on_unknown_platform(lock_tracker, patch_manager, monkeypatch):
    """探测失败(UNKNOWN)放行 — 宁可多跑一轮也不要漏采 MTK 崩溃信号。"""
    session = _platform_session(
        lock_tracker, patch_manager, monkeypatch, "UNKNOWN", _OkReconciler,
    )
    session._maybe_start_aee_reconciler()

    assert session._reconciler is not None, "UNKNOWN 必须放行(见 _platform_supports_aee)"
    session.__exit__(None, None, None)


def test_reconciler_platform_allowlist_is_configurable(lock_tracker, patch_manager, monkeypatch):
    """运维逃生阀:STP_WATCHER_AEE_RECONCILE_PLATFORMS 可放开非 MTK 平台。"""
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_PLATFORMS", "MTK,UNISOC")
    session = _platform_session(
        lock_tracker, patch_manager, monkeypatch, "UNISOC", _OkReconciler,
    )
    session._maybe_start_aee_reconciler()

    assert session._reconciler is not None, "白名单显式放开后 UNISOC 应能启动"
    session.__exit__(None, None, None)


def test_platform_gate_precedes_watcher_check(lock_tracker, patch_manager, monkeypatch):
    """平台门禁应在 watcher 检查之前 — 展锐机型的日志要给出平台原因。"""
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "1")
    monkeypatch.delenv("STP_WATCHER_AEE_RECONCILE_HOSTS", raising=False)
    monkeypatch.setattr(
        "backend.agent.device_platform.detect_device_platform",
        lambda *a, **k: "UNISOC",
    )
    patch_manager(_MgrWithAdb(mode="ok", capability="inotifyd_root"))

    session = JobSession(
        job_payload=_make_payload(),
        host_id="host-unittest",
        log_dir="/tmp/jobs/101",
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
    )
    session.__enter__()
    session._handle.impl = None  # watcher 也不可用

    assert session._platform_supports_aee() is False, (
        "handle.impl 为 None 时平台门禁仍应独立判定并拦下"
    )
    session.__exit__(None, None, None)


def test_log_signal_count_includes_reconciler_emits(lock_tracker, patch_manager, monkeypatch, caplog):
    """#96: log_signal_count 必须并入 reconciler 发射数，不能只剩 watcher 计数。

    回归场景：之前 job_session_exited signals 只取 watcher.signals_emitted，
    遗漏 reconciler_stats.signals_emitted —— 曾导致 9.124 真机验证时
    `job_session_exited signals=0` 被误判为 reconciler 失效，实际 reconciler
    在同一 Job 发了 2 条已落库。
    """
    import logging

    # watcher stop 返回 handle，stats 里 signals_emitted=3（watcher 路径发了 3 条）
    class _MgrStopWithSignals(_MgrWithAdb):
        def stop(self, watcher_id: str, *, drain: bool = True, timeout: float = 5.0):
            self.stopped.append(watcher_id)
            handle = WatcherHandle(
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
            handle.stats["signals_emitted"] = 3
            return handle

    patch_manager(_MgrStopWithSignals(mode="ok", capability="inotifyd_root"))

    # reconciler stop 返回 signals_emitted=2（reconciler 路径发了 2 条）
    class _ReconcilerWithTwoSignals:
        def __init__(self, **kwargs):
            pass

        def start(self):
            return True

        def stop(self, timeout: float = 5.0):
            return ReconcilerStats(signals_emitted=2)

    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "1")
    monkeypatch.delenv("STP_WATCHER_AEE_RECONCILE_HOSTS", raising=False)
    monkeypatch.setattr(
        "backend.agent.device_platform.detect_device_platform",
        lambda *a, **k: "MTK",
    )
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.AeeDbHistoryReconciler",
        _ReconcilerWithTwoSignals,
    )

    session = JobSession(
        job_payload=_make_payload(),
        host_id="host-unittest",
        log_dir="/tmp/jobs/101",
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
    )
    session.__enter__()
    session._handle.impl = _PlatformImpl()
    # __enter__ 内第一次调 _maybe_start_aee_reconciler 时 impl 还没注入，
    # 这里补一次（与现有平台门禁测试同构）让 reconciler 真正启动
    session._maybe_start_aee_reconciler()
    assert session._reconciler is not None, "reconciler 应已启动（MTK + impl 已就位）"

    with caplog.at_level(logging.INFO, logger="backend.agent.job_session"):
        session.__exit__(None, None, None)

    summary = session.summary
    # 核心：log_signal_count = watcher(3) + reconciler(2) = 5
    assert summary.log_signal_count == 5, (
        f"log_signal_count 应并入 reconciler（3+2=5），实际 {summary.log_signal_count}"
    )
    assert summary.watcher_signal_count == 3
    assert summary.reconciler_signal_count == 2
    assert summary.reconciler_stats["signals_emitted"] == 2

    # to_complete_payload 同步带出 per-source 拆分
    payload = summary.to_complete_payload()
    assert payload["log_signal_count"] == 5
    assert payload["watcher_signal_count"] == 3
    assert payload["reconciler_signal_count"] == 2

    # 日志行必须按来源拆开，避免再次出现裸 signals=0 误导
    exited = [r for r in caplog.records if "job_session_exited" in r.getMessage()]
    assert exited, "应记 job_session_exited 日志"
    log_line = exited[0].getMessage()
    assert "signals=5" in log_line
    assert "watcher=3" in log_line
    assert "reconciler=2" in log_line


def test_log_signal_count_zero_when_reconciler_not_started(lock_tracker, patch_manager):
    """#96 旁路：reconciler 未启动（如展锐）时 per-source 拆分仍存在且不崩。

    watcher 单独发的信号照常计入 log_signal_count；reconciler 那一档为 0。
    """
    # 还原 _FakeManager.stop 默认（stats.signals_emitted=0）
    patch_manager(_FakeManager(mode="ok", capability="stub"))

    session = JobSession(
        job_payload=_make_payload(),
        host_id="host-unittest",
        log_dir="/tmp/jobs/101",
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
    )
    session.__enter__()
    session.__exit__(None, None, None)

    summary = session.summary
    assert summary.watcher_signal_count == 0
    assert summary.reconciler_signal_count == 0
    assert summary.log_signal_count == 0
    assert summary.reconciler_stats == {}
