"""B11-U1（#4199 / #4174）阶段 1：A 段只读提单表单生成引擎全量反例与契约测试。

覆盖 #4199 §4.2 B11-U1 全部 8 类必测反例：
1. 覆盖率门禁拒放行（409）与显式 allow_incomplete_coverage 留痕；
2. 同 occurrence_key 跨 run 归并为 Count=1 + 无伴随映射历史 CURRENT 回退 (serial, entry) 并在 manifest 标注；
3. CurProcessNone / *_temp / 未绑定行全入 manifest.json（不静默混入或丢失）；
4. 坏钟（2010 占位）回退 DLE.detected_at 判定本次/基线，且仅基线只标注不自动剔除；
5. 15 列表头逐字一致（含 "ExpType " 尾空格、整数 DeviceCount）、产物标 PENDING_EXPORT、Path 不伪造厂商挂载前缀；
6. platform="unisoc" 返回 400 fail-closed；
7. B3 公式注入转义（= + - @ TAB CR LF NUL 与全角 ＝ ＋ － ＠；数值保持数值语义）；
8. A 段全流程断言零 aee_extract / -m 0 调用、零 15.4 写入、缺 *_members.json fail-closed、下载防穿越与符号链接拒绝。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
import xlrd
import xlwt

from backend.models.device_log_event import DeviceLogEvent
from backend.models.issue_fact import PartitionMode, ScanRound
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.models.project import TestProject as ProjectModel
from backend.services.jira_handoff_builder import (
    HANDOFF_MANIFEST_SCHEMA,
    JiraHandoffError,
    compute_manifest_sha256,
    create_handoff_bundle,
    get_handoff_bundle,
    list_handoff_bundles,
    resolve_handoff_download_file,
    run_handoff_preflight,
    validate_platform,
)
from backend.services.jira_handoff_identity import (
    FORM_COLUMNS,
    HANDOFF_STATUS_PENDING_EXPORT,
    ORG_COLUMNS,
    classify_event_baseline,
    classify_issue_baseline,
    is_placeholder_org_row,
    neutralize_spreadsheet_cell_text,
    safe_xls_cell,
)


# ── 纯离线测试夹具：真实 .xls / .mapping.json / stub -dedup_org 工具 ──────────


class _FakeScalarResult:
    def __init__(self, rows: list):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)

    def all(self) -> list:
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows: list):
        self._rows = list(rows)

    def scalars(self) -> _FakeScalarResult:
        return _FakeScalarResult(self._rows)

    def all(self) -> list:
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeBeginNested:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeHandoffDB:
    """面向 B11-U1 只读查询与审计写入的轻量内存 Session，零生产库接触。"""

    def __init__(self) -> None:
        self.plans: dict[int, SimpleNamespace] = {}
        self.projects: dict[int, SimpleNamespace] = {}
        self.runs: dict[int, SimpleNamespace] = {}
        self.devices: dict[int, SimpleNamespace] = {}
        self.jobs: list[SimpleNamespace] = []
        self.targets: list[SimpleNamespace] = []
        self.pr_hosts: list[SimpleNamespace] = []
        self.artifacts: list[SimpleNamespace] = []
        self.dles: list[SimpleNamespace] = []
        self.partition_modes: list[SimpleNamespace] = []
        self.scan_rounds: list[SimpleNamespace] = []
        self.audit_entries: list = []
        self.committed = False

    def get(self, model, pk):
        if model is Plan:
            return self.plans.get(pk)
        if model is ProjectModel:
            return self.projects.get(pk)
        if model is PlanRun:
            return self.runs.get(pk)
        return None

    def add(self, obj) -> None:
        self.audit_entries.append(obj)

    def flush(self) -> None:
        pass

    def begin_nested(self) -> _FakeBeginNested:
        return _FakeBeginNested()

    def commit(self) -> None:
        self.committed = True

    def execute(self, stmt) -> _FakeResult:
        compiled = stmt.compile()
        params = compiled.params
        sql = str(compiled).lower()
        froms = [getattr(f, "name", str(f)) for f in stmt.get_final_froms()]
        primary = froms[0] if froms else ""

        def _param_values(substr: str) -> list:
            out = []
            for k, v in params.items():
                if substr in k.lower():
                    if isinstance(v, (list, tuple, set)):
                        out.extend(v)
                    else:
                        out.append(v)
            return out

        run_ids = set(_param_values("plan_run_id") or _param_values("id_"))

        if "from plan_run " in sql or primary == PlanRun.__tablename__:
            ids = set(_param_values("id"))
            rows = [r for rid, r in self.runs.items() if not ids or rid in ids]
            return _FakeResult(rows)

        if "from partition_mode" in sql or primary == PartitionMode.__tablename__:
            pr_ids = set(_param_values("plan_run_id"))
            plats = {str(p).lower() for p in _param_values("platform")}
            rows = [
                pm for pm in self.partition_modes
                if (not pr_ids or pm.plan_run_id in pr_ids)
                and (not plats or pm.platform.lower() in plats)
            ]
            return _FakeResult(rows)

        if "from scan_round" in sql or primary == ScanRound.__tablename__:
            pr_ids = set(_param_values("plan_run_id"))
            plats = {str(p).lower() for p in _param_values("platform")}
            rows = [
                sr for sr in self.scan_rounds
                if (not pr_ids or sr.plan_run_id in pr_ids)
                and (not plats or sr.platform.lower() in plats)
            ]
            rows.sort(key=lambda r: (r.round_no, r.id))
            return _FakeResult(rows)

        if "from plan_run_artifact" in sql or primary == PlanRunArtifact.__tablename__:
            pr_ids = set(_param_values("plan_run_id"))
            rows = [
                a for a in self.artifacts
                if (not pr_ids or a.plan_run_id in pr_ids)
                and a.artifact_type == "scan_result_xls"
            ]
            rows.sort(key=lambda a: (a.created_at, a.id), reverse=True)
            return _FakeResult(rows)

        if "from device_log_event" in sql or primary == DeviceLogEvent.__tablename__:
            pr_ids = set(_param_values("plan_run_id"))
            plats = {str(p).lower() for p in _param_values("lower") or _param_values("platform")}
            rows = [
                d for d in self.dles
                if (not pr_ids or d.plan_run_id in pr_ids)
                and (not plats or d.platform.lower() in plats)
            ]
            rows.sort(key=lambda d: (d.detected_at, str(d.id)))
            return _FakeResult(rows)

        if "from job_instance" in sql:
            pr_ids = set(_param_values("plan_run_id"))
            host_ids = set(_param_values("host_id"))
            matched = [
                j for j in self.jobs
                if (not pr_ids or j.plan_run_id in pr_ids)
                and (not host_ids or j.host_id in host_ids)
            ]
            if "join device" in sql:
                cols = len(stmt.selected_columns)
                if cols == 5:
                    return _FakeResult([
                        (
                            j.plan_run_id,
                            self.devices[j.device_id].id,
                            self.devices[j.device_id].serial,
                            self.devices[j.device_id].product,
                            self.devices[j.device_id].build_display_id,
                        )
                        for j in matched
                        if j.device_id in self.devices
                    ])
                if cols == 2:
                    return _FakeResult([
                        (j.host_id, self.devices[j.device_id].platform)
                        for j in matched
                        if j.device_id in self.devices
                    ])
            return _FakeResult([j.host_id for j in matched if j.host_id])

        if "from plan_run_target_device" in sql:
            pr_ids = set(_param_values("plan_run_id"))
            host_ids = set(_param_values("host_id_snapshot"))
            matched = [
                t for t in self.targets
                if (not pr_ids or t.plan_run_id in pr_ids)
                and (not host_ids or t.host_id_snapshot in host_ids)
            ]
            if "join device" in sql:
                cols = len(stmt.selected_columns)
                if cols == 5:
                    return _FakeResult([
                        (
                            t.plan_run_id,
                            self.devices[t.device_id].id,
                            self.devices[t.device_id].serial,
                            self.devices[t.device_id].product,
                            self.devices[t.device_id].build_display_id,
                        )
                        for t in matched
                        if t.device_id in self.devices
                    ])
                if cols == 2:
                    return _FakeResult([
                        (t.host_id_snapshot, self.devices[t.device_id].platform)
                        for t in matched
                        if t.device_id in self.devices
                    ])
            return _FakeResult([t.host_id_snapshot for t in matched if t.host_id_snapshot])

        if "from plan_run_host" in sql:
            pr_ids = set(_param_values("plan_run_id"))
            return _FakeResult([h.host_id for h in self.pr_hosts if not pr_ids or h.plan_run_id in pr_ids])

        raise AssertionError(f"Unhandled SQL in FakeHandoffDB: {sql} (run_ids={run_ids})")


def _write_org_xls(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = xlwt.Workbook(encoding="utf-8")
    ws = wb.add_sheet("aeeexp", cell_overwrite_ok=True)
    for col_idx, header in enumerate(ORG_COLUMNS):
        ws.write(0, col_idx, header)
    for idx, row in enumerate(rows, start=1):
        vals = [
            idx - 1,
            row.get("Path", ""),
            row.get("Version", "V100"),
            row.get("ExpTime", "2026-11-20 12:00:00"),
            row.get("ExpClass", "Java (JE)"),
            row.get("ExpType", "JE"),
            row.get("CurProcess", "com.demo.app"),
            row.get("Package", "com.demo.app"),
            row.get("Detail", "NullPointerException"),
            row.get("CausedBy", "java.lang.NullPointerException: demo"),
            row.get("extraTag", ""),
            row.get("Count", 1),
            row.get("Activity", "MainActivity"),
            row.get("DeviceId", "SN001"),
            row.get("Rom_Ram", "8+256"),
        ]
        for col_idx, val in enumerate(vals):
            ws.write(idx, col_idx, val)
    wb.save(str(path))


def _write_mapping_json(org_xls: Path, entries: list[dict]) -> Path:
    map_path = org_xls.with_name(org_xls.name + ".mapping.json")
    payload = {
        "schema": "stp.scan_report_mapping",
        "schema_version": "1.0",
        "events_unavailable": False,
        "spill_unstaged": [],
        "rows": entries,
    }
    map_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return map_path


def _create_stub_dedup_org_tool(tmp_path: Path, *, emit_members: bool = True) -> dict[str, str]:
    """构建真实可执行的 Python stub 工具，按 (ExpClass, Package, CausedBy) 归并并产出 *_members.json。"""
    tool_dir = tmp_path / "stub_scan_tool"
    tool_dir.mkdir(parents=True, exist_ok=True)
    script = tool_dir / "start_log_scan.py"
    code = f"""
