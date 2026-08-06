"""job→PlanRunHost 映射 + progress-aware barrier 判定（#117）。

映射：同一 PRH 的 job 是 barrier 的 peer；同 host 多 PlanRun 时等待方必须只
把同 PRH 的 job 当同伴——否则会把别的 PlanRun 的活跃 job 当成 peer 而无限续期。

判定表（review 约定）：
  - WAITING_EXECUTION_SLOT → 活（排队等槽位，被 cap 限流，不是卡住）
  - EXECUTING_STEP 且 last_progress_at 新鲜 → 活（打戳步骤的戳在刷新）
  - WAITING_BARRIER → 不算（它自己也在等，否则互相续期成死锁）
  - 其它 / 无 last_progress_at → 不算
"""

from datetime import datetime, timedelta, timezone

from backend.agent.coordinator import HostRunCoordinator
from backend.agent.pipeline_engine import PipelineEngine

_NOW = datetime.now(timezone.utc)


def _coord() -> HostRunCoordinator:
    return HostRunCoordinator(
        api_url="http://127.0.0.1:1", host_id="h1", agent_instance_id="a1",
    )


class _Engine:
    """只借 _peers_are_progressing，不构造整个 PipelineEngine。"""

    def __init__(self, run_id: int):
        self._run_id = run_id

    _peers_are_progressing = PipelineEngine._peers_are_progressing


class TestJobPrhMapping:
    def test_peers_of_returns_only_same_prh(self):
        coord = _coord()
        coord.register_job(1, prh_id=10)
        coord.register_job(2, prh_id=10)
        coord.register_job(3, prh_id=20)

        peers = coord.peers_of(1)
        assert [v.job_id for v in peers] == [2]
        assert coord.peers_of(3) == []

    def test_deregister_cleans_mapping(self):
        coord = _coord()
        coord.register_job(1, prh_id=10)
        coord.register_job(2, prh_id=10)
        coord.deregister_job(2)
        assert coord.peers_of(1) == []

    def test_register_without_prh_has_no_peers(self):
        coord = _coord()
        coord.register_job(1)
        assert coord.peers_of(1) == []


class TestPeersAreProgressing:
    def test_waiting_execution_slot_counts_as_alive(self):
        """排队等槽位 = 活——23 台大 host 排队时不能把等待槽位的 peer 误判停滞。"""
        coord = _coord()
        coord.register_job(1, prh_id=10)
        peer = coord.register_job(2, prh_id=10)
        peer.update(state="WAITING_EXECUTION_SLOT")
        assert _Engine(1)._peers_are_progressing(coord)

    def test_executing_with_fresh_progress_counts_as_alive(self):
        coord = _coord()
        coord.register_job(1, prh_id=10)
        peer = coord.register_job(2, prh_id=10)
        peer.update(state="EXECUTING_STEP", progress_ts=_NOW.isoformat())
        assert _Engine(1)._peers_are_progressing(coord)

    def test_executing_with_stale_progress_not_alive(self):
        """EXECUTING_STEP 但 last_progress_at 陈旧（300s 前）= 停滞。"""
        coord = _coord()
        coord.register_job(1, prh_id=10)
        peer = coord.register_job(2, prh_id=10)
        peer.update(
            state="EXECUTING_STEP",
            progress_ts=(_NOW - timedelta(seconds=300)).isoformat(),
        )
        assert not _Engine(1)._peers_are_progressing(coord)

    def test_waiting_barrier_not_alive(self):
        """WAITING_BARRIER 不算活——否则两个等待方互相续期成死锁。"""
        coord = _coord()
        coord.register_job(1, prh_id=10)
        peer = coord.register_job(2, prh_id=10)
        peer.update(state="WAITING_BARRIER")
        assert not _Engine(1)._peers_are_progressing(coord)

    def test_no_peers_not_alive(self):
        coord = _coord()
        coord.register_job(1, prh_id=10)
        assert not _Engine(1)._peers_are_progressing(coord)

    def test_other_prh_jobs_do_not_count(self):
        """别的 PlanRun 的活跃 job 不是 peer——不能因此无限续期。"""
        coord = _coord()
        coord.register_job(1, prh_id=10)
        other = coord.register_job(2, prh_id=20)
        other.update(state="EXECUTING_STEP", progress_ts=_NOW.isoformat())
        assert not _Engine(1)._peers_are_progressing(coord)


# ── _await_phase_barrier 集成：peer 推进续期 / 全体停滞超时（#117 review）──


