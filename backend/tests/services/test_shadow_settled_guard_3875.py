"""#3875 的 settled、动态能力守卫与现行链路反例。"""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, AsyncMock, MagicMock, patch

import pytest

from backend.core.settings.scheduler import SchedulerSettings
from backend.models.host import Host
from backend.services import issue_fact_store as facts, shadow_scan_rounds as shadow
from backend.services import artifact_digest
from backend.agent.contracts.round_report_layout import round_manifest_path
from backend.tests.services.test_shadow_scan_rounds import (
    shadow_env as shadow_env, _observe_running_run, _rows, _receipt, _delivery_rounds,
)


def _closed_final(env):
    factory, run_id, root, _queued, _settings = env
    _observe_running_run(env)
    with factory() as db:
        db.get(shadow.PlanRun, run_id).status = "SUCCESS"
        db.commit()
    now = datetime.now(timezone.utc)
    shadow.sweep_shadow_scan_rounds(now=now)
    final = _delivery_rounds(factory, run_id)[0]
    for unit in final.expected_set:
        _receipt(root, run_id, final.id, unit["host_id"])
    shadow.sweep_shadow_scan_rounds(now=now + timedelta(seconds=1))
    return _delivery_rounds(factory, run_id)[0]


def test_settled_exits_candidates_and_all_entry_points_do_no_manifest_io(shadow_env, monkeypatch):
    factory, run_id, root, queued, settings = shadow_env
    final = _closed_final(shadow_env)
    settled_at = final.closed_at + timedelta(seconds=settings.stp_scan_round_report_wait_seconds + 1)
    shadow.sweep_shadow_scan_rounds(now=settled_at)
    with factory() as db:
        assert run_id not in facts.list_shadow_scan_run_ids(db, platform="mtk")
        assert facts.get_partition_mode(db, plan_run_id=run_id, platform="mtk").shadow_settled_at == settled_at
    # settled 后再落入旧轮命名空间：包括 stale candidate 与 stale SAQ 任务也不能重开。
    _receipt(root, run_id, final.id, "host-a", empty=False)
    no_io = Mock(side_effect=AssertionError("settled partition manifest I/O"))
    monkeypatch.setattr(shadow, "read_round_reports", no_io)
    queued.reset_mock()
    shadow.sweep_shadow_scan_rounds(now=settled_at + timedelta(days=1))
    with factory() as db:
        assert shadow._advance_run(db, run_id, str(root), settled_at, settings) == []
        same = facts.settle_shadow_partition(db, plan_run_id=run_id, platform="mtk",
            settled_at=settled_at + timedelta(days=1), report_wait_seconds=1)
        assert same.shadow_settled_at == settled_at
        with pytest.raises(facts.ImmutableIssueFactError, match="settled"):
            facts.record_scan_round(db, plan_run_id=run_id, platform="mtk", round_no=99,
                kind="SUPPLEMENT", expected_set=[], input_cutoff_at=settled_at)
    assert shadow.prepare_shadow_scan(run_id, final.id) == []
    assert len(_delivery_rounds(factory, run_id)) == 1
    no_io.assert_not_called()
    queued.assert_not_called()


@pytest.mark.parametrize("condition", ["no_final", "open_final", "wait", "open_supplement"])
def test_unsettled_terminal_partition_stays_a_candidate(shadow_env, condition):
    factory, run_id, _root, _queued, settings = shadow_env
    if condition == "no_final":
        _observe_running_run(shadow_env)
        with factory() as db:
            db.get(shadow.PlanRun, run_id).status = "FAILED"
            db.commit()
        now = datetime.now(timezone.utc)
    else:
        final = _closed_final(shadow_env)
        now = final.closed_at + timedelta(seconds=settings.stp_scan_round_report_wait_seconds)
        if condition in {"open_final", "open_supplement"}:
            with factory() as db:
                if condition == "open_final":
                    row = facts.get_scan_round(db, round_id=final.id)
                    row.status = "RUNNING"
                    row.input_cutoff_at = now + timedelta(days=1)
                else:
                    facts.record_scan_round(db, plan_run_id=run_id, platform="mtk", round_no=3,
                        kind="SUPPLEMENT", expected_set=[{"host_id": "absent", "platform": "mtk", "coverage": "MISSING"}],
                        input_cutoff_at=now + timedelta(days=1))
                db.commit()
    shadow.sweep_shadow_scan_rounds(now=now)
    with factory() as db:
        assert run_id in facts.list_shadow_scan_run_ids(db, platform="mtk")
        assert facts.get_partition_mode(db, plan_run_id=run_id, platform="mtk").shadow_settled_at is None


def test_late_report_becomes_supplement_and_restarts_quiet_wait(shadow_env):
    factory, run_id, root, _queued, settings = shadow_env
    final = _closed_final(shadow_env)
    late_at = final.closed_at + timedelta(seconds=settings.stp_scan_round_report_wait_seconds - 1)
    _receipt(root, run_id, final.id, "host-a", empty=False)
    shadow.sweep_shadow_scan_rounds(now=late_at)
    original, supplement = _delivery_rounds(factory, run_id)
    assert original.expected_set == final.expected_set
    assert supplement.kind == "SUPPLEMENT" and supplement.status == "CLOSED"
    assert supplement.expected_set[0]["reports"][0]["fact_round_id"] == final.id
    shadow.sweep_shadow_scan_rounds(now=late_at + timedelta(seconds=2))
    with factory() as db:
        assert run_id in facts.list_shadow_scan_run_ids(db, platform="mtk")
    shadow.sweep_shadow_scan_rounds(now=late_at + timedelta(seconds=settings.stp_scan_round_report_wait_seconds + 1))
    with factory() as db:
        assert run_id not in facts.list_shadow_scan_run_ids(db, platform="mtk")


