"""B8 G6 第 3 轮返修探针：真实 GET 路由接线 + 溢出意图窗口饥饿。

两处缺口都在 14895494（反例基线）复现、返修后转绿；本文件的 HTTP 探针用
``httpx.ASGITransport(app=fastapi_app)`` 让请求真正穿过 FastAPI 的
``list_device_log_events`` 路由 → 服务层 → 隔离 PG16（testcontainers），
不再像 R2 那样用进程内桥接直接调用服务层（那会绕过路由接线）。

- 缺口 1：路由未声明/转发 ``purpose`` 与 ``ids``。请求返回 200 但两个参数
  都到不了服务层。用 spy 记录服务层收到的 kwargs + 行为断言（按用途过滤、
  按 ID 恢复）双重验收。
- 缺口 2：``_recover_spill_intents`` 每轮都读同一批前 200 个持久意图。
  临时 SQLite 放 201 个溢出意图（前 200 个耗尽预算、第 201 个还剩一次），
  真实恢复循环连续两轮，基线跨轮饥饿、返修后第 201 个入队且保留原次数。
  覆盖有台账 / 首次失败无台账两种情况；普通事件不入队、耗尽的不从零开始。
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from backend.api.routes import agent_api as route_mod
from backend.core.database import SessionLocal
from backend.main import fastapi_app
from backend.models.device_log_event import DeviceLogEvent
from backend.models.host import Host
from backend.models.issue_fact import PartitionMode, SpillLedger
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun

SECRET = "g6-r3-probe-secret"


def _seed_route_case() -> dict:
    """DEDUP_FIRST 分区 + 非溢出/溢出/无分区三种事件，验证 purpose/ids。"""
    host_id = f"g6r3-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(Host(
            id=host_id, hostname=host_id, name=host_id, ip="192.0.2.220",
            ip_address="192.0.2.220", status="ONLINE", last_heartbeat=now,
        ))
        plan = Plan(name=f"g6r3-{uuid4()}", created_by="pytest")
        db.add(plan)
        db.flush()
        run = PlanRun(
            plan_id=plan.id, status="RUNNING", plan_snapshot={},
            run_type="MANUAL", triggered_by="pytest",
        )
        db.add(run)
        db.flush()
        db.add(PartitionMode(plan_run_id=run.id, platform="mtk", mode="DEDUP_FIRST"))

        nonspill, spill, plain = uuid4(), uuid4(), uuid4()

        def add(eid: UUID, plan_run_id, delta: int) -> None:
            db.add(DeviceLogEvent(
                id=eid, serial="SN", platform="MTK", event_type="JE",
                state="UPLOAD_FAILED", local_path=f"/data/aee/{eid}",
                host_id=host_id, plan_run_id=plan_run_id,
                detected_at=now - timedelta(seconds=delta),
            ))

        # 同一 DEDUP_FIRST 分区：非溢出（应被 purpose=recovery 排除）、
        # 溢出（有台账，应保留）；无分区事件（dedup_first=False，应保留）。
        add(nonspill, run.id, 30)
        add(spill, run.id, 20)
        add(plain, None, 10)
        db.add(SpillLedger(
            plan_run_id=run.id, platform="mtk", dle_id=spill,
            size_bytes=1, spilled_at=now, created_at=now,
        ))
        db.commit()
        return {
            "host_id": host_id,
            "nonspill_id": str(nonspill),
            "spill_id": str(spill),
            "plain_id": str(plain),
        }
    finally:
        db.close()


@pytest.mark.asyncio(loop_scope="module")
async def test_http_get_forwards_purpose_and_ids_to_service(monkeypatch):
    """真实 GET 穿过 FastAPI 到 PG16：purpose / ids 必须到达服务层并生效。

    14895494 基线：路由不声明这两个参数 → 服务层 kwargs 无 purpose/ids，
    purpose 过滤不发生、ids 过滤不发生 → 本探针失败。
    """
    monkeypatch.setenv("AGENT_SECRET", SECRET)
    monkeypatch.setenv("TESTING", "1")

    real = route_mod.list_agent_device_log_events
    seen: list[dict] = []

    async def spy(db, **kwargs):
        seen.append(dict(kwargs))
        return await real(db, **kwargs)

    monkeypatch.setattr(route_mod, "list_agent_device_log_events", spy)

    seed = _seed_route_case()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url="http://test",
        headers={"X-Agent-Secret": SECRET},
    ) as client:
        r_purpose = await client.get(
            "/api/v1/agent/device-log-events",
            params={
                "host_id": seed["host_id"],
                "state": "UPLOAD_FAILED,UPLOADING",
                "limit": 200,
                "purpose": "recovery",
            },
        )
        r_ids = await client.get(
            "/api/v1/agent/device-log-events",
            params={
                "host_id": seed["host_id"],
                "limit": 200,
                "ids": seed["plain_id"],
            },
        )

    assert r_purpose.status_code == 200
    assert r_ids.status_code == 200
    assert len(seen) == 2, "两次 GET 都必须走到服务层"

    # 证据 A：两个参数逐字透传到服务层（spy 记录 kwargs）。
    assert seen[0].get("purpose") == "recovery", (
        f"purpose 未到达服务层: {seen[0]}"
    )
    assert seen[1].get("ids") == seed["plain_id"], (
        f"ids 未到达服务层: {seen[1]}"
    )

    # 证据 B：行为验收——按用途过滤（非溢出 DEDUP_FIRST 被排除前于 LIMIT）。
    purpose_ids = {e["id"] for e in r_purpose.json()["data"]["events"]}
    assert seed["nonspill_id"] not in purpose_ids, "purpose=recovery 未排除非溢出代表"
    assert seed["spill_id"] in purpose_ids, "有台账溢出事件应保留"
    assert seed["plain_id"] in purpose_ids, "无分区普通事件应保留"

    # 证据 C：按 ID 恢复——精确返回请求的事件（含 DEDUP_FIRST 首失败无台账）。
    ids_ids = {e["id"] for e in r_ids.json()["data"]["events"]}
    assert ids_ids == {seed["plain_id"]}, f"ids 未按 ID 精确恢复: {ids_ids}"


def _seed_intent_case(with_ledger: bool, skip_kind: str) -> dict:
    """201 个 DLE：前 200 个按 key/detected_at 在前，第 201 个在最后。

    UUID 用 ``UUID(int=n)`` 生成，零填充定宽 → 字符串字典序 == 数字序，
    保证 ``ORDER BY key`` 与 ``ORDER BY detected_at`` 的前 200 一致：
    基线把 ``ids`` 丢弃时，服务层按 detected_at 返回的仍是同一批 200 个
    （这样基线复现的是纯粹的「意图窗口饥饿」，不掺入路由漏接的其他效应）。

    ``skip_kind`` 控制前 200 个为何被跳过：
    - ``exhausted``：state=UPLOAD_FAILED + attempts=5（已耗尽预算）；
    - ``wrong_state``：state=LOCAL（不属于慢循环 UPLOAD_FAILED,UPLOADING 的
      wanted 集合，当前循环无法处理），attempts=2 未耗尽。
    两者都不得永久占窗；第 201 个（state=UPLOAD_FAILED, attempts=4）须入队。
    """
    host_id = f"g6r3w-{uuid4().hex[:8]}"
    base = datetime(2026, 11, 1, tzinfo=timezone.utc)
    db = SessionLocal()
    try:
        db.add(Host(
            id=host_id, hostname=host_id, name=host_id, ip="192.0.2.221",
            ip_address="192.0.2.221", status="ONLINE", last_heartbeat=base,
        ))
        plan = Plan(name=f"g6r3w-{uuid4()}", created_by="pytest")
        db.add(plan)
        db.flush()
        run = PlanRun(
            plan_id=plan.id, status="RUNNING", plan_snapshot={},
            run_type="MANUAL", triggered_by="pytest",
        )
        db.add(run)
        db.flush()
        db.add(PartitionMode(plan_run_id=run.id, platform="mtk", mode="DEDUP_FIRST"))

        intent_uuids = [UUID(int=n + 1) for n in range(201)]
        for index, eid in enumerate(intent_uuids):
            # 第 201 个始终是可入队的 UPLOAD_FAILED 溢出事件；前 200 个按
            # skip_kind 设置成「耗尽」或「当前循环无法处理」两种被跳过形态。
            if index == 200:
                state = "UPLOAD_FAILED"
            elif skip_kind == "wrong_state":
                state = "LOCAL"
            else:
                state = "UPLOAD_FAILED"
            db.add(DeviceLogEvent(
                id=eid, serial="SN", platform="MTK", event_type="JE",
                state=state, local_path=f"/data/aee/{eid}",
                host_id=host_id, plan_run_id=run.id,
                detected_at=base + timedelta(seconds=index),
            ))
            if with_ledger:
                db.add(SpillLedger(
                    plan_run_id=run.id, platform="mtk", dle_id=eid,
                    size_bytes=1, spilled_at=base, created_at=base,
                ))

        # 普通事件（无溢出意图）：无论哪种情况都不应入队，detected_at 更晚。
        ordinary = [uuid4() for _ in range(3)]
        for index, eid in enumerate(ordinary):
            db.add(DeviceLogEvent(
                id=eid, serial="SN", platform="MTK", event_type="JE",
                state="UPLOAD_FAILED", local_path=f"/data/aee/{eid}",
                host_id=host_id, plan_run_id=run.id,
                detected_at=base + timedelta(seconds=1000 + index),
            ))
        db.commit()
        return {
            "host_id": host_id,
            "intent_ids": [str(u) for u in intent_uuids],
            "ordinary_ids": [str(u) for u in ordinary],
        }
    finally:
        db.close()


def _write_state_db(path, intent_ids: list[str], skip_kind: str) -> None:
    """临时 SQLite：201 个持久溢出意图。

    第 201 个 attempts=4（还剩一次）。前 200 个：exhausted 形态 attempts=5
    （耗尽），wrong_state 形态 attempts=2（未耗尽，靠状态不匹配被跳过）。
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_state "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        for index, event_id in enumerate(intent_ids):
            conn.execute(
                "INSERT OR REPLACE INTO agent_state (key, value) VALUES (?, ?)",
                (f"event_upload_spill:{event_id}", "1"),
            )
            if index == 200:
                attempts = 4
            elif skip_kind == "wrong_state":
                attempts = 2
            else:
                attempts = 5
            conn.execute(
                "INSERT OR REPLACE INTO agent_state (key, value) VALUES (?, ?)",
                (f"event_upload_attempts:{event_id}", str(attempts)),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("skip_kind", ["exhausted", "wrong_state"])
@pytest.mark.parametrize("with_ledger", [True, False], ids=["with-ledger", "no-ledger"])
async def test_intent_window_201_starvation_via_real_http(
    monkeypatch, tmp_path, with_ledger, skip_kind,
):
    """真实恢复循环 + 真实 HTTP：200 条被跳过后第 201 条仍能入队。

    ``requests.get`` 桥接到 ASGITransport，让按 ID 恢复的查询真正穿过
    FastAPI 到 PG16。基线 14895494 两轮都读同一批 200 个意图、全部跳过，
    第 201 个从未被查询/入队；返修后 keyset 游标翻页，第 201 个入队且
    ``attempt=4``（保留原次数），普通事件不入队、耗尽的不从零开始。
    """
    monkeypatch.setenv("AGENT_SECRET", SECRET)
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("STP_DEVICE_LOG_EVENT_ENABLED", "1")

    state_db = tmp_path / "agent_state.db"
    seed = _seed_intent_case(with_ledger, skip_kind)
    _write_state_db(state_db, seed["intent_ids"], skip_kind)
    monkeypatch.setenv("STP_AGENT_STATE_DB", str(state_db))

    from backend.agent import event_uploader as eu

    up = eu.EventUploader()
    up.configure(
        api_url="http://test", agent_secret=SECRET,
        host_id=seed["host_id"], nfs_root=str(tmp_path / "nfs"),
    )
    up._stop_evt.clear()

    loop = asyncio.get_running_loop()

    class _Resp:
        def __init__(self, response: httpx.Response):
            self.status_code = response.status_code
            self._payload = response.json()

        def json(self):
            return self._payload

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url="http://test",
        headers={"X-Agent-Secret": SECRET},
    ) as client:
        queried_ids: list[str] = []

        def fake_get(url, params=None, headers=None, timeout=None):
            path = url.replace("http://test", "", 1)
            if params and params.get("ids"):
                queried_ids.append(str(params["ids"]))
            coro = client.get(
                path, params=params, headers={"X-Agent-Secret": SECRET},
            )
            return _Resp(asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=30))

        monkeypatch.setattr(eu.requests, "get", fake_get)

        # 连续两轮真实恢复循环（慢速状态集）。
        await asyncio.to_thread(up._recover_spill_intents, "UPLOAD_FAILED,UPLOADING")
        await asyncio.to_thread(up._recover_spill_intents, "UPLOAD_FAILED,UPLOADING")

    jobs = []
    while not up._queue.empty():
        jobs.append(up._queue.get_nowait())
    enqueued = [job.event_id for job in jobs]

    last_intent = seed["intent_ids"][200]
    # 核心验收：第 201 条被跳过的前 200 条之后仍能入队。
    assert last_intent in enqueued, (
        f"第 201 条未入队（跨轮饥饿）: queried_id_batches={len(queried_ids)}, "
        f"enqueued={enqueued[:5]}"
    )
    assert enqueued.count(last_intent) == 1, "两轮不得重复入队同一事件"
    job = next(j for j in jobs if j.event_id == last_intent)
    assert job.attempt == 4, f"原尝试次数未保留: {job.attempt}"

    # 已耗尽预算的 200 条全部跳过，且 attempts 不被清零。
    assert all(i not in enqueued for i in seed["intent_ids"][:200])
    # 无溢出意图的普通事件始终不入队。
    assert all(o not in enqueued for o in seed["ordinary_ids"])

    conn = sqlite3.connect(state_db)
    try:
        row = conn.execute(
            "SELECT value FROM agent_state WHERE key=?",
            (f"event_upload_attempts:{seed['intent_ids'][0]}",),
        ).fetchone()
    finally:
        conn.close()
    expected_first = "5" if skip_kind == "exhausted" else "2"
    assert row is not None and row[0] == expected_first, (
        "被跳过的意图不得被清零/重置 attempts"
    )
