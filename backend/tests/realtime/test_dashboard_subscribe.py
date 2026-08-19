"""ADR-0029 v2.3 D — DashboardNamespace.on_subscribe 收窄（room 校验）。

覆盖三层：
- 格式白名单（_ROOM_PATTERN）：job:/run:/plan_run:/console: 的合法形态与
  非法形态（任意字符串、agent:、非数字 id、超长 id、错误 console 前缀）。
- 实体存在性（_dashboard_room_exists）：job:/run: → job_instance 行、
  plan_run: → plan_run 行（DB）；console: → RunConsole 进程内 run。
- on_subscribe 行为：格式非法 / 实体不存在 → 不 enter_room；合法 → enter_room。

边界（G13 定性）：本层只校验「房间合法 + 实体存在」，不做归属过滤——REST 面
本就允许任意登录用户读任意 run，实时通道不设更严门槛（非越权安全洞）。
"""
from __future__ import annotations

import pytest

from backend.models.host import Device
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.realtime.socketio_server import (
    DashboardNamespace,
    _ROOM_PATTERN,
    _dashboard_room_exists,
)
from backend.services.run_console import ConsoleRun, RunConsole

# ── 格式白名单 ────────────────────────────────────────────────────────────────

VALID_ROOMS = [
    "job:1",
    "job:123456789012345678",  # 18 位上限
    "run:42",
    "plan_run:7",
    "console:con-abcdef012345",
    "console:con-0123456789abcdef0123456789abcdef",  # 32 hex 上限
]

INVALID_ROOMS = [
    "",
    "garbage",
    "job:",
    "job:abc",
    "job:1.5",
    "job:-1",
    "job: 1",
    "job:1234567890123456789",  # 19 位超上限
    "job:123:456",
    "run:0x10",
    "plan_run:abc",
    "console:foo",
    "console:con-",
    "console:con-xyz",  # 非 hex
    "console:con-" + "0" * 33,  # 33 位超上限
    "agent:5",  # agent: 是 /agent namespace 内部房间，dashboard 订阅无意义
    "job:1\n",  # 尾随空白
]


@pytest.mark.parametrize("room", VALID_ROOMS)
def test_room_pattern_accepts(room):
    assert _ROOM_PATTERN.fullmatch(room) is not None


@pytest.mark.parametrize("room", INVALID_ROOMS)
def test_room_pattern_rejects(room):
    assert _ROOM_PATTERN.fullmatch(room) is None


# ── 实体存在性（console 分支，进程内存态，无 DB）────────────────────────────


@pytest.mark.asyncio
async def test_console_room_exists_process_memory():
    inst = RunConsole.instance()
    key = "con-abcdef012345"
    inst._runs[key] = ConsoleRun(run_id=key, run_key="test", label="test")
    try:
        assert await _dashboard_room_exists("console", key)
        assert not await _dashboard_room_exists("console", "con-000000000000")
    finally:
        inst._runs.pop(key, None)


# ── 实体存在性（DB 分支：job:/run: → job_instance，plan_run: → plan_run）──────


@pytest.mark.asyncio
async def test_db_room_exists(db_session):
    device = Device(serial="sub-exist-serial")
    plan = Plan(name="sub-exist-plan")
    db_session.add_all([device, plan])
    db_session.commit()
    run = PlanRun(plan_id=plan.id, plan_snapshot={}, run_type="MANUAL")
    db_session.add(run)
    db_session.commit()
    job = JobInstance(
        plan_run_id=run.id, plan_id=plan.id, device_id=device.id, pipeline_def={},
    )
    db_session.add(job)
    db_session.commit()

    assert await _dashboard_room_exists("job", str(job.id))
    assert await _dashboard_room_exists("run", str(job.id))  # run: 与 job: 同实体
    assert await _dashboard_room_exists("plan_run", str(run.id))
    assert not await _dashboard_room_exists("job", "999999999")
    assert not await _dashboard_room_exists("plan_run", "999999999")


# ── on_subscribe 行为 ─────────────────────────────────────────────────────────


class _FakeSioServer:
    """仅记录 enter_room 调用的假 server（AsyncNamespace.enter_room 委托给它）。"""

    def __init__(self):
        self.entered: list[str] = []

    async def enter_room(self, sid, room, namespace=None):
        self.entered.append(room)

    async def leave_room(self, sid, room, namespace=None):
        pass


@pytest.mark.asyncio
async def test_subscribe_rejects_invalid_format():
    ns = DashboardNamespace("/dashboard")
    ns.server = _FakeSioServer()
    for room in ("garbage", "job:abc", "agent:5", "console:foo", ""):
        await ns.on_subscribe("sid-X", {"room": room})
    assert ns.server.entered == []


@pytest.mark.asyncio
async def test_subscribe_rejects_missing_entity():
    ns = DashboardNamespace("/dashboard")
    ns.server = _FakeSioServer()
    await ns.on_subscribe("sid-X", {"room": "job:999999999"})
    await ns.on_subscribe("sid-X", {"room": "plan_run:999999999"})
    assert ns.server.entered == []


@pytest.mark.asyncio
async def test_subscribe_rejects_unknown_console_run():
    ns = DashboardNamespace("/dashboard")
    ns.server = _FakeSioServer()
    await ns.on_subscribe("sid-X", {"room": "console:con-000000000000"})
    assert ns.server.entered == []


@pytest.mark.asyncio
async def test_subscribe_accepts_existing_entities(db_session):
    device = Device(serial="sub-accept-serial")
    plan = Plan(name="sub-accept-plan")
    db_session.add_all([device, plan])
    db_session.commit()
    run = PlanRun(plan_id=plan.id, plan_snapshot={}, run_type="MANUAL")
    db_session.add(run)
    db_session.commit()
    job = JobInstance(
        plan_run_id=run.id, plan_id=plan.id, device_id=device.id, pipeline_def={},
    )
    db_session.add(job)
    db_session.commit()

    ns = DashboardNamespace("/dashboard")
    ns.server = _FakeSioServer()
    await ns.on_subscribe("sid-Y", {"room": f"job:{job.id}"})
    await ns.on_subscribe("sid-Y", {"room": f"run:{job.id}"})
    await ns.on_subscribe("sid-Y", {"room": f"plan_run:{run.id}"})
    assert ns.server.entered == [f"job:{job.id}", f"run:{job.id}", f"plan_run:{run.id}"]


@pytest.mark.asyncio
async def test_subscribe_accepts_live_console_run():
    inst = RunConsole.instance()
    key = "con-fedcba987654"
    inst._runs[key] = ConsoleRun(run_id=key, run_key="t", label="t")
    try:
        ns = DashboardNamespace("/dashboard")
        ns.server = _FakeSioServer()
        await ns.on_subscribe("sid-Z", {"room": f"console:{key}"})
        await ns.on_subscribe("sid-Z", {"room": "console:con-000000000000"})
        assert ns.server.entered == [f"console:{key}"]
    finally:
        inst._runs.pop(key, None)
