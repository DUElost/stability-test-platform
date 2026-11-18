"""B8 #4043 基线探针：同一份探针在 ``e7325af0`` 上必须为红，在 HEAD 上必须为绿。

本文件**只使用主干既有的 API、既有 env 名与真实入口**（不 import 本单新增的模块），因此它能
原样在基线 checkout 上跑。它表达的是 #4043 的三条契约：

1. Owner 白名单命中的实际 PlanRun × MTK，在第一个受模式影响的动作**之前**就有已提交的
   ``DEDUP_FIRST`` 记录；
2. 分流必须读那次提交的结果：``upload_task`` 不得在模式尚未提交时按「无记录=现行」把
   先去重分区的事件标成 ``UPLOAD_PENDING``；
3. extract / merge 的分流（旧链前置条件与先去重的轮次合并）也必须发生在提交之后。

主干行为（探针因此在基线为红）：``shadow_scan_rounds`` 的两处写点无条件写 ``CURRENT``；
``scan_task`` / ``upload_task`` / 手动 merge / 手动 extract 独立进入处理链，会在任何模式记录
存在之前就完成分流。基线上单独把影子轮的默认值改成 ``DEDUP_FIRST`` 也救不了这些入口——
那只会把「先到的旧链副作用」掩盖成已分流。第 6 项是反向护栏（基线同样为绿，但不是判别项）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.core.database import SessionLocal
from backend.core.settings.scheduler import reset_scheduler_settings_cache
from backend.models.device_log_event import DeviceLogEvent
from backend.models.host import Device, Host
from backend.models.issue_fact import PartitionMode
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services import dedup_extract
from backend.services import issue_fact_store as facts
from backend.services import shadow_scan_rounds as shadow
from backend.tasks import saq_tasks

EVENT = "2026_1118_120000_123_db.74.ANR"


@pytest.fixture
def owner_whitelist(monkeypatch):
    """Owner 已批准该实际 run 并打开阶段二开关（基线上这两个键不存在，也不报错）。"""

    def _apply(*, enabled=True, whitelist=()):
        monkeypatch.setenv("STP_DEDUP_FIRST_ENABLED", "1" if enabled else "0")
        monkeypatch.setenv("STP_SCAN_ROUND_SHADOW_ENABLED", "1")
        monkeypatch.setenv(
            "STP_DEDUP_FIRST_PLAN_RUN_IDS", ",".join(str(rid) for rid in whitelist),
        )
        reset_scheduler_settings_cache()

    yield _apply
    reset_scheduler_settings_cache()


def _now():
    return datetime.now(timezone.utc)


def _digest():
    from backend.services.artifact_digest import (
        ARTIFACT_KIND_CODE,
        compute_desired_artifact_digest,
    )

    return compute_desired_artifact_digest(kind=ARTIFACT_KIND_CODE)


def _admitted_run(db, *, status="RUNNING", capable=True):
    """纯 MTK、中等规模、Agent 能力可证明的新 run —— 试点白名单的目标形态。"""
    plan = Plan(name=f"probe4043-{uuid4()}", created_by="pytest")
    db.add(plan)
    db.commit()
    run = PlanRun(
        plan_id=plan.id, status=status, plan_snapshot={}, run_type="MANUAL",
        triggered_by="pytest", started_at=_now(),
        ended_at=_now() if status != "RUNNING" else None,
    )
    db.add(run)
    db.commit()
    host_id = f"pr-{uuid4().hex[:8]}"
    octet = int(host_id.split("-")[1][:3], 16)
    address = f"203.0.113.{octet % 250}"
    now = _now()
    db.add(Host(
        id=host_id, hostname=host_id, name=host_id, ip=address, ip_address=address,
        status="ONLINE", last_heartbeat=now,
        agent_artifact_digest=_digest() if capable else "sha256:legacy",
        agent_artifact_digest_reported_at=(
            now if capable else now - timedelta(hours=1)
        ),
    ))
    db.commit()
    device = Device(
        serial=f"SN-{uuid4().hex[:8]}", host_id=host_id, platform="MTK",
        status="ONLINE", model="test", tags=[],
    )
    db.add(device)
    db.flush()
    db.add(JobInstance(
        plan_run_id=run.id, plan_id=run.plan_id, device_id=device.id, host_id=host_id,
        status="COMPLETED", pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    ))
    db.commit()
    return run, host_id, device.serial


def _unisoc_job_on_same_host(db, run, host_id):
    """在同一台 host 上再挂一台展锐设备：混合运行的现行分区必须与候选同机（裁决第 3 项反例面）。"""
    device = Device(
        serial=f"SN-U-{uuid4().hex[:8]}", host_id=host_id, platform="UNISOC",
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


def _mode_rows(db, run_id):
    return sorted(
        (row.platform, row.mode)
        for row in db.execute(
            select(PartitionMode.platform, PartitionMode.mode).where(
                PartitionMode.plan_run_id == run_id,
            )
        ).all()
    )


def _committed_mode_elsewhere(run_id):
    with SessionLocal() as other:
        row = facts.get_partition_mode(other, plan_run_id=run_id, platform="mtk")
        return row.mode if row is not None else None


def _shadow_env(monkeypatch, tmp_path):
    monkeypatch.setattr(shadow, "resolve_shared_storage_root", lambda: str(tmp_path))
    monkeypatch.setattr(shadow, "enqueue_sync", MagicMock())


def test_probe_shadow_entry_records_admitted_mode(db_session, owner_whitelist,
                                                  monkeypatch, tmp_path):
    """获批且能力已证实的新 run：影子轮登记前必须已写入 DEDUP_FIRST。"""
    run, _host_id, _serial = _admitted_run(db_session)
    owner_whitelist(whitelist=[run.id])
    _shadow_env(monkeypatch, tmp_path)

    shadow.sweep_shadow_scan_rounds()

    assert _mode_rows(db_session, run.id) == [("mtk", "DEDUP_FIRST")]


@pytest.mark.asyncio
async def test_probe_upload_marking_reads_committed_mode(db_session, owner_whitelist):
    """标记 UPLOAD_PENDING 之前，模式必须已提交（基线：无记录 → 旧链先分流）。"""
    run, host_id, serial = _admitted_run(db_session, status="SUCCESS")
    event = DeviceLogEvent(
        id=uuid4(), serial=serial, platform="MTK", event_type="ANR", state="LOCAL",
        local_path=f"/data/aee/{EVENT}", remote_path=None, host_id=host_id,
        plan_run_id=run.id, detected_at=_now(),
    )
    db_session.add(event)
    db_session.commit()
    owner_whitelist(whitelist=[run.id])
    seen = {}

    def _names(_db, run_id):
        seen["mode"] = _committed_mode_elsewhere(run_id)
        return [EVENT]

    with patch.object(dedup_extract, "collect_upload_event_dir_names", _names):
        await saq_tasks.upload_task({}, plan_run_id=run.id)

    assert seen["mode"] == "DEDUP_FIRST"
    db_session.expire_all()
    assert db_session.get(DeviceLogEvent, event.id).state == "LOCAL"


@pytest.mark.asyncio
async def test_probe_pure_dedup_partition_is_not_legacy_scanned(
    db_session, owner_whitelist, monkeypatch,
):
    """纯 MTK 获批分区不得再被旧 scan_now 扫（裁决第 3 项）。基线：照发并进轮询 → 红。

    基线侧必须也能在几秒内跑完，所以这里把轮询预算压到 0、并桩掉 SAQ 队列（基线会走到
    「enqueue upload/merge」那一步）。判据只有一个：旧 ``scan_now`` 的下发次数必须为 0。
    """
    run, host_id, _serial = _admitted_run(db_session)
    owner_whitelist(whitelist=[run.id])
    monkeypatch.setenv("STP_SCAN_POLL_MAX_WAIT", "0")
    dispatches = []

    async def _dispatch(host_id_arg, action, payload=None):
        dispatches.append((host_id_arg, action))
        return True

    queue = MagicMock()

    async def _enqueue(job):
        return job

    queue.enqueue = _enqueue
    monkeypatch.setattr(
        saq_tasks, "_query_hosts_for_scan",
        lambda *_a: ([(host_id, {"device_serials": ["SN"]})], [], [], {host_id: {"mtk"}}),
    )
    with patch("backend.realtime.socketio_server.call_agent_control", _dispatch), \
         patch("backend.core.task_queue.get_queue", return_value=queue):
        await saq_tasks.scan_task({}, plan_run_id=run.id, is_final=False)

    assert dispatches == []
    assert _mode_rows(db_session, run.id) == [("mtk", "DEDUP_FIRST")]


@pytest.mark.asyncio
async def test_probe_mixed_legacy_dispatch_happens_only_after_mode_committed(
    db_session, owner_whitelist, monkeypatch,
):
    """混合运行的旧 scan_now 必须照发（展锐现行），且发之前模式已提交（基线：None）。"""
    run, host_id, _serial = _admitted_run(db_session)
    _unisoc_job_on_same_host(db_session, run, host_id)
    owner_whitelist(whitelist=[run.id])
    seen = {}

    async def _dispatch(host_id_arg, action, payload=None):
        seen["mode"] = _committed_mode_elsewhere(run.id)
        seen["action"] = action
        raise RuntimeError("probe-stop-after-first-dispatch")

    monkeypatch.setattr(
        saq_tasks, "_query_hosts_for_scan",
        lambda *_a: ([(host_id, {"device_serials": ["SN"]})], [], [], {host_id: {"mtk"}}),
    )
    with patch("backend.realtime.socketio_server.call_agent_control", _dispatch):
        with pytest.raises(RuntimeError, match="probe-stop-after-first-dispatch"):
            await saq_tasks.scan_task({}, plan_run_id=run.id, is_final=False)

    assert seen["action"] == "scan_now"
    assert seen["mode"] == "DEDUP_FIRST"


def test_probe_manual_extract_refuses_dedup_partition(client, auth_headers, db_session,
                                                      owner_whitelist):
    """手动 extract 的分流前必须有提交好的模式；纯先去重分区返回 409。"""
    run, _host_id, _serial = _admitted_run(db_session, status="SUCCESS")
    owner_whitelist(whitelist=[run.id])

    resp = client.post(f"/api/v1/plan-runs/{run.id}/dedup/extract", headers=auth_headers)

    assert resp.status_code == 409
    assert "no CURRENT partition" in resp.json()["detail"]
    assert _mode_rows(db_session, run.id) == [("mtk", "DEDUP_FIRST")]


def test_probe_manual_merge_does_not_require_old_scan_artifacts(
    client, auth_headers, db_session, owner_whitelist,
):
    """手动 merge 对先去重分区不再要求旧链 scan 产物（基线：先按现行要求「run scan first」）。"""
    run, _host_id, _serial = _admitted_run(db_session, status="SUCCESS")
    owner_whitelist(whitelist=[run.id])

    resp = client.post(f"/api/v1/plan-runs/{run.id}/dedup/merge", headers=auth_headers)

    assert resp.status_code == 409
    assert "no closed FINAL/SUPPLEMENT round" in resp.json()["detail"]
    assert _mode_rows(db_session, run.id) == [("mtk", "DEDUP_FIRST")]


def test_probe_unverified_host_never_gets_dedup_first(db_session, owner_whitelist,
                                                     monkeypatch, tmp_path):
    """反向护栏（非判别项）：预期 MTK host 能力未证实时，任何入口都不得写 DEDUP_FIRST。"""
    run, _host_id, _serial = _admitted_run(db_session, capable=False)
    owner_whitelist(whitelist=[run.id])
    _shadow_env(monkeypatch, tmp_path)

    shadow.sweep_shadow_scan_rounds()

    assert "DEDUP_FIRST" not in [mode for _platform, mode in _mode_rows(db_session, run.id)]
