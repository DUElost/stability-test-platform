"""#1520 垂直切片：PlanRun chain 服务层直测。"""

from __future__ import annotations

from unittest.mock import MagicMock

from backend.models.enums import PlanRunStatus
from backend.services.plan_run_chain import (
    MAX_CHAIN_DEPTH,
    chain_node_from_run,
)


def test_max_chain_depth_mirrors_write_side():
    assert MAX_CHAIN_DEPTH == 20


def test_chain_node_from_run_maps_fields():
    pr = MagicMock()
    pr.plan_id = 3
    pr.id = 10
    pr.status = PlanRunStatus.SUCCESS.value
    pr.chain_index = 1
    pr.started_at = None
    pr.ended_at = None
    pr.failed_job_count = 2

    node = chain_node_from_run(pr, "plan-a", is_current=True)
    assert node.plan_id == 3
    assert node.plan_name == "plan-a"
    assert node.plan_run_id == 10
    assert node.is_current is True
    assert node.failed_jobs == 2  # ADR-0048
    assert node.chain_index == 1
