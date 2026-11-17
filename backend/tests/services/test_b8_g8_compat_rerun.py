"""B8 G8：受控兼容补跑（ADR-0062 D15，§11 场景 10；#4031 第 2 轮返修判据）。

覆盖：

- 只接受 Owner/admin 对单个 DEDUP_FIRST 分区的显式发起；
- 源限定为 I4 保护期内的 Agent 本地原件或中心已校验副本，覆盖 ARCHIVED、
  PRUNED、全量源、部分源；
- B1：同 key 重试由一次尝试独占执行（两独立会话并发只复制一次）；迟到的
  失败登记不得改写已提交的 COMPLETED（状态、次数、产物路径保全）；顺序
  失败重试复用同一登记并递增次数；
- B3：同 key 绑定首次接受的交付版本——重试省略版本沿用绑定版本；显式另一
  版本在复制前 409；新 key 显式 v2 独立执行；复制/计数/台账/manifest 同版本；
- B2：15 列现行格式的列值取自已登记轮次的原生 org 报表（经伴随映射绑定），
  原生空列保留为空；原生值无法复原 → FAILED + 可定位到发生与字段的缺口；
  选用替补发生时 Path/ExpTime 等发生相关字段来自同一实际发生。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import xlrd
import xlwt
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


@pytest.fixture
def compat_env(db_session, tmp_path, monkeypatch):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
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


def _extra_occurrence(db, run, signature, dle, *, occurrence_key, build_version="V2"):
    """把另一个 DLE 挂到既有签名下（同问题的第二次发生，用于版本范围隔离）。"""
    occurrence = facts.record_issue_occurrence(
        db, signature_id=signature.id, occurrence_key=occurrence_key,
        serial=dle.serial, observed_at=dle.detected_at,
        first_seen_source="local", first_seen_plan_run_id=run.id,
        build_version=build_version,
    )
    facts.link_occurrence_event(
        db, occurrence_id=occurrence.id, dle_id=dle.id, signature_id=signature.id,
    )
    db.flush()
    return occurrence


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


#: 现行 merge 产物（aeeexp）的 15 列表头，逐字对拍
#: ``/mnt/stp-log/dedup/{run}/merge/{plat}/Result_MergeFiles.xls``（2026-11-16，
#: run 1216）；第 6 列带尾随空格，第 14 列是 DeviceCount（org 报表为 DeviceId）。
MERGE_HEADERS_15 = [
    "Id", "Path", "Version", "ExpTime", "ExpClass", "ExpType ",
    "CurProcess", "Package", "Detail", "CausedBy", "extraTag",
    "Count", "Activity", "DeviceCount", "Rom_Ram",
]
ORG_HEADERS_15 = [
    "Id", "Path", "Version", "ExpTime", "ExpClass", "ExpType ",
    "CurProcess", "Package", "Detail", "CausedBy", "extraTag",
    "Count", "Activity", "DeviceId", "Rom_Ram",
]


def _seed_native_round(db, root, run, dles, *, drop_org=False, round_no=1):
    """写 org 报表 + 伴随映射，并把覆盖记录写进已关闭 FINAL 轮（B2 列值来源）。

    ``drop_org=True`` 时删除 org 文件（保留 mapping），模拟原生材料缺失。
    """
    org_dir = root / "dedup" / str(run.id) / "_rounds" / str(round_no) / PLATFORM
    org_dir.mkdir(parents=True, exist_ok=True)
    org_path = org_dir / f"host_Result_{round_no}_org.xls"
    mapping_rows = []
    book = xlwt.Workbook(encoding="utf-8")
    sheet = book.add_sheet("aeeexp")
    for column, header in enumerate(ORG_HEADERS_15):
        sheet.write(0, column, header)
    for index, dle in enumerate(dles, start=1):
        agent_path = str(dle.local_path)
        values = {
            "Id": index - 1,
            "Path": agent_path,
            "Version": f"ORGV-{dle.serial}",
            # 工具原生时间串（采集侧本地时区）；按序区分代表与替补。
            "ExpTime": f"Sun Nov {15 + index} 10:40:46 CST 2026",
            "ExpClass": "Java (JE)" if index == 1 else "ANR",
            "ExpType": f"exp_type_{dle.serial}",
            "CurProcess": f"proc.{dle.serial}",
            "Package": f"orgpkg.{dle.serial}",
            "Detail": f"Device_id: {dle.serial}\n解析库版本：recognizeexception 1.0.8",
            "CausedBy": f"org-caused-{dle.serial}",
            "extraTag": "",
            "Count": 1,
            "Activity": "None",
            "DeviceId": dle.serial,
            "Rom_Ram": "128GB+6GB",
        }
        for column, header in enumerate(ORG_HEADERS_15):
            sheet.write(index, column, values.get(header.strip(), ""))
        mapping_rows.append({
            "sheet": "aeeexp",
            "row": index + 1,
            "path": agent_path,
            "dle_id": str(dle.id),
            "occurrence_key": dle.occurrence_key,
            "mapped": True,
            "duplicate_dle_ids": [],
        })
    book.save(str(org_path))
    if drop_org:
        org_path.unlink()
    mapping_path = Path(str(org_path) + ".mapping.json")
    mapping_path.write_text(json.dumps({
        "schema_version": 1,
        "plan_run_id": run.id,
        "host_id": HOST,
        "platform": PLATFORM,
        "fact_round_id": round_no,
        "rows": mapping_rows,
    }), encoding="utf-8")

    now = datetime.now(timezone.utc)
    units = [{
        "host_id": HOST, "platform": PLATFORM,
        "coverage": "MISSING", "expected": True,
    }]
    round_row = facts.record_scan_round(
        db, plan_run_id=run.id, platform=PLATFORM, round_no=round_no,
        kind=facts.SCAN_ROUND_KIND_FINAL, expected_set=units,
        input_cutoff_at=now,
    )
    covered = [{
        "host_id": HOST, "platform": PLATFORM,
        "coverage": "RECEIVED", "expected": True,
        "reports": [{
            "host_id": HOST, "platform": PLATFORM,
            "fact_round_id": round_row.id, "empty_receipt": False,
            "storage_uri": str(org_path), "mapping_uri": str(mapping_path),
        }],
    }]
    facts.record_scan_round_coverage(db, round_id=round_row.id, coverage=covered)
    facts.set_scan_round_status(
        db, round_id=round_row.id, status="CLOSED", closed_at=now,
    )
    facts.record_scan_round_merge(db, round_id=round_row.id, status="OK", summary={})
    db.flush()
    return round_row.id


def _version(db, run, *, no, issue_ids=(), event_ids=(), round_id=1):
    return facts.record_delivery_version(
        db, plan_run_id=run.id, platform=PLATFORM, version_no=no,
        software_versions=["V1"], round_ids=[round_id],
        basis_rounds=[{"round_id": round_id, "merge_generation": 1}],
        effective_coverage=[], issue_ids=list(issue_ids),
        event_ids=[str(value) for value in event_ids],
        stats_snapshot_version="a" * 64,
    )


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


def _seed_failed_registration(db, run, *, key, version_no):
    """预置一笔已提交的 FAILED 登记（上一轮失败后的状态）。"""
    row = facts.record_compat_rerun(
        db, plan_run_id=run.id, platform=PLATFORM, request_key=key,
        requested_by="owner", source_delivery_version_no=version_no,
    )
    facts.update_compat_rerun(
        db, row=row, status=facts.COMPAT_RERUN_STATUS_FAILED,
        attempts=1, error="prior failure",
    )
    db.flush()
    return row


def test_full_source_with_native_org_reports_completes_faithful_values(compat_env):
    """B2：全源 + 原生 org 可读 → COMPLETED，列值逐字来自工具原生行。"""
    CompatRerunError, trigger_compat_rerun = _api()
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        arch_name = _event_name()
        archived, sig_a, _occ_a = _dle(
            db, run, serial="SN-ARCH", event=arch_name, state="ARCHIVED",
            remote_path=str(_event_dir(root, "devices", str(run.id), arch_name)),
        )
        local_root = root / "agent-local"
        local_name = _event_name()
        local, sig_l, _occ_l = _dle(
            db, run, serial="SN-LOCAL", event=local_name, state="LOCAL",
            local_path=str(_event_dir(
                local_root, COLLECTION_FOLDER, "SN-LOCAL", "aee_exp", local_name,
            )),
        )
        round_id = _seed_native_round(db, root, run, [archived, local])
        _version(
            db, run, no=1,
            issue_ids=[sig_a.id, sig_l.id],
            event_ids=[archived.id, local.id],
            round_id=round_id,
        )
        before = _snapshot(db, run)
        db.commit()
        run_id = run.id

    with factory() as db:
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
        assert outcome.rerun.error is None
        assert outcome.rerun.source_delivery_version_no == 1
        assert {row["source_kind"] for row in outcome.rerun.source_records} == {
            "center_remote_verified", "agent_local_original",
        }
        assert outcome.rerun.coverage_scope["covered_events"] == 2
        assert outcome.rerun.coverage_scope["field_gaps"] == []

        assert _read_raw_headers(outcome.rerun.merge_xls_path) == MERGE_HEADERS_15
        rows = _read_report(outcome.rerun.merge_xls_path)
        assert len(rows) == 2
        by_caused = {row["CausedBy"]: row for row in rows}
        assert set(by_caused) == {"org-caused-SN-ARCH", "org-caused-SN-LOCAL"}
        arch = by_caused["org-caused-SN-ARCH"]
        local_row = by_caused["org-caused-SN-LOCAL"]
        # Id 是 0 起行序（现行 merge 同），不是 signature_id。
        assert (rows[0]["Id"], rows[1]["Id"]) == (0, 1)
        assert rows[0]["CausedBy"] == "org-caused-SN-ARCH"
        for row, dle in ((arch, archived), (local_row, local)):
            # 原生列值逐字对拍（含展现形与采集侧时区串）。
            assert row["ExpClass"] == ("Java (JE)" if dle is archived else "ANR")
            assert row["ExpType"] == f"exp_type_{dle.serial}"
            assert row["CurProcess"] == f"proc.{dle.serial}"
            assert row["Package"] == f"orgpkg.{dle.serial}"
            assert row["Detail"].startswith(f"Device_id: {dle.serial}")
            assert row["Rom_Ram"] == "128GB+6GB"
            assert row["Activity"] == "None"
            assert row["extraTag"] == ""  # 原生本来就为空 → 保留为空
            assert row["Version"] == f"ORGV-{dle.serial}"
            assert row["Count"] == 1
            assert row["DeviceCount"] == 1
            assert "/compat_reruns/" in row["Path"] and "/aee_exp/" in row["Path"]
        # ExpTime 来自与 Path 同一个实际发生的原生行（代表在前 → Nov 16/17）。
        assert arch["ExpTime"] == "Sun Nov 16 10:40:46 CST 2026"
        assert local_row["ExpTime"] == "Sun Nov 17 10:40:46 CST 2026"
        manifest = json.loads(Path(outcome.rerun.manifest_path).read_text("utf-8"))
        assert manifest["registered_status"] == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert manifest["field_gaps"] == []
        assert manifest["binding"]["delivery_version_no"] == 1
        assert manifest["binding"]["event_ids"] == [str(archived.id), str(local.id)]
        assert manifest["binding"]["rows"][0]["org_row"] == 2
        assert _snapshot(db, run) == before


def test_full_source_with_missing_org_report_fails_with_field_gaps(compat_env):
    """B2：全源可读但原生值无法复原 → FAILED + 可定位到发生与字段的缺口。"""
    CompatRerunError, trigger_compat_rerun = _api()
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        name = _event_name()
        dle, sig, _occ = _dle(
            db, run, serial="SN-GAP", event=name, state="ARCHIVED",
            remote_path=str(_event_dir(root, "devices", str(run.id), name)),
        )
        round_id = _seed_native_round(db, root, run, [dle], drop_org=True)
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[dle.id],
                 round_id=round_id)
        db.commit()
        run_id = run.id

    with factory() as db:
        outcome = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin", delivery_version_no=1,
        )
        db.commit()
        assert outcome.rerun.status == facts.COMPAT_RERUN_STATUS_FAILED
        assert "native values unreconstructable" in (outcome.rerun.error or "")
        assert outcome.rerun.coverage_scope["is_partial"] is False
        assert outcome.rerun.coverage_scope["covered_events"] == 1
        gaps = outcome.rerun.coverage_scope["field_gaps"]
        assert len(gaps) == 1
        assert gaps[0]["dle_id"] == str(dle.id)
        assert gaps[0]["signature_id"] == sig.id
        assert "ExpTime" in gaps[0]["fields"]
        assert "Detail" in gaps[0]["fields"]
        assert "Rom_Ram" in gaps[0]["fields"]
        manifest = json.loads(Path(outcome.rerun.manifest_path).read_text("utf-8"))
        assert manifest["registered_status"] == facts.COMPAT_RERUN_STATUS_FAILED
        assert manifest["field_gaps"] == gaps
        rows = _read_report(outcome.rerun.merge_xls_path)
        assert rows[0]["Path"] != ""
        assert rows[0]["ExpTime"] == ""
        assert rows[0]["Detail"] == ""


def test_substitute_row_fields_come_from_substitute_occurrence(compat_env):
    """B2 补充：替补发生时 Path/ExpTime 等来自同一替补（时间不得错绑原代表）。"""
    CompatRerunError, trigger_compat_rerun = _api()
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        rep_name = _event_name()
        # 代表 PRUNED 且无任何中心/本地源可用 → 缺源。
        rep, sig, _occ = _dle(
            db, run, serial="SN-REP", event=rep_name, state="PRUNED",
        )
        sub_name = _event_name()
        sub = DeviceLogEvent(
            serial="SN-SUB", platform="MTK", event_type="JE", state="ARCHIVED",
            local_path=f"/data/aee/{COLLECTION_FOLDER}/SN-SUB/aee_exp/{sub_name}",
            remote_path=str(_event_dir(root, "devices", str(run.id), sub_name)),
            host_id=HOST, plan_run_id=run.id,
            occurrence_key="sub-occ", detected_at=rep.detected_at,
        )
        db.add(sub)
        db.flush()
        _extra_occurrence(db, run, sig, sub, occurrence_key="sub-occ")
        round_id = _seed_native_round(db, root, run, [rep, sub])
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[rep.id, sub.id],
                 round_id=round_id)
        before = _snapshot(db, run)
        db.commit()
        run_id = run.id

    with factory() as db:
        outcome = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin", delivery_version_no=1,
        )
        db.commit()
        assert outcome.rerun.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert outcome.rerun.is_partial is True
        assert [item["dle_id"] for item in outcome.rerun.missing_items] == [str(rep.id)]
        rows = _read_report(outcome.rerun.merge_xls_path)
        assert len(rows) == 1
        # Path 与 ExpTime 必须来自同一替补发生（sub 的 org 行，Nov 17）。
        assert rows[0]["Path"].endswith(f"aee_exp/{sub_name}")
        assert rows[0]["ExpTime"] == "Sun Nov 17 10:40:46 CST 2026"
        manifest = json.loads(Path(outcome.rerun.manifest_path).read_text("utf-8"))
        binding_row = manifest["binding"]["rows"][0]
        assert binding_row["dle_id"] == str(sub.id)
        assert binding_row["is_substitute"] is True
        assert manifest["source_records"][0]["dle_id"] == str(sub.id)
        assert _snapshot(db, run) == before


def test_pruned_without_center_copy_is_partial_and_never_uses_local(compat_env):
    trigger_compat_rerun = _api()[1]
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        local_root = root / "agent-local"
        local_root.mkdir()
        pruned, sig, _occ = _dle(
            db, run, serial="SN-PRUNED", event="evt-pruned", state="PRUNED",
            local_path=str(_event_dir(local_root, "SN-PRUNED", "evt-pruned")),
        )
        round_id = _seed_native_round(db, root, run, [pruned])
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[pruned.id],
                 round_id=round_id)
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
    trigger_compat_rerun = _api()[1]
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        pruned, sig, _occ = _dle(
            db, run, serial="SN-PRUNED-EVID", event="evt-pruned-evidence",
            state="PRUNED",
        )
        _publication(db, root, pruned, run)
        round_id = _seed_native_round(db, root, run, [pruned])
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[pruned.id],
                 round_id=round_id)
        db.commit()
        run_id = run.id

    with factory() as db:
        outcome = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin",
            delivery_version_no=1,
        )
        db.commit()
        assert outcome.rerun.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert outcome.rerun.is_partial is False
        assert outcome.rerun.source_records[0]["source_kind"] == "center_evidence_verified"


def test_duplicate_request_replays_without_new_artifacts_or_counts(compat_env):
    trigger_compat_rerun = _api()[1]
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        dle, sig, _occ = _dle(
            db, run, serial="SN-REPLAY", event=_event_name(),
            remote_path=str(_event_dir(root, "devices", str(run.id), "evt-replay")),
        )
        round_id = _seed_native_round(db, root, run, [dle])
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[dle.id],
                 round_id=round_id)
        before = _snapshot(db, run)
        db.commit()
        run_id = run.id

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
    CompatRerunError, trigger_compat_rerun = _api()
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        dle, sig, _occ = _dle(
            db, run, serial="SN-RETRY", event=_event_name(),
            remote_path=str(_event_dir(root, "devices", str(run.id), "evt-retry")),
        )
        round_id = _seed_native_round(db, root, run, [dle])
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[dle.id],
                 round_id=round_id)
        before = _snapshot(db, run)
        db.commit()
        run_id = run.id

    def broken_copy(_src, _dst, **_kwargs):
        raise OSError("copy failed")

    with factory() as db:
        with pytest.raises(CompatRerunError):
            trigger_compat_rerun(
                db, plan_run_id=run_id, platform=PLATFORM,
                requested_by="owner", requester_role="admin",
                request_key="retry-me", delivery_version_no=1,
                copy_fn=broken_copy,
            )
        db.commit()

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
    trigger_compat_rerun = _api()[1]
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        event_one = _event_dir(root, "devices", str(run.id), "evt-one")
        event_two = _event_dir(root, "devices", str(run.id), "evt-two")
        first, sig, _occ = _dle(
            db, run, serial="SN-V1", event=_event_name(),
            remote_path=str(event_one), build_version="V1",
        )
        second, _sig2, _occ2 = _dle(
            db, run, serial="SN-V2", event=_event_name(),
            remote_path=str(event_two), build_version="V2",
        )
        extra_path = _event_dir(root, "devices", str(run.id), "evt-two-b")
        extra = DeviceLogEvent(
            serial="SN-V2B", platform="MTK", event_type="JE", state="ARCHIVED",
            local_path=f"/data/aee/{COLLECTION_FOLDER}/SN-V2B/aee_exp/evt-two-b",
            remote_path=str(extra_path), host_id=HOST, plan_run_id=run.id,
            occurrence_key="v2-extra", detected_at=second.detected_at,
        )
        db.add(extra)
        db.flush()
        _extra_occurrence(db, run, sig, extra, occurrence_key="v2-extra")
        round_id = _seed_native_round(db, root, run, [first, second, extra])
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[first.id],
                 round_id=round_id)
        _version(
            db, run, no=2, issue_ids=[sig.id, _sig2.id],
            event_ids=[first.id, second.id, extra.id], round_id=round_id,
        )
        db.commit()
        run_id = run.id

    with factory() as db:
        v1 = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin",
            request_key="v1", delivery_version_no=1,
        )
        db.commit()
        assert v1.rerun.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        rows = _read_report(v1.rerun.merge_xls_path)
        assert len(rows) == 1
        assert rows[0]["Count"] == 1
        assert rows[0]["DeviceCount"] == 1

    with factory() as db:
        v2 = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin",
            request_key="v2", delivery_version_no=2,
        )
        db.commit()
        assert v2.rerun.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        rows = _read_report(v2.rerun.merge_xls_path)
        assert len(rows) == 2
        assert (rows[0]["Id"], rows[1]["Id"]) == (0, 1)
        assert rows[0]["Count"] == 2
        assert rows[0]["DeviceCount"] == 2
        assert rows[1]["Count"] == 1
        assert rows[1]["DeviceCount"] == 1


def test_same_key_retry_uses_bound_version_when_latest_advances(compat_env):
    """B3：同 key 省略版本 → 沿用首次接受的 v1，即使最新已推进到 v2。"""
    CompatRerunError, trigger_compat_rerun = _api()
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        name_one = _event_name()
        first, sig, _occ = _dle(
            db, run, serial="SN-B3A", event=name_one,
            remote_path=str(_event_dir(root, "devices", str(run.id), name_one)),
        )
        name_two = _event_name()
        second, _sig2, _occ2 = _dle(
            db, run, serial="SN-B3B", event=name_two,
            remote_path=str(_event_dir(root, "devices", str(run.id), name_two)),
        )
        round_id = _seed_native_round(db, root, run, [first, second])
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[first.id],
                 round_id=round_id)
        _version(
            db, run, no=2, issue_ids=[sig.id, _sig2.id],
            event_ids=[first.id, second.id], round_id=round_id,
        )
        _seed_failed_registration(db, run, key="vb", version_no=1)
        db.commit()
        run_id, first_id = run.id, first.id

    with factory() as db:
        outcome = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin", request_key="vb",
        )
        db.commit()
        assert outcome.rerun.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert outcome.rerun.attempts == 2
        assert outcome.rerun.source_delivery_version_no == 1
        # 实际复制的发生只属于 v1 范围。
        assert outcome.rerun.coverage_scope["covered_dle_ids"] == [str(first_id)]
        manifest = json.loads(Path(outcome.rerun.manifest_path).read_text("utf-8"))
        assert manifest["source_delivery_version_no"] == 1
        assert manifest["binding"]["delivery_version_no"] == 1
        assert manifest["binding"]["event_ids"] == [str(first_id)]
        rows = _read_report(outcome.rerun.merge_xls_path)
        assert len(rows) == 1 and rows[0]["Count"] == 1


def test_same_key_explicit_other_version_rejected_before_copy(compat_env):
    """B3：同 key 显式另一版本 → 复制前 409，登记行不受影响。"""
    CompatRerunError, trigger_compat_rerun = _api()
    from backend.services import compat_rerun as compat_module

    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        name_one = _event_name()
        first, sig, _occ = _dle(
            db, run, serial="SN-B3C", event=name_one,
            remote_path=str(_event_dir(root, "devices", str(run.id), name_one)),
        )
        name_two = _event_name()
        second, _sig2, _occ2 = _dle(
            db, run, serial="SN-B3D", event=name_two,
            remote_path=str(_event_dir(root, "devices", str(run.id), name_two)),
        )
        round_id = _seed_native_round(db, root, run, [first, second])
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[first.id],
                 round_id=round_id)
        _version(
            db, run, no=2, issue_ids=[sig.id, _sig2.id],
            event_ids=[first.id, second.id], round_id=round_id,
        )
        # 预置已完成登记（绑定 v1），验证 409 不触碰任何状态。
        bound = facts.record_compat_rerun(
            db, plan_run_id=run.id, platform=PLATFORM, request_key="vb",
            requested_by="owner", source_delivery_version_no=1,
        )
        facts.update_compat_rerun(
            db, row=bound, status=facts.COMPAT_RERUN_STATUS_COMPLETED,
            attempts=1, completed_at=datetime.now(timezone.utc),
        )
        db.commit()
        run_id = run.id

    copy_calls = {"n": 0}
    original_copy = compat_module.copytree_no_symlinks

    def spying_copy(src, dst, **kwargs):
        copy_calls["n"] += 1
        return original_copy(src, dst, **kwargs)

    compat_module.copytree_no_symlinks = spying_copy
    try:
        with factory() as db:
            with pytest.raises(Exception) as rejected:
                trigger_compat_rerun(
                    db, plan_run_id=run_id, platform=PLATFORM,
                    requested_by="owner", requester_role="admin",
                    request_key="vb", delivery_version_no=2,
                )
            db.commit()
    finally:
        compat_module.copytree_no_symlinks = original_copy
    assert rejected.value.status_code == 409
    assert "bound to delivery version v1" in rejected.value.detail
    assert copy_calls["n"] == 0

    with factory() as db:
        row = facts.get_compat_rerun_by_request(
            db, plan_run_id=run_id, platform=PLATFORM, request_key="vb",
        )
        assert row.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert row.attempts == 1
        assert row.source_delivery_version_no == 1


def test_new_key_explicit_v2_executes_independently(compat_env):
    """B3：更换交付版本使用新 key，按 v2 独立执行，计数/台账/manifest 同版本。"""
    CompatRerunError, trigger_compat_rerun = _api()
    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        name_one = _event_name()
        first, sig, _occ = _dle(
            db, run, serial="SN-B3E", event=name_one,
            remote_path=str(_event_dir(root, "devices", str(run.id), name_one)),
        )
        name_two = _event_name()
        second, _sig2, _occ2 = _dle(
            db, run, serial="SN-B3F", event=name_two,
            remote_path=str(_event_dir(root, "devices", str(run.id), name_two)),
        )
        round_id = _seed_native_round(db, root, run, [first, second])
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[first.id],
                 round_id=round_id)
        _version(
            db, run, no=2, issue_ids=[sig.id, _sig2.id],
            event_ids=[first.id, second.id], round_id=round_id,
        )
        db.commit()
        run_id, first_id, second_id = run.id, first.id, second.id

    with factory() as db:
        v1 = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin",
            request_key="k1", delivery_version_no=1,
        )
        db.commit()
        assert v1.rerun.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert v1.rerun.coverage_scope["covered_dle_ids"] == [str(first_id)]

    with factory() as db:
        v2 = trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin",
            request_key="k2", delivery_version_no=2,
        )
        db.commit()
        assert v2.rerun.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert v2.rerun.source_delivery_version_no == 2
        assert v2.rerun.coverage_scope["covered_dle_ids"] == sorted(
            [str(first_id), str(second_id)]
        )
        manifest = json.loads(Path(v2.rerun.manifest_path).read_text("utf-8"))
        assert manifest["source_delivery_version_no"] == 2
        assert manifest["binding"]["delivery_version_no"] == 2
        assert manifest["binding"]["event_ids"] == [str(first_id), str(second_id)]
        rows = _read_report(v2.rerun.merge_xls_path)
        assert len(rows) == 2
        assert rows[0]["Count"] == 1
        assert rows[1]["Count"] == 1


def test_concurrent_retry_single_copy_two_sessions(compat_env):
    """B1：两个独立 PG 会话并发重试同一 FAILED 行，实际复制只发生一次。"""
    CompatRerunError, trigger_compat_rerun = _api()
    from backend.services import compat_rerun as compat_module

    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        name = _event_name()
        dle, sig, _occ = _dle(
            db, run, serial="SN-CC", event=name,
            remote_path=str(_event_dir(root, "devices", str(run.id), name)),
        )
        round_id = _seed_native_round(db, root, run, [dle])
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[dle.id],
                 round_id=round_id)
        _seed_failed_registration(db, run, key="cc", version_no=1)
        db.commit()
        run_id = run.id

    copy_calls = {"n": 0}
    call_lock = threading.Lock()
    original_copy = compat_module.copytree_no_symlinks

    def slow_copy(src, dst, **kwargs):
        with call_lock:
            copy_calls["n"] += 1
        time.sleep(0.4)  # 让 B 的行锁领取与 A 的事务重叠
        return original_copy(src, dst, **kwargs)

    results: dict[str, dict] = {}

    def attempt(name):
        try:
            with factory() as db:
                outcome = trigger_compat_rerun(
                    db, plan_run_id=run_id, platform=PLATFORM,
                    requested_by="owner", requester_role="admin",
                    request_key="cc",
                )
                db.commit()
                results[name] = {
                    "replayed": outcome.replayed,
                    "status": outcome.rerun.status,
                    "attempts": outcome.rerun.attempts,
                }
        except Exception as exc:  # noqa: BLE001 — 探针按类型归类
            results[name] = {"error": getattr(exc, "status_code", type(exc).__name__)}

    compat_module.copytree_no_symlinks = slow_copy
    threads = []
    try:
        thread_a = threading.Thread(target=attempt, args=("a",))
        thread_b = threading.Thread(target=attempt, args=("b",))
        threads = [thread_a, thread_b]
        thread_a.start()
        time.sleep(0.15)  # 确保 A 先领取并进入复制
        thread_b.start()
        thread_a.join(timeout=20)
        thread_b.join(timeout=20)
    finally:
        compat_module.copytree_no_symlinks = original_copy
    assert not any(thread.is_alive() for thread in threads), results
    assert copy_calls["n"] == 1, results
    assert results["a"]["replayed"] is False
    assert results["a"]["status"] == facts.COMPAT_RERUN_STATUS_COMPLETED
    assert results["b"]["replayed"] is True
    with factory() as db:
        row = facts.get_compat_rerun_by_request(
            db, plan_run_id=run_id, platform=PLATFORM, request_key="cc",
        )
        assert row.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert row.attempts == 2
        assert db.query(_model()).filter_by(plan_run_id=run_id).count() == 1


def test_late_failure_write_preserves_committed_completion(compat_env):
    """B1：A 失败后暂停、B 成功提交、A 再登记失败 → 最终仍为 B 的 COMPLETED。"""
    CompatRerunError, trigger_compat_rerun = _api()
    from backend.services import compat_rerun as compat_module

    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        name = _event_name()
        dle, sig, _occ = _dle(
            db, run, serial="SN-LATE", event=name,
            remote_path=str(_event_dir(root, "devices", str(run.id), name)),
        )
        round_id = _seed_native_round(db, root, run, [dle])
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[dle.id],
                 round_id=round_id)
        _seed_failed_registration(db, run, key="late", version_no=1)
        db.commit()
        run_id = run.id

    a_rolled_back = threading.Event()
    release_a = threading.Event()
    b_done = threading.Event()
    original_record = compat_module._record_failed_registration

    def pausing_record(**kwargs):
        # A 失败后停在「主事务已回滚、独立登记前」的交错点。
        a_rolled_back.set()
        release_a.wait(timeout=15)
        return original_record(**kwargs)

    def broken_copy(_src, _dst, **_kwargs):
        raise OSError("copy failed")

    results: dict[str, dict] = {}

    def attempt_a():
        try:
            with factory() as db:
                trigger_compat_rerun(
                    db, plan_run_id=run_id, platform=PLATFORM,
                    requested_by="owner", requester_role="admin",
                    request_key="late", copy_fn=broken_copy,
                )
                db.commit()
                results["a"] = {"ok": True}
        except Exception as exc:  # noqa: BLE001 — 探针按类型归类
            results["a"] = {"error": getattr(exc, "status_code", type(exc).__name__)}

    def attempt_b():
        with factory() as db:
            outcome = trigger_compat_rerun(
                db, plan_run_id=run_id, platform=PLATFORM,
                requested_by="owner", requester_role="admin", request_key="late",
            )
            db.commit()
            results["b"] = {
                "replayed": outcome.replayed,
                "attempts": outcome.rerun.attempts,
            }
        b_done.set()

    compat_module._record_failed_registration = pausing_record
    threads = []
    try:
        thread_a = threading.Thread(target=attempt_a)
        threads = [thread_a]
        thread_a.start()
        assert a_rolled_back.wait(timeout=15)
        thread_b = threading.Thread(target=attempt_b)
        threads.append(thread_b)
        thread_b.start()
        assert b_done.wait(timeout=20)
        release_a.set()
        thread_a.join(timeout=20)
    finally:
        compat_module._record_failed_registration = original_record
    assert not any(thread.is_alive() for thread in threads), results
    assert results["a"] == {"error": 500}, results
    assert results["b"]["replayed"] is False
    assert results["b"]["attempts"] == 2

    with factory() as db:
        row = facts.get_compat_rerun_by_request(
            db, plan_run_id=run_id, platform=PLATFORM, request_key="late",
        )
        # B 已提交的完成状态、次数与产物路径完整保留，未被 A 的迟到失败改写。
        assert row.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert row.attempts == 2
        assert row.error is None
        assert row.completed_at is not None
        assert row.merge_xls_path and row.manifest_path
        manifest = json.loads(Path(row.manifest_path).read_text("utf-8"))
        assert manifest["registered_status"] == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert manifest["coverage_scope"]["covered_events"] == 1


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
    """真实路由失败→失败登记持久化→同 key 重试复用同一行并递增 attempts。"""
    CompatRerunError, _trigger = _api()
    from backend.services import compat_rerun as compat_module

    factory, root = compat_env
    with factory() as db:
        run = _run(db)
        name = _event_name()
        dle, sig, _occ = _dle(
            db, run, serial="SN-ROUTE", event=name,
            remote_path=str(_event_dir(root, "devices", str(run.id), name)),
        )
        round_id = _seed_native_round(db, root, run, [dle])
        _version(db, run, no=1, issue_ids=[sig.id], event_ids=[dle.id],
                 round_id=round_id)
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
    CompatRerunError, trigger_compat_rerun = _api()
    factory, _root = compat_env
    with factory() as db:
        run = _run(db)
        db.commit()
        run_id = run.id

    with factory() as db:
        with pytest.raises(Exception) as forbidden:
            trigger_compat_rerun(
                db, plan_run_id=run_id, platform=PLATFORM,
                requested_by="user", requester_role="user",
            )
        assert forbidden.value.status_code == 403

        from backend.models.issue_fact import PartitionMode

        db.query(PartitionMode).filter_by(plan_run_id=run_id).delete()
        db.commit()
        with pytest.raises(Exception) as not_dedup:
            trigger_compat_rerun(
                db, plan_run_id=run_id, platform=PLATFORM,
                requested_by="owner", requester_role="admin",
            )
        assert not_dedup.value.status_code == 409