import json, sys
from pathlib import Path
import xlrd, xlwt

args = sys.argv[1:]
if "-m" in args or "aee_extract" in " ".join(args):
    sys.stderr.write("FORBIDDEN_M_MODE_CALLED\\n")
    sys.exit(99)
if "-dedup_org" not in args:
    sys.stderr.write("missing -dedup_org\\n")
    sys.exit(2)

org_path = Path(args[args.index("-dedup_org") + 1])
book = xlrd.open_workbook(str(org_path))
sheet = book.sheet_by_name("aeeexp")
headers = [str(v).strip() for v in sheet.row_values(0)]
col = {{h.lower(): i for i, h in enumerate(headers)}}

rows_out = []
buckets = {{}}
bucket_order = []
for i in range(1, sheet.nrows):
    vals = sheet.row_values(i)
    pkg = str(vals[col["package"]]).strip()
    if pkg == "com.filtered.discard":
        rows_out.append({{"org_row": i, "branch": "filtered", "filter_reason": "discard_dbg_package", "group": None}})
        continue
    key = (str(vals[col["expclass"]]).strip(), pkg, str(vals[col["causedby"]]).strip())
    if key not in buckets:
        buckets[key] = []
        bucket_order.append(key)
    buckets[key].append((i, vals))

groups = []
wb = xlwt.Workbook(encoding="utf-8")
ws = wb.add_sheet("aeeexp")
for c_idx, h in enumerate(sheet.row_values(0)):
    ws.write(0, c_idx, h)

