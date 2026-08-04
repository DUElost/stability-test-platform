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

import pytest

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
