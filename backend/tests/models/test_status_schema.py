from sqlalchemy import Enum as SAEnum

from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun


def test_job_instance_status_uses_sqlalchemy_enum():
    assert isinstance(JobInstance.__table__.c.status.type, SAEnum)


def test_plan_run_status_uses_sqlalchemy_enum():
    assert isinstance(PlanRun.__table__.c.status.type, SAEnum)


def test_job_instance_has_plan_run_status_composite_index():
    indexes = {
        index.name: [column.name for column in index.columns]
        for index in JobInstance.__table__.indexes
    }
    assert indexes["idx_job_instance_plan_run_status"] == ["plan_run_id", "status"]