for g_idx, key in enumerate(bucket_order, start=1):
    members = buckets[key]
    member_nums = [m[0] for m in members]
    rep_num, rep_vals = members[0]
    dev_ids = sorted({{str(m[1][col["deviceid"]]).strip() for m in members}})
    for m_num in member_nums:
        rows_out.append({{"org_row": m_num, "branch": "similarity" if len(member_nums) > 1 else "singleton", "group": g_idx}})
    groups.append({{
        "group": g_idx,
        "final_row": g_idx,
        "representative_row": rep_num,
        "member_rows": member_nums,
        "count": len(member_nums),
        "device_ids": dev_ids,
    }})
    for c_idx, v in enumerate(rep_vals):
        ws.write(g_idx, c_idx, v)

dedup_xls = org_path.with_name(org_path.stem + "_dedup_org_20261127_110000.xls")
wb.save(str(dedup_xls))

if {emit_members!r}:
    members_json = org_path.with_name(org_path.stem + "_dedup_org_20261127_110000_members.json")
    members_json.write_text(
        json.dumps({{"schema": "stp.scan_membership", "schema_version": "1.0", "rows": rows_out, "groups": groups}}, ensure_ascii=False),
        encoding="utf-8",
    )
"""
    script.write_text(code, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return {"python": sys.executable, "script": str(script)}


def _setup_base_db(
    *,
    run_id: int = 101,
    hosts: tuple[str, ...] = ("host-a",),
    started_at: datetime | None = None,
    build: str = "MLD-NX2-16-261117V108",
    product: str = "MLD-NX2",
    jira_key: str = "MLD",
) -> FakeHandoffDB:
    db = FakeHandoffDB()
    t0 = started_at or datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    db.plans[1] = SimpleNamespace(id=1, name="MTK Monkey 长稳")
    db.projects[10] = SimpleNamespace(id=10, project_key="mld-nx2", jira_project_key=jira_key)
    device_builds = {}
    for idx, hid in enumerate(hosts, start=1):
        dev_id = 1000 + idx
        serial = f"SN{idx:03d}"
        db.devices[dev_id] = SimpleNamespace(
            id=dev_id, serial=serial, platform="mtk", product=product, build_display_id=build,
        )
        db.jobs.append(SimpleNamespace(plan_run_id=run_id, device_id=dev_id, host_id=hid))
        db.pr_hosts.append(SimpleNamespace(plan_run_id=run_id, host_id=hid))
        device_builds[str(dev_id)] = build
    db.runs[run_id] = SimpleNamespace(
        id=run_id,
        plan_id=1,
        status="SUCCESS",
        started_at=t0,
        ended_at=t0 + timedelta(hours=2),
        project_id=10,
        build_version=build,
        run_context={"device_builds": device_builds},
    )
    return db


# ── 反例 1：覆盖率门禁拒放行 & allow_incomplete_coverage 显式留痕 ────────────


def test_b11_u1_coverage_gate_blocks_missing_host_and_allows_explicit_override(tmp_path):
    db = _setup_base_db(run_id=101, hosts=("host-a", "host-b"))
    t0 = db.runs[101].started_at
    dle_id = uuid4()
    db.dles.append(
        SimpleNamespace(
            id=dle_id,
            serial="SN001",
            platform="mtk",
            plan_run_id=101,
            host_id="host-a",
            occurrence_key="occ-key-001",
            detected_at=t0 + timedelta(minutes=10),
            local_path="/mnt/hdd/aee_events/1120/SN001/aee_exp/2026_1120_181000_db.00.JE",
            remote_path="/mnt/stp-aee/devices/101/SN001/2026_1120_181000_db.00.JE",
        )
    )
    org_a = tmp_path / "dedup" / "101" / "mtk" / "host-a_Result_org.xls"
    row_path = "/mnt/hdd/.stp-scan/pr101/SN001/aee_exp/2026_1120_181000_db.00.JE/db.00.JE.dbg.DEC/__exp_main.txt"
    _write_org_xls(org_a, [{"Path": row_path, "DeviceId": "SN001", "ExpTime": "2026-11-20 18:10:00"}])
    _write_mapping_json(org_a, [{"sheet": "aeeexp", "row": 2, "path": row_path, "mapped": True, "dle_id": str(dle_id), "occurrence_key": "occ-key-001"}])
    db.artifacts.append(
        SimpleNamespace(
            id=1, plan_run_id=101, host_id="host-a", storage_uri=str(org_a),
            artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=2),
        )
    )

    # 仅 host-a 有产物，host-b 缺失 → preflight 报不完整，create 默认 409 拒绝
    preflight = run_handoff_preflight(db, plan_run_ids=[101], platform="mtk")
    assert preflight["gates"]["coverage_complete"] is False
    assert preflight["runs"][0]["missing_hosts"] == ["host-b"]

    tool = _create_stub_dedup_org_tool(tmp_path)
    storage = tmp_path / "center"
    with pytest.raises(JiraHandoffError) as exc_info:
        create_handoff_bundle(
            db, plan_run_ids=[101], platform="mtk",
            storage_root=str(storage), scan_tool=tool,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "handoff_coverage_incomplete"
    assert not (storage / "_meta" / "handoff").exists()

    # 显式 allow_incomplete_coverage=True 放行并在 manifest 中留痕
    manifest = create_handoff_bundle(
        db, plan_run_ids=[101], platform="mtk",
        allow_incomplete_coverage=True,
        storage_root=str(storage), scan_tool=tool, username="owner",
    )
    assert manifest["gates"]["coverage_complete"] is False
    assert manifest["allow_incomplete_coverage"] is True
    assert manifest["runs"][0]["missing_hosts"] == ["host-b"]
    assert db.committed is True


# ── 反例 2：同 occurrence_key 跨 run 归并 Count=1 + 无映射历史 CURRENT 回退 ───


def test_b11_u1_occurrence_key_cross_run_dedup_and_compat_serial_entry_fallback(tmp_path):
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    db = _setup_base_db(run_id=101, hosts=("host-a",), started_at=t0)
    # 第二个续跑 run 102（同设备 SN001，历史无伴随映射场景 + 同一 occurrence_key 在两 run 重复观测）
    db.runs[102] = SimpleNamespace(
        id=102, plan_id=1, status="FAILED",
        started_at=t0 + timedelta(hours=3), ended_at=t0 + timedelta(hours=5),
        project_id=10, build_version="MLD-NX2-16-261117V108",
        run_context={"device_builds": {"1001": "MLD-NX2-16-261117V108"}},
    )
    db.jobs.append(SimpleNamespace(plan_run_id=102, device_id=1001, host_id="host-a"))
    db.pr_hosts.append(SimpleNamespace(plan_run_id=102, host_id="host-a"))

    dle1, dle2, dle_compat = uuid4(), uuid4(), uuid4()
    db.dles.extend([
        SimpleNamespace(
            id=dle1, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a",
            occurrence_key="occ-shared-999", detected_at=t0 + timedelta(minutes=20),
            local_path="/mnt/hdd/aee_events/1120/SN001/aee_exp/entry_r101_db.00.JE",
            remote_path="/mnt/stp-aee/devices/101/SN001/entry_r101_db.00.JE",
        ),
        SimpleNamespace(
            id=dle2, serial="SN001", platform="mtk", plan_run_id=102, host_id="host-a",
            occurrence_key="occ-shared-999", detected_at=t0 + timedelta(hours=3, minutes=5),
            local_path="/mnt/hdd/aee_events/1120/SN001/aee_exp/entry_r102_alias_db.00.JE",
            remote_path="/mnt/stp-aee/evidence/101_MLD/SN001/entry_r102_alias_db.00.JE",
        ),
        SimpleNamespace(
            id=dle_compat, serial="SN001", platform="mtk", plan_run_id=102, host_id="host-a",
            occurrence_key=None, detected_at=t0 + timedelta(hours=4),
            local_path="/mnt/hdd/aee_events/1120/SN001/aee_exp/2026_1120_220000_db.01.ANR",
            remote_path=None,
        ),
    ])

    # Run 101 有伴随映射
    org101 = tmp_path / "dedup" / "101" / "mtk" / "host-a_Result_org.xls"
    p1 = "/mnt/hdd/.stp-scan/pr101/SN001/aee_exp/entry_r101_db.00.JE/db.00.JE.dbg.DEC/__exp_main.txt"
    _write_org_xls(org101, [{"Path": p1, "DeviceId": "SN001", "ExpTime": "2026-11-20 18:20:00", "CausedBy": "NPE-A"}])
    _write_mapping_json(org101, [{"sheet": "aeeexp", "row": 2, "path": p1, "mapped": True, "dle_id": str(dle1), "occurrence_key": "occ-shared-999"}])

    # Run 102 无伴随映射（模拟 Run 1216 历史分片）：第 1 行通过 DLE 命中 occ-shared-999，第 2 行走 compat_serial_entry
    org102 = tmp_path / "dedup" / "102" / "mtk" / "host-a_Result_org.xls"
    p2 = "/mnt/hdd/.stp-scan/pr102/SN001/aee_exp/entry_r102_alias_db.00.JE/db.00.JE.dbg.DEC/__exp_main.txt"
    p3 = "/mnt/hdd/.stp-scan/pr102/SN001/aee_exp/2026_1120_220000_db.01.ANR/db.01.ANR.dbg.DEC/__exp_main.txt"
    _write_org_xls(org102, [
        {"Path": p2, "DeviceId": "SN001", "ExpTime": "2026-11-20 18:20:00", "CausedBy": "NPE-A"},
        {"Path": p3, "DeviceId": "SN001", "ExpTime": "2026-11-20 22:00:00", "ExpClass": "ANR", "ExpType": "ANR", "CausedBy": "ANR-B"},
    ])

    db.artifacts.extend([
        SimpleNamespace(id=1, plan_run_id=101, host_id="host-a", storage_uri=str(org101), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=2)),
        SimpleNamespace(id=2, plan_run_id=102, host_id="host-a", storage_uri=str(org102), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=5)),
    ])

    tool = _create_stub_dedup_org_tool(tmp_path)
    manifest = create_handoff_bundle(
        db, plan_run_ids=[101, 102], platform="mtk",
        storage_root=str(tmp_path / "center"), scan_tool=tool,
    )
    assert manifest["identity_gate"]["total_org_rows"] == 3
    assert manifest["identity_gate"]["unique_events"] == 2
    assert manifest["identity_gate"]["keyed_rows"] == 2
    assert manifest["identity_gate"]["compat_fallback_rows"] == 1

    b = manifest["builds"][0]
    assert b["issue_count"] == 2
    by_cause = {iss["form_fields"]["CausedBy"]: iss for iss in b["issues"]}
    # 同一 occurrence_key 跨 run 归并为 Count=1，source_runs=[101, 102]，并优选 evidence 层路径
    assert by_cause["NPE-A"]["count"] == 1
    assert by_cause["NPE-A"]["device_count"] == 1
    assert by_cause["NPE-A"]["source_runs"] == [101, 102]
    assert by_cause["NPE-A"]["representative"]["identity_source"] == "occurrence_key"
    assert by_cause["NPE-A"]["representative"]["evidence_tier"] == "evidence"
    # 历史无映射分片走 compat_serial_entry 并在 manifest 显式标注
    assert by_cause["ANR-B"]["count"] == 1
    assert by_cause["ANR-B"]["representative"]["identity_source"] == "compat_serial_entry"


# ── 反例 3：CurProcessNone / *_temp / 未绑定行全入 manifest.json ─────────────


def test_b11_u1_placeholders_and_unbound_rows_recorded_explicitly(tmp_path):
    assert is_placeholder_org_row({"Path": "/a/db.00.JE_temp/x", "CurProcess": "app"}) == "temp_path_placeholder"
    assert is_placeholder_org_row({"Path": "/a/db.00.JE/x", "CurProcess": "CurProcessNone"}) == "vendor_parse_placeholder"

    db = _setup_base_db(run_id=101, hosts=("host-a",))
    t0 = db.runs[101].started_at
    dle_ok = uuid4()
    db.dles.append(
        SimpleNamespace(
            id=dle_ok, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a",
            occurrence_key="occ-valid-1", detected_at=t0 + timedelta(minutes=15),
            local_path="/mnt/hdd/SN001/aee_exp/db.00.JE", remote_path=None,
        )
    )
    org = tmp_path / "dedup" / "101" / "mtk" / "host-a_Result_org.xls"
    rows = [
        {"Path": "/scan/SN001/aee_exp/db.00.JE/db.00.JE.dbg.DEC/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "ValidErr"},
        {"Path": "/scan/SN001/aee_exp/db.01.JE_temp/db.rand/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "TempErr"},
        {"Path": "/scan/SN001/aee_exp/db.02.JE/db.02.JE.dbg.DEC/__exp_main.txt", "DeviceId": "SN001", "CurProcess": "CurProcessNone", "CausedBy": "Placeholder"},
        {"Path": "/scan/SN001/aee_exp/db.03.NE/db.03.NE.dbg.DEC/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "UnmappedErr"},
    ]
    _write_org_xls(org, rows)
    _write_mapping_json(org, [
        {"sheet": "aeeexp", "row": 2, "path": rows[0]["Path"], "mapped": True, "dle_id": str(dle_ok), "occurrence_key": "occ-valid-1"},
        {"sheet": "aeeexp", "row": 3, "path": rows[1]["Path"], "mapped": False, "unmapped_reason": "no_source"},
        {"sheet": "aeeexp", "row": 4, "path": rows[2]["Path"], "mapped": False, "unmapped_reason": "no_source"},
        {"sheet": "aeeexp", "row": 5, "path": rows[3]["Path"], "mapped": False, "unmapped_reason": "outside_scope"},
    ])
    db.artifacts.append(
        SimpleNamespace(id=1, plan_run_id=101, host_id="host-a", storage_uri=str(org), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=1))
    )

    manifest = create_handoff_bundle(
        db, plan_run_ids=[101], platform="mtk",
        storage_root=str(tmp_path / "center"), scan_tool=_create_stub_dedup_org_tool(tmp_path),
    )
    assert manifest["identity_gate"]["total_org_rows"] == 4
    assert manifest["identity_gate"]["unique_events"] == 1
    assert manifest["identity_gate"]["unbound_rows"] == 3
    assert manifest["identity_gate"]["unbound_by_reason"] == {
        "outside_scope": 1,
        "temp_path_placeholder": 1,
        "vendor_parse_placeholder": 1,
    }
    assert len(manifest["unbound_rows"]) == 3
    assert manifest["builds"][0]["grouped_events"] == 1


# ── 反例 4：坏钟（2010 占位）回退 DLE.detected_at + 仅基线只标注不自动剔除 ────


def test_b11_u1_bad_clock_fallback_and_baseline_annotation_not_dropped(tmp_path):
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    # 纯函数直测
    cls_bad_cur, fb_cur = classify_event_baseline("2010-01-01 00:00:00", t0 + timedelta(minutes=5), t0)
    assert (cls_bad_cur, fb_cur) == ("current", True)
    cls_bad_base, fb_base = classify_event_baseline("2010-01-01 00:00:00", t0 - timedelta(hours=1), t0)
    assert (cls_bad_base, fb_base) == ("baseline", True)
    assert classify_issue_baseline(["baseline", "baseline"]) == "baseline_only"
    assert classify_issue_baseline(["baseline", "current"]) == "mixed"
    assert classify_issue_baseline(["current"]) == "current_only"

    # 端到端验证：baseline_only 留在问题列表不自动剔除，mixed 优先选 current 作代表
    db = _setup_base_db(run_id=101, hosts=("host-a",), started_at=t0)
    d1, d2, d3 = uuid4(), uuid4(), uuid4()
    db.dles.extend([
        SimpleNamespace(id=d1, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a", occurrence_key="occ-b1", detected_at=t0 - timedelta(hours=2), local_path="/hdd/SN001/aee_exp/e1", remote_path=None),
        SimpleNamespace(id=d2, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a", occurrence_key="occ-m1", detected_at=t0 - timedelta(minutes=30), local_path="/hdd/SN001/aee_exp/e2", remote_path=None),
        SimpleNamespace(id=d3, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a", occurrence_key="occ-m2", detected_at=t0 + timedelta(minutes=30), local_path="/hdd/SN001/aee_exp/e3", remote_path=None),
    ])
    org = tmp_path / "dedup" / "101" / "mtk" / "host-a_Result_org.xls"
    rows = [
        {"Path": "/s/SN001/aee_exp/e1/d.DEC/__exp_main.txt", "DeviceId": "SN001", "ExpTime": "2010-01-01 00:00:00", "CausedBy": "OldBaselineIssue"},
        {"Path": "/s/SN001/aee_exp/e2/d.DEC/__exp_main.txt", "DeviceId": "SN001", "ExpTime": "2026-11-20 17:30:00", "CausedBy": "MixedIssue"},
        {"Path": "/s/SN001/aee_exp/e3/d.DEC/__exp_main.txt", "DeviceId": "SN001", "ExpTime": "2010-01-01 00:00:00", "CausedBy": "MixedIssue"},
    ]
    _write_org_xls(org, rows)
    _write_mapping_json(org, [
        {"sheet": "aeeexp", "row": 2, "path": rows[0]["Path"], "mapped": True, "dle_id": str(d1), "occurrence_key": "occ-b1"},
        {"sheet": "aeeexp", "row": 3, "path": rows[1]["Path"], "mapped": True, "dle_id": str(d2), "occurrence_key": "occ-m1"},
        {"sheet": "aeeexp", "row": 4, "path": rows[2]["Path"], "mapped": True, "dle_id": str(d3), "occurrence_key": "occ-m2"},
    ])
    db.artifacts.append(
        SimpleNamespace(id=1, plan_run_id=101, host_id="host-a", storage_uri=str(org), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=1))
    )

    manifest = create_handoff_bundle(
        db, plan_run_ids=[101], platform="mtk",
        storage_root=str(tmp_path / "center"), scan_tool=_create_stub_dedup_org_tool(tmp_path),
    )
    b = manifest["builds"][0]
    assert b["baseline_distribution"] == {"current_only": 0, "mixed": 1, "baseline_only": 1}
    by_cause = {i["form_fields"]["CausedBy"]: i for i in b["issues"]}
    assert by_cause["OldBaselineIssue"]["baseline_class"] == "baseline_only"
    assert by_cause["OldBaselineIssue"]["clock_fallback_count"] == 1
    assert by_cause["MixedIssue"]["baseline_class"] == "mixed"
    assert by_cause["MixedIssue"]["representative"]["entry"] == "e3"


# ── 反例 5 & 7：15 列表头逐字一致、PENDING_EXPORT、B3 公式注入中和、5 页复核表 ─


def test_b11_u1_15_column_contract_pending_export_and_b3_formula_neutralization(tmp_path):
    # B3 纯函数全字符集合验证
    for ch in ("=", "+", "-", "@", "\t", "\r", "\n", "\x00", "＝", "＋", "－", "＠"):
        assert neutralize_spreadsheet_cell_text(f"{ch}cmd") == f"'{ch}cmd"
    assert safe_xls_cell(-5) == -5
    assert isinstance(safe_xls_cell(3), int)
    assert safe_xls_cell("=HYPERLINK(\"http://evil\")").startswith("'=HYPERLINK")

    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    db = _setup_base_db(run_id=101, hosts=("host-a",), started_at=t0)
    d1, d_filt = uuid4(), uuid4()
    db.dles.extend([
        SimpleNamespace(id=d1, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a", occurrence_key="occ-f1", detected_at=t0 + timedelta(minutes=10), local_path="/hdd/SN001/aee_exp/e1", remote_path=None),
        SimpleNamespace(id=d_filt, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a", occurrence_key="occ-f2", detected_at=t0 + timedelta(minutes=12), local_path="/hdd/SN001/aee_exp/e2", remote_path=None),
    ])
    org = tmp_path / "dedup" / "101" / "mtk" / "host-a_Result_org.xls"
    rows = [
        {"Path": "/s/SN001/aee_exp/e1/d.DEC/__exp_main.txt", "DeviceId": "SN001", "Package": "=CMD|' /C calc'!A0", "CausedBy": "+1+1", "Detail": "＠inject"},
        {"Path": "/s/SN001/aee_exp/e2/d.DEC/__exp_main.txt", "DeviceId": "SN001", "Package": "com.filtered.discard", "CausedBy": "FilteredOut"},
    ]
    _write_org_xls(org, rows)
    _write_mapping_json(org, [
        {"sheet": "aeeexp", "row": 2, "path": rows[0]["Path"], "mapped": True, "dle_id": str(d1), "occurrence_key": "occ-f1"},
        {"sheet": "aeeexp", "row": 3, "path": rows[1]["Path"], "mapped": True, "dle_id": str(d_filt), "occurrence_key": "occ-f2"},
    ])
    db.artifacts.append(
        SimpleNamespace(id=1, plan_run_id=101, host_id="host-a", storage_uri=str(org), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=1))
    )

    storage = tmp_path / "center"
    manifest = create_handoff_bundle(
        db, plan_run_ids=[101], platform="mtk",
        storage_root=str(storage), scan_tool=_create_stub_dedup_org_tool(tmp_path),
    )
    assert manifest["schema"] == HANDOFF_MANIFEST_SCHEMA
    assert manifest["status"] == HANDOFF_STATUS_PENDING_EXPORT
    assert manifest["manifest_sha256"] == compute_manifest_sha256(manifest)

    # 校验 15 列预览 .xls
    preview_path, _, _ = resolve_handoff_download_file(
        manifest["handoff_id"], "preview_xls", storage_root=str(storage),
    )
    wb = xlrd.open_workbook(str(preview_path))
    sh = wb.sheet_by_name("aeeexp")
    assert tuple(sh.row_values(0)) == FORM_COLUMNS
    assert sh.row_values(0)[5] == "ExpType "  # 尾空格不可丢
    assert sh.nrows == 2
    row1 = sh.row_values(1)
    assert str(row1[1]).startswith("PENDING_EXPORT://")
    assert "/home/jxtinno/sonic_tinno/" not in str(row1[1])
    assert row1[7] == "'=CMD|' /C calc'!A0"
    assert row1[8] == "'＠inject"
    assert row1[9] == "'+1+1"
    assert int(row1[11]) == 1  # Count
    assert int(row1[13]) == 1  # DeviceCount

    # 校验 5 页复核工作簿
    review_path, _, _ = resolve_handoff_download_file(
        manifest["handoff_id"], "review_workbook", storage_root=str(storage),
    )
    rwb = xlrd.open_workbook(str(review_path))
    assert rwb.sheet_names() == ["概览", "问题汇总", "代表日志清单", "全部唯一事件", "未绑定与口径说明"]
    overview_text = "\n".join(str(rwb.sheet_by_name("概览").row_values(i)) for i in range(rwb.sheet_by_name("概览").nrows))
    assert "PENDING_EXPORT" in overview_text
    assert rwb.sheet_by_name("问题汇总").nrows == 2
    assert rwb.sheet_by_name("未绑定与口径说明").nrows == 2  # 表头 + 1 条 filtered


# ── 反例 6：platform="unisoc" 返回 400 fail-closed ────────────────────────────


def test_b11_u1_non_mtk_platform_rejected_with_400():
    with pytest.raises(JiraHandoffError) as exc_info:
        validate_platform("unisoc")
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "handoff_unsupported_platform"


# ── 反例 8：零 aee_extract/-m 0、缺 *_members.json fail-closed、防穿越与软链 ───


def test_b11_u1_zero_aee_extract_members_fail_closed_and_download_guards(tmp_path, monkeypatch):
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    db = _setup_base_db(run_id=101, hosts=("host-a",), started_at=t0)
    d1 = uuid4()
    db.dles.append(
        SimpleNamespace(id=d1, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a", occurrence_key="occ-z1", detected_at=t0 + timedelta(minutes=5), local_path="/hdd/SN001/aee_exp/e1", remote_path=None)
    )
    org = tmp_path / "dedup" / "101" / "mtk" / "host-a_Result_org.xls"
    rows = [{"Path": "/s/SN001/aee_exp/e1/d.DEC/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "ErrZ"}]
    _write_org_xls(org, rows)
    _write_mapping_json(org, [{"sheet": "aeeexp", "row": 2, "path": rows[0]["Path"], "mapped": True, "dle_id": str(d1), "occurrence_key": "occ-z1"}])
    db.artifacts.append(
        SimpleNamespace(id=1, plan_run_id=101, host_id="host-a", storage_uri=str(org), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=1))
    )

    storage = tmp_path / "center"
    # 1) 工具不产出 *_members.json → fail-closed 且不残留半成品目录
    bad_tool = _create_stub_dedup_org_tool(tmp_path / "bad", emit_members=False)
    with pytest.raises(JiraHandoffError) as exc_bad:
        create_handoff_bundle(
            db, plan_run_ids=[101], platform="mtk",
            storage_root=str(storage), scan_tool=bad_tool,
        )
    assert exc_bad.value.code == "handoff_members_json_missing"
    handoff_root = storage / "_meta" / "handoff"
    assert not handoff_root.exists() or list(handoff_root.iterdir()) == []

    # 2) 记录真实 subprocess.run argv，断言零 -m / aee_extract 调用且仅调 -dedup_org
    recorded_argvs: list[list[str]] = []
    real_run = subprocess.run

    def _spy_run(argv, *args, **kwargs):
        recorded_argvs.append([str(a) for a in argv])
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr("backend.services.jira_handoff_builder.subprocess.run", _spy_run)
    good_tool = _create_stub_dedup_org_tool(tmp_path / "good", emit_members=True)
    manifest = create_handoff_bundle(
        db, plan_run_ids=[101], platform="mtk",
        storage_root=str(storage), scan_tool=good_tool,
    )
    assert len(recorded_argvs) == 1
    assert Path(recorded_argvs[0][1]).name == "start_log_scan.py"
    cli_flags = recorded_argvs[0][2:]
    assert "-dedup_org" in cli_flags
    assert "-m" not in cli_flags
    assert all("aee_extract" not in a for a in cli_flags)
    assert all("/mnt/jxtinno-sonic" not in a for a in recorded_argvs[0])

    # 3) list / get / 下载白名单与越界/符号链接拒绝
    listed = list_handoff_bundles(storage_root=str(storage))
    assert len(listed) == 1 and listed[0]["handoff_id"] == manifest["handoff_id"]
    fetched = get_handoff_bundle(manifest["handoff_id"], storage_root=str(storage))
    assert fetched["manifest_sha256"] == manifest["manifest_sha256"]

    with pytest.raises(JiraHandoffError) as exc_id:
        resolve_handoff_download_file("../escape", "manifest", storage_root=str(storage))
    assert exc_id.value.status_code == 400

    with pytest.raises(JiraHandoffError) as exc_kind:
        resolve_handoff_download_file(manifest["handoff_id"], "evil_kind", storage_root=str(storage))
    assert exc_kind.value.status_code == 400

    # 篡改符号链接替换 preview_xls 必须被拒绝
    preview_rel = manifest["builds"][0]["artifacts"]["preview_xls"]["rel_path"]
    preview_file = storage / "_meta" / "handoff" / manifest["handoff_id"] / preview_rel
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    preview_file.unlink()
    preview_file.symlink_to(outside)
    with pytest.raises(JiraHandoffError) as exc_sym:
        resolve_handoff_download_file(manifest["handoff_id"], "preview_xls", storage_root=str(storage))
    assert exc_sym.value.status_code in (400, 404)


def test_b11_u1_dedup_first_scan_round_with_empty_receipt_passes_coverage_gate(tmp_path):
    """DEDUP_FIRST 分区：host-a 有映射报表、host-b 显式空回执 → 覆盖率完整，manifest 正常生成。"""
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    db = _setup_base_db(run_id=301, hosts=("host-a", "host-b"), started_at=t0)
    db.partition_modes.append(
        SimpleNamespace(plan_run_id=301, platform="mtk", mode="DEDUP_FIRST")
    )
    dle1 = uuid4()
    db.dles.append(
        SimpleNamespace(
            id=dle1, serial="SN001", platform="mtk", plan_run_id=301, host_id="host-a",
            occurrence_key="occ-df-001", detected_at=t0 + timedelta(minutes=10),
            local_path="/hdd/SN001/aee_exp/e_df_1",
            remote_path="/mnt/stp-aee/evidence/301_MLD/SN001/e_df_1",
        )
    )
    org_a = tmp_path / "dedup" / "301" / "mtk" / "host-a_Result_org.xls"
    row_path = "/s/SN001/aee_exp/e_df_1/d.DEC/__exp_main.txt"
    _write_org_xls(org_a, [{"Path": row_path, "DeviceId": "SN001", "CausedBy": "DedupFirstCrash"}])
    map_a = _write_mapping_json(
        org_a,
        [{"sheet": "aeeexp", "row": 2, "path": row_path, "mapped": True, "dle_id": str(dle1), "occurrence_key": "occ-df-001"}],
    )
    db.scan_rounds.append(
        SimpleNamespace(
            id=901,
            plan_run_id=301,
            platform="mtk",
            round_no=1,
            kind="FINAL",
            status="CLOSED",
            expected_set=[
                {
                    "host_id": "host-a",
                    "platform": "mtk",
                    "reports": [{"storage_uri": str(org_a), "mapping_uri": str(map_a)}],
                },
                {
                    "host_id": "host-b",
                    "platform": "mtk",
                    "reports": [{"empty_receipt": True}],
                },
            ],
        )
    )

    storage = tmp_path / "center"
    manifest = create_handoff_bundle(
        db, plan_run_ids=[301], platform="mtk",
        storage_root=str(storage), scan_tool=_create_stub_dedup_org_tool(tmp_path),
    )
    assert manifest["gates"]["coverage_complete"] is True
    assert manifest["runs"][0]["partition_mode"] == "DEDUP_FIRST"
    assert manifest["runs"][0]["missing_hosts"] == []
    assert manifest["builds"][0]["issues"][0]["representative"]["evidence_tier"] == "evidence"

