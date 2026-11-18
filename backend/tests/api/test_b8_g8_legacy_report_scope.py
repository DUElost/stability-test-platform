"""B8 G8：既有报表入口的分区边界（ADR-0062 D10，§11 场景 13）。

``POST /api/v1/jira/runs` 的 ``source=plan_run`` 是既有的「按 PlanRun 产物
取报表」入口。本文件验证：

- 纯 DEDUP_FIRST 分区的报表在消费前被拒绝；
- 混合平台运行只接受可确认归属的 CURRENT 分区报表；
- 无模式存量运行沿用既有产物读取规则；
- 同链其他实际 PlanRun 的产物与错误类型在启动工具前被拒绝。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.models.jira_run import JiraRun
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services import issue_fact_store as facts
from backend.services.run_console import RunConsole


@pytest.fixture(autouse=True)
def reset_run_console_singleton():
    RunConsole._reset_for_tests()
    yield
    RunConsole._reset_for_tests()


@pytest.fixture
def mock_run_console(monkeypatch):
    inst = MagicMock()
    inst.start.side_effect = lambda *args, **kwargs: kwargs.get("run_id") or "con-fake"
    monkeypatch.setattr("backend.api.routes.dedup.RunConsole.instance", lambda: inst)
    return inst


@pytest.fixture
def vendor_env(monkeypatch):
    monkeypatch.setenv("STP_JIRA_TRANSSION_PYTHON", "/opt/fake/python")
    monkeypatch.setenv("STP_JIRA_TRANSSION_DIR", "/opt/fake/tool")


def _run(db, *, mode=None, platform="mtk", status="SUCCESS", root_plan_run_id=None):
    plan = Plan(name=f"g8-legacy-{platform}-{mode}", created_by="pytest")
    db.add(plan)
    db.flush()
    run = PlanRun(
        plan_id=plan.id, status=status, plan_snapshot={},
        run_type="MANUAL", triggered_by="pytest",
        root_plan_run_id=root_plan_run_id,
    )
    db.add(run)
    db.flush()
    if mode is not None:
        facts.record_partition_mode(
            db, plan_run_id=run.id, platform=platform, mode=mode,
        )
    db.commit()
    return run


def _artifact(db, run, uri, *, artifact_type="scan_result_xls"):
    row = PlanRunArtifact(
        plan_run_id=run.id, storage_uri=uri, artifact_type=artifact_type,
    )
    db.add(row)
    db.commit()
    return row


def _post(client, headers, artifact_id, *, plan_run_id=None):
    data = {
        "vendor": "transsion",
        "stage": "upload_list",
        "dry_run": "true",
        "source": "plan_run",
        "artifact_id": str(artifact_id),
    }
    if plan_run_id is not None:
        data["plan_run_id"] = str(plan_run_id)
    return client.post("/api/v1/jira/runs", data=data, headers=headers)


def _assert_zero_start(mock_run_console, db, run_id):
    mock_run_console.start.assert_not_called()
    assert db.query(JiraRun).filter(JiraRun.plan_run_id == run_id).count() == 0


def test_pure_dedup_first_artifact_rejected_before_start(
    client, admin_headers, vendor_env, mock_run_console, db_session,
):
    run = _run(db_session, mode=facts.PARTITION_MODE_DEDUP_FIRST)
    artifact = _artifact(
        db_session, run, f"/center/dedup/{run.id}/mtk/host_Result_1_org.xls",
    )
    resp = _post(client, admin_headers, artifact.id, plan_run_id=run.id)
    assert resp.status_code == 409
    assert "CURRENT" in resp.json()["detail"]
    _assert_zero_start(mock_run_console, db_session, run.id)


def test_mixed_run_accepts_only_current_partition_report(
    client, admin_headers, vendor_env, mock_run_console, db_session,
):
    run = _run(db_session, mode=facts.PARTITION_MODE_DEDUP_FIRST, platform="mtk")
    facts.record_partition_mode(
        db_session, plan_run_id=run.id, platform="unisoc",
        mode=facts.PARTITION_MODE_CURRENT,
    )
    db_session.commit()
    mtk = _artifact(
        db_session, run, f"/center/dedup/{run.id}/mtk/host_Result_1_org.xls",
    )
    unisoc = _artifact(
        db_session, run, f"/center/dedup/{run.id}/unisoc/host_Result_2_org.xls",
    )

    rejected = _post(client, admin_headers, mtk.id, plan_run_id=run.id)
    assert rejected.status_code == 409
    mock_run_console.start.assert_not_called()

    accepted = _post(client, admin_headers, unisoc.id, plan_run_id=run.id)
    assert accepted.status_code == 200, accepted.text
    mock_run_console.start.assert_called_once()
    argv = mock_run_console.start.call_args.kwargs["cmd"]
    assert str(unisoc.storage_uri) in argv


def test_no_mode_legacy_artifact_keeps_existing_rule(
    client, admin_headers, vendor_env, mock_run_console, db_session,
):
    run = _run(db_session, mode=None)
    artifact = _artifact(db_session, run, "/mnt/fake/Result_org.xls")
    resp = _post(client, admin_headers, artifact.id, plan_run_id=run.id)
    assert resp.status_code == 200, resp.text
    mock_run_console.start.assert_called_once()


def test_artifact_from_another_actual_plan_run_rejected(
    client, admin_headers, vendor_env, mock_run_console, db_session,
):
    run_a = _run(db_session, mode=facts.PARTITION_MODE_CURRENT)
    run_b = _run(
        db_session, mode=facts.PARTITION_MODE_CURRENT,
        root_plan_run_id=run_a.id,
    )
    other = _artifact(
        db_session, run_b, f"/center/dedup/{run_b.id}/mtk/host_Result_1_org.xls",
    )
    resp = _post(client, admin_headers, other.id, plan_run_id=run_a.id)
    assert resp.status_code == 409
    assert "different plan run" in resp.json()["detail"]
    _assert_zero_start(mock_run_console, db_session, run_a.id)
    _assert_zero_start(mock_run_console, db_session, run_b.id)


def test_unconfirmable_or_non_report_artifact_rejected(
    client, admin_headers, vendor_env, mock_run_console, db_session,
):
    run = _run(db_session, mode=facts.PARTITION_MODE_CURRENT)
    flat = _artifact(db_session, run, "/mnt/fake/Result_org.xls")
    bundle = _artifact(
        db_session, run, f"/center/dedup/{run.id}/mtk/bundle.zip",
        artifact_type="extract_bundle",
    )

    unconfirmed = _post(client, admin_headers, flat.id, plan_run_id=run.id)
    assert unconfirmed.status_code == 409
    assert "cannot be confirmed" in unconfirmed.json()["detail"]

    non_report = _post(client, admin_headers, bundle.id, plan_run_id=run.id)
    assert non_report.status_code == 409
    assert "not a report" in non_report.json()["detail"]
    mock_run_console.start.assert_not_called()
