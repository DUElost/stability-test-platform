"""#1520 垂直切片：PlanRun timeline 业务规则服务层直测。"""

from __future__ import annotations

from backend.models.enums import PlanRunStatus
from backend.services.plan_run_job_artifacts import is_non_issue_aee_artifact_filename
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

    def test_running_init_completed_when_patrol_started_and_no_active_init_jobs(self):
        """即使存在跳过/中止设备导致 succeeded < job_total，只要无活跃 init 作业且已进入 patrol，init 应收敛为 completed。"""
        assert (
            _stage_status_from_steps(
                "init",
                pr_status=PlanRunStatus.RUNNING.value,
                job_total=10,
                succeeded=8,
                skipped=1,
                failed=0,
                has_running_jobs=True,
                has_active_stage_jobs=False,
                has_next_stage_started=True,
                has_required_steps=True,
            )
            == "completed"
        )

    def test_running_init_completed_when_no_required_init_steps(self):
        assert (
            _stage_status_from_steps(
                "init",
                pr_status=PlanRunStatus.RUNNING.value,
                job_total=3,
                succeeded=0,
                failed=0,
                has_running_jobs=True,
                has_active_stage_jobs=False,
                has_next_stage_started=True,
                has_required_steps=False,
            )
            == "completed"
        )

    def test_running_patrol_pending_before_patrol_starts(self):
        assert (
            _stage_status_from_steps(
                "patrol",
                pr_status=PlanRunStatus.RUNNING.value,
                job_total=3,
                succeeded=0,
                failed=0,
                has_running_jobs=True,
                has_stage_started=False,
            )
            == "pending"
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


class TestNonIssueAeeArtifactFilter:
    def test_filters_non_issue_metadata_and_lock_files(self):
        for name in (
            "1795192503706_aee.lck",
            "1795192503709_db_history",
            "1795192503731_file_tree.txt",
            "1795322515947_SWT_JBT_TRACES",
            "aee.lck",
            "1795192503706_foo.tmp",
        ):
            assert is_non_issue_aee_artifact_filename(name) is True, name

    def test_keeps_real_crash_artifacts(self):
        for name in (
            "1795192503706_db.01.NE.dbg",
            "1795192503706_db.0.0",
            "bugreport-2026-11-24.zip",
            "db.fatal.00.KE.tar.gz",
        ):
            assert is_non_issue_aee_artifact_filename(name) is False, name
