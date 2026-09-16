"""#1520 垂直切片：PlanRun timeline 业务规则服务层直测。"""

from __future__ import annotations

from backend.models.enums import PlanRunStatus
from backend.services.plan_run_timeline import _stage_status_from_steps


class TestStageStatusFromSteps:
    def test_terminal_teardown_skipped_when_empty(self):
        assert (
            _stage_status_from_steps(
                "teardown",
                pr_status=PlanRunStatus.SUCCESS.value,
                job_total=3,
                succeeded=0,
                failed=0,
                has_running_jobs=False,
            )
            == "skipped"
        )

    def test_terminal_failed_when_any_failed(self):
        assert (
            _stage_status_from_steps(
                "init",
                pr_status=PlanRunStatus.FAILED.value,
                job_total=3,
                succeeded=2,
                failed=1,
                has_running_jobs=False,
            )
            == "failed"
        )

    def test_running_init_completed_when_all_succeeded(self):
        assert (
            _stage_status_from_steps(
                "init",
                pr_status=PlanRunStatus.RUNNING.value,
                job_total=3,
                succeeded=3,
                failed=0,
                has_running_jobs=True,
            )
            == "completed"
        )

    def test_running_patrol_is_running(self):
        assert (
            _stage_status_from_steps(
                "patrol",
                pr_status=PlanRunStatus.RUNNING.value,
                job_total=3,
                succeeded=1,
                failed=0,
                has_running_jobs=True,
            )
            == "running"
        )

    def test_running_teardown_stays_pending(self):
        assert (
            _stage_status_from_steps(
                "teardown",
                pr_status=PlanRunStatus.RUNNING.value,
                job_total=3,
                succeeded=0,
                failed=0,
                has_running_jobs=True,
            )
            == "pending"
        )
