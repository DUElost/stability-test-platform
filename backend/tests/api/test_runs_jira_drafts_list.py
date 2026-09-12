"""#1532：跨 Job 的缓存 JIRA 草稿列表端点（``GET /api/v1/runs/jira-drafts``）。

回归对象：草稿列表若由前端「取 N 条 PlanRun → 逐 Run 列 jobs → 逐 Job 取草稿」
自算，无草稿的 Run 上内层短路不触发，退化成 ``1 + N + Σ(每个 Run 全部 Job)``
次串行 404。本端点把这次扇出收敛成单次查询。

本文件锁定端点的 id 域契约：``job_id`` 是 JobInstance id（与单品草稿端点同口径），
``plan_run_id`` 是 PlanRun id。同一 PlanRun 下多个 Job 共享一个 ``plan_run_id``，
故两个字段不可能是同一个值域——这是「调用方不需要自算 id 换算」的机器可判定形式。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.models.host import Device
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun

DRAFT = {
    "summary": "Crash on boot",
    "priority": "Minor",
    "project_key": "ABC",
    "issue_type": "Bug",
}


def _seed_job(
    db_session,
    *,
    plan_run: PlanRun,
    device: Device,
    when: datetime,
    draft: dict | None = DRAFT,
    post_processed: bool = True,
) -> JobInstance:
    job = JobInstance(
        plan_run_id=plan_run.id,
        plan_id=plan_run.plan_id,
        device_id=device.id,
        host_id=device.host_id,
        status="COMPLETED",
        pipeline_def={"stages": {"prepare": [], "execute": [], "post_process": []}},
        started_at=when,
        ended_at=when,
        created_at=when,
        updated_at=when,
        jira_draft_json=draft,
        post_processed_at=when if post_processed else None,
    )
    db_session.add(job)
    db_session.commit()
    return job


def _extra_device(db_session, sample_host, serial: str) -> Device:
    device = Device(serial=serial, host_id=sample_host.id, status="ONLINE")
    db_session.add(device)
    db_session.commit()
    return device


class TestRecentJiraDraftsList:
    def test_job_id_and_plan_run_id_are_independent_id_domains(
        self, client, auth_headers, db_session, sample_plan_run, sample_device, sample_host,
    ):
        """同一 PlanRun 的两个 Job：job_id 各不相同，plan_run_id 同一个。"""
        now = datetime.now(timezone.utc)
        second_device = _extra_device(db_session, sample_host, "test-device-1532")
        job_a = _seed_job(
            db_session, plan_run=sample_plan_run, device=sample_device, when=now,
        )
        job_b = _seed_job(
            db_session, plan_run=sample_plan_run, device=second_device, when=now,
        )

        resp = client.get("/api/v1/runs/jira-drafts", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        items = resp.json()["data"]

        assert {item["job_id"] for item in items} == {job_a.id, job_b.id}
        assert {item["plan_run_id"] for item in items} == {sample_plan_run.id}
        assert [item["draft"] for item in items] == [DRAFT, DRAFT]

    def test_excludes_not_post_processed_and_draft_less_jobs(
        self, client, auth_headers, db_session, sample_plan_run, sample_device, sample_host,
    ):
        """列表语义 = 缓存：未 post-process 或没草稿的 Job 都不出现。"""
        now = datetime.now(timezone.utc)
        second_device = _extra_device(db_session, sample_host, "test-device-1532")
        third_device = _extra_device(db_session, sample_host, "test-device-1532-b")
        cached = _seed_job(
            db_session, plan_run=sample_plan_run, device=sample_device, when=now,
        )
        _seed_job(
            db_session, plan_run=sample_plan_run, device=second_device, when=now,
            post_processed=False,
        )
        _seed_job(
            db_session, plan_run=sample_plan_run, device=third_device, when=now,
            draft=None,
        )

        resp = client.get("/api/v1/runs/jira-drafts", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        items = resp.json()["data"]

        assert [item["job_id"] for item in items] == [cached.id]
        assert items[0]["post_processed_at"] is not None
        assert items[0]["ended_at"] is not None

    def test_newest_first_and_limit_respected(
        self, client, auth_headers, db_session, sample_plan_run, sample_device, sample_host,
    ):
        now = datetime.now(timezone.utc)
        second_device = _extra_device(db_session, sample_host, "test-device-1532")
        older = _seed_job(
            db_session, plan_run=sample_plan_run, device=sample_device,
            when=now - timedelta(hours=2),
        )
        newer = _seed_job(
            db_session, plan_run=sample_plan_run, device=second_device, when=now,
        )

        resp = client.get("/api/v1/runs/jira-drafts", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert [item["job_id"] for item in resp.json()["data"]] == [newer.id, older.id]

        limited = client.get("/api/v1/runs/jira-drafts?limit=1", headers=auth_headers)
        assert limited.status_code == 200, limited.text
        assert [item["job_id"] for item in limited.json()["data"]] == [newer.id]

    def test_limit_is_bounded(self, client, auth_headers):
        """越界 limit 被 FastAPI 校验挡下，不会退化成无界查询。"""
        resp = client.get("/api/v1/runs/jira-drafts?limit=0", headers=auth_headers)
        assert resp.status_code == 422, resp.text
