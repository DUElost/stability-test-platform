"""B8 G6 R1 返修：共享 DLE 查询必须按用途返回，旧上传入口在入队处拒绝。

第 1 轮复核 F1：list_agent_device_log_events 对所有 state 无条件过滤
DEDUP_FIRST 且只放行 SpillLedger 行，导致
- HddSpill 看不到 DEDUP_FIRST 的 LOCAL 候选（D2 第 3 条 / I2）；
- 首次溢出失败（尚无台账）的 UPLOAD_FAILED/UPLOADING 在重启后不可见（I4）；
- scan_scope 的 state="" 查询丢失本 host/run 输入。
本文件同时覆盖完整链：候选查询 → force 入队 → 首传失败 → 重启恢复 →
校验回执 → 补扫读回。

第 2 轮复核：恢复循环共用同一有界窗口，被入队守卫拒绝的事件不转态、会永久
占满窗口。这里覆盖「200 条不可入队填充 + 第 201 条仍有预算的溢出事件」在有
台账（purpose=recovery 窗口）与无台账（按事件 ID 恢复持久意图）两种情形下都
能实际入队，且普通事件始终不入队、已持久 attempts 不被清零。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.core.database import AsyncSessionLocal, SessionLocal
from backend.models.device_log_event import DeviceLogEvent
from backend.models.host import Host
from backend.models.issue_fact import PartitionMode, SpillLedger
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
        db.query(SpillLedger).filter(
            SpillLedger.plan_run_id == seed["run_id"],
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


def _dle_at(seed, *, detected_at, state="UPLOAD_FAILED", platform="MTK", suffix=""):
    event_id = uuid4()
    local = f"/data/aee/{FOLDER}/{seed['serial']}/aee_exp/{EVENT}{suffix}"
    db = SessionLocal()
    try:
        db.add(DeviceLogEvent(
            id=event_id, serial=seed["serial"], platform=platform,
            event_type="JE", state=state, local_path=local,
            host_id=seed["host_id"], plan_run_id=seed["run_id"],
            detected_at=detected_at,
        ))
        db.commit()
    finally:
        db.close()
    return event_id, local


def _add_ledger(seed, dle_id):
    db = SessionLocal()
    try:
        db.add(SpillLedger(
            plan_run_id=seed["run_id"], platform="mtk", dle_id=dle_id,
            size_bytes=1, spilled_at=datetime.now(timezone.utc),
        ))
        db.commit()
    finally:
        db.close()


def _seed_fillers(seed, count, *, start_at, platform="MTK", suffix_prefix="f"):
    rows = []
    db = SessionLocal()
    try:
        for index in range(count):
            event_id = uuid4()
            local = (
                f"/data/aee/{FOLDER}/{seed['serial']}/aee_exp/"
                f"{EVENT}-{suffix_prefix}{index}"
            )
            db.add(DeviceLogEvent(
                id=event_id, serial=seed["serial"], platform=platform,
                event_type="JE", state="UPLOAD_FAILED", local_path=local,
                host_id=seed["host_id"], plan_run_id=seed["run_id"],
                detected_at=start_at + timedelta(seconds=index),
            ))
            rows.append((event_id, local))
        db.commit()
    finally:
        db.close()
    return rows


@pytest.mark.asyncio(loop_scope="module")
async def test_recovery_purpose_frees_window_for_later_spill_event():
    """第 2 轮复核 F1：200 条不可入队事件不得把第 201 条溢出事件挤出窗口。"""
    seed = _seed()
    try:
        start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        fillers = _seed_fillers(seed, 200, start_at=start)
        spill_id, _ = _dle_at(
            seed, detected_at=start + timedelta(seconds=300), suffix="-spill",
        )
        _add_ledger(seed, spill_id)

        # 默认用途（HddSpill 候选 / scan_scope / 旧行为）：如实返回，窗口被填充占满
        default_rows = await _list(
            seed, state="UPLOAD_FAILED,UPLOADING", limit=200,
        )
        default_ids = {row["id"] for row in default_rows["events"]}
        assert len(default_ids) == 200
        assert str(spill_id) not in default_ids

        # 恢复用途：窗口截取前排除不可走旧链的事件，第 201 条可见
        recovery_rows = await _list(
            seed, state="UPLOAD_FAILED,UPLOADING", limit=200, purpose="recovery",
        )
        recovery_ids = {row["id"] for row in recovery_rows["events"]}
        assert recovery_ids == {str(spill_id)}
        assert recovery_rows["events"][0]["hdd_spill"] is True
        assert all(str(filler_id) not in recovery_ids for filler_id, _ in fillers)
    finally:
        _cleanup(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_recovery_purpose_keeps_current_and_no_mode_events():
    """CURRENT / 无模式分区本来就走旧链，恢复窗不得把它们排除掉。"""
    seed = _seed(mode="CURRENT")
    try:
        start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        current_id, _ = _dle_at(seed, detected_at=start, platform="MTK")
        unisoc_id, _ = _dle_at(
            seed, detected_at=start + timedelta(seconds=1), platform="UNISOC",
        )
        rows = await _list(
            seed, state="UPLOAD_FAILED,UPLOADING", purpose="recovery",
        )
        ids = {row["id"] for row in rows["events"]}
        assert ids == {str(current_id), str(unisoc_id)}
    finally:
        _cleanup(seed)

    seed = _seed(mode=None)
    try:
        no_mode_id, _ = _dle_at(
            seed, detected_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
        rows = await _list(
            seed, state="UPLOAD_FAILED,UPLOADING", purpose="recovery",
        )
        assert {row["id"] for row in rows["events"]} == {str(no_mode_id)}
    finally:
        _cleanup(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_ids_query_returns_no_ledger_spill_and_is_bounded():
    """首次溢出失败（尚无台账）只能按事件 ID 取回；ids 有界且格式校验。"""
    seed = _seed()
    try:
        start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        spill_id, spill_local = _dle_at(seed, detected_at=start, suffix="-no-ledger")
        filler_ids = [
            _dle_at(
                seed, detected_at=start + timedelta(seconds=index + 1),
                suffix=f"-f{index}",
            )[0]
            for index in range(3)
        ]

        rows = await _list(
            seed, state="UPLOAD_FAILED,UPLOADING",
            ids=str(spill_id),
        )
        assert [row["id"] for row in rows["events"]] == [str(spill_id)]
        assert rows["events"][0]["hdd_spill"] is False
        assert rows["events"][0]["local_path"] == spill_local

        # 恢复用途会排除它（没有台账），证明 by-ID 路径不可替代
        recovery = await _list(
            seed, state="UPLOAD_FAILED,UPLOADING", purpose="recovery",
        )
        assert str(spill_id) not in {row["id"] for row in recovery["events"]}

        too_many = ",".join(str(uuid4()) for _ in range(201))
        from backend.services.errors import BadRequest

        with pytest.raises(BadRequest):
            await _list(seed, ids=too_many)
        with pytest.raises(BadRequest):
            await _list(seed, ids="not-a-uuid")
        with pytest.raises(BadRequest):
            await _list(seed, purpose="unknown")
        assert filler_ids
    finally:
        _cleanup(seed)


class _ListResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return {"data": self._payload}


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("with_ledger", [True, False])
async def test_real_retry_loop_recovers_spill_behind_200_unqueueable_events(
    tmp_path, monkeypatch, with_ledger,
):
    """真实 _retry_failed_loop：200 条不可入队填充 + 1 条仍有预算的溢出事件。

    有台账：窗口查询（purpose=recovery）直接取回。
    无台账：首次溢出失败，只能靠 Agent 持久化的溢出意图按事件 ID 取回。
    两种情形都必须实际入队，普通填充永不入队，已持久 attempts 不被清零。
    """
    import asyncio

    from backend.agent import event_uploader as event_uploader_module
    from backend.agent.event_uploader import EventUploader

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
    monkeypatch.setenv("STP_DEVICE_LOG_EVENT_ENABLED", "1")
    monkeypatch.delenv("STP_EVENT_UPLOADER_PRUNE_LOCAL", raising=False)

    seed = _seed()
    try:
        start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        fillers = _seed_fillers(seed, 200, start_at=start)
        spill_id, spill_local = _dle_at(
            seed, detected_at=start + timedelta(seconds=300), suffix="-spill",
        )
        if with_ledger:
            _add_ledger(seed, spill_id)
        else:
            # 首次溢出失败：回执尚未成功，只有 Agent 持久化的溢出意图
            assert event_uploader_module._durable_spill_flag(str(spill_id), True)
        event_uploader_module._durable_set_attempts(str(spill_id), 4)

        loop = asyncio.get_running_loop()

        async def _query(params):
            async with AsyncSessionLocal() as db:
                return await list_agent_device_log_events(
                    db,
                    host_id=params["host_id"],
                    state=params.get("state"),
                    limit=int(params["limit"]) if params.get("limit") else None,
                    ids=params.get("ids"),
                    purpose=params.get("purpose"),
                )

        def fake_get(url, params=None, headers=None, timeout=None):
            payload = asyncio.run_coroutine_threadsafe(
                _query(params), loop,
            ).result(timeout=30)
            return _ListResponse(payload)

        up = EventUploader()
        up.configure(
            api_url="http://cp", agent_secret="s", host_id=seed["host_id"],
            nfs_root=str(tmp_path / "nfs"),
        )
        up._stop_evt.clear()
        calls = {"n": 0}

        def stop_after_two(*_args, **_kwargs):
            calls["n"] += 1
            return calls["n"] > 2

        monkeypatch.setattr(event_uploader_module.requests, "get", fake_get)
        monkeypatch.setattr(up._stop_evt, "wait", stop_after_two)
        await asyncio.to_thread(up._retry_failed_loop)

        jobs = []
        while not up._queue.empty():
            jobs.append(up._queue.get_nowait())

        assert [job.event_id for job in jobs] == [str(spill_id)]
        assert jobs[0].hdd_spill is True
        assert jobs[0].attempt == 4
        assert jobs[0].local_path == spill_local
        assert all(str(filler_id) != jobs[0].event_id for filler_id, _ in fillers)
        # 预算未被清零，后续轮次仍从 4 起算
        assert up._load_attempts(str(spill_id)) == 4
    finally:
        _cleanup(seed)
