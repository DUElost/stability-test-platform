"""B8 G8 实测验证：按交付版本绑定提单、FAILED 交付与受控兼容补跑（#4023 / #3800）。

使用隔离 PG16（testcontainers）、临时文件系统及真实 RunConsole 子进程替身工具，
端到端验证：
1. 按已登记 DeliveryVersion 绑定提单（同链两个 run、同 run 多版本、跨 run/平台/版本拒绝、
   独立可写工作区、快速结束/启动失败/重复回写、可验证归因 vs 无证据缺口不猜配、
   upload 与 plan_run 现行入口回归）；
2. FAILED PlanRun 交付与零自动提单（§11 场景 11：DEDUP_FIRST 继续最终轮/代表发布/
   交付恢复、自动提单清单与自动 JiraRun 为零、人工查看/下载/显式触发可用、CURRENT 按现行）；
3. 受控兼容补跑（§11 场景 10：Owner-only 鉴权、非 DEDUP_FIRST 拒绝、ARCHIVED/PRUNED、
   全量/部分源缺失清单与非全量声明、重复请求幂等与失败重试、DLE/交付版本/累计发生不变、
   重复建单消费拦截）。
"""

from __future__ import annotations

import json
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import xlrd
from sqlalchemy.orm import sessionmaker

from backend.core.security import create_access_token
from backend.core.settings.scheduler import SchedulerSettings
from backend.models.device_log_event import DeviceLogEvent
from backend.models.enums import HostStatus, PlanRunStatus
from backend.models.host import Host
from backend.models.issue_fact import (
    DeliveryVersion,
    IssueSignature,
)
from backend.models.jira_run import JiraOccurrenceLink, JiraRun
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.models.user import User
from backend.scheduler import cron_scheduler
from backend.services import (
    compat_rerun,
    delivery_download,
    evidence_publication,
    issue_fact_store as facts,
    jira_delivery,
    shadow_delivery,
    shadow_merge,
    shadow_scan_rounds,
)
from backend.services.run_console import RunConsole, RunConsoleError

FOLDER_A = "MLD-NX3-P535AA-HONOR-NX3-16-1031V78_1031_MonkeyAEEinfo"
FOLDER_B = "MLD-NX3-P535AA-HONOR-NX3-16-1102V80_1102_MonkeyAEEinfo"
SERIAL_1 = "AZ6Q100001"
SERIAL_2 = "AZ6Q100002"
EVENT_DIR_1 = "2026_1101_100000_101_db.74.ANR"
EVENT_DIR_2 = "2026_1101_100500_202_db.74.JE"
EVENT_DIR_3 = "2026_1102_110000_303_db.74.NE"


@pytest.fixture(autouse=True)
def _reset_console():
    RunConsole._reset_for_tests()
    yield
    RunConsole._reset_for_tests()