def test_unreadable_manifest_cannot_be_interpreted_as_quiet(shadow_env):
    factory, run_id, root, _queued, settings = shadow_env
    final = _closed_final(shadow_env)
    round_manifest_path(str(root), run_id, final.id, "host-a").write_text("{broken")
    shadow.sweep_shadow_scan_rounds(now=final.closed_at + timedelta(seconds=settings.stp_scan_round_report_wait_seconds + 1))
    with factory() as db:
        assert run_id in facts.list_shadow_scan_run_ids(db, platform="mtk")
        assert facts.get_partition_mode(db, plan_run_id=run_id, platform="mtk").shadow_settled_at is None


@pytest.mark.parametrize("reported", [None, "", "sha256:" + "a" * 64, "matched"])
def test_dynamic_digest_guard_marks_unverified_hosts_missing(shadow_env, monkeypatch, caplog, reported):
    factory, run_id, _root, _queued, _settings = shadow_env
    # 计算真实当前载荷摘要；不在实现中维护版本或摘要名单。
    desired = artifact_digest.compute_desired_artifact_digest(kind=artifact_digest.ARTIFACT_KIND_CODE)
    actual = desired if reported == "matched" else reported
    with factory() as db:
        db.add(Host(id="host-a", hostname="shadow-capability-a", status="ONLINE", agent_artifact_digest=actual))
        db.commit()
    monkeypatch.setattr(shadow, "iter_plan_run_scan_hosts", lambda *_: [("host-a", "ONLINE", False)])
    build = Mock(return_value={"device_serials": ["a"], "fact_round_id": 1})
    monkeypatch.setattr(shadow, "build_scan_now_payload", build)
    shadow.sweep_shadow_scan_rounds()
    row = _rows(factory, run_id)[0]
    payloads = shadow.prepare_shadow_scan(run_id, row.id)
    unit = _rows(factory, run_id)[0].expected_set[0]
    if reported == "matched":
        assert len(payloads) == 1
        assert build.call_args.kwargs["fact_round_id"] == row.id
        assert "reason" not in unit
        # 发布改变 desired 后，下个可下发时间点立刻拒绝旧摘要。
        monkeypatch.setattr(shadow, "compute_desired_artifact_digest", lambda **_: "sha256:" + "f" * 64)
        with factory() as db:
            coverage = [dict(u) for u in facts.get_scan_round(db, round_id=row.id).expected_set]
            coverage[0].pop("last_dispatched_at")
            facts.record_scan_round_coverage(db, round_id=row.id, coverage=coverage)
            db.commit()
        assert shadow.prepare_shadow_scan(run_id, row.id) == []
        unit = _rows(factory, run_id)[0].expected_set[0]
    else:
        assert payloads == []
        build.assert_not_called()
    assert unit["coverage"] == "MISSING" and unit["reason"] == "capability_unverified"
    assert "shadow_scan_capability_unverified" in caplog.text


def test_shadow_disabled_does_not_compute_digest_or_read_storage(monkeypatch):
    monkeypatch.setattr(shadow, "get_scheduler_settings", lambda: SchedulerSettings())
    no_io = Mock(side_effect=AssertionError("shadow disabled I/O"))
    for name in ["SessionLocal", "resolve_shared_storage_root", "compute_desired_artifact_digest"]:
        monkeypatch.setattr(shadow, name, no_io)
    shadow.sweep_shadow_scan_rounds()
    assert shadow.prepare_shadow_scan(42, 1) == []
    no_io.assert_not_called()


@pytest.mark.asyncio
async def test_current_scan_now_still_dispatches_without_capability_guard(monkeypatch):
    from backend.tasks import saq_tasks
    from backend.tests.tasks.test_saq_scan_pipeline import _scan_task_env

    no_shadow = Mock(side_effect=AssertionError("current scan entered shadow guard"))
    monkeypatch.setattr(shadow, "_shadow_capability_verified", no_shadow)
    scan_sync = MagicMock(return_value="1")
    completeness = MagicMock()
    record_archive, queue = MagicMock(), MagicMock()
    queue.enqueue = AsyncMock()

    async def to_thread(fn, *args, **kwargs):
        if fn is scan_sync:
            return "1"
        if fn is completeness:
            return 1
        return fn(*args, **kwargs)

    with _scan_task_env(saq_tasks, monkeypatch, [("old-agent", "ONLINE")],
        to_thread=AsyncMock(side_effect=to_thread), scan_sync=scan_sync,
        completeness=completeness, record_archive=record_archive, queue=queue):
        with patch("backend.realtime.socketio_server.call_agent_control", new=AsyncMock(return_value=True)) as control:
            await saq_tasks.scan_task({}, plan_run_id=42)
            control.assert_awaited_once_with("old-agent", "scan_now", payload={
                "plan_run_id": 42, "is_final": False, "device_serials": [], "run_date_stamps": [],
            })
    no_shadow.assert_not_called()
