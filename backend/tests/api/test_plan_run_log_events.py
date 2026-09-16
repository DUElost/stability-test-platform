"""#529 — PlanRun log-events endpoint (DLE archive authority)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from backend.models.device_log_event import DeviceLogEvent
from backend.models.job import JobInstance, JobLogSignal
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.scheduler.signal_link_reconciler import reconcile_signal_links_once


def _seed_plan_run(db_session, sample_device):
    now = datetime.now(timezone.utc)
    plan = Plan(name="log-events-plan", failure_threshold=0.05)
    db_session.add(plan)
    db_session.flush()
    pr = PlanRun(
        plan_id=plan.id,
        status="SUCCESS",
        failure_threshold=0.05,
        plan_snapshot={},
        run_type="MANUAL",
        started_at=now,
        ended_at=now,
    )
    db_session.add(pr)
    db_session.flush()
    job = JobInstance(
        plan_run_id=pr.id,
        plan_id=plan.id,
        device_id=sample_device.id,
        host_id=sample_device.host_id,
        status="COMPLETED",
        pipeline_def={"lifecycle": {}},
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(job)
    db_session.flush()
    return pr, job, now


def test_plan_run_log_events_lists_dle_rows(client, auth_headers, db_session, sample_device):
    pr, job, now = _seed_plan_run(db_session, sample_device)
    event_id = uuid4()
    db_session.add(DeviceLogEvent(
        id=event_id,
        serial=sample_device.serial,
        platform="MTK",
        event_type="AEE",
        event_subtype="KE",
        detected_at=now,
        state="REMOTE",
        local_path="/local/aee/1",
        remote_path="/nfs/devices/1/aee/1",
        host_id=str(sample_device.host_id),
        job_id=job.id,
        plan_run_id=pr.id,
    ))
    db_session.commit()

    resp = client.get(
        f"/api/v1/plan-runs/{pr.id}/log-events",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["plan_run_id"] == pr.id
    assert data["data_authority"] == "device_log_event"
    assert data["total"] == 1
    assert data["items"][0]["id"] == str(event_id)
    assert data["items"][0]["remote_path"] == "/nfs/devices/1/aee/1"


def test_plan_run_log_events_filters_by_platform(client, auth_headers, db_session, sample_device):
    """#2184：平台筛选在**服务端**。

    客户端过滤只作用于已加载页，会谎报「该平台只有 N 条」（实际是第 1 页有 N 条）。
    这里同时钉住 ``total`` 随筛选收窄——否则前端「已显示 1 / 2」照样误导。
    """
    pr, job, now = _seed_plan_run(db_session, sample_device)
    for platform in ("MTK", "UNISOC"):
        db_session.add(DeviceLogEvent(
            id=uuid4(),
            serial=sample_device.serial,
            platform=platform,
            event_type="AEE",
            event_subtype="KE",
            detected_at=now,
            state="REMOTE",
            local_path=f"/local/aee/{platform}",
            host_id=str(sample_device.host_id),
            job_id=job.id,
            plan_run_id=pr.id,
        ))
    db_session.commit()

    all_resp = client.get(
        f"/api/v1/plan-runs/{pr.id}/log-events",
        headers=auth_headers,
    )
    assert all_resp.status_code == 200, all_resp.text
    assert all_resp.json()["data"]["total"] == 2

    uni_resp = client.get(
        f"/api/v1/plan-runs/{pr.id}/log-events",
        params={"platform": "UNISOC"},
        headers=auth_headers,
    )
    assert uni_resp.status_code == 200, uni_resp.text
    data = uni_resp.json()["data"]
    assert data["total"] == 1
    assert [item["platform"] for item in data["items"]] == ["UNISOC"]


def test_watcher_summary_reports_links_made_by_reconcile_sweep(
    client, auth_headers, db_session, sample_device,
):
    """#556: the reconcile sweep repairs; watcher-summary only reports.

    Before the request performs no write, so link_stats reflects the database
    as-is; after a sweep tick the same endpoint reports the repaired link.
    """
    pr, job, now = _seed_plan_run(db_session, sample_device)
    event_id = uuid4()
    db_session.add(DeviceLogEvent(
        id=event_id,
        serial=sample_device.serial,
        platform="MTK",
        event_type="AEE",
        event_subtype="KE",
        detected_at=now,
        state="LOCAL",
        local_path="/local/aee/1",
        host_id=str(sample_device.host_id),
        job_id=job.id,
        plan_run_id=pr.id,
        signal_seq_no=1,
    ))
    db_session.add(JobLogSignal(
        job_id=job.id,
        host_id=str(sample_device.host_id),
        device_serial=sample_device.serial,
        seq_no=1,
        category="AEE",
        source="reconciler",
        path_on_device="/data/aee/1",
        detected_at=now,
        received_at=now,
    ))
    db_session.commit()

    before = client.get(
        f"/api/v1/plan-runs/{pr.id}/watcher-summary?window_minutes=60",
        headers=auth_headers,
    )
    assert before.status_code == 200, before.text
    assert before.json()["data"]["archive"]["link_stats"]["linked_signals"] == 0

    reconcile_signal_links_once()

    resp = client.get(
        f"/api/v1/plan-runs/{pr.id}/watcher-summary?window_minutes=60",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    link_stats = resp.json()["data"]["archive"]["link_stats"]
    assert link_stats["linked_signals"] == 1
    assert link_stats["link_rate"] == 1.0


def test_log_events_returns_platform_universe(
    client, auth_headers, db_session, sample_device
):
    """#2288：响应带该 run 的平台**全集**，且不随 `platform`/`limit` 收窄。

    前端筛选选项据此渲染。旧做法是前端从「已加载行」派生：最新一页恰好全是 MTK
    （单平台主导 + `detected_at DESC` 的常见形态）时只剩一个选项 → 整行筛选隐藏，
    UNISOC 永远翻不到，`MAX_LIMIT=500` 也救不了。
    """
    pr, job, now = _seed_plan_run(db_session, sample_device)
    for idx, platform in enumerate(["MTK", "MTK", "UNISOC"]):
        db_session.add(DeviceLogEvent(
            id=uuid4(),
            serial=sample_device.serial,
            platform=platform,
            event_type="AEE",
            event_subtype="KE",
            detected_at=now,
            state="REMOTE",
            local_path=f"/local/aee/2288-{idx}",
            remote_path=f"/nfs/devices/2288/{idx}",
            host_id=str(sample_device.host_id),
            job_id=job.id,
            plan_run_id=pr.id,
        ))
    db_session.commit()

    resp = client.get(f"/api/v1/plan-runs/{pr.id}/log-events", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["platforms"] == ["MTK", "UNISOC"], "全集按平台名字典序，供直接渲染 chip"

    # 筛到单一平台 + 极小窗口时全集**不得**跟着收窄（否则选项集自噬、切不回去）
    narrowed = client.get(
        f"/api/v1/plan-runs/{pr.id}/log-events?platform=MTK&limit=1",
        headers=auth_headers,
    ).json()["data"]
    assert narrowed["platforms"] == ["MTK", "UNISOC"]
    assert len(narrowed["items"]) == 1, "items 仍受 limit 约束"
    assert narrowed["total"] == 2, "total 随 platform 筛选收窄（与全集语义区分）"
