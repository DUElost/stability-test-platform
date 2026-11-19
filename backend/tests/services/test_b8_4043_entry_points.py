"""B8 #4043：真实入口的模式提交必须先于第一次分流（ADR-0062 D3 第 1 条 / D13）。

本文件不用「直接调准入函数」来证明任何东西，而是沿 #4043 指定的入口链跑真实代码：

- 手动 extract / merge 路由（真实 HTTP）与路由入队的 SAQ 作业（真实 ``scan_task``）；
- 真实 ``upload_task``（标记 UPLOAD_PENDING 之前）；
- 自动影子轮 ``sweep_shadow_scan_rounds``（登记轮次之前）。

「已**提交**」的判据是跨连接可见：探针用另一个数据库会话读 ``partition_mode``。同一个事务里
「先写后分流」在只读本连接的测试里看不出来，而真正要防的正是模式尚未提交时旧链已经按
「无记录=现行」分流（#4043 基线注记：单独改影子轮默认值会漏掉先到的分流动作）。这些探针在
主干 ``e7325af0`` 上必须为红，见 PR 的基线/HEAD 对拍。
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from backend.agent.contracts.capabilities import SCAN_ROUND_V1
from backend.core.database import SessionLocal
from backend.core.settings.scheduler import reset_scheduler_settings_cache
from backend.models.device_log_event import DeviceLogEvent
from backend.models.host import Device, Host
from backend.models.issue_fact import PartitionMode
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services import dedup_extract
from backend.services import issue_fact_store as facts
from backend.services import partition_mode_admission as admission
from backend.services import partition_scope
from backend.services import shadow_scan_rounds as shadow
from backend.tasks import saq_tasks

EVENT = "2026_1118_120000_123_db.74.ANR"


@pytest.fixture
def phase_two(monkeypatch):
    """按真实配置路径接进阶段二开关与 Owner 白名单（env → Settings → 准入）。"""

    def _apply(*, enabled=True, shadow_enabled=True, whitelist=()):
        monkeypatch.setenv("STP_DEDUP_FIRST_ENABLED", "1" if enabled else "0")
        monkeypatch.setenv("STP_SCAN_ROUND_SHADOW_ENABLED", "1" if shadow_enabled else "0")
        monkeypatch.setenv(
            "STP_DEDUP_FIRST_PLAN_RUN_IDS", ",".join(str(rid) for rid in whitelist),
        )
        reset_scheduler_settings_cache()

    _apply(enabled=False, whitelist=())
    yield _apply
    reset_scheduler_settings_cache()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run(db, *, status="RUNNING"):
    plan = Plan(name=f"ep4043-{uuid4()}", created_by="pytest")
    db.add(plan)
    db.commit()
    run = PlanRun(
        plan_id=plan.id, status=status, plan_snapshot={}, run_type="MANUAL",
        triggered_by="pytest", started_at=_now(),
        ended_at=_now() if status != "RUNNING" else None,
    )
    db.add(run)
    db.commit()
    return run


def _host(db, *, status="ONLINE", reported_at="__default__", capable=True):
    host_id = f"ep-{uuid4().hex[:8]}"
    now = _now()
    octet = int(host_id.split("-")[1][:3], 16)
    address = f"198.51.{octet % 250}.{(octet // 10) % 250}"
    if reported_at == "__default__":
        report_at = now if capable else None
    else:
        report_at = reported_at
    db.add(Host(
        id=host_id, hostname=host_id, name=host_id, ip=address, ip_address=address,
        status=status, last_heartbeat=now,
        agent_capabilities=[SCAN_ROUND_V1] if capable else None,
        agent_capabilities_reported_at=report_at if capable else None,
        agent_capabilities_instance_id="agent-a" if capable else None,
    ))
    db.commit()
    return host_id


def _device_job(db, run, host_id, *, platform="MTK"):
    device = Device(
        serial=f"SN-{uuid4().hex[:8]}", host_id=host_id, platform=platform,
        status="ONLINE", model="test", tags=[],
    )
    db.add(device)
    db.flush()
    db.add(JobInstance(
        plan_run_id=run.id, plan_id=run.plan_id, device_id=device.id, host_id=host_id,
        status="COMPLETED", pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    ))
    db.commit()
    return device


def _dle(db, run_id, host_id, *, platform, serial):
    row = DeviceLogEvent(
        id=uuid4(), serial=serial, platform=platform, event_type="ANR", state="LOCAL",
        local_path=f"/data/aee/{EVENT}", remote_path=None, host_id=host_id,
        plan_run_id=run_id, detected_at=_now(),
    )
    db.add(row)
    db.commit()
    return row


def _committed_mode_elsewhere(run_id, platform="mtk"):
    """另一个连接里能读到的模式；读不到说明决定还没提交。"""
    db = SessionLocal()
    try:
        row = facts.get_partition_mode(db, plan_run_id=run_id, platform=platform)
        return row.mode if row is not None else None
    finally:
        db.close()


def _mode_rows(db, run_id):
    return sorted(
        (row.platform, row.mode)
        for row in db.execute(
            select(PartitionMode.platform, PartitionMode.mode).where(
                PartitionMode.plan_run_id == run_id,
            )
        ).all()
    )


def _enqueue_from_manual_scan_route(client, auth_headers, db_session, run_id):
    """跑真实手动 scan 路由，返回它入队的 SAQ 作业（路由本身只入队，不固化模式）。"""
    captured = []
    queue = MagicMock()

    async def _enqueue(job):
        captured.append(job)
        return job

    queue.enqueue = _enqueue
    with patch("backend.core.task_queue.get_queue", return_value=queue):
        resp = client.post(
            f"/api/v1/plan-runs/{run_id}/dedup/scan", headers=auth_headers,
        )
    assert resp.status_code == 200, resp.text
    assert captured and captured[0].function == "scan_task"
    assert _mode_rows(db_session, run_id) == []
    return captured[0]


@pytest.mark.asyncio
async def test_pure_dedup_partition_never_gets_legacy_scan_dispatch(
    client, auth_headers, db_session, phase_two,
):
    """纯 MTK 且已 DEDUP_FIRST：旧 scan_now 一律不下发，扫描只由已登记的事实轮次承担。"""
    run = _run(db_session, status="RUNNING")
    _device_job(db_session, run, _host(db_session))
    phase_two(whitelist=[run.id])
    job = _enqueue_from_manual_scan_route(client, auth_headers, db_session, run.id)

    dispatch = MagicMock()
    hosts = MagicMock(side_effect=AssertionError("_query_hosts_for_scan 不该被调用"))
    with patch("backend.realtime.socketio_server.call_agent_control", dispatch), \
         patch.object(saq_tasks, "_query_hosts_for_scan", hosts):
        await saq_tasks.scan_task({}, **job.kwargs)

    dispatch.assert_not_called()
    hosts.assert_not_called()
    assert _mode_rows(db_session, run.id) == [("mtk", "DEDUP_FIRST")]


@pytest.mark.asyncio
async def test_mixed_run_legacy_dispatch_reads_committed_mode_before_emit(
    client, auth_headers, db_session, phase_two,
):
    """混合运行：展锐现行扫描照发，但下发前模式必须已提交（跨连接可见）。"""
    run = _run(db_session, status="RUNNING")
    host_id = _host(db_session)
    _device_job(db_session, run, host_id, platform="MTK")
    unisoc_serial = _device_job(db_session, run, host_id, platform="UNISOC").serial
    phase_two(whitelist=[run.id])
    job = _enqueue_from_manual_scan_route(client, auth_headers, db_session, run.id)

    seen = {}

    async def _dispatch_probe(host_id_arg, action, payload=None):
        seen["action"] = action
        seen["mode_at_dispatch"] = _committed_mode_elsewhere(run.id)
        seen["payload_serials"] = payload.get("device_serials") if payload else None
        raise RuntimeError("probe-stops-before-poll")

    def _hosts(plan_run_id, is_final=False, allow_retired=False):
        return (
            [(host_id, {
                "plan_run_id": plan_run_id, "is_final": is_final,
                "device_serials": [unisoc_serial], "run_date_stamps": [],
            })],
            [], [], {host_id: {"mtk", "unisoc"}},
        )

    with patch("backend.realtime.socketio_server.call_agent_control", _dispatch_probe), \
         patch.object(saq_tasks, "_query_hosts_for_scan", _hosts):
        with pytest.raises(RuntimeError, match="probe-stops-before-poll"):
            await saq_tasks.scan_task({}, **job.kwargs)

    assert seen["action"] == "scan_now"
    assert seen["mode_at_dispatch"] == "DEDUP_FIRST"
    assert _mode_rows(db_session, run.id) == [("mtk", "DEDUP_FIRST")]
    # 已固化分区的混合下发不需要候选留痕（模式不可再翻转）。
    with SessionLocal() as db:
        context = db.get(PlanRun, run.id).run_context or {}
    assert admission.LEGACY_SCAN_DISPATCH_SECTION not in context


@pytest.mark.asyncio
async def test_upload_task_commits_mode_before_marking(db_session, phase_two):
    """``upload_task``：标记 UPLOAD_PENDING 就是旧链上传的分流点，模式须在其之前提交。"""
    run = _run(db_session, status="SUCCESS")
    host_id = _host(db_session)
    mtk_serial = _device_job(db_session, run, host_id, platform="MTK").serial
    unisoc_serial = _device_job(db_session, run, host_id, platform="UNISOC").serial
    mtk = _dle(db_session, run.id, host_id, platform="MTK", serial=mtk_serial)
    unisoc = _dle(db_session, run.id, host_id, platform="UNISOC", serial=unisoc_serial)
    phase_two(whitelist=[run.id])

    seen = {}

    def _names(_db, run_id):
        seen["mode_at_marking"] = _committed_mode_elsewhere(run_id)
        return [EVENT]

    with patch.object(dedup_extract, "collect_upload_event_dir_names", _names):
        await saq_tasks.upload_task({}, plan_run_id=run.id)

    assert seen["mode_at_marking"] == "DEDUP_FIRST"
    db_session.expire_all()
    assert db_session.get(DeviceLogEvent, mtk.id).state == "LOCAL"
    assert db_session.get(DeviceLogEvent, unisoc.id).state == "UPLOAD_PENDING"


@pytest.mark.asyncio
async def test_in_flight_old_run_stays_on_current_chain(db_session, phase_two):
    """在途旧 run：已有旧链痕迹 ⇒ 固化 CURRENT，标记照常，绝不两套链混跑。"""
    run = _run(db_session, status="SUCCESS")
    host_id = _host(db_session)
    serial = _device_job(db_session, run, host_id).serial
    db_session.add(PlanRunArtifact(
        plan_run_id=run.id, artifact_type="merge_result_xls", host_id=host_id,
        storage_uri=f"/mnt/stp-log/dedup/{run.id}/mtk/Result_Merge.xls",
    ))
    db_session.commit()
    mtk = _dle(db_session, run.id, host_id, platform="MTK", serial=serial)
    phase_two(whitelist=[run.id])

    with patch.object(dedup_extract, "collect_upload_event_dir_names", lambda *_: [EVENT]):
        await saq_tasks.upload_task({}, plan_run_id=run.id)

    assert _mode_rows(db_session, run.id) == [("mtk", "CURRENT")]
    db_session.expire_all()
    assert db_session.get(DeviceLogEvent, mtk.id).state == "UPLOAD_PENDING"


def test_shadow_sweep_uses_admission_instead_of_blanket_current(
    db_session, phase_two, tmp_path, monkeypatch,
):
    """影子轮：获批且能力证实的 run 在登记轮次前写入 DEDUP_FIRST（主干只会写 CURRENT）。"""
    run = _run(db_session, status="RUNNING")
    host_id = _host(db_session)
    _device_job(db_session, run, host_id)
    phase_two(whitelist=[run.id])

    monkeypatch.setattr(shadow, "resolve_shared_storage_root", lambda: str(tmp_path))
    queued = MagicMock()
    monkeypatch.setattr(shadow, "enqueue_sync", queued)

    shadow.sweep_shadow_scan_rounds()

    assert _mode_rows(db_session, run.id) == [("mtk", "DEDUP_FIRST")]
    # 测试侧读轮次一律用无锁版本：list_scan_rounds 带 FOR UPDATE，夹具会话读它会把行锁留在
    # idle-in-transaction 事务里，被测代码在另一连接锁同一行就会永久等待。
    rounds = facts.list_scan_rounds_for_read(db_session, plan_run_id=run.id, platform="mtk")
    db_session.commit()
    assert [row.kind for row in rounds] == [facts.SCAN_ROUND_KIND_PERIODIC]
    assert queued.call_args.kwargs["fact_round_id"] == rounds[0].id


def test_shadow_sweep_without_admission_still_records_current(
    db_session, phase_two, tmp_path, monkeypatch,
):
    """未获批分区仍按主干既有记账落 CURRENT（#3875 settled 出口需要这一行）。"""
    run = _run(db_session, status="RUNNING")
    _device_job(db_session, run, _host(db_session))
    phase_two(whitelist=[])

    monkeypatch.setattr(shadow, "resolve_shared_storage_root", lambda: str(tmp_path))
    monkeypatch.setattr(shadow, "enqueue_sync", MagicMock())

    shadow.sweep_shadow_scan_rounds()

    assert _mode_rows(db_session, run.id) == [("mtk", "CURRENT")]


