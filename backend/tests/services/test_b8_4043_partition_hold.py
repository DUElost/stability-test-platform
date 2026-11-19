"""B8 #4043 返修：候选等待能力证据期间的零分流，与已固化 DEDUP_FIRST 的真实轮次下发。

对应规划裁决的三组证据：

1. **候选等待态不产生受模式影响的副作用**（裁决第 2 项）：旧 ``scan_task`` 不下发、
   ``upload_task`` 不标记 MTK、merge 不跑旧 merge、extract 不提取该分区、影子轮既不登记也不
   下发；同一运行的展锐现行分区照常处理。
2. **两种「库里没有模式记录」必须可区分**：``held=True``（Owner 点名、等能力证据，入口要停）
   与 ``held=False``（存量/未点名/开关关闭，按 D13 第 2 条读作现行，行为与改动前一致）。
3. **在途旧链工作即失格**（裁决第 1 项）：旧扫描一旦下发，即使报表还没登记，后续入场也只能
   固化 ``CURRENT``；并用受控交错证明「能力未证实→先发旧扫描→能力恢复→另一入口写
   ``DEDUP_FIRST``」这条路径不可达。
4. **已固化 DEDUP_FIRST 的 MTK 实际只经登记轮次扫描**（裁决第 3 项）：自动、手动与终态入口
   都不再下发旧 ``scan_now``，首次轮次下发前用独立会话读到唯一模式记录。
5. **同主机反例**：直接跑真实 Agent 代码路径，证明控制面过滤串号**不能**抑制 MTK 扫描——
   这条边界现有接口无法表达，随 PR 交 Owner 裁定。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.agent.contracts.capabilities import SCAN_ROUND_V1
from backend.core.database import SessionLocal
from backend.core.settings.scheduler import reset_scheduler_settings_cache
from backend.models.device_log_event import DeviceLogEvent
from backend.models.host import Device, Host
from backend.models.issue_fact import PartitionMode
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services import dedup_extract
from backend.services import dedup_scan
from backend.services import issue_fact_store as facts
from backend.services import partition_mode_admission as admission
from backend.services import shadow_scan_rounds as shadow
from backend.tasks import saq_tasks

EVENT = "2026_1118_120000_123_db.74.ANR"


@pytest.fixture
def phase_two(monkeypatch):
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


def _now():
    return datetime.now(timezone.utc)


def _run(db, *, status="RUNNING"):
    plan = Plan(name=f"hold4043-{uuid4()}", created_by="pytest")
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


def _host(db=None, *, capable=True):
    host_id = f"hd-{uuid4().hex[:8]}"
    now = _now()
    octet = int(host_id.split("-")[1][:3], 16)
    address = f"192.0.2.{octet % 250}"
    db.add(Host(
        id=host_id, hostname=host_id, name=host_id, ip=address, ip_address=address,
        status="ONLINE" if capable else "OFFLINE", last_heartbeat=now,
        agent_capabilities=[SCAN_ROUND_V1] if capable else None,
        agent_capabilities_reported_at=now if capable else None,
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


def _modes(db, run_id):
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


def _rounds(db, run_id):
    """测试侧读轮次必须走**无锁**版本。

    ``facts.list_scan_rounds`` 带 ``FOR UPDATE``（调度路径的既定语义）。测试夹具会话读它会把
    轮次行锁留在一个 idle-in-transaction 的事务里，随后被测代码在另一连接上锁同一行就永久等待
    —— 表现为「测试挂住」而不是断言失败，排查代价高（本次就被它拖住过一次）。
    """
    rows = facts.list_scan_rounds_for_read(db, plan_run_id=run_id, platform="mtk")
    db.commit()
    return rows


def _candidate(db_session, phase_two, *, mixed_host=False):
    """Owner 已点名、但预期 MTK host 能力未证实的候选分区。"""
    run = _run(db_session)
    host_id = _host(db_session, capable=False)
    _device_job(db_session, run, host_id, platform="MTK")
    if mixed_host:
        _device_job(db_session, run, host_id, platform="UNISOC")
    phase_two(whitelist=[run.id])
    return run, host_id


def _recover(host_id):
    with SessionLocal() as session:
        host = session.get(Host, host_id)
        host.status = "ONLINE"
        recovered = _now()
        host.last_heartbeat = recovered
        host.agent_capabilities = [SCAN_ROUND_V1]
        host.agent_capabilities_reported_at = recovered
        host.agent_capabilities_instance_id = "agent-a"
        session.commit()


def _one_host_scan(host_id, serials, platforms):
    """把 scan_task 的主机查询固定成「这台 host 要扫这些串号」。"""
    return patch.object(
        saq_tasks, "_query_hosts_for_scan",
        lambda *_a: (
            [(host_id, {
                "plan_run_id": 0, "device_serials": list(serials),
                "is_final": False, "run_date_stamps": [],
            })],
            [], [], {host_id: set(platforms)},
        ),
    )


def test_candidate_wait_is_distinguishable_from_unrecorded_partition(
    db_session, phase_two,
):
    """裁决第 2 项要求区分：候选等待 ≠ 存量按现行。"""
    candidate, _host_id = _candidate(db_session, phase_two)

    action = admission.partition_action_at_entry(
        candidate.id, entrypoint=admission.ENTRY_SCAN_TASK,
    )
    assert (action.mode, action.held) == (None, True)
    assert action.reason == admission.REASON_CAPABILITY_UNVERIFIED
    assert not action.legacy_allowed and not action.dedup_first

    historical = _run(db_session)
    _device_job(db_session, historical, _host(db_session))
    phase_two(whitelist=[])  # 同样的开关状态，但这个 run 没被点名
    legacy = admission.partition_action_at_entry(
        historical.id, entrypoint=admission.ENTRY_SCAN_TASK,
    )
    assert (legacy.mode, legacy.held) == (None, False)
    assert legacy.reason == admission.REASON_NOT_WHITELISTED
    assert legacy.legacy_allowed


@pytest.mark.asyncio
async def test_candidate_wait_produces_no_legacy_side_effects(db_session, phase_two):
    """纯 MTK 候选等待：scan 不下发、upload 不标记、merge 不跑旧工具、extract 不提取。"""
    run, host_id = _candidate(db_session, phase_two)
    mtk = _dle(db_session, run.id, host_id, platform="MTK", serial="SN-HOLD")

    legacy_dispatch = AsyncMock()
    hosts = MagicMock()
    with patch("backend.realtime.socketio_server.call_agent_control", legacy_dispatch), \
         patch.object(saq_tasks, "_query_hosts_for_scan", hosts):
        await saq_tasks.scan_task({}, plan_run_id=run.id, is_final=False)
    legacy_dispatch.assert_not_called()
    hosts.assert_not_called()

    collect = MagicMock()
    with patch.object(dedup_extract, "collect_upload_event_dir_names", collect):
        await saq_tasks.upload_task({}, plan_run_id=run.id)
    collect.assert_not_called()  # 纯候选等待：整段跳过，连空水位线都不写
    db_session.expire_all()
    assert db_session.get(DeviceLogEvent, mtk.id).state == "LOCAL"

    current, dedup, held = dedup_scan._partition_targets(run.id)
    assert (current, dedup, held) == ([], [], ["mtk"])
    old_merge = MagicMock()
    with patch.object(dedup_scan, "run_merge_all_platforms_sync", old_merge):
        assert dedup_scan.run_merge_for_partitions_sync(run.id) == "ok"
    old_merge.assert_not_called()

    assert dedup_extract.run_extract_sync(run.id) == -4
    assert _modes(db_session, run.id) == []


@pytest.mark.asyncio
async def test_candidate_wait_keeps_current_partition_of_same_run(db_session, phase_two):
    """候选等待只挡住它自己：同运行的展锐现行事件照常标记。"""
    run, host_id = _candidate(db_session, phase_two, mixed_host=True)
    unisoc = _dle(db_session, run.id, host_id, platform="UNISOC", serial="SN-U-HOLD")
    mtk = _dle(db_session, run.id, host_id, platform="MTK", serial="SN-M-HOLD")

    with patch.object(dedup_extract, "collect_upload_event_dir_names", lambda *_: [EVENT]):
        await saq_tasks.upload_task({}, plan_run_id=run.id)

    db_session.expire_all()
    assert db_session.get(DeviceLogEvent, unisoc.id).state == "UPLOAD_PENDING"
    assert db_session.get(DeviceLogEvent, mtk.id).state == "LOCAL"
    current, _dedup, held = dedup_scan._partition_targets(run.id)
    assert held == ["mtk"] and current == ["unisoc"]


def test_candidate_wait_defers_round_registration_until_evidence_returns(
    db_session, phase_two, tmp_path, monkeypatch,
):
    """候选等待期间不登记轮次也不入队；能力恢复后 sweep 固化模式并建轮。"""
    run, host_id = _candidate(db_session, phase_two)
    monkeypatch.setattr(shadow, "resolve_shared_storage_root", lambda: str(tmp_path))
    queued = MagicMock()
    monkeypatch.setattr(shadow, "enqueue_sync", queued)

    shadow.sweep_shadow_scan_rounds()
    assert _rounds(db_session, run.id) == []
    assert queued.call_args_list == []
    assert _modes(db_session, run.id) == []

    _recover(host_id)
    shadow.sweep_shadow_scan_rounds()
    assert _modes(db_session, run.id) == [("mtk", "DEDUP_FIRST")]
    rounds = _rounds(db_session, run.id)
    assert [row.kind for row in rounds] == [facts.SCAN_ROUND_KIND_PERIODIC]
    assert queued.call_args.kwargs["fact_round_id"] == rounds[0].id


def test_legacy_dispatch_marker_forfeits_admission_without_reports(
    db_session, phase_two,
):
    """裁决第 1 项：痕迹先于报表——已下发的旧扫描本身就否定 DEDUP_FIRST。"""
    run, host_id = _candidate(db_session, phase_two, mixed_host=True)
    assert admission.mark_legacy_scan_dispatched(
        run.id, entrypoint=admission.ENTRY_SCAN_TASK, host_ids=[host_id],
    ) is True

    with SessionLocal() as db:
        effects = admission.old_chain_effects(db, run.id)
    assert effects["legacy_scan_dispatched"] is True
    assert effects.get("mtk_report_artifacts", 0) == 0, "只靠在途痕迹就够，不需要报表登记"

    assert admission.fixate_partition_mode_at_entry(
        run.id, entrypoint=admission.ENTRY_MERGE,
    ) == "CURRENT"
    assert _modes(db_session, run.id) == [("mtk", "CURRENT")]


def test_forfeit_survives_still_unverified_capability(db_session, phase_two):
    """失格是持久事实：能力仍未证实时也必须固化 CURRENT，而不是继续读成「等待」。

    这条就是判据顺序的回归位——若把能力检查放在旧链痕迹之前，这里会拿到 ``held`` + 空记录，
    于是「已发旧扫描 + 心跳还没恢复」的窗口里没有任何入口会固化模式，下一个入场可以凭空写进
    ``DEDUP_FIRST``（裁决第 1、2 项的合取正是用来排除它）。
    """
    run, host_id = _candidate(db_session, phase_two, mixed_host=True)
    assert admission.mark_legacy_scan_dispatched(
        run.id, entrypoint=admission.ENTRY_SCAN_TASK, host_ids=[host_id],
    ) is True

    with SessionLocal() as db:
        host = db.get(Host, host_id)
        assert host.status == "OFFLINE", "本用例的前提就是能力仍未证实"
    # 用自开会话的入口版：它自己提交，测试读到的才是别的连接也能看到的已固化结果。
    action = admission.partition_action_at_entry(
        run.id, entrypoint=admission.ENTRY_UPLOAD_TASK,
    )
    assert (action.mode, action.held) == ("CURRENT", False)
    assert action.reason == admission.REASON_OLD_CHAIN_EFFECTS
    assert _modes(db_session, run.id) == [("mtk", "CURRENT")]


def test_marker_is_not_written_for_non_candidates(db_session, phase_two):
    """非候选不写痕迹：未点名的 run 行为与改动前逐字一致，也不新增 run_context 段。"""
    run = _run(db_session)
    _device_job(db_session, run, _host(db_session))
    phase_two(whitelist=[])

    assert admission.mark_legacy_scan_dispatched(
        run.id, entrypoint=admission.ENTRY_SCAN_TASK,
    ) is False
    with SessionLocal() as db:
        assert (db.get(PlanRun, run.id).run_context or {}) == {}


@pytest.mark.asyncio
async def test_controlled_interleaving_cannot_fixate_dedup_after_legacy_scan(
    db_session, phase_two,
):
    """被点名的交错不可达：旧扫描在途 → 能力恢复 → 另一入场只能固化 CURRENT。"""
    run, host_id = _candidate(db_session, phase_two, mixed_host=True)

    # 1)
    # 为展锐现行侧照发旧 scan，并在下发之前落库内痕迹：探针就在下发那一刻跨会话读痕迹，
    # 读不到说明「在途旧链工作」这件事还没成为事实，之后的准入仍可能被翻转。
    seen = {}

    async def _dispatch_and_stop(host_id_arg, action, payload=None):
        with SessionLocal() as db:
            seen["effects_at_dispatch"] = admission.old_chain_effects(db, run.id)
        raise RuntimeError("stop-after-dispatch")

    with _one_host_scan(host_id, ["SN-U"], {"mtk", "unisoc"}), \
         patch("backend.realtime.socketio_server.call_agent_control", _dispatch_and_stop):
        with pytest.raises(RuntimeError, match="stop-after-dispatch"):
            await saq_tasks.scan_task({}, plan_run_id=run.id, is_final=False)

    assert seen["effects_at_dispatch"].get("legacy_scan_dispatched") is True

    # 2)
    # 能力恢复，且没有任何报表登记。
    _recover(host_id)
    with SessionLocal() as db:
        assert admission.old_chain_effects(db, run.id) == {
            "run_context_sections": [admission.LEGACY_SCAN_DISPATCH_SECTION],
            "legacy_scan_dispatched": True,
        }

    # 3)
    # 另一个入口：只能 CURRENT，绝不写 DEDUP_FIRST。
    assert admission.fixate_partition_mode_at_entry(
        run.id, entrypoint=admission.ENTRY_SHADOW_SWEEP,
    ) == "CURRENT"
    assert _modes(db_session, run.id) == [("mtk", "CURRENT")]


@pytest.mark.asyncio
async def test_admitted_partition_scans_only_through_registered_rounds(
    db_session, phase_two, tmp_path, monkeypatch,
):
    """裁决第 3 项：DEDUP_FIRST 的 MTK 只经已登记轮次下发，首次下发前模式已提交且唯一。"""
    run = _run(db_session, status="RUNNING")
    host_id = _host(db_session)
    _device_job(db_session, run, host_id)
    phase_two(whitelist=[run.id])

    monkeypatch.setattr(shadow, "resolve_shared_storage_root", lambda: str(tmp_path))
    queued = MagicMock()
    monkeypatch.setattr(shadow, "enqueue_sync", queued)

    legacy = AsyncMock()
    with patch("backend.realtime.socketio_server.call_agent_control", legacy):
        await saq_tasks.scan_task({}, plan_run_id=run.id, is_final=False)
        await saq_tasks.scan_task({}, plan_run_id=run.id, is_final=True)
    legacy.assert_not_called()

    shadow.sweep_shadow_scan_rounds()
    rounds = _rounds(db_session, run.id)
    assert [row.kind for row in rounds] == [facts.SCAN_ROUND_KIND_PERIODIC]
    round_id = rounds[0].id

    seen = {}

    async def _round_dispatch(host_id_arg, *, payload=None, required_instance_id=None):
        # #4049：影子下发携带 required_instance_id；本探针只核对模式提交时点。
        seen["mode"] = _committed_mode_elsewhere(run.id)
        seen["fact_round_id"] = (payload or {}).get("fact_round_id")
        seen["rows"] = _modes_from_other_session(run.id)
        seen["required_instance_id"] = required_instance_id
        return {"ok": True}

    monkeypatch.setattr(
        shadow, "build_scan_now_payload",
        lambda db, pid, host, **kwargs: {
            "plan_run_id": pid, "device_serials": ["SN-ANY"],
            "fact_round_id": kwargs["fact_round_id"], "spill_inputs": [],
        },
    )
    with patch("backend.realtime.socketio_server.call_shadow_scan_control", _round_dispatch):
        await saq_tasks.scan_task({}, plan_run_id=run.id, fact_round_id=round_id)

    assert seen["mode"] == "DEDUP_FIRST"
    assert seen["fact_round_id"] == round_id
    assert seen["rows"] == [("mtk", "DEDUP_FIRST")]
    assert seen["required_instance_id"] == "agent-a"


def _modes_from_other_session(run_id):
    with SessionLocal() as db:
        return _modes(db, run_id)


@pytest.mark.asyncio
async def test_terminal_entry_also_routes_dedup_partition_to_final_round(
    db_session, phase_two, tmp_path, monkeypatch,
):
    """终态入口（is_final）同样不发旧 scan，改由 FINAL 轮承担扫描。"""
    run = _run(db_session, status="SUCCESS")
    host_id = _host(db_session)
    _device_job(db_session, run, host_id)
    phase_two(whitelist=[run.id])

    assert admission.fixate_partition_mode_at_entry(
        run.id, entrypoint=admission.ENTRY_SCAN_TASK,
    ) == "DEDUP_FIRST"

    legacy = AsyncMock()
    with patch("backend.realtime.socketio_server.call_agent_control", legacy):
        await saq_tasks.scan_task({}, plan_run_id=run.id, is_final=True)
    legacy.assert_not_called()

    monkeypatch.setattr(shadow, "resolve_shared_storage_root", lambda: str(tmp_path))
    monkeypatch.setattr(shadow, "enqueue_sync", MagicMock())
    shadow.sweep_shadow_scan_rounds()
    kinds = [row.kind for row in _rounds(db_session, run.id)]
    assert facts.SCAN_ROUND_KIND_FINAL in kinds


def test_same_host_legacy_scan_still_runs_mtk_runner(monkeypatch):
    """同主机反例（交 Owner 裁边界）：旧 scan_now 只看 runner 是否配置，不看串号平台。

    控制面只传展锐串号**不能**抑制 Agent 侧的 MTK 扫描——``ScanRunner._execute_job`` 在 MTK
    runner 已配置时无条件调用它（``scan_runner.py`` 的 MTK 分支没有串号门控）。本用例直接跑
    真实 Agent 代码路径，把这条现有接口无法表达的边界固定下来。
    """
    from backend.agent import scan_runner as agent_scan
    from backend.agent import unisoc_scan_runner as unisoc_scan

    mtk_calls = []
    unisoc_calls = []

    mtk_runner = SimpleNamespace(
        is_configured=lambda: True,
        run_scan_and_upload=lambda pid, hid, **kwargs: mtk_calls.append(kwargs),
    )
    unisoc_runner = SimpleNamespace(
        is_configured=lambda: True,
        run_scan_and_upload=lambda pid, hid, **kwargs: unisoc_calls.append(kwargs),
    )
    monkeypatch.setattr(agent_scan.ScanRunner, "instance", lambda: mtk_runner)
    monkeypatch.setattr(unisoc_scan.UnisocScanRunner, "instance", lambda: unisoc_runner)
    monkeypatch.setattr(
        agent_scan, "detect_device_platform", lambda _adb, serial: "UNISOC",
    )

    job = agent_scan._ScanJob(
        plan_run_id=1, host_id="h", is_final=False,
        device_serials=("SN-UNISOC-ONLY",), run_date_stamps=(),
    )
    agent_scan.ScanRunner._execute_job(job)

    assert mtk_calls, "MTK runner 仍被调用：过滤串号不足以抑制旧 MTK 扫描"
    assert unisoc_calls and unisoc_calls[0]["device_serials"] == ("SN-UNISOC-ONLY",)
