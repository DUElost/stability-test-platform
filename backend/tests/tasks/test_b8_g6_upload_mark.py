"""B8 G6：现行 upload_task 不得标记 DEDUP_FIRST 分区的事件（D8 第 6 条）。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from backend.core.database import SessionLocal
from backend.models.device_log_event import DeviceLogEvent
from backend.models.host import Host
from backend.models.issue_fact import PartitionMode
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services import dedup_extract
from backend.tasks import saq_tasks

EVENT = "2026_1031_120000_123_db.74.ANR"


@pytest.mark.asyncio(loop_scope="module")
async def test_upload_task_skips_dedup_first_but_keeps_unisoc(monkeypatch):
    monkeypatch.setattr(
        dedup_extract, "collect_upload_event_dir_names", lambda db, run_id: [EVENT],
    )
    plan = Plan(name=f"g6-upload-{uuid4()}", created_by="pytest")
    host_id = f"g6-{uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        db.add(Host(
            id=host_id, hostname=host_id, name=host_id, ip="192.0.2.201",
            ip_address="192.0.2.201", status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc),
        ))
        db.add(plan)
        db.flush()
        run = PlanRun(
            plan_id=plan.id, status="SUCCESS", plan_snapshot={},
            run_type="MANUAL", triggered_by="pytest",
        )
        db.add(run)
        db.flush()
        db.add(PartitionMode(
            plan_run_id=run.id, platform="mtk", mode="DEDUP_FIRST",
        ))
        mtk = DeviceLogEvent(
            id=uuid4(), serial="SN-MTK", platform="MTK", event_type="JE",
            state="LOCAL", local_path=f"/data/aee/{EVENT}",
            host_id=host_id, plan_run_id=run.id, detected_at=datetime.now(timezone.utc),
        )
        unisoc = DeviceLogEvent(
            id=uuid4(), serial="SN-UNI", platform="UNISOC", event_type="JE",
            state="LOCAL", local_path=f"/data/aee/{EVENT}",
            host_id=host_id, plan_run_id=run.id, detected_at=datetime.now(timezone.utc),
        )
        db.add_all([mtk, unisoc])
        db.commit()
        plan_id, run_id, mtk_id, unisoc_id = plan.id, run.id, mtk.id, unisoc.id
    finally:
        db.close()

    try:
        await saq_tasks.upload_task({}, plan_run_id=run_id)
        db = SessionLocal()
        try:
            assert db.get(DeviceLogEvent, mtk_id).state == "LOCAL"
            assert db.get(DeviceLogEvent, unisoc_id).state == "UPLOAD_PENDING"
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(DeviceLogEvent).filter(
                DeviceLogEvent.plan_run_id == run_id,
            ).delete()
            db.query(PartitionMode).filter(
                PartitionMode.plan_run_id == run_id,
            ).delete()
            db.query(PlanRun).filter(PlanRun.id == run_id).delete()
            db.query(Plan).filter(Plan.id == plan_id).delete()
            db.query(Host).filter(Host.id == host_id).delete()
            db.commit()
        finally:
            db.close()