def test_manual_extract_route_fixates_mode_before_its_partition_split(
    client, auth_headers, db_session, phase_two,
):
    """手动 extract：分流（只提取现行分区）之前模式已提交；纯先去重分区返回 409。"""
    run = _run(db_session, status="SUCCESS")
    _device_job(db_session, run, _host(db_session))
    phase_two(whitelist=[run.id])

    resp = client.post(
        f"/api/v1/plan-runs/{run.id}/dedup/extract", headers=auth_headers,
    )

    assert resp.status_code == 409, resp.text
    assert "no CURRENT partition" in resp.json()["detail"]
    assert _mode_rows(db_session, run.id) == [("mtk", "DEDUP_FIRST")]


def test_manual_merge_route_fixates_mode_and_skips_old_merge(
    client, auth_headers, db_session, phase_two,
):
    """手动 merge：先去重分区不再被旧链的「先跑 scan」前置拦住，改按轮次合并。"""
    run = _run(db_session, status="SUCCESS")
    _device_job(db_session, run, _host(db_session))
    phase_two(whitelist=[run.id])

    resp = client.post(
        f"/api/v1/plan-runs/{run.id}/dedup/merge", headers=auth_headers,
    )

    assert resp.status_code == 409, resp.text
    assert "no closed FINAL/SUPPLEMENT round" in resp.json()["detail"]
    assert _mode_rows(db_session, run.id) == [("mtk", "DEDUP_FIRST")]


