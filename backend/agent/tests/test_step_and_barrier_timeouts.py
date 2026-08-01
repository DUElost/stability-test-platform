"""步骤墙钟与 barrier 预算的可配性（#114 / #117 第一步）。

两条都是**安全网**语义，不是完成判据：真正判断"还在不在推进"要等 #115 的
进度信号。这里只保证「可配、缺省不变、0 能表达不限」。
"""

import pytest

from backend.agent.pipeline_engine import (
    _DEFAULT_STEP_WALL_CLOCK_SECONDS,
    _resolve_step_wall_clock,
)


class TestStepWallClock:
    def test_plan_step_value_wins(self):
        assert _resolve_step_wall_clock({"timeout_seconds": 43200}) == 43200

    def test_legacy_timeout_key_still_honoured(self):
        assert _resolve_step_wall_clock({"timeout": 90}) == 90

    def test_default_stays_300_when_nothing_is_configured(self, monkeypatch):
        """缺省**不能**被悄悄抬到 12h。

        那会让每个从未配过 timeout_seconds 的步骤，卡死后的回收时间从 5 分钟
        变成 12 小时 —— permit cap=5 时等于让一个卡死步骤吃掉该 host 1/5 的
        容量整整半天。长墙钟必须在 PlanStep 上显式声明，让代价可见。
        """
        monkeypatch.delenv("STP_STEP_WALL_CLOCK_SECONDS", raising=False)
        assert _resolve_step_wall_clock({}) == _DEFAULT_STEP_WALL_CLOCK_SECONDS
        assert _DEFAULT_STEP_WALL_CLOCK_SECONDS == 300

    def test_env_provides_a_fleet_wide_default(self, monkeypatch):
        monkeypatch.setenv("STP_STEP_WALL_CLOCK_SECONDS", "43200")
        assert _resolve_step_wall_clock({}) == 43200

    def test_plan_step_overrides_env(self, monkeypatch):
        monkeypatch.setenv("STP_STEP_WALL_CLOCK_SECONDS", "43200")
        assert _resolve_step_wall_clock({"timeout_seconds": 60}) == 60

    @pytest.mark.parametrize("source", ["step", "env"])
    def test_zero_means_unlimited(self, monkeypatch, source):
        """None → communicate(timeout=None) → 一直等到子进程退出。"""
        monkeypatch.delenv("STP_STEP_WALL_CLOCK_SECONDS", raising=False)
        if source == "step":
            assert _resolve_step_wall_clock({"timeout_seconds": 0}) is None
        else:
            monkeypatch.setenv("STP_STEP_WALL_CLOCK_SECONDS", "0")
            assert _resolve_step_wall_clock({}) is None

    def test_garbage_env_falls_back_instead_of_crashing(self, monkeypatch):
        monkeypatch.setenv("STP_STEP_WALL_CLOCK_SECONDS", "not-a-number")
        assert _resolve_step_wall_clock({}) == _DEFAULT_STEP_WALL_CLOCK_SECONDS

    def test_garbage_step_value_falls_back(self, monkeypatch):
        monkeypatch.delenv("STP_STEP_WALL_CLOCK_SECONDS", raising=False)
        assert _resolve_step_wall_clock({"timeout_seconds": "soon"}) == (
            _DEFAULT_STEP_WALL_CLOCK_SECONDS
        )


class _Engine:
    """只借 _resolve_barrier_timeout，不构造整个 PipelineEngine。"""

    def __init__(self, configured=None):
        if configured is not None:
            self._barrier_timeout_seconds = configured

    from backend.agent.pipeline_engine import PipelineEngine as _PE
    _resolve_barrier_timeout = _PE._resolve_barrier_timeout


class TestBarrierTimeout:
    def test_default_600_preserves_existing_behaviour(self, monkeypatch):
        monkeypatch.delenv("STP_BARRIER_TIMEOUT_SECONDS", raising=False)
        assert _Engine()._resolve_barrier_timeout() == 600.0

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("STP_BARRIER_TIMEOUT_SECONDS", "7200")
        assert _Engine()._resolve_barrier_timeout() == 7200.0

    def test_plan_level_value_wins_over_env(self, monkeypatch):
        """含自动刷机的计划要能独立抬高，而不是全平台一刀切。"""
        monkeypatch.setenv("STP_BARRIER_TIMEOUT_SECONDS", "600")
        assert _Engine(configured=172800)._resolve_barrier_timeout() == 172800.0

    def test_non_positive_plan_value_falls_back_to_600(self, monkeypatch):
        """barrier 没有"不限"语义 —— 0 会让先到者立刻超时并连坐失败。"""
        monkeypatch.delenv("STP_BARRIER_TIMEOUT_SECONDS", raising=False)
        assert _Engine(configured=0)._resolve_barrier_timeout() == 600.0
        assert _Engine(configured=-1)._resolve_barrier_timeout() == 600.0

    def test_garbage_value_falls_back(self, monkeypatch):
        monkeypatch.delenv("STP_BARRIER_TIMEOUT_SECONDS", raising=False)
        assert _Engine(configured="later")._resolve_barrier_timeout() == 600.0
