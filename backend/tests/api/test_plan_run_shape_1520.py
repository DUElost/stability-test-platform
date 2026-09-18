"""#1520 形状正规化的运行时用例：summary / job artifacts（声明面之外的实弹面）。

静态双向对拍在 `tests/test_api_response_shape_contract.py`（`_MODEL_PAIRS`）；
本文件钉**运行时刻**：信封、键集合不被 response_model 静默裁剪、派生字段
（filename 取 storage_uri 尾段）与归属校验行为不变。
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.models.job import JobArtifact, JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun


class _Seed:
    @staticmethod
    def one(db_session, sample_device):
        now = datetime.now(timezone.utc)
        plan = Plan(name="shape-1520", description="", failure_threshold=0.05)
        db_session.add(plan)
        db_session.flush()
        run = PlanRun(
            plan_id=plan.id, status="SUCCESS", failure_threshold=0.05,
            plan_snapshot={"name": plan.name, "plan_id": plan.id}, run_type="MANUAL",
            triggered_by="pytest", started_at=now, ended_at=now,
        )
        db_session.add(run)
        db_session.flush()
        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=sample_device.id,
            host_id=sample_device.host_id, status="COMPLETED", status_reason=None,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            started_at=now, ended_at=now,
        )
        db_session.add(job)
        db_session.commit()
        return run, job


def test_summary_envelope_and_exact_keys(client, auth_headers, db_session, sample_device):
    run, _job = _Seed.one(db_session, sample_device)
    resp = client.get(f"/api/v1/plan-runs/{run.id}/summary", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"data", "error"} and body["error"] is None
    data = body["data"]
    # 键集合逐键相等——response_model 若漏声明字段，这里立刻红（静默裁剪正是
    # 手写 dict → 模型迁移唯一会**新引入**的失败形态）。
    assert set(data) == {
        "plan_run_id", "status", "total_jobs", "status_counts", "pass_rate",
        "started_at", "ended_at", "result_summary",
    }
    assert data["plan_run_id"] == run.id
    assert data["total_jobs"] == 1
    assert data["status_counts"] == {"COMPLETED": 1}
    assert data["pass_rate"] == 1.0


def test_artifacts_keys_and_derived_filename(client, auth_headers, db_session, sample_device):
    run, job = _Seed.one(db_session, sample_device)
    art = JobArtifact(
        job_id=job.id,
        storage_uri="/mnt/stp-aee/jobs/9/serial/file.tar.gz",
        artifact_type="log_archive", size_bytes=7, checksum="sha",
    )
    db_session.add(art)
    db_session.commit()

    resp = client.get(
        f"/api/v1/plan-runs/{run.id}/jobs/{job.id}/artifacts", headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"data", "error"}
    rows = body["data"]
    assert len(rows) == 1
    assert set(rows[0]) == {
        "id", "job_id", "filename", "artifact_type",
        "size_bytes", "checksum", "created_at",
    }
    assert rows[0]["filename"] == "file.tar.gz"


def test_artifacts_ownership_404(client, auth_headers, db_session, sample_device):
    run, job = _Seed.one(db_session, sample_device)
    resp = client.get(
        f"/api/v1/plan-runs/{run.id + 999}/jobs/{job.id}/artifacts",
        headers=auth_headers,
    )
    assert resp.status_code == 404


class TestWriteSideSummaryBranches:
    """#1520 写侧摘要刀：三端点升具名模型后的**逐分支**键形状（#2089 判据）。

    只看并集拦不住多分支形态——每个分支单独断言键集合与值的真实性。
    """

    def test_queued_abort_returns_full_key_set_with_true_empties(
        self, client, auth_headers, db_session, sample_device
    ):
        """QUEUED 分支：此前**缺** abort_requested_jobs 键；统一为恒在 + 空数组。

        空值与该分支写入的 run_context.abort_requested.requested_job_ids=[] 同事实，
        不是幻影占位（区别于 #2089 的 released_leases：那个是恒 0 却承诺"已释放"）。
        """
        run, job = _Seed.one(db_session, sample_device)
        # QUEUED 终态分支的前提是**零 job**（有 job 会按不变量①回退标准 abort）
        db_session.delete(job)
        run.status = "QUEUED"
        db_session.commit()

        resp = client.post(f"/api/v1/plan-runs/{run.id}/abort", json={}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {"data", "error"}
        data = body["data"]
        assert set(data) == {
            "plan_run_id", "status", "phase", "aborted_jobs", "abort_requested_jobs",
        }
        assert data["phase"] == "queued"
        assert data["status"] == "FAILED"
        assert data["aborted_jobs"] == []
        assert data["abort_requested_jobs"] == []

    def test_running_branch_abort_keys_present_with_values(
        self, client, auth_headers, db_session, sample_device
    ):
        """RUNNING 分支（PENDING job 立即中止）：同一键集合，值为真列表。"""
        from backend.models.enums import JobStatus

        run, job = _Seed.one(db_session, sample_device)
        run.status = "RUNNING"
        job.status = JobStatus.PENDING.value
        job.ended_at = None
        db_session.commit()

        resp = client.post(f"/api/v1/plan-runs/{run.id}/abort", json={}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert set(data) == {
            "plan_run_id", "status", "phase", "aborted_jobs", "abort_requested_jobs",
        }
        assert data["phase"] == "running"
        assert job.id in data["aborted_jobs"]

    def test_archive_trigger_response_keys(
        self, client, auth_headers, db_session, sample_device, monkeypatch
    ):
        """archive 端点（admin 面）：模型五键 + 跳过位数组形状（无前端消费者的认领面）。"""
        from backend.api.routes import plan_runs as proute

        run, job = _Seed.one(db_session, sample_device)
        run.status = "SUCCESS"
        db_session.commit()

        async def fake_emit(*a, **k):
            return None

        monkeypatch.setattr(proute, "archive_plan_run_logs", _noop_async_result())
        resp = client.post(f"/api/v1/plan-runs/{run.id}/archive", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert set(data) == {
            "plan_run_id", "archived_now", "triggered_hosts",
            "skipped_offline", "skipped_retired",
        }
        assert data["plan_run_id"] == run.id and data["archived_now"] is True


def _noop_async_result():
    """替换 archive service 为固定模型返回——只验路由响应形状，不触 SocketIO。"""
    from backend.api.schemas.plan_run import PlanRunArchiveTriggerOut

    async def _f(db, run_id, **kw):
        return PlanRunArchiveTriggerOut(
            plan_run_id=run_id, archived_now=True,
            triggered_hosts=["host-a"], skipped_offline=[],
            skipped_retired=[{"host_id": "host-r", "status": "OFFLINE"}],
        )
    return _f