@pytest.mark.asyncio
async def test_switch_off_blocks_new_entry_but_keeps_fixed_partition_consumers(
    db_session, phase_two,
):
    """D13 第 5 条：关开关只影响后续入场；已固定分区继续按 DEDUP_FIRST 处理。"""
    admitted = _run(db_session, status="SUCCESS")
    host_id = _host(db_session)
    serial = _device_job(db_session, admitted, host_id).serial
    kept = _dle(db_session, admitted.id, host_id, platform="MTK", serial=serial)
    phase_two(whitelist=[admitted.id])
    assert admission.fixate_partition_mode_at_entry(
        admitted.id, entrypoint=admission.ENTRY_SCAN_TASK,
    ) == "DEDUP_FIRST"

    phase_two(enabled=False)
    with patch.object(dedup_extract, "collect_upload_event_dir_names", lambda *_: [EVENT]):
        await saq_tasks.upload_task({}, plan_run_id=admitted.id)
    db_session.expire_all()
    assert db_session.get(DeviceLogEvent, kept.id).state == "LOCAL"
    assert partition_scope.dedup_first_platforms(db_session, admitted.id) == ["mtk"]

    later = _run(db_session, status="SUCCESS")
    _device_job(db_session, later, _host(db_session))
    phase_two(enabled=False, whitelist=[later.id])
    assert admission.fixate_partition_mode_at_entry(
        later.id, entrypoint=admission.ENTRY_SCAN_TASK,
    ) is None
    assert _mode_rows(db_session, later.id) == []
    decision = admission.decide_partition_mode(db_session, later.id, platform="mtk")
    assert decision.reason == admission.REASON_SWITCH_DISABLED


