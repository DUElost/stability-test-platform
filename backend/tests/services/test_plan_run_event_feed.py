"""#1520 垂直切片：PlanRun events 合成阶段规则服务层直测。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from backend.models.enums import PlanRunStatus
from backend.services.plan_run_event_feed import (
    build_synthetic_stage_events,
    log_signal_severity,
    log_signal_title,
)


def _meta():
    return {
        "init": {
            "started_at": datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc),
            "ended_at": datetime(2026, 9, 16, 10, 5, tzinfo=timezone.utc),
            "has_terminal_success": True,
            "has_failure": False,
        },
        "patrol": {
            "started_at": datetime(2026, 9, 16, 10, 5, tzinfo=timezone.utc),
            "ended_at": None,
            "has_terminal_success": False,
            "has_failure": False,
        },
        "teardown": {
            "started_at": None,
            "ended_at": None,
            "has_terminal_success": False,
            "has_failure": False,
        },
    }


class TestLogSignalHelpers:
    def test_severity_err_for_aee(self):
        assert log_signal_severity("AEE") == "err"
        assert log_signal_severity("tombstone") == "err"

    def test_severity_warn_for_anr(self):
        assert log_signal_severity("ANR") == "warn"

    def test_title_known_categories(self):
        assert "AEE" in log_signal_title("AEE")
        assert log_signal_title("UNKNOWN_CAT") == "UNKNOWN_CAT"


class TestSyntheticStageEvents:
    def test_empty_jobs_returns_empty(self):
        pr = SimpleNamespace(id=1, status=PlanRunStatus.RUNNING.value, started_at=None)
        assert build_synthetic_stage_events(pr, [], _meta()) == []

    def test_emits_init_complete_and_patrol_start(self):
        pr = SimpleNamespace(
            id=7,
            status=PlanRunStatus.RUNNING.value,
            started_at=datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc),
        )
        job = SimpleNamespace(
            patrol_cycle_count=2,
            last_patrol_heartbeat_at=datetime.now(timezone.utc),
            current_patrol_step="monkey",
            started_at=datetime(2026, 9, 16, 10, 5, tzinfo=timezone.utc),
        )
        events = build_synthetic_stage_events(pr, [job], _meta())
        titles = [e.title for e in events]
        assert "INIT 完成" in titles
        assert "PATROL 开始" in titles
        assert any(t.startswith("PATROL 进行中") for t in titles)
