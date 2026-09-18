"""#1520 垂直切片：PlanRun summary / artifacts / result views 直测。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from unittest.mock import MagicMock

from backend.services.plan_run_job_artifacts import list_plan_run_job_artifacts
from backend.services.plan_run_summary import build_plan_run_summary


def test_summary_not_found_404():
    db = MagicMock()
    db.get = MagicMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        build_plan_run_summary(db, 1)
    assert exc.value.status_code == 404


def test_summary_empty_jobs():
    pr = MagicMock()
    pr.status = "RUNNING"
    # #2623：MagicMock 的 plan_id 默认是另一个 MagicMock（truthy），
    # resolve_plan_name 会查库并把 MagicMock 塞进 plan_name → ValidationError。
    # 空 job 场景不关心 plan 名；显式 None 走短路径。
    pr.plan_id = None
    pr.started_at = None
    pr.ended_at = None
    pr.result_summary = {}
    db = MagicMock()
    db.get = MagicMock(return_value=pr)
    result = MagicMock()
    result.all.return_value = []
    db.execute = MagicMock(return_value=result)
    out = build_plan_run_summary(db, 9)
    assert out.plan_run_id == 9
    assert out.total_jobs == 0
    assert out.pass_rate == 0.0
    assert out.plan_name is None


def test_list_artifacts_job_mismatch_404():
    job = MagicMock()
    job.plan_run_id = 2
    db = MagicMock()
    db.get = MagicMock(return_value=job)
    with pytest.raises(HTTPException) as exc:
        list_plan_run_job_artifacts(db, run_id=1, job_id=5)
    assert exc.value.status_code == 404


def test_list_artifacts_maps_filename():
    job = MagicMock()
    job.plan_run_id = 1
    art = MagicMock()
    art.id = 3
    art.job_id = 5
    art.storage_uri = "/nfs/jobs/5/a.tar.gz"
    art.artifact_type = "log"
    art.size_bytes = 10
    art.checksum = "abc"
    art.created_at = None
    result = MagicMock()
    result.scalars.return_value.all.return_value = [art]
    db = MagicMock()
    db.get = MagicMock(return_value=job)
    db.execute = MagicMock(return_value=result)
    out = list_plan_run_job_artifacts(db, 1, 5)
    assert out[0].filename == "a.tar.gz"
    assert out[0].id == 3