class _BarrierEngine(_Engine):
    """构造一个可驱动 _await_phase_barrier 的最小引擎。

    覆盖内部依赖：barrier 总启用、非最后到达、不锁丢失不取消。
    """

    def __init__(
        self,
        run_id: int,
        prh_id: int,
        timeout: float,
        max_wait: float | None = None,
    ):
        super().__init__(run_id)
        self._plan_run_host_id = prh_id
        self._barrier_timeout_seconds = timeout
        self._barrier_max_wait_seconds = max_wait
        self._coordinator = self  # peers_of / wait_barrier 指向自己
        self._canceled = False
        self._peers = []
        self._wait_calls = 0
        self._released = False

    def _barrier_enabled(self):
        return True

    def _is_lock_lost(self):
        return False

    def _arrive_phase_barrier(self, next_phase):
        return False  # 永远不是最后一个到达者

    def _update_execution_state(self, state, progress_ts=None):
        pass

    def peers_of(self, job_id):
        return self._peers

    def wait_barrier(self, prh_id, timeout=None):
        self._wait_calls += 1
        return self._released

    _peers_are_progressing = PipelineEngine._peers_are_progressing
    _resolve_barrier_timeout = PipelineEngine._resolve_barrier_timeout
    _await_phase_barrier = PipelineEngine._await_phase_barrier
    _peer_state_snapshot = PipelineEngine._peer_state_snapshot


def _peer(coord, job_id, prh_id, state, progress_ts=None):
    view = coord.register_job(job_id, prh_id=prh_id)
    view.update(state=state, progress_ts=progress_ts)
    return view


class TestBarrierRenewal:
    def test_peer_progressing_renews_past_original_timeout(self):
        """peer 持续推进 → 原 timeout 到期后 barrier 仍在等待（续期生效）。"""
        import threading
        import time as _time

        coord = _coord()
        eng = _BarrierEngine(run_id=1, prh_id=10, timeout=0.2)
        peer_view = _peer(coord, 2, 10, "EXECUTING_STEP", progress_ts=_NOW.isoformat())
        eng._peers = [peer_view]

        def _refresh():
            coord.register_job(2, prh_id=10).update(
                state="EXECUTING_STEP",
                progress_ts=datetime.now(timezone.utc).isoformat(),
            )

        result: list = []

        def _run():
            result.append(eng._await_phase_barrier("PATROL"))

        real_sleep = _time.sleep
        _time.sleep = lambda s: real_sleep(0.001)
        try:
            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            # 0.5s > 原 timeout 0.2s：peer 在推进 → 线程必须还活着
            for _ in range(50):
                _refresh()  # 持续推进
                _time.sleep(0.01)
            assert thread.is_alive(), "推进中的 peer 不应让 barrier 超时"
            # 释放 barrier,线程正常返回
            eng._released = True
            thread.join(timeout=2)
            assert result == [True]
        finally:
            _time.sleep = real_sleep

    def test_all_peers_stalled_times_out_with_original_timeout(self):
        """所有 peer 停滞 → 按原 barrier_timeout 超时失败。"""
        import threading
        import time as _time

        coord = _coord()
        eng = _BarrierEngine(run_id=1, prh_id=10, timeout=0.2)
        _peer(coord, 2, 10, "EXECUTING_STEP",
              progress_ts=(_NOW - timedelta(seconds=300)).isoformat())

        result: list = []

        def _run():
            result.append(eng._await_phase_barrier("PATROL"))

        real_sleep = _time.sleep
        _time.sleep = lambda s: real_sleep(0.001)
        try:
            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            thread.join(timeout=3)
            assert not thread.is_alive(), "停滞 peer 应在原 timeout 后返回"
            assert result == [False]
            assert eng._wait_calls >= 1
        finally:
            _time.sleep = real_sleep

    def test_max_wait_hard_limit_fires_despite_renewal(self):
        """#174: 配 barrier_max_wait_seconds 后，peer 持续推进也不能越过硬顶。"""
        import threading
        import time as _time

        coord = _coord()
        # 滑动窗 0.2s 会被 peer 推进无限续期；绝对硬顶 0.3s 必须先到。
        eng = _BarrierEngine(run_id=1, prh_id=10, timeout=0.2, max_wait=0.3)
        peer_view = _peer(coord, 2, 10, "EXECUTING_STEP", progress_ts=_NOW.isoformat())
        eng._peers = [peer_view]

        def _refresh():
            coord.register_job(2, prh_id=10).update(
                state="EXECUTING_STEP",
                progress_ts=datetime.now(timezone.utc).isoformat(),
            )

        result: list = []
        started = _time.monotonic()

        def _run():
            result.append(eng._await_phase_barrier("PATROL"))

        real_sleep = _time.sleep
        _time.sleep = lambda s: real_sleep(0.001)
        try:
            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            # 持续推进 peer，直到硬顶把 barrier 打断
            for _ in range(200):
                _refresh()
                _time.sleep(0.01)
            thread.join(timeout=3)
            elapsed = _time.monotonic() - started
            assert not thread.is_alive(), "绝对硬顶必须打断续期"
            assert result == [False], result
            assert elapsed < 3.0, f"硬顶应远早于无限续期: {elapsed:.2f}s"
        finally:
            _time.sleep = real_sleep