@pytest.mark.asyncio
async def test_unverified_host_at_task_retry_blocks_entry_until_evidence_returns(
    db_session, phase_two,
):
    """重试期间能力仍未证实：每次入场都不写 DEDUP_FIRST；证据恢复后由下一次入场固化。"""
    run = _run(db_session, status="RUNNING")
    host_id = _host(db_session, reported_at=None)
    _device_job(db_session, run, host_id)
    phase_two(whitelist=[run.id])

    with SessionLocal() as db:
        db.get(Host, host_id).status = "OFFLINE"
        db.commit()

    for _attempt in range(3):
        assert admission.fixate_partition_mode_at_entry(
            run.id, entrypoint=admission.ENTRY_SCAN_TASK,
        ) is None
        assert _mode_rows(db_session, run.id) == []

    with SessionLocal() as db:
        host = db.get(Host, host_id)
        host.status = "ONLINE"
        recovered_at = _now()
        host.last_heartbeat = recovered_at
        host.agent_capabilities = [SCAN_ROUND_V1]
        host.agent_capabilities_reported_at = recovered_at
        host.agent_capabilities_instance_id = "agent-a"
        db.commit()

    assert admission.fixate_partition_mode_at_entry(
        run.id, entrypoint=admission.ENTRY_SCAN_TASK,
    ) == "DEDUP_FIRST"
    with SessionLocal() as db:
        rows = db.execute(
            select(func.count()).select_from(PartitionMode).where(
                PartitionMode.plan_run_id == run.id,
            )
        ).scalar_one()
    assert rows == 1