@pytest.fixture
def env_roots(tmp_path, monkeypatch, db_session):
    """隔离存储根 + 隔离 Jira work_dir + SessionLocal 绑定同 PG16 引擎。"""
    nfs_root = tmp_path / "nfs"
    nfs_root.mkdir(parents=True, exist_ok=True)
    cp_root = tmp_path / "cp"
    cp_root.mkdir(parents=True, exist_ok=True)
    work_dir = tmp_path / "jira_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    console_dir = tmp_path / "console_logs"
    console_dir.mkdir(parents=True, exist_ok=True)
    RunConsole.instance().configure(log_root=str(console_dir))

    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    for mod in (shadow_delivery, evidence_publication, shadow_scan_rounds, shadow_merge):
        monkeypatch.setattr(mod, "SessionLocal", factory)
    monkeypatch.setattr("backend.api.routes.dedup.SessionLocal", factory)
    monkeypatch.setattr(cron_scheduler, "SessionLocal", factory)

    from backend.core import artifact_paths, storage_root

    for mod in (
        shadow_delivery,
        delivery_download,
        jira_delivery,
        compat_rerun,
        evidence_publication,
        shadow_scan_rounds,
        artifact_paths,
        storage_root,
    ):
        if hasattr(mod, "resolve_shared_storage_root"):
            monkeypatch.setattr(mod, "resolve_shared_storage_root", lambda r=str(nfs_root): r)
    monkeypatch.setattr(artifact_paths, "resolve_control_plane_storage_root", lambda: str(cp_root))
    monkeypatch.setattr(artifact_paths, "resolve_legacy_log_storage_root", lambda: "")

    settings = SchedulerSettings(stp_scan_round_shadow_enabled=True)
    monkeypatch.setattr(shadow_delivery, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(evidence_publication, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(shadow_scan_rounds, "get_scheduler_settings", lambda: settings)

    monkeypatch.setenv("STP_DEDUP_WORK_DIR", str(work_dir))
    return SimpleNamespace(nfs_root=nfs_root, cp_root=cp_root, work_dir=work_dir)


@pytest.fixture
def operator_headers(db_session):
    user = User(
        username=f"op_{uuid4().hex[:8]}",
        hashed_password="x",
        role="operator",
        is_active="Y",
    )
    db_session.add(user)
    db_session.commit()
    token = create_access_token(
        data={
            "sub": str(user.id),
            "username": user.username,
            "role": user.role,
            "ver": user.token_version,
        }
    )
    return {"Authorization": f"Bearer {token}"}


def _seed_host(db, host_id: str) -> Host:
    row = Host(
        id=host_id,
        hostname=f"{host_id}.local",
        name=host_id,
        ip="192.0.2.31",
        ip_address="192.0.2.31",
        status=HostStatus.ONLINE.value,
        last_heartbeat=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def _seed_plan_run(db, *, plan_name: str, status: str = PlanRunStatus.SUCCESS.value, root_id: int | None = None) -> PlanRun:
    plan = Plan(name=f"{plan_name}-{uuid4().hex[:6]}", created_by="pytest")
    db.add(plan)
    db.flush()
    run = PlanRun(
        plan_id=plan.id,
        status=status,
        plan_snapshot={},
        run_type="MANUAL",
        triggered_by="pytest",
        root_plan_run_id=root_id,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        ended_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    db.add(run)
    db.flush()
    if run.root_plan_run_id is None:
        run.root_plan_run_id = run.id
        db.flush()
    return run


def _make_evidence_dir(nfs_root: Path, root_run_id: int, folder: str, serial: str, event_dir: str) -> tuple[str, Path]:
    rel = f"evidence/{root_run_id}_{folder}/{serial}/{event_dir}"
    full = nfs_root / rel
    full.mkdir(parents=True, exist_ok=True)
    (full / "db.dbg").write_bytes(b"aee-evidence-payload")
    return rel, full


def _seed_dle_and_fact(
    db,
    *,
    plan_run_id: int,
    root_run_id: int,
    host_id: str,
    serial: str,
    event_type: str,
    sig_raw: str,
    norm_key: str,
    occ_key: str,
    folder: str,
    event_dir: str,
    nfs_root: Path,
    dle_state: str = "UPLOADED",
    verified_pub: bool = True,
    select_rep: bool = True,
    local_root: Path | None = None,
) -> tuple[DeviceLogEvent, int, int]:
    local_path = f"/data/aee/{folder}/{serial}/{event_dir}"
    if local_root is not None:
        ev = local_root / folder / serial / event_dir
        ev.mkdir(parents=True, exist_ok=True)
        (ev / "local.dbg").write_bytes(b"agent-local-original")
        local_path = str(ev)

    dle = DeviceLogEvent(
        id=uuid4(),
        serial=serial,
        platform="mtk",
        event_type=event_type,
        detected_at=datetime.now(timezone.utc),
        state=dle_state,
        local_path=local_path,
        plan_run_id=plan_run_id,
        host_id=host_id,
        occurrence_key=occ_key,
    )
    db.add(dle)
    db.flush()

    now = datetime.now(timezone.utc)
    sig = facts.record_issue_signature(
        db,
        signature_raw=sig_raw,
        normalized_key=norm_key,
        platform="mtk",
        event_type=event_type,
        seen_at=now,
    )
    occ = facts.record_issue_occurrence(
        db,
        signature_id=sig.id,
        serial=serial,
        observed_at=now,
        first_seen_source=facts.FIRST_SEEN_LOCAL,
        occurrence_key=occ_key,
        build_version="V78",
        risk_level="A",
        first_seen_plan_run_id=plan_run_id,
    )
    facts.link_occurrence_event(
        db, occurrence_id=occ.id, dle_id=dle.id, signature_id=sig.id,
    )
    sig_id = sig.id
    occ_id = occ.id
    if select_rep:
        facts.record_representative_selection(
            db,
            plan_run_id=plan_run_id,
            platform="mtk",
            signature_id=sig_id,
            selection_status=facts.SELECTION_SELECTED,
            evidence_status=facts.EVIDENCE_COMPLETE,
            dle_id=dle.id,
        )
    if verified_pub:
        rel, full = _make_evidence_dir(nfs_root, root_run_id, folder, serial, event_dir)
        pub = facts.record_evidence_publication(
            db,
            plan_run_id=plan_run_id,
            platform="mtk",
            dle_id=dle.id,
            root_plan_run_id=root_run_id,
            source_kind=facts.PUBLICATION_SOURCE_LOCAL,
            source_path=local_path,
            collection_folder=folder,
            naming_rule_version="aee-folder-name/v1",
            destination_rel_path=rel,
            executor_host_id=host_id,
        )
        facts.update_evidence_publication_state(
            db,
            row=pub,
            state=facts.PUBLICATION_STATE_VERIFIED,
            attempts=1,
            checksum="sha256:test",
            size_bytes=(full / "db.dbg").stat().st_size,
        )
    return dle, sig_id, occ_id


def _register_dv(
    db,
    *,
    plan_run_id: int,
    version_no: int,
    issue_ids: list[int],
    event_ids: list[str],
    nfs_root: Path,
    issues_payload: list[dict] | None = None,
) -> DeliveryVersion:
    dv = DeliveryVersion(
        plan_run_id=plan_run_id,
        platform="mtk",
        version_no=version_no,
        software_versions=["V78"],
        round_ids=[version_no],
        basis_rounds=[{"round_id": version_no, "merge_generation": 1}],
        effective_coverage=[{"host_id": "h1", "platform": "mtk", "covered": True, "round_ids": [version_no]}],
        issue_ids=issue_ids,
        event_ids=event_ids,
        stats_snapshot_version=f"v{version_no}",
        pending_items=[],
        gaps=[],
        primary_folders=[],
    )
    db.add(dv)
    db.flush()
    export_path = shadow_delivery.delivery_export_path(str(nfs_root), plan_run_id, "mtk", version_no)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    computed_issues = issues_payload
    if computed_issues is None:
        issue_set = set(issue_ids)
        computed_issues = [
            item for item in facts.list_partition_delivery_issues(db, plan_run_id=plan_run_id, platform="mtk")
            if int(item["signature_id"]) in issue_set
        ]
    export_payload = {
        "schema": "stp.delivery_stats",
        "schema_version": 1,
        "plan_run_id": plan_run_id,
        "platform": "mtk",
        "version_no": version_no,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "issues": computed_issues,
        "event_ids": event_ids,
    }
    export_path.write_text(json.dumps(export_payload, ensure_ascii=False), encoding="utf-8")
    return dv


def _install_vendor_double(tool_dir: Path, capture_file: Path, *, mode: str = "unprovable") -> None:
    """安装真实可执行 Python 替身工具，记录收到的 CLI、Excel 行内容和实际 cwd。"""
    tool_dir.mkdir(parents=True, exist_ok=True)
    common = textwrap.dedent(
        f"""
        import json, os, sys, pathlib, xlrd

        CAPTURE = pathlib.Path({str(capture_file)!r})

        def record(stage, xls_path):
            book = xlrd.open_workbook(xls_path)
            sheet = book.sheet_by_name("aeeexp")
            headers = [sheet.cell_value(0, c) for c in range(sheet.ncols)]
            rows = []
            for r in range(1, sheet.nrows):
                rows.append(dict(zip(headers, [sheet.cell_value(r, c) for c in range(sheet.ncols)])))
            entry = {{
                "stage": stage,
                "cwd": os.getcwd(),
                "argv": sys.argv[1:],
                "xls_path": str(xls_path),
                "rows": rows,
                "manifest_exists": (pathlib.Path(os.getcwd()) / "manifest.json").is_file(),
            }}
            CAPTURE.parent.mkdir(parents=True, exist_ok=True)
            with CAPTURE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\\n")
            return rows
        """
    ).strip()
    (tool_dir / "double_common.py").write_text(common + "\n", encoding="utf-8")

    gen_script = textwrap.dedent(
        """
        import argparse, pathlib
        from double_common import record

        ap = argparse.ArgumentParser()
        ap.add_argument("--add-main-excel", required=True)
        ap.add_argument("--set-project-key", default=None)
        args = ap.parse_args()
        record("upload_list", args.add_main_excel)
        print("generated upload list for", args.add_main_excel, flush=True)
        """
    ).strip()
    (tool_dir / "generate_transsion_jira_upload_list.py").write_text(gen_script + "\n", encoding="utf-8")

    if mode == "verified":
        create_body = textwrap.dedent(
            """
            import argparse, json, pathlib
            from double_common import record

            ap = argparse.ArgumentParser()
            ap.add_argument("--add-excel-file", required=True)
            ap.add_argument("--dry-run", action="store_true")
            ap.add_argument("--reporter", default=None)
            args = ap.parse_args()
            record("create", args.add_excel_file)
            ws = pathlib.Path.cwd()
            manifest = json.loads((ws / "manifest.json").read_text(encoding="utf-8"))
            cands = manifest.get("attribution_candidates", [])
            links = []
            keys = []
            for idx, c in enumerate(cands, start=1):
                key = f"STB-{100 + idx}"
                keys.append(key)
                links.append({
                    "issue_key": key,
                    "signature_id": c["signature_id"],
                    "occurrence_id": c["occurrence_id"],
                    "evidence_source": "vendor_structured_output_v1",
                })
                print(f"第 {idx} 行创建成功: {key}", flush=True)
            # Also include a rogue out-of-scope candidate in attribution_mode=rogue if requested
            if (ws / "trigger_rogue.flag").exists():
                links.append({
                    "issue_key": "STB-999",
                    "signature_id": 999999,
                    "occurrence_id": 999999,
                    "evidence_source": "rogue",
                })
                print("第 99 行创建成功: STB-999", flush=True)
            (ws / "attribution.json").write_text(json.dumps({"links": links}), encoding="utf-8")
            """
        ).strip()
    elif mode == "fast_exit":
        create_body = textwrap.dedent(
            """
            import argparse, sys
            from double_common import record

            ap = argparse.ArgumentParser()
            ap.add_argument("--add-excel-file", required=True)
            ap.add_argument("--dry-run", action="store_true")
            ap.add_argument("--reporter", default=None)
            args = ap.parse_args()
            record("create", args.add_excel_file)
            print("第 1 行创建成功: STB-777", flush=True)
            sys.exit(0)
            """
        ).strip()
    else:
        # Matches actual stability_Jira-Automation behavior: result JSON has NO occurrence_id / signature_id!
        create_body = textwrap.dedent(
            """
            import argparse, json, pathlib
            from double_common import record

            ap = argparse.ArgumentParser()
            ap.add_argument("--add-excel-file", required=True)
            ap.add_argument("--dry-run", action="store_true")
            ap.add_argument("--reporter", default=None)
            args = ap.parse_args()
            record("create", args.add_excel_file)
            res_dir = pathlib.Path.cwd() / "result"
            res_dir.mkdir(parents=True, exist_ok=True)
            (res_dir / "transsion_jira_batch_create_result_20261116.json").write_text(
                json.dumps([{"row": 2, "status": "created", "issue_key": "STB-201"}]),
                encoding="utf-8",
            )
            print("第 1 行创建成功: STB-201", flush=True)
            """
        ).strip()

    (tool_dir / "create_transsion_jira_batch_from_excel.py").write_text(create_body + "\n", encoding="utf-8")


def _wait_jira_terminal(db, console_run_id: str, timeout: float = 8.0) -> JiraRun:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        db.expire_all()
        row = db.query(JiraRun).filter_by(console_run_id=console_run_id).first()
        st = RunConsole.instance().status(console_run_id)
        console_done = st is None or st.get("status") in {"SUCCESS", "FAILED", "CANCELED"}
        if row is not None and row.status in {"SUCCESS", "FAILED", "CANCELED"} and console_done:
            return row
        time.sleep(0.05)
    raise AssertionError(f"JiraRun {console_run_id} did not reach terminal state within {timeout}s")


def test_delivery_version_binding_two_runs_same_chain_and_multi_versions(
    client, auth_headers, db_session, env_roots, monkeypatch, tmp_path,
):
    """需求 1：同链两实际 run + 同 run 多版本 + 隔离可写工作区 + 跨边界拒绝。"""
    _seed_host(db_session, "h1")
    run_a = _seed_plan_run(db_session, plan_name="chain")
    run_b = _seed_plan_run(db_session, plan_name="chain", root_id=run_a.id)
    facts.record_partition_mode(db_session, plan_run_id=run_a.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)
    facts.record_partition_mode(db_session, plan_run_id=run_b.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)

    dle1, sig1, _occ1 = _seed_dle_and_fact(
        db_session,
        plan_run_id=run_a.id,
        root_run_id=run_a.id,
        host_id="h1",
        serial=SERIAL_1,
        event_type="ANR",
        sig_raw="com.app.anr.One",
        norm_key="anr_one",
        occ_key="occ-chain-a-1",
        folder=FOLDER_A,
        event_dir=EVENT_DIR_1,
        nfs_root=env_roots.nfs_root,
    )
    dle2, sig2, _occ2 = _seed_dle_and_fact(
        db_session,
        plan_run_id=run_a.id,
        root_run_id=run_a.id,
        host_id="h1",
        serial=SERIAL_2,
        event_type="JE",
        sig_raw="com.app.je.Two",
        norm_key="je_two",
        occ_key="occ-chain-a-2",
        folder=FOLDER_A,
        event_dir=EVENT_DIR_2,
        nfs_root=env_roots.nfs_root,
    )
    dle_b, sig_b, _occ_b = _seed_dle_and_fact(
        db_session,
        plan_run_id=run_b.id,
        root_run_id=run_a.id,  # 同链根！物理文件夹同根但 run 不同
        host_id="h1",
        serial=SERIAL_1,
        event_type="NE",
        sig_raw="com.app.ne.RunB",
        norm_key="ne_run_b",
        occ_key="occ-chain-b-1",
        folder=FOLDER_B,
        event_dir=EVENT_DIR_3,
        nfs_root=env_roots.nfs_root,
    )

    dv_a1 = _register_dv(
        db_session,
        plan_run_id=run_a.id,
        version_no=1,
        issue_ids=[sig1],
        event_ids=[str(dle1.id)],
        nfs_root=env_roots.nfs_root,
    )
    dv_a2 = _register_dv(
        db_session,
        plan_run_id=run_a.id,
        version_no=2,
        issue_ids=[sig1, sig2],
        event_ids=[str(dle1.id), str(dle2.id)],
        nfs_root=env_roots.nfs_root,
    )
    dv_b1 = _register_dv(
        db_session,
        plan_run_id=run_b.id,
        version_no=1,
        issue_ids=[sig_b],
        event_ids=[str(dle_b.id)],
        nfs_root=env_roots.nfs_root,
    )
    db_session.commit()

    tool_dir = tmp_path / "tool_double"
    capture_file = tmp_path / "capture.jsonl"
    _install_vendor_double(tool_dir, capture_file, mode="unprovable")
    monkeypatch.setenv("STP_JIRA_TRANSSION_PYTHON", sys.executable)
    monkeypatch.setenv("STP_JIRA_TRANSSION_DIR", str(tool_dir))

    # 1. 验收同 run 多版本（run_a v1 vs run_a v2）和同链第二 run（run_b v1）
    for target_run, target_dv, expected_keys in (
        (run_a.id, dv_a1, {"anr_one"}),
        (run_a.id, dv_a2, {"anr_one", "je_two"}),
        (run_b.id, dv_b1, {"ne_run_b"}),
    ):
        resp = client.post(
            "/api/v1/jira/runs",
            data={
                "vendor": "transsion",
                "stage": "upload_list",
                "source": "delivery_version",
                "plan_run_id": str(target_run),
                "platform": "mtk",
                "delivery_version_no": str(target_dv.version_no),
                "delivery_version_id": str(target_dv.id),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["delivery_version_id"] == target_dv.id
        assert body["delivery_version_no"] == target_dv.version_no
        assert body["workspace_dir"]
        row = _wait_jira_terminal(db_session, body["console_run_id"])
        assert row.status == "SUCCESS"
        assert row.workspace_dir == body["workspace_dir"]
        ws = Path(row.workspace_dir)
        assert ws.is_dir()
        assert not ws.is_relative_to(env_roots.nfs_root / "evidence")
        assert not ws.is_relative_to(env_roots.nfs_root / "devices")
        captures = [json.loads(line) for line in capture_file.read_text(encoding="utf-8").splitlines()]
        latest = captures[-1]
        assert latest["cwd"] == str(ws.resolve())
        assert latest["manifest_exists"] is True
        actual_keys = {r["ExpType "] for r in latest["rows"]}
        assert actual_keys == expected_keys
        for r in latest["rows"]:
            if r["Path"]:
                assert str(env_roots.nfs_root / "evidence") in r["Path"]
                assert Path(r["Path"]).is_dir()

    # 2. 跨 run / 跨平台 / 错版本均被拒绝
    cross_run = client.post(
        "/api/v1/jira/runs",
        data={
            "vendor": "transsion",
            "stage": "upload_list",
            "source": "delivery_version",
            "plan_run_id": str(run_a.id),
            "platform": "mtk",
            "delivery_version_id": str(dv_b1.id),
        },
        headers=auth_headers,
    )
    assert cross_run.status_code == 409

    cross_platform = client.post(
        "/api/v1/jira/runs",
        data={
            "vendor": "transsion",
            "stage": "upload_list",
            "source": "delivery_version",
            "plan_run_id": str(run_a.id),
            "platform": "qcom",
            "delivery_version_no": "1",
        },
        headers=auth_headers,
    )
    assert cross_platform.status_code == 409

    wrong_version = client.post(
        "/api/v1/jira/runs",
        data={
            "vendor": "transsion",
            "stage": "upload_list",
            "source": "delivery_version",
            "plan_run_id": str(run_a.id),
            "platform": "mtk",
            "delivery_version_no": "2",
            "delivery_version_id": str(dv_a1.id),
        },
        headers=auth_headers,
    )
    assert wrong_version.status_code == 409


def test_jira_attribution_verified_vs_unprovable_gaps_and_lifecycle(
    client, auth_headers, db_session, env_roots, monkeypatch, tmp_path,
):
    """需求 1：有证据写回关联；无发生证据不猜配并记缺口；快速结束/启动失败/重复回调/upload+plan_run 兼容。"""
    _seed_host(db_session, "h1")
    run_a = _seed_plan_run(db_session, plan_name="attr")
    facts.record_partition_mode(db_session, plan_run_id=run_a.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)
    dle1, sig1, occ1 = _seed_dle_and_fact(
        db_session,
        plan_run_id=run_a.id,
        root_run_id=run_a.id,
        host_id="h1",
        serial=SERIAL_1,
        event_type="ANR",
        sig_raw="com.app.anr.Verified",
        norm_key="anr_verified",
        occ_key="occ-attr-1",
        folder=FOLDER_A,
        event_dir=EVENT_DIR_1,
        nfs_root=env_roots.nfs_root,
    )
    dv1 = _register_dv(
        db_session,
        plan_run_id=run_a.id,
        version_no=1,
        issue_ids=[sig1],
        event_ids=[str(dle1.id)],
        nfs_root=env_roots.nfs_root,
    )

    # 现行 PlanRunArtifact 供 source=plan_run 回归校验
    current_run = _seed_plan_run(db_session, plan_name="current-legacy")
    cur_xls = env_roots.nfs_root / "dedup" / str(current_run.id) / "Result_MergeFiles_test.xls"
    jira_delivery.write_dedup_xls(
        cur_xls,
        [{"Id": "1", "Path": "", "Version": "V1", "ExpTime": "", "ExpClass": "ANR", "ExpType ": "cur_anr", "CurProcess": "p", "Package": "", "Detail": "d", "CausedBy": "", "Count": 1, "DeviceId": SERIAL_1, "Device Count": 1}],
    )
    cur_art = PlanRunArtifact(
        plan_run_id=current_run.id,
        artifact_type="merge_result_xls",
        storage_uri=str(cur_xls),
    )
    db_session.add(cur_art)
    db_session.commit()

    tool_dir = tmp_path / "tool_attr"
    capture_file = tmp_path / "attr_capture.jsonl"
    monkeypatch.setenv("STP_JIRA_TRANSSION_PYTHON", sys.executable)
    monkeypatch.setenv("STP_JIRA_TRANSSION_DIR", str(tool_dir))

    # A) 无法证明发生对应关系的传统厂商输出（无 occurrence_id / signature_id）→ 记缺口、不猜配
    _install_vendor_double(tool_dir, capture_file, mode="unprovable")
    r_unprov = client.post(
        "/api/v1/jira/runs",
        data={
            "vendor": "transsion",
            "stage": "create",
            "source": "delivery_version",
            "plan_run_id": str(run_a.id),
            "platform": "mtk",
            "delivery_version_no": "1",
        },
        headers=auth_headers,
    )
    assert r_unprov.status_code == 200
    cid_unprov = r_unprov.json()["data"]["console_run_id"]
    row_unprov = _wait_jira_terminal(db_session, cid_unprov)
    assert row_unprov.status == "SUCCESS"
    assert row_unprov.issue_keys == ["STB-201"]
    assert row_unprov.attribution_status == jira_delivery.ATTRIBUTION_UNATTRIBUTED
    assert any(
        g.get("issue_key") == "STB-201" and g.get("reason") == "vendor_result_json_lacks_occurrence_id"
        for g in row_unprov.attribution_gaps
    )
    assert db_session.query(JiraOccurrenceLink).filter_by(jira_run_id=row_unprov.id).count() == 0
    sig_row = db_session.get(IssueSignature, sig1)
    assert sig_row.issue_keys == []

    # B) 有可验证结构化归因输出 → 写回 JiraOccurrenceLink + IssueSignature.issue_keys，并测重复回调幂等
    _install_vendor_double(tool_dir, capture_file, mode="verified")
    r_ver = client.post(
        "/api/v1/jira/runs",
        data={
            "vendor": "transsion",
            "stage": "create",
            "source": "delivery_version",
            "plan_run_id": str(run_a.id),
            "platform": "mtk",
            "delivery_version_id": str(dv1.id),
        },
        headers=auth_headers,
    )
    assert r_ver.status_code == 200
    cid_ver = r_ver.json()["data"]["console_run_id"]
    row_ver = _wait_jira_terminal(db_session, cid_ver)
    assert row_ver.attribution_status == jira_delivery.ATTRIBUTION_VERIFIED
    assert row_ver.attribution_gaps == []
    links = db_session.query(JiraOccurrenceLink).filter_by(jira_run_id=row_ver.id).all()
    assert len(links) == 1
    assert links[0].signature_id == sig1
    assert links[0].occurrence_id == occ1
    assert links[0].issue_key == "STB-101"
    db_session.expire_all()
    assert "STB-101" in db_session.get(IssueSignature, sig1).issue_keys

    # HTTP 详情端返回关联列表
    detail_resp = client.get(f"/api/v1/jira/runs/{cid_ver}/record", headers=auth_headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["data"]["occurrence_links"][0]["issue_key"] == "STB-101"

    # 重复回写幂等（第二次回调不会重复写 link 或 issue_keys）
    replayed = jira_delivery.record_jira_run_completion(
        db_session,
        jira_run=row_ver,
        status="FAILED",
        exit_code=1,
        ended_at=datetime.now(timezone.utc),
        error="late duplicate",
        log_lines=["STB-999"],
    )
    assert replayed is False
    assert row_ver.status == "SUCCESS"
    assert db_session.query(JiraOccurrenceLink).filter_by(jira_run_id=row_ver.id).count() == 1

    # C) 子进程秒退（#1084 先落库后启动）
    _install_vendor_double(tool_dir, capture_file, mode="fast_exit")
    r_fast = client.post(
        "/api/v1/jira/runs",
        data={
            "vendor": "transsion",
            "stage": "create",
            "source": "delivery_version",
            "plan_run_id": str(run_a.id),
            "platform": "mtk",
            "delivery_version_no": "1",
        },
        headers=auth_headers,
    )
    assert r_fast.status_code == 200
    row_fast = _wait_jira_terminal(db_session, r_fast.json()["data"]["console_run_id"])
    assert row_fast.status == "SUCCESS"
    assert row_fast.issue_keys == ["STB-777"]

    # D) 现行 source=plan_run 和 source=upload 仍可用
    r_cur = client.post(
        "/api/v1/jira/runs",
        data={"vendor": "transsion", "stage": "upload_list", "source": "plan_run", "artifact_id": str(cur_art.id)},
        headers=auth_headers,
    )
    assert r_cur.status_code == 200
    row_cur = _wait_jira_terminal(db_session, r_cur.json()["data"]["console_run_id"])
    assert row_cur.status == "SUCCESS"

    r_up = client.post(
        "/api/v1/jira/runs",
        data={"vendor": "transsion", "stage": "create", "source": "upload"},
        files={"file": ("JIRA_Upload_List_test.xlsx", cur_xls.read_bytes(), "application/vnd.ms-excel")},
        headers=auth_headers,
    )
    assert r_up.status_code == 200
    row_up = _wait_jira_terminal(db_session, r_up.json()["data"]["console_run_id"])
    assert row_up.status == "SUCCESS"

    # E) 启动失败时先落库的行回写 FAILED，不留 RUNNING 悬挂
    failing_console = MagicMock()
    failing_console.start.side_effect = RunConsoleError("spawn failed")
    failing_console.status.return_value = None
    monkeypatch.setattr("backend.api.routes.dedup.RunConsole.instance", lambda: failing_console)
    r_fail = client.post(
        "/api/v1/jira/runs",
        data={
            "vendor": "transsion",
            "stage": "upload_list",
            "source": "delivery_version",
            "plan_run_id": str(run_a.id),
            "platform": "mtk",
            "delivery_version_no": "1",
        },
        headers=auth_headers,
    )
    assert r_fail.status_code == 500
    failed_runs = db_session.query(JiraRun).filter_by(plan_run_id=run_a.id, status="FAILED").all()
    assert len(failed_runs) == 1
    assert "spawn failed" in (failed_runs[0].error or "")


def test_failed_plan_run_delivery_continues_and_zero_auto_jira(
    client, auth_headers, db_session, env_roots, monkeypatch, tmp_path,
):
    """需求 2（§11 场景 11）：FAILED PlanRun 继续最终轮/代表发布/交付恢复，自动 Jira 为零，人工入口可用。"""
    _seed_host(db_session, "h1")
    failed_run = _seed_plan_run(db_session, plan_name="failed-dedup", status=PlanRunStatus.FAILED.value)
    # 模拟周期轮尚未建就已 FAILED 的 DEDUP_FIRST 分区 + 一个对比现行分区 PlanRun
    facts.record_partition_mode(db_session, plan_run_id=failed_run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)
    from backend.models.host import Device
    from backend.models.job import JobInstance

    dev = Device(serial=SERIAL_1, host_id="h1", platform="mtk", status="ONLINE", model="test", tags=[])
    db_session.add(dev)
    db_session.flush()
    db_session.add(JobInstance(
        plan_run_id=failed_run.id, plan_id=failed_run.plan_id, host_id="h1", device_id=dev.id, status="FAILED", pipeline_def={},
    ))

    dle, sig_id, _occ_id = _seed_dle_and_fact(
        db_session,
        plan_run_id=failed_run.id,
        root_run_id=failed_run.id,
        host_id="h1",
        serial=SERIAL_1,
        event_type="ANR",
        sig_raw="com.app.anr.FailedRun",
        norm_key="anr_failed_run",
        occ_key="occ-failed-1",
        folder=FOLDER_A,
        event_dir=EVENT_DIR_1,
        nfs_root=env_roots.nfs_root,
        verified_pub=False,
        select_rep=False,
    )

    failed_current_run = _seed_plan_run(db_session, plan_name="failed-current", status=PlanRunStatus.FAILED.value)
    db_session.commit()

    # 1. 影子调度为未建轮次的 FAILED DEDUP_FIRST 分区补出 FINAL 轮
    dispatched: list[tuple[str, dict]] = []
    monkeypatch.setattr(shadow_scan_rounds, "enqueue_sync", lambda fn, **kw: dispatched.append((fn, kw)))
    shadow_scan_rounds.sweep_shadow_scan_rounds()
    assert any(kw.get("plan_run_id") == failed_run.id for _, kw in dispatched)

    # 写回 h1 的 round_report 并再次 sweep 关闭 FINAL 轮
    rnd = facts.list_scan_rounds_for_read(db_session, plan_run_id=failed_run.id, platform="mtk")[0]
    from backend.agent.contracts.round_report_layout import round_report_dir, round_manifest_path

    report_dir = round_report_dir(str(env_roots.nfs_root), failed_run.id, rnd.id, "h1")
    report_dir.mkdir(parents=True, exist_ok=True)
    # 写入契约格式 empty_receipt 回执以关闭 FINAL 轮，同时已有 DLE/occurrence 触发代表与交付恢复
    manifest_p = round_manifest_path(str(env_roots.nfs_root), failed_run.id, rnd.id, "h1")
    manifest_p.parent.mkdir(parents=True, exist_ok=True)
    manifest_p.write_text(json.dumps({
        "schema_version": 1,
        "plan_run_id": failed_run.id,
        "host_id": "h1",
        "artifacts": [
            {
                "fact_round_id": rnd.id,
                "platform": "mtk",
                "empty_receipt": True,
            }
        ],
    }), encoding="utf-8")
    shadow_scan_rounds.sweep_shadow_scan_rounds()
    db_session.expire_all()
    rnd = facts.get_scan_round(db_session, round_id=rnd.id)
    assert rnd.status == facts.SCAN_ROUND_STATUS_CLOSED

    # 2. 影子 merge（含候选事件）→ 代表发布 → 交付版本登记与 D9 判定
    facts.record_representative_selection(
        db_session,
        plan_run_id=failed_run.id,
        platform="mtk",
        signature_id=sig_id,
        selection_status=facts.SELECTION_SELECTED,
        evidence_status=facts.EVIDENCE_COMPLETE,
        dle_id=dle.id,
    )
    facts.record_scan_round_merge(
        db_session,
        round_id=rnd.id,
        status="OK",
        summary={"candidates": []},
    )
    rel, full = _make_evidence_dir(env_roots.nfs_root, failed_run.id, FOLDER_A, SERIAL_1, EVENT_DIR_1)
    pub = facts.record_evidence_publication(
        db_session,
        plan_run_id=failed_run.id,
        platform="mtk",
        dle_id=dle.id,
        root_plan_run_id=failed_run.id,
        source_kind=facts.PUBLICATION_SOURCE_SPILL,
        source_path=str(full),
        collection_folder=FOLDER_A,
        naming_rule_version="aee-folder-name/v1",
        destination_rel_path=rel,
        executor_host_id=None,
    )
    # sweep_evidence_publications 应完成 FAILED run DEDUP_FIRST 的交付版本恢复与登记
    facts.update_evidence_publication_state(
        db_session,
        row=pub,
        state=facts.PUBLICATION_STATE_VERIFIED,
        attempts=1,
        checksum="sha256:spill",
        size_bytes=(full / "db.dbg").stat().st_size,
    )
    db_session.commit()

    evidence_publication.sweep_evidence_publications()
    db_session.expire_all()
    dv = facts.get_latest_delivery_version(db_session, plan_run_id=failed_run.id, platform="mtk")
    assert dv is not None and dv.version_no == 1
    decision = shadow_delivery.current_delivery_decision(db_session, plan_run_id=failed_run.id, platform="mtk")
    assert decision.status == shadow_delivery.DELIVERY_STATUS_READY

    # 3. 验证自动路径零提单清单、零自动 JiraRun
    assert jira_delivery.allow_auto_jira_for_plan_run(
        db_session, plan_run_id=failed_run.id, partition_mode=facts.PARTITION_MODE_DEDUP_FIRST,
    ) is False
    enqueued_legacy: list[int] = []
    monkeypatch.setattr(
        "backend.services.dedup_scan.enqueue_dedup_terminal_sync",
        lambda run_id, **kw: enqueued_legacy.append(run_id),
    )
    cron_scheduler.auto_archive_sweep()
    assert failed_run.id not in enqueued_legacy
    assert db_session.query(JiraRun).filter_by(plan_run_id=failed_run.id).count() == 0
    assert list(env_roots.work_dir.glob("*")) == []

    # CURRENT 对比分区在 FAILED 时按现行规则（should_trigger_dedup -> run_merge_sync 跳过）
    from backend.services import dedup_scan

    assert dedup_scan.should_trigger_dedup(failed_current_run.status) is True
    assert dedup_scan.run_merge_sync(failed_current_run.id, allow_failed=False) == "skipped_failed"

    # 4. 人工查看、下载与显式触发在 FAILED run 上仍可用
    dl_resp = client.get(
        f"/api/v1/plan-runs/{failed_run.id}/delivery/mtk/versions/1/download",
        headers=auth_headers,
    )
    assert dl_resp.status_code == 200
    assert dl_resp.json()["plan_run_id"] == failed_run.id

    ev_dl = client.get(
        f"/api/v1/plan-runs/{failed_run.id}/log-events/{dle.id}/download",
        headers=auth_headers,
    )
    assert ev_dl.status_code == 200

    tool_dir = tmp_path / "tool_failed_manual"
    capture_file = tmp_path / "failed_manual.jsonl"
    _install_vendor_double(tool_dir, capture_file, mode="verified")
    monkeypatch.setenv("STP_JIRA_TRANSSION_PYTHON", sys.executable)
    monkeypatch.setenv("STP_JIRA_TRANSSION_DIR", str(tool_dir))
    manual_resp = client.post(
        "/api/v1/jira/runs",
        data={
            "vendor": "transsion",
            "stage": "create",
            "source": "delivery_version",
            "plan_run_id": str(failed_run.id),
            "platform": "mtk",
            "delivery_version_no": "1",
        },
        headers=auth_headers,
    )
    assert manual_resp.status_code == 200
    manual_row = _wait_jira_terminal(db_session, manual_resp.json()["data"]["console_run_id"])
    assert manual_row.status == "SUCCESS"
    assert manual_row.issue_keys == ["STB-101"]


def test_controlled_compat_rerun_archived_pruned_partial_retry_and_no_dup_jira(
    client, admin_headers, operator_headers, db_session, env_roots, monkeypatch, tmp_path,
):
    """需求 3（§11 场景 10）：Owner 受控兼容补跑、ARCHIVED/PRUNED、部分源、幂等重试、不改原事实与防重复提单。"""
    _seed_host(db_session, "h1")
    run = _seed_plan_run(db_session, plan_name="compat")
    facts.record_partition_mode(db_session, plan_run_id=run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)

    local_root = tmp_path / "agent_local"
    # DLE1: PRUNED 但有中心已校验副本（EvidencePublication VERIFIED）→ 可恢复！
    dle_pruned_verified, sig1, _ = _seed_dle_and_fact(
        db_session,
        plan_run_id=run.id,
        root_run_id=run.id,
        host_id="h1",
        serial=SERIAL_1,
        event_type="ANR",
        sig_raw="com.app.anr.PrunedVerified",
        norm_key="anr_pv",
        occ_key="occ-compat-1",
        folder=FOLDER_A,
        event_dir=EVENT_DIR_1,
        nfs_root=env_roots.nfs_root,
        dle_state="PRUNED",
        verified_pub=True,
    )
    # DLE2: ARCHIVED 且本地原件仍在（中心未发布但仍保留本地原件）→ 可用本地原件！
    dle_archived_local, sig2, _ = _seed_dle_and_fact(
        db_session,
        plan_run_id=run.id,
        root_run_id=run.id,
        host_id="h1",
        serial=SERIAL_2,
        event_type="JE",
        sig_raw="com.app.je.ArchivedLocal",
        norm_key="je_al",
        occ_key="occ-compat-2",
        folder=FOLDER_A,
        event_dir=EVENT_DIR_2,
        nfs_root=env_roots.nfs_root,
        dle_state="ARCHIVED",
        verified_pub=False,
        local_root=local_root,
    )
    # DLE3: PRUNED 且无中心副本（已按策略回收）→ 缺失！必须列缺失清单并声明非全量
    dle_pruned_gone, sig3, _ = _seed_dle_and_fact(
        db_session,
        plan_run_id=run.id,
        root_run_id=run.id,
        host_id="h1",
        serial="AZ6Q100003",
        event_type="NE",
        sig_raw="com.app.ne.PrunedGone",
        norm_key="ne_pg",
        occ_key="occ-compat-3",
        folder=FOLDER_A,
        event_dir=EVENT_DIR_3,
        nfs_root=env_roots.nfs_root,
        dle_state="PRUNED",
        verified_pub=False,
        local_root=local_root,  # 即使本地路径名存在，因 state==PRUNED 也不得回读
    )

    dv = _register_dv(
        db_session,
        plan_run_id=run.id,
        version_no=1,
        issue_ids=[sig1, sig2, sig3],
        event_ids=[str(dle_pruned_verified.id), str(dle_archived_local.id), str(dle_pruned_gone.id)],
        nfs_root=env_roots.nfs_root,
    )
    current_run = _seed_plan_run(db_session, plan_name="compat-current")
    db_session.commit()

    before_occ_count = facts.count_partition_occurrences(db_session, plan_run_id=run.id, platform="mtk")
    assert before_occ_count == 3

    # 1. 非 Owner（operator）发起 → 403；CURRENT 分区 → 409
    deny = client.post(
        f"/api/v1/plan-runs/{run.id}/dedup/compat-rerun?platform=mtk",
        headers=operator_headers,
    )
    assert deny.status_code == 403

    wrong_mode = client.post(
        f"/api/v1/plan-runs/{current_run.id}/dedup/compat-rerun?platform=mtk",
        headers=admin_headers,
    )
    assert wrong_mode.status_code == 409

    # 2. 失败 + 重试（同 request_key）
    def _boom(_src: Path, _dst: Path) -> None:
        raise OSError("simulated nfs write error")

    with pytest.raises(compat_rerun.CompatRerunError) as exc_info:
        compat_rerun.trigger_compat_rerun(
            db_session,
            plan_run_id=run.id,
            platform="mtk",
            requested_by="owner",
            requester_role="admin",
            request_key="req-partial-retry",
            local_source_roots={"h1": str(local_root)},
            copy_fn=_boom,
        )
    assert exc_info.value.status_code == 500
    db_session.commit()
    failed_row = facts.get_compat_rerun_by_request(
        db_session, plan_run_id=run.id, platform="mtk", request_key="req-partial-retry",
    )
    assert failed_row.status == facts.COMPAT_RERUN_STATUS_FAILED
    assert failed_row.attempts == 1

    # 同 request_key 重试恢复成功（部分源：覆盖 dle1 + dle2，缺失 dle3）
    outcome = compat_rerun.trigger_compat_rerun(
        db_session,
        plan_run_id=run.id,
        platform="mtk",
        requested_by="owner",
        requester_role="admin",
        request_key="req-partial-retry",
        local_source_roots={"h1": str(local_root)},
    )
    db_session.commit()
    assert outcome.replayed is False
    row = outcome.rerun
    assert row.status == facts.COMPAT_RERUN_STATUS_COMPLETED
    assert row.attempts == 2
    assert row.is_partial is True
    assert len(row.missing_items) == 1
    assert row.missing_items[0]["dle_id"] == str(dle_pruned_gone.id)
    assert len(row.source_records) == 2
    kinds = {rec["dle_id"]: rec["source_kind"] for rec in row.source_records}
    assert kinds[str(dle_pruned_verified.id)] == "center_evidence_verified"
    assert kinds[str(dle_archived_local.id)] == "agent_local_original"

    # 验证独立现行格式产物（Result_*.xls + compat_manifest.json）
    merge_xls = Path(row.merge_xls_path)
    assert merge_xls.is_file()
    book = xlrd.open_workbook(str(merge_xls))
    sheet = book.sheet_by_name("aeeexp")
    assert sheet.nrows == 4  # 1 header + 3 issues
    manifest_data = json.loads(Path(row.manifest_path).read_text(encoding="utf-8"))
    assert manifest_data["is_partial"] is True
    assert len(manifest_data["missing_items"]) == 1

    # 3. 重复请求幂等重放
    replay_resp = client.post(
        f"/api/v1/plan-runs/{run.id}/dedup/compat-rerun?platform=mtk&request_key=req-partial-retry",
        headers=admin_headers,
    )
    assert replay_resp.status_code == 200
    assert replay_resp.json()["data"]["replayed"] is True
    assert replay_resp.json()["data"]["rerun"]["id"] == row.id
    assert replay_resp.json()["data"]["rerun"]["attempts"] == 2

    # 4. 全量源兼容补跑（仅范围包含 dle1 + dle2 的 v2 版本）
    dv_full = _register_dv(
        db_session,
        plan_run_id=run.id,
        version_no=2,
        issue_ids=[sig1, sig2],
        event_ids=[str(dle_pruned_verified.id), str(dle_archived_local.id)],
        nfs_root=env_roots.nfs_root,
    )
    db_session.commit()
    full_outcome = compat_rerun.trigger_compat_rerun(
        db_session,
        plan_run_id=run.id,
        platform="mtk",
        requested_by="owner",
        requester_role="admin",
        request_key="req-full-v2",
        delivery_version_no=2,
        local_source_roots={"h1": str(local_root)},
    )
    db_session.commit()
    assert full_outcome.rerun.is_partial is False
    assert full_outcome.rerun.missing_items == []
    assert len(full_outcome.rerun.source_records) == 2

    # 5. 断言原 DLE 状态（仍为 PRUNED / ARCHIVED，不写 LOCAL）、原交付版本、累计发生数均保持原样
    db_session.expire_all()
    assert db_session.get(DeviceLogEvent, dle_pruned_verified.id).state == "PRUNED"
    assert db_session.get(DeviceLogEvent, dle_archived_local.id).state == "ARCHIVED"
    assert db_session.get(DeviceLogEvent, dle_pruned_gone.id).state == "PRUNED"
    assert facts.count_partition_occurrences(db_session, plan_run_id=run.id, platform="mtk") == before_occ_count
    assert db_session.get(DeliveryVersion, dv.id).issue_ids == [sig1, sig2, sig3]
    assert db_session.get(DeliveryVersion, dv_full.id).issue_ids == [sig1, sig2]

    # 6. 绑定 compat_rerun 提单：首次 create 成功，重复执行被 409 拦截不新增提单
    tool_dir = tmp_path / "tool_compat"
    capture_file = tmp_path / "compat_jira.jsonl"
    _install_vendor_double(tool_dir, capture_file, mode="verified")
    monkeypatch.setenv("STP_JIRA_TRANSSION_PYTHON", sys.executable)
    monkeypatch.setenv("STP_JIRA_TRANSSION_DIR", str(tool_dir))

    j_first = client.post(
        "/api/v1/jira/runs",
        data={
            "vendor": "transsion",
            "stage": "create",
            "source": "compat_rerun",
            "plan_run_id": str(run.id),
            "platform": "mtk",
            "compat_rerun_id": str(full_outcome.rerun.id),
        },
        headers=admin_headers,
    )
    assert j_first.status_code == 200
    j_row = _wait_jira_terminal(db_session, j_first.json()["data"]["console_run_id"])
    assert j_row.status == "SUCCESS"
    assert j_row.compat_rerun_id == full_outcome.rerun.id

    j_dup = client.post(
        "/api/v1/jira/runs",
        data={
            "vendor": "transsion",
            "stage": "create",
            "source": "compat_rerun",
            "plan_run_id": str(run.id),
            "platform": "mtk",
            "compat_rerun_id": str(full_outcome.rerun.id),
        },
        headers=admin_headers,
    )
    assert j_dup.status_code == 409
    assert db_session.query(JiraRun).filter_by(compat_rerun_id=full_outcome.rerun.id).count() == 1

    list_resp = client.get(f"/api/v1/plan-runs/{run.id}/dedup/compat-reruns?platform=mtk", headers=admin_headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()["data"]) == 2
