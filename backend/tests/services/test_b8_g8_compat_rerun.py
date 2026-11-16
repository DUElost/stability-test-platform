"""B8 G8：受控兼容补跑（ADR-0062 D15，§11 场景 10）。

覆盖 #4031 第 2 项：

- 只接受 Owner/admin 对单个 DEDUP_FIRST 分区的显式发起；
- 源限定为 I4 保护期内的 Agent 本地原件或中心已校验副本，覆盖 ARCHIVED、
  PRUNED、全量源、部分源；
- 重复请求幂等、失败重试递增 attempts 且复用原登记；
- 原交付版本、证据发布、DLE 状态与累计发生数在补跑前后保持一致；
- 多个交付版本之间不串读，报表计数按所选版本的事件集合计算。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import xlrd
from sqlalchemy.orm import sessionmaker

from backend.models.device_log_event import DeviceLogEvent
from backend.models.host import Host
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services import issue_fact_store as facts


def _api():
    from backend.services.compat_rerun import (
        CompatRerunError,
        trigger_compat_rerun,
    )

    return CompatRerunError, trigger_compat_rerun


def _model():
    from backend.models.issue_fact import CompatRerun

    return CompatRerun

PLATFORM = "mtk"
HOST = "host-g8"
# 合法采集目录名（get_aee_log_folder_name 形态）；副本布局按它保留 aee 结构。
COLLECTION_FOLDER = "MLD-NX3-P535AA-HONOR-NX3-16-1031V78_1031_MonkeyAEEinfo"

_EVENT_SEQ = {"n": 0}


def _event_name() -> str:
    """合法 AEE 事件目录名（副本布局派生要求事件名过 is_event_dir_basename）。"""
    _EVENT_SEQ["n"] += 1
    return f"2026_1116_100000_{_EVENT_SEQ['n']:03d}_db.74.ANR"


def _settings(monkeypatch):
    from backend.core.settings.scheduler import SchedulerSettings

    settings = SchedulerSettings(stp_scan_round_shadow_enabled=True)
    monkeypatch.setattr(
        "backend.services.shadow_delivery.get_scheduler_settings", lambda: settings,
    )


@pytest.fixture
def compat_env(db_session, tmp_path, monkeypatch):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    _settings(monkeypatch)
    try:
        from backend.services import compat_rerun as compat_module
    except Exception:
        # 基线没有该模块时仍允许收集/运行探针，让 RED 来自行为而非收集失败。
        compat_module = None
    if compat_module is not None:
        monkeypatch.setattr(
            compat_module, "resolve_shared_storage_root", lambda: str(tmp_path),
        )
        monkeypatch.setattr(compat_module, "SessionLocal", factory)
    from backend.core import artifact_paths, storage_root

    for module in (artifact_paths, storage_root):
        monkeypatch.setattr(module, "resolve_shared_storage_root", lambda: str(tmp_path))
        monkeypatch.setattr(
            module, "resolve_control_plane_storage_root", lambda: str(tmp_path / "cp"),
        )
        monkeypatch.setattr(module, "resolve_legacy_log_storage_root", lambda: "")
    (tmp_path / "cp").mkdir(exist_ok=True)
    (tmp_path / "devices").mkdir(exist_ok=True)
    (tmp_path / "evidence").mkdir(exist_ok=True)
    return factory, tmp_path


def _run(db, *, status="FAILED", mode=facts.PARTITION_MODE_DEDUP_FIRST):
    plan = Plan(name=f"g8-compat-{uuid4().hex[:6]}", created_by="pytest")
    db.add(plan)
    db.flush()
    run = PlanRun(
        plan_id=plan.id, status=status, plan_snapshot={},
        run_type="MANUAL", triggered_by="pytest",
    )
    db.add(run)
    db.flush()
    if db.get(Host, HOST) is None:
        db.add(Host(id=HOST, hostname=HOST, status="ONLINE"))
        db.flush()
    if mode is not None:
        facts.record_partition_mode(
            db, plan_run_id=run.id, platform=PLATFORM, mode=mode,
        )
    db.flush()
    return run


def _event_dir(root: Path, *parts: str) -> Path:
    path = root.joinpath(*parts)
    path.mkdir(parents=True)
    (path / "main.dbg").write_bytes(b"dbg-" + b"-".join(p.encode() for p in parts))
    return path


def _dle(
    db, run, *, serial, event, state="ARCHIVED", remote_path=None,
    local_path=None, occurrence_key=None, build_version="V1",
):
    row = DeviceLogEvent(
        serial=serial, platform="MTK", event_type="JE", state=state,
        local_path=local_path
        or f"/data/aee/{COLLECTION_FOLDER}/{serial}/aee_exp/{event}",
        remote_path=remote_path, host_id=HOST, plan_run_id=run.id,
        occurrence_key=occurrence_key or f"{serial}-{event}",
        detected_at=datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc),
    )
    db.add(row)
    db.flush()
    signature = facts.record_issue_signature(
        db,
        signature_raw=json.dumps({
            "exp_class": "JE", "event": event,
            "package": f"pkg.{serial}", "caused_by": f"caused-{serial}",
        }),
        normalized_key=f"key-{serial}-{event}", platform=PLATFORM, event_type="JE",
        seen_at=datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc),
    )
    occurrence = facts.record_issue_occurrence(
        db, signature_id=signature.id, occurrence_key=row.occurrence_key,
        serial=serial, observed_at=row.detected_at, first_seen_source="local",
        first_seen_plan_run_id=run.id, build_version=build_version,
    )
    facts.link_occurrence_event(
        db, occurrence_id=occurrence.id, dle_id=row.id, signature_id=signature.id,
    )
    facts.record_representative_selection(
        db, plan_run_id=run.id, platform=PLATFORM, signature_id=signature.id,
        selection_status=facts.SELECTION_SELECTED, dle_id=row.id,
        evidence_status=facts.EVIDENCE_COMPLETE,
    )
    db.flush()
    return row, signature, occurrence


def _publication(db, root, dle, run):
    primary = (
        f"{run.id}_MLD-NX3-P535AA-HONOR-NX3-16-1031V78_1031_MonkeyAEEinfo"
    )
    event = "2026_1116_100000_123_db.74.ANR"
    _event_dir(root, "evidence", primary, dle.serial, event)
    publication = facts.record_evidence_publication(
        db, plan_run_id=run.id, platform=PLATFORM, dle_id=dle.id,
        root_plan_run_id=run.id,
        source_kind=facts.PUBLICATION_SOURCE_LOCAL,
        source_path=str(root / "src"), collection_folder=primary,
        naming_rule_version="v1",
        destination_rel_path=f"evidence/{primary}/{dle.serial}/{event}",
        state=facts.PUBLICATION_STATE_PENDING,
    )
    facts.update_evidence_publication_state(
        db, row=publication, state=facts.PUBLICATION_STATE_VERIFIED,
        checksum="b" * 64, size_bytes=1,
    )
    db.flush()
    return publication


def _version(db, run, *, no, issue_ids=(), event_ids=()):
    return facts.record_delivery_version(
        db, plan_run_id=run.id, platform=PLATFORM, version_no=no,
        software_versions=["V1"], round_ids=[1],
        basis_rounds=[{"round_id": 1, "merge_generation": 1}],
        effective_coverage=[], issue_ids=list(issue_ids),
        event_ids=[str(value) for value in event_ids],
        stats_snapshot_version="a" * 64,
    )


#: 现行 merge 产物（aeeexp）的 15 列表头，逐字对拍
#: ``/mnt/stp-log/dedup/{run}/merge/{plat}/Result_MergeFiles.xls``（2026-11-16，
#: run 1216）；第 6 列带尾随空格，第 14 列是 DeviceCount（org 报表为 DeviceId）。
MERGE_HEADERS_15 = [
    "Id", "Path", "Version", "ExpTime", "ExpClass", "ExpType ",
    "CurProcess", "Package", "Detail", "CausedBy", "extraTag",
    "Count", "Activity", "DeviceCount", "Rom_Ram",
]


def _read_report(path: str) -> list[dict]:
    book = xlrd.open_workbook(path)
    sheet = book.sheet_by_index(0)
    headers = [str(sheet.cell_value(0, column)).strip() for column in range(sheet.ncols)]
    return [
        {headers[column]: sheet.cell_value(row, column)
         for column in range(sheet.ncols)}
        for row in range(1, sheet.nrows)
    ]


def _read_raw_headers(path: str) -> list[str]:
    book = xlrd.open_workbook(path)
    sheet = book.sheet_by_index(0)
    headers = [str(sheet.cell_value(0, column)) for column in range(sheet.ncols)]
    book.release_resources()
    return headers


def _snapshot(db, run):
    versions = [
        (row.version_no, list(row.issue_ids or []), list(row.event_ids or []))
        for row in facts.list_delivery_versions(db, plan_run_id=run.id, platform=PLATFORM)
    ]
    publications = [
        (str(row.dle_id), row.state, row.destination_rel_path)
        for row in facts.list_partition_evidence_publications(
            db, plan_run_id=run.id, platform=PLATFORM,
        )
    ]
    states = {
        str(row.id): row.state
        for row in db.query(DeviceLogEvent).filter_by(plan_run_id=run.id).all()
    }
    counts = {
        str(row["signature_id"]): (
            facts.count_occurrences(
                db, signature_id=row["signature_id"],
                plan_run_id=run.id, platform=PLATFORM,
            ),
            facts.count_machines(
                db, signature_id=row["signature_id"],
                plan_run_id=run.id, platform=PLATFORM,
            ),
        )
        for row in facts.list_partition_delivery_issues(
            db, plan_run_id=run.id, platform=PLATFORM,
        )
    }
    return versions, publications, states, counts


def test_full_source_archived_and_local_preserves_facts(compat_env):
    CompatRerunError, trigger_compat_rerun = _api()
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        arch_name = _event_name()
        archived, sig_a, occ_a = _dle(
            db, run, serial="SN-ARCH", event=arch_name, state="ARCHIVED",
            remote_path=str(_event_dir(root, "devices", str(run.id), arch_name)),
        )
        local_root = root / "agent-local"
        local_root.mkdir()
        local, sig_l, occ_l = _dle(
            db, run, serial="SN-LOCAL", event="evt-local", state="LOCAL",
            local_path=str(
                _event_dir(
                    local_root, COLLECTION_FOLDER, "SN-LOCAL", "aee_exp", _event_name(),
                )
            ),
        )
        _version(
            db, run, no=1,
            issue_ids=[sig_a.id, sig_l.id],
            event_ids=[archived.id, local.id],
        )
        before = _snapshot(db, run)
        db.commit()
        run_id = run.id

    with factory() as db:
        run = db.get(PlanRun, run_id)
        outcome = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin",
            delivery_version_no=1,
            local_source_roots={HOST: str(root / "agent-local")},
        )
        db.commit()
        assert outcome.replayed is False
        assert outcome.rerun.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert outcome.rerun.is_partial is False
        assert outcome.rerun.source_delivery_version_no == 1
        assert {row["source_kind"] for row in outcome.rerun.source_records} == {
            "center_remote_verified", "agent_local_original",
        }
        assert outcome.rerun.coverage_scope["covered_events"] == 2
        assert _read_raw_headers(outcome.rerun.merge_xls_path) == MERGE_HEADERS_15
        rows = _read_report(outcome.rerun.merge_xls_path)
        assert len(rows) == 2
        by_caused = {row["CausedBy"]: row for row in rows}
        assert set(by_caused) == {"caused-SN-ARCH", "caused-SN-LOCAL"}
        arch = by_caused["caused-SN-ARCH"]
        local_row = by_caused["caused-SN-LOCAL"]
        # Id 是 0 起行序（现行 merge 同），不是 signature_id。
        assert (rows[0]["Id"], rows[1]["Id"]) == (0, 1)
        assert rows[0]["CausedBy"] == "caused-SN-ARCH"
        for row in (arch, local_row):
            # 事实层可忠实复原的列。
            assert row["Count"] == 1
            assert row["DeviceCount"] == 1
            assert row["ExpClass"] == "JE"
            assert row["Package"].startswith("pkg.")
            assert row["Version"] == "V1"
            assert "/compat_reruns/" in row["Path"] and "/aee_exp/" in row["Path"]
            # 事实层无载体、平台不复刻工具解析规则：登记为空缺偏离（Agent Note）。
            assert row["ExpType"] == ""
            assert row["CurProcess"] == ""
            assert row["Detail"] == ""
            assert row["Activity"] == ""
            assert row["Rom_Ram"] == ""
            assert row["extraTag"] == ""
        assert arch["ExpTime"] == time.asctime(
            time.gmtime(archived.detected_at.timestamp()),
        )
        assert arch["Path"].endswith(f"aee_exp/{arch_name}")
        assert local_row["Path"].endswith(
            f"aee_exp/{Path(local.local_path).name}"
        )
        manifest = json.loads(Path(outcome.rerun.manifest_path).read_text("utf-8"))
        assert manifest["is_partial"] is False
        assert manifest["source_records"] == outcome.rerun.source_records
        assert _snapshot(db, run) == before


def test_pruned_without_center_copy_is_partial_and_never_uses_local(compat_env):
    CompatRerunError, trigger_compat_rerun = _api()
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        local_root = root / "agent-local"
        local_root.mkdir()
        pruned, sig, _occ = _dle(
            db, run, serial="SN-PRUNED", event="evt-pruned", state="PRUNED",
            local_path=str(_event_dir(local_root, "SN-PRUNED", "evt-pruned")),
        )
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[pruned.id])
        before = _snapshot(db, run)
        db.commit()
        run_id = run.id

    with factory() as db:
        run = db.get(PlanRun, run_id)
        outcome = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin",
            delivery_version_no=1,
            local_source_roots={HOST: str(local_root)},
        )
        db.commit()
        assert outcome.rerun.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert outcome.rerun.is_partial is True
        assert outcome.rerun.source_records == []
        assert outcome.rerun.missing_items[0]["dle_state"] == "PRUNED"
        assert outcome.rerun.missing_items[0]["reason"] == (
            "missing_after_prune_or_unavailable"
        )
        assert outcome.rerun.coverage_scope["covered_events"] == 0
        rows = _read_report(outcome.rerun.merge_xls_path)
        assert len(rows) == 1
        assert rows[0]["Id"] == 0
        assert rows[0]["Path"] == ""
        assert rows[0]["Count"] == 1
        assert rows[0]["DeviceCount"] == 1
        assert _snapshot(db, run) == before


def test_pruned_still_uses_center_verified_evidence(compat_env):
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        pruned, sig, _occ = _dle(
            db, run, serial="SN-PRUNED-EVID", event="evt-pruned-evidence",
            state="PRUNED",
        )
        _publication(db, root, pruned, run)
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[pruned.id])
        db.commit()
        run_id = run.id

    CompatRerunError, trigger_compat_rerun = _api()
    with factory() as db:
        outcome = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin",
            delivery_version_no=1,
        )
        db.commit()
        assert outcome.rerun.is_partial is False
        assert outcome.rerun.source_records[0]["source_kind"] == "center_evidence_verified"


def test_duplicate_request_replays_without_new_artifacts_or_counts(compat_env):
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        dle, sig, _occ = _dle(
            db, run, serial="SN-REPLAY", event="evt-replay",
            remote_path=str(_event_dir(root, "devices", str(run.id), "evt-replay")),
        )
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[dle.id])
        before = _snapshot(db, run)
        db.commit()
        run_id = run.id

    CompatRerunError, trigger_compat_rerun = _api()
    with factory() as db:
        first = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin",
            request_key="same-request", delivery_version_no=1,
        )
        db.commit()
        second = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin",
            request_key="same-request", delivery_version_no=1,
        )
        db.commit()
        assert second.replayed is True
        assert second.rerun.id == first.rerun.id
        assert second.rerun.attempts == first.rerun.attempts == 1
        assert db.query(_model()).filter_by(plan_run_id=run_id).count() == 1
        assert _snapshot(db, run) == before


def test_failed_attempt_retries_same_row_and_increments_attempts(compat_env):
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        dle, sig, _occ = _dle(
            db, run, serial="SN-RETRY", event="evt-retry",
            remote_path=str(_event_dir(root, "devices", str(run.id), "evt-retry")),
        )
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[dle.id])
        before = _snapshot(db, run)
        db.commit()
        run_id = run.id

    def broken_copy(_src, _dst, **_kwargs):
        raise OSError("copy failed")

    CompatRerunError, trigger_compat_rerun = _api()
    with factory() as db:
        with pytest.raises(CompatRerunError) as failed:
            trigger_compat_rerun(
                db, plan_run_id=run_id, platform=PLATFORM,
                requested_by="owner", requester_role="admin",
                request_key="retry-me", delivery_version_no=1,
                copy_fn=broken_copy,
            )
        db.commit()
        assert failed.value.status_code == 500

    with factory() as db:
        row = facts.get_compat_rerun_by_request(
            db, plan_run_id=run_id, platform=PLATFORM, request_key="retry-me",
        )
        assert row.status == facts.COMPAT_RERUN_STATUS_FAILED
        assert row.attempts == 1
        outcome = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin",
            request_key="retry-me", delivery_version_no=1,
        )
        db.commit()
        assert outcome.rerun.id == row.id
        assert outcome.rerun.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert outcome.rerun.attempts == 2
        assert db.query(_model()).filter_by(plan_run_id=run_id).count() == 1
        assert _snapshot(db, run) == before


def test_delivery_version_scope_prevents_cross_version_counts(compat_env):
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        event_one = _event_dir(root, "devices", str(run.id), "evt-one")
        event_two = _event_dir(root, "devices", str(run.id), "evt-two")
        first, sig, _occ = _dle(
            db, run, serial="SN-V1", event="evt-one",
            remote_path=str(event_one), build_version="V1",
        )
        second, _sig2, _occ2 = _dle(
            db, run, serial="SN-V2", event="evt-two",
            remote_path=str(event_two), build_version="V2",
        )
        # 同一问题的第二次发生只出现在 v2。
        extra_path = _event_dir(root, "devices", str(run.id), "evt-two-b")
        extra = DeviceLogEvent(
            serial="SN-V2B", platform="MTK", event_type="JE", state="ARCHIVED",
            local_path="/data/aee/SN-V2B/aee_exp/evt-two-b",
            remote_path=str(extra_path), host_id=HOST, plan_run_id=run.id,
            occurrence_key="v2-extra", detected_at=second.detected_at,
        )
        db.add(extra)
        db.flush()
        v2_occurrence = facts.record_issue_occurrence(
            db, signature_id=sig.id, occurrence_key="v2-extra",
            serial=extra.serial, observed_at=extra.detected_at,
            first_seen_source="local", first_seen_plan_run_id=run.id,
            build_version="V2",
        )
        facts.link_occurrence_event(
            db, occurrence_id=v2_occurrence.id,
            dle_id=extra.id, signature_id=sig.id,
        )
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[first.id])
        _version(
            db, run, no=2, issue_ids=[sig.id, _sig2.id],
            event_ids=[first.id, second.id, extra.id],
        )
        db.commit()
        run_id = run.id

    CompatRerunError, trigger_compat_rerun = _api()
    with factory() as db:
        v1 = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin",
            request_key="v1", delivery_version_no=1,
        )
        db.commit()
        assert len(_read_report(v1.rerun.merge_xls_path)) == 1
        assert _read_report(v1.rerun.merge_xls_path)[0]["Count"] == 1

    with factory() as db:
        v2 = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin",
            request_key="v2", delivery_version_no=2,
        )
        db.commit()
        rows = _read_report(v2.rerun.merge_xls_path)
        assert len(rows) == 2
        # Id 是 0 起行序；all_issues 按 signature_id 升序 → 第一行是 sig。
        assert (rows[0]["Id"], rows[1]["Id"]) == (0, 1)
        assert rows[0]["Count"] == 2
        assert rows[0]["DeviceCount"] == 2
        assert rows[1]["Count"] == 1
        assert rows[1]["DeviceCount"] == 1


def test_route_is_admin_only_and_replay_is_visible(
    compat_env, client, admin_headers, auth_headers,
):
    factory, _root = compat_env
    with factory() as db:
        run = _run(db)
        db.commit()
        run_id = run.id

    forbidden = client.post(
        f"/api/v1/plan-runs/{run_id}/dedup/compat-rerun",
        params={"platform": PLATFORM, "request_key": "route-key"},
        headers=auth_headers,
    )
    assert forbidden.status_code == 403

    first = client.post(
        f"/api/v1/plan-runs/{run_id}/dedup/compat-rerun",
        params={"platform": PLATFORM, "request_key": "route-key"},
        headers=admin_headers,
    )
    assert first.status_code == 200, first.text
    payload = first.json()["data"]
    assert payload["replayed"] is False
    assert payload["rerun"]["status"] == facts.COMPAT_RERUN_STATUS_COMPLETED

    second = client.post(
        f"/api/v1/plan-runs/{run_id}/dedup/compat-rerun",
        params={"platform": PLATFORM, "request_key": "route-key"},
        headers=admin_headers,
    )
    assert second.status_code == 200
    assert second.json()["data"]["replayed"] is True

    listed = client.get(
        f"/api/v1/plan-runs/{run_id}/dedup/compat-reruns",
        params={"platform": PLATFORM},
        headers=admin_headers,
    )
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 1


def test_route_failure_registers_and_retry_reuses_row(
    compat_env, client, admin_headers,
):
    """真实路由失败→失败登记持久化→同 key 重试复用同一行并递增 attempts。

    复核 B-1 的路由探针：失败路径经 HTTP 入口触发（copy 注入 OSError），
    服务必须用独立会话持久化 FAILED，路由回滚不得丢弃登记。
    """
    CompatRerunError, _trigger = _api()
    from backend.services import compat_rerun as compat_module

    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        dle, sig, _occ = _dle(
            db, run, serial="SN-ROUTE", event="evt-route",
            remote_path=str(_event_dir(root, "devices", str(run.id), "evt-route")),
        )
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[dle.id])
        db.commit()
        run_id = run.id

    original_copy = compat_module.copytree_no_symlinks

    def broken_copy(_src, _dst, **_kwargs):
        raise OSError("copy failed")

    compat_module.copytree_no_symlinks = broken_copy
    try:
        failed = client.post(
            f"/api/v1/plan-runs/{run_id}/dedup/compat-rerun",
            params={"platform": PLATFORM, "request_key": "route-retry"},
            headers=admin_headers,
        )
    finally:
        compat_module.copytree_no_symlinks = original_copy
    assert failed.status_code == 500

    with factory() as db:
        row = facts.get_compat_rerun_by_request(
            db, plan_run_id=run_id, platform=PLATFORM, request_key="route-retry",
        )
        assert row is not None
        assert row.status == facts.COMPAT_RERUN_STATUS_FAILED
        assert row.attempts == 1
        assert "copy failed" in (row.error or "")
        first_id = row.id

    retried = client.post(
        f"/api/v1/plan-runs/{run_id}/dedup/compat-rerun",
        params={"platform": PLATFORM, "request_key": "route-retry"},
        headers=admin_headers,
    )
    assert retried.status_code == 200, retried.text
    payload = retried.json()["data"]
    assert payload["replayed"] is False
    assert payload["rerun"]["id"] == first_id
    assert payload["rerun"]["status"] == facts.COMPAT_RERUN_STATUS_COMPLETED
    assert payload["rerun"]["attempts"] == 2

    with factory() as db:
        assert db.query(_model()).filter_by(plan_run_id=run_id).count() == 1
        final = facts.get_compat_rerun_by_request(
            db, plan_run_id=run_id, platform=PLATFORM, request_key="route-retry",
        )
        assert final.attempts == 2
        assert final.status == facts.COMPAT_RERUN_STATUS_COMPLETED


def test_concurrent_request_key_conflict_maps_to_409(
    compat_env, client, admin_headers,
):
    """并发同 key 触发的唯一约束冲突映射为 409，不再 500。"""
    CompatRerunError, _trigger = _api()
    factory, _root = compat_env
    with factory() as db:
        run = _run(db)
        db.commit()
        run_id = run.id

    from sqlalchemy.exc import IntegrityError

    def conflicting_insert(*args, **kwargs):
        raise IntegrityError("simulated concurrent insert", None, None)

    original_record = facts.record_compat_rerun
    facts.record_compat_rerun = conflicting_insert
    try:
        resp = client.post(
            f"/api/v1/plan-runs/{run_id}/dedup/compat-rerun",
            params={"platform": PLATFORM, "request_key": "conflict"},
            headers=admin_headers,
        )
    finally:
        facts.record_compat_rerun = original_record
    assert resp.status_code == 409
    assert "request_key" in resp.json()["detail"]

    replay = client.post(
        f"/api/v1/plan-runs/{run_id}/dedup/compat-rerun",
        params={"platform": PLATFORM, "request_key": "conflict"},
        headers=admin_headers,
    )
    assert replay.status_code == 200, replay.text


def test_scope_requires_admin_and_dedup_first(compat_env):
    factory, _root = compat_env
    with factory() as db:
        run = _run(db)
        db.commit()
        run_id = run.id

    CompatRerunError, trigger_compat_rerun = _api()
    with factory() as db:
        with pytest.raises(CompatRerunError) as forbidden:
            trigger_compat_rerun(
                db, plan_run_id=run_id, platform=PLATFORM,
                requested_by="user", requester_role="user",
            )
        assert forbidden.value.status_code == 403

        from backend.models.issue_fact import PartitionMode

        db.query(PartitionMode).filter_by(plan_run_id=run_id).delete()
        db.commit()
        with pytest.raises(CompatRerunError) as not_dedup:
            trigger_compat_rerun(
                db, plan_run_id=run_id, platform=PLATFORM,
                requested_by="owner", requester_role="admin",
            )
        assert not_dedup.value.status_code == 409
