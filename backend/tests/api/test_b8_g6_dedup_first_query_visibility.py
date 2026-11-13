"""B8 G6 R1 返修：共享 DLE 查询必须按用途返回，旧上传入口在入队处拒绝。

第 1 轮复核 F1：list_agent_device_log_events 对所有 state 无条件过滤
DEDUP_FIRST 且只放行 SpillLedger 行，导致
- HddSpill 看不到 DEDUP_FIRST 的 LOCAL 候选（D2 第 3 条 / I2）；
- 首次溢出失败（尚无台账）的 UPLOAD_FAILED/UPLOADING 在重启后不可见（I4）；
- scan_scope 的 state="" 查询丢失本 host/run 输入。
本文件同时覆盖完整链：候选查询 → force 入队 → 首传失败 → 重启恢复 →
校验回执 → 补扫读回。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from backend.core.database import AsyncSessionLocal, SessionLocal
from backend.models.device_log_event import DeviceLogEvent
from backend.models.host import Host
from backend.models.issue_fact import PartitionMode
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services import issue_fact_store as facts
from backend.services.agent_device_log_events import (
    DeviceLogEventBatchIn,
    DeviceLogEventIn,
    ingest_agent_device_log_events,
    list_agent_device_log_events,
)

EVENT = "2026_1031_120000_123_db.74.ANR"
FOLDER = "MLD-NX3-P535AA-HONOR-NX3-16-1031V78_1031_MonkeyAEEinfo"


def _seed(*, mode="DEDUP_FIRST"):
    host_id = f"g6-q-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(Host(
            id=host_id, hostname=host_id, name=host_id, ip="192.0.2.211",
            ip_address="192.0.2.211", status="ONLINE", last_heartbeat=now,
        ))
        plan = Plan(name=f"g6-q-{uuid4()}", created_by="pytest")
        db.add(plan)
        db.flush()
        run = PlanRun(
            plan_id=plan.id, status="RUNNING", plan_snapshot={},
            run_type="MANUAL", triggered_by="pytest",
        )
        db.add(run)
        db.flush()
        if mode:
            db.add(PartitionMode(plan_run_id=run.id, platform="mtk", mode=mode))
        db.commit()
        return {
            "host_id": host_id, "plan_id": plan.id, "run_id": run.id,
            "serial": f"SN-{uuid4().hex[:6]}",
        }
    finally:
        db.close()


def _dle(seed, *, platform="MTK", state="LOCAL", suffix=""):
    now = datetime.now(timezone.utc)
    event_id = uuid4()
    local = f"/data/aee/{FOLDER}/{seed['serial']}/aee_exp/{EVENT}{suffix}"
    db = SessionLocal()
    try:
        db.add(DeviceLogEvent(
            id=event_id, serial=seed["serial"], platform=platform,
            event_type="JE", state=state, local_path=local,
            host_id=seed["host_id"], plan_run_id=seed["run_id"],
            detected_at=now,
        ))
        db.commit()
        return event_id, local
    finally:
        db.close()


def _cleanup(seed):
    db = SessionLocal()
    try:
        db.query(DeviceLogEvent).filter(
            DeviceLogEvent.host_id == seed["host_id"],
        ).delete()
        db.query(PartitionMode).filter(
            PartitionMode.plan_run_id == seed["run_id"],
        ).delete()
        db.query(PlanRun).filter(PlanRun.id == seed["run_id"]).delete()
        db.query(Plan).filter(Plan.id == seed["plan_id"]).delete()
        db.query(Host).filter(Host.id == seed["host_id"]).delete()
        db.commit()
    finally:
        db.close()


async def _list(seed, **kwargs):
    async with AsyncSessionLocal() as db:
        return await list_agent_device_log_events(db, host_id=seed["host_id"], **kwargs)


@pytest.mark.asyncio(loop_scope="module")
async def test_hddspill_local_candidates_visible_in_dedup_first():
    seed = _seed()
    try:
        event_id, _ = _dle(seed, state="LOCAL")
        response = await _list(seed, state="LOCAL")
        rows = {row["id"]: row for row in response["events"]}
        assert str(event_id) in rows
        assert rows[str(event_id)]["dedup_first"] is True
    finally:
        _cleanup(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_first_failed_spill_retry_visible_without_ledger():
    seed = _seed()
    try:
        event_id, _ = _dle(seed, state="UPLOAD_FAILED")
        # 尚无 SpillLedger：回执尚未成功
        response = await _list(seed, state="UPLOAD_FAILED,UPLOADING")
        rows = {row["id"]: row for row in response["events"]}
        assert str(event_id) in rows
        assert rows[str(event_id)]["dedup_first"] is True
        db = SessionLocal()
        try:
            assert facts.list_partition_spill_inputs(
                db, plan_run_id=seed["run_id"], platform="mtk",
                host_id=seed["host_id"],
            ) == []
        finally:
            db.close()
    finally:
        _cleanup(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_scan_inputs_visible_for_dedup_first_host_and_run():
    seed = _seed()
    try:
        event_id, _ = _dle(seed, state="LOCAL")
        response = await _list(seed, state="", plan_run_id=seed["run_id"])
        rows = {row["id"]: row for row in response["events"]}
        assert str(event_id) in rows
        assert rows[str(event_id)]["dedup_first"] is True
    finally:
        _cleanup(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_current_and_unisoc_events_are_not_marked_dedup_first():
    seed = _seed()
    try:
        mtk, _ = _dle(seed, state="LOCAL")
        unisoc, _ = _dle(seed, platform="UNISOC", state="UPLOAD_FAILED")
        response = await _list(seed, state="LOCAL,UPLOAD_FAILED")
        rows = {row["id"]: row for row in response["events"]}
        assert rows[str(mtk)]["dedup_first"] is True
        assert rows[str(unisoc)]["dedup_first"] is False
    finally:
        _cleanup(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_no_mode_run_events_are_not_marked_dedup_first():
    seed = _seed(mode=None)
    try:
        event_id, _ = _dle(seed, state="LOCAL")
        response = await _list(seed, state="LOCAL")
        rows = {row["id"]: row for row in response["events"]}
        assert rows[str(event_id)]["dedup_first"] is False
    finally:
        _cleanup(seed)



@pytest.mark.asyncio(loop_scope="module")
async def test_spill_recovery_full_chain_candidate_to_rescan(tmp_path, monkeypatch):
    """真实候选查询 → force 入队 → 首传失败 → 重启恢复 → 回执 → 补扫读回。"""
    from backend.agent import event_uploader as event_uploader_module
    from backend.agent.aee import device_log_event_client as dle_client_module
    from backend.agent.event_uploader import EventUploader

    local_root = tmp_path / "local"
    nfs_root = tmp_path / "nfs"
    local_root.mkdir()
    nfs_root.mkdir()
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(local_root))
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs_root))
    monkeypatch.delenv("STP_DEVICE_LOG_EVENT_ENABLED", raising=False)
    state_db = tmp_path / "agent_state.db"
    conn = sqlite3.connect(state_db)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setenv("STP_AGENT_STATE_DB", str(state_db))

    seed = _seed()
    try:
        event_id, local_path = _dle(seed, state="LOCAL")
        event_dir = local_root / FOLDER / seed["serial"] / "aee_exp" / EVENT
        event_dir.mkdir(parents=True)
        (event_dir / "main.dbg").write_bytes(b"dbg")
        # 用真实目录替换 DLE 的 local_path，供 Agent 复制与中心路径校验
        db = SessionLocal()
        try:
            row = db.get(DeviceLogEvent, event_id)
            row.local_path = str(event_dir)
            db.commit()
        finally:
            db.close()

        # 1) 真实候选查询：HddSpill 能发现 DEDUP_FIRST 的 LOCAL 候选
        response = await _list(seed, state="LOCAL")
        candidate = next(
            row for row in response["events"] if row["id"] == str(event_id)
        )
        assert candidate["dedup_first"] is True

        # 2) force 入队（HddSpill 语义）：写持久溢出意图；进程在首传失败前退出
        first = EventUploader()
        first.configure(
            api_url="http://cp", agent_secret="s", host_id=seed["host_id"],
            nfs_root=str(nfs_root),
        )
        assert first.enqueue_local_event(
            event={**candidate, "hdd_spill": True},
            force=True, prune_after_upload=True,
        ) is True
        first._queue.get_nowait()
        assert event_uploader_module._durable_spill_flag(str(event_id)) is True

        # 首传失败：Agent 已打 UPLOADING、尚无 SpillLedger（回执未成功）
        db = SessionLocal()
        try:
            db.get(DeviceLogEvent, event_id).state = "UPLOADING"
            db.commit()
        finally:
            db.close()
        db = SessionLocal()
        try:
            assert facts.list_partition_spill_inputs(
                db, plan_run_id=seed["run_id"], platform="mtk",
                host_id=seed["host_id"],
            ) == []
        finally:
            db.close()

        # 3) 重启恢复：新实例从持久意图识别溢出，不再依赖 SpillLedger
        restarted = EventUploader()
        restarted.configure(
            api_url="http://cp", agent_secret="s", host_id=seed["host_id"],
            nfs_root=str(nfs_root),
        )
        recovery = await _list(seed, state="UPLOAD_FAILED,UPLOADING")
        recovered = next(
            row for row in recovery["events"] if row["id"] == str(event_id)
        )
        assert recovered["dedup_first"] is True
        assert restarted.enqueue_local_event(
            event={
                **recovered,
                "local_path": str(event_dir),
            },
            force=True,
        ) is True
        job = restarted._queue.get_nowait()
        assert job.hdd_spill is True

        # 4) 复制完成但回执失败：源文件必须保留、SpillLedger 尚未登记
        patches = []
        fail_remote = {"value": True}

        def fake_post(url, json, headers, timeout):
            payload = json["events"][0]
            patches.append(payload)
            if payload["state"] == "REMOTE" and fail_remote["value"]:
                return _Resp(500)
            return _Resp(200)

        class _Resp:
            def __init__(self, status):
                self.status_code = status
                self.text = ""

            def json(self):
                return {"data": {}}

        monkeypatch.setattr(event_uploader_module.requests, "post", fake_post)
        monkeypatch.setattr(dle_client_module.requests, "post", fake_post)
        restarted._upload_one(job)
        assert event_dir.is_dir()  # 回执前不删源
        db = SessionLocal()
        try:
            assert facts.list_partition_spill_inputs(
                db, plan_run_id=seed["run_id"], platform="mtk",
                host_id=seed["host_id"],
            ) == []
        finally:
            db.close()

        # 5) 回执成功：REMOTE + hdd_spill 载荷 → 服务端登记 SpillLedger → 补扫读回
        fail_remote["value"] = False
        restarted._upload_one(job)
        remote = next(
            payload for payload in patches
            if payload["state"] == "REMOTE" and payload.get("hdd_spill") is True
        )
        async with AsyncSessionLocal() as adb:
            result = await ingest_agent_device_log_events(
                adb, DeviceLogEventBatchIn(events=[DeviceLogEventIn(**remote)]),
            )
        assert result["upserted"] == 1
        db = SessionLocal()
        try:
            spill_inputs = facts.list_partition_spill_inputs(
                db, plan_run_id=seed["run_id"], platform="mtk",
                host_id=seed["host_id"],
            )
            assert [item["dle_id"] for item in spill_inputs] == [str(event_id)]
            assert spill_inputs[0]["remote_path"] == remote["remote_path"]
        finally:
            db.close()
    finally:
        _cleanup(seed)
