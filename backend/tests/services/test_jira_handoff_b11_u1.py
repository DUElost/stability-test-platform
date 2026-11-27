"""B11-U1（#4199 v1.1 / #4174）阶段 1：A 段只读提单表单生成引擎全量 13 类反例与契约测试。

覆盖 #4199 v1.1 §4.2 B11-U1 全部 13 类必测反例：
1. 覆盖率硬门禁：缺主机、坏表/错表头空表/缺必需列、多出非期望 host 均阻断（无放行开关）；
2. 发生身份严格绑定：同 occurrence_key 跨 run 计 1；缺 DLE/key 冲突/跨 run/跨 host/串号错绑/
   有伴随缺 key 均入 unbound_rows；仅无伴随历史 CURRENT 唯一锚定走 compat_serial_entry，
   多候选歧义入 unbound_rows，DEDUP_FIRST 缺伴随不回退；
3. 未绑定与占位行 + 全未绑定明细：CurProcessNone / *_temp / 未绑定全入 manifest；0 有效事件
   全未绑定时预检与 422 均返回完整 unbound_rows 明细；
4. DEDUP_FIRST 多轮有效分片并集：同 host 已关闭 FINAL + SUPPLEMENT 独有发生均保留归并，
   未关闭轮次不计入有效覆盖；
5. snapshot_cutoff 双冻结：截止后登记的新分片与截止后关闭的补充轮次均不改变截止前选择与统计；
6. 按源 run build 归属与 slug 防碰撞：同串号跨 run 换 build 各自归属源 run build；
   同基础 slug 的不同 build 产出互不覆写的独立预览与复核文件；
7. 发布工具只读隔离（§7① 回归钉子）：原发布目录含 23 字节 config/blacklist.txt 且只读时，
   模拟工具写空当前目录 config/blacklist.txt 后原文件字节与 SHA256 完全不变；校验 release.json；
8. 工具输出强契约校验：错误 schema / 输入 sha256 / 行 path / branch / group / 非法索引 /
   缺失或损坏终表 .xls 均 fail-closed 返回结构化 JiraHandoffError(502)；
9. 坏钟（2010 占位）回退 DLE.detected_at 判定本次/基线，仅基线只标注不自动剔除；
10. 15 列契约（含 "ExpType " 尾空格、整数 DeviceCount）+ PENDING_EXPORT + manifest_sha256；
11. 平台字面契约：platform="unisoc"、"MTK "、"MTK" 均返回 400 fail-closed；
12. B3 公式注入中和 + 零 aee_extract / -m 0 调用、零 15.4 写入；
13. 受控根祖先 symlink、bundle 越根/非普通文件及输入分片越出受控中心根全部拒绝。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
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


def _write_org_xls(path: Path, rows: list[dict], *, headers: tuple[str, ...] = ORG_COLUMNS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = xlwt.Workbook(encoding="utf-8")
    ws = wb.add_sheet("aeeexp", cell_overwrite_ok=True)
    for col_idx, header in enumerate(headers):
        ws.write(0, col_idx, header)
    for idx, row in enumerate(rows, start=1):
        full_map = {
            "id": idx - 1,
            "path": row.get("Path", ""),
            "version": row.get("Version", "V100"),
            "exptime": row.get("ExpTime", "2026-11-20 12:00:00"),
            "expclass": row.get("ExpClass", "Java (JE)"),
            "exptype": row.get("ExpType", "JE"),
            "curprocess": row.get("CurProcess", "com.demo.app"),
            "package": row.get("Package", "com.demo.app"),
            "detail": row.get("Detail", "NullPointerException"),
            "causedby": row.get("CausedBy", "java.lang.NullPointerException: demo"),
            "extratag": row.get("extraTag", ""),
            "count": row.get("Count", 1),
            "activity": row.get("Activity", "MainActivity"),
            "deviceid": row.get("DeviceId", "SN001"),
            "rom_ram": row.get("Rom_Ram", "8+256"),
        }
        for col_idx, header in enumerate(headers):
            ws.write(idx, col_idx, full_map.get(header.strip().lower(), ""))
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


def _create_stub_dedup_org_tool(
    tmp_path: Path,
    *,
    emit_members: bool = True,
    emit_final_xls: bool = True,
    corrupt_final_xls: bool = False,
    release_version: str | None = "2.1.1-runtime-20261106",
    release_commit: str | None = "dc5d9c1fe22273dcfc28e41b285e8fdb3bfb6694",
    write_release: bool = True,
    truncate_blacklist_in_cwd: bool = False,
    blacklist_bytes: bytes | None = None,
    mutate_members_mode: str | None = None,
) -> dict[str, str]:
    """构建真实可执行的 Python stub 工具（含 release.json、完整 *_members.json 强契约与故障注入）。"""
    tool_dir = tmp_path / "stub_scan_tool"
    tool_dir.mkdir(parents=True, exist_ok=True)
    if write_release:
        rel_obj: dict[str, object] = {}
        if release_version is not None:
            rel_obj["version"] = release_version
        if release_commit is not None:
            rel_obj["git_commit"] = release_commit
        (tool_dir / "release.json").write_text(json.dumps(rel_obj), encoding="utf-8")
    if blacklist_bytes is not None:
        cfg = tool_dir / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "blacklist.txt").write_bytes(blacklist_bytes)

    script = tool_dir / "start_log_scan.py"
    code = f"""
import hashlib, json, sys
from pathlib import Path
import xlrd, xlwt

args = sys.argv[1:]
if "-m" in args or "aee_extract" in " ".join(args):
    sys.stderr.write("FORBIDDEN_M_MODE_CALLED\\n")
    sys.exit(99)
if "-dedup_org" not in args:
    sys.stderr.write("missing -dedup_org\\n")
    sys.exit(2)

# 复现 start-log-scan 2.1.1 BaseScan.py:2006 离线写空 config/blacklist.txt 并写 logs/tmp
if {truncate_blacklist_in_cwd!r}:
    bl = Path.cwd() / "config" / "blacklist.txt"
    bl.parent.mkdir(parents=True, exist_ok=True)
    bl.write_text("", encoding="utf-8")
    (Path.cwd() / "logs").mkdir(exist_ok=True)
    (Path.cwd() / "logs" / "run.log").write_text("ran\\n", encoding="utf-8")
    (Path.cwd() / "tmp").mkdir(exist_ok=True)
    (Path.cwd() / "tmp" / "scratch.tmp").write_text("ok\\n", encoding="utf-8")

org_path = Path(args[args.index("-dedup_org") + 1])
org_sha = hashlib.sha256(org_path.read_bytes()).hexdigest()
book = xlrd.open_workbook(str(org_path))
sheet = book.sheet_by_name("aeeexp")
headers = [str(v).strip() for v in sheet.row_values(0)]
col = {{h.lower(): i for i, h in enumerate(headers)}}

rows_out = []
buckets = {{}}
bucket_order = []
for i in range(1, sheet.nrows):
    vals = sheet.row_values(i)
    row_path = str(vals[col["path"]]).strip()
    pkg = str(vals[col["package"]]).strip()
    if pkg == "com.filtered.discard":
        rows_out.append({{
            "org_row": i,
            "path": row_path,
            "branch": "filtered",
            "filter_reason": "discard_dbg_package",
            "group": None,
        }})
        continue
    key = (str(vals[col["expclass"]]).strip(), pkg, str(vals[col["causedby"]]).strip())
    if key not in buckets:
        buckets[key] = []
        bucket_order.append(key)
    buckets[key].append((i, row_path, vals))

groups = []
wb = xlwt.Workbook(encoding="utf-8")
ws = wb.add_sheet("aeeexp")
for c_idx, h in enumerate(sheet.row_values(0)):
    ws.write(0, c_idx, h)

for g_idx, key in enumerate(bucket_order, start=1):
    members = buckets[key]
    member_nums = [m[0] for m in members]
    rep_num, _, rep_vals = members[0]
    dev_ids = sorted({{str(m[2][col["deviceid"]]).strip() for m in members}})
    for m_num, m_path, _ in members:
        rows_out.append({{
            "org_row": m_num,
            "path": m_path,
            "branch": "similarity" if len(member_nums) > 1 else "singleton",
            "group": g_idx,
        }})
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
if {emit_final_xls!r}:
    if {corrupt_final_xls!r}:
        dedup_xls.write_bytes(b"NOT_AN_XLS_BINARY_CORRUPT_HEADER")
    else:
        wb.save(str(dedup_xls))

final_sha = hashlib.sha256(dedup_xls.read_bytes()).hexdigest() if dedup_xls.is_file() else ""
payload = {{
    "schema": "stp.scan_membership",
    "schema_version": "1.0",
    "meta": {{
        "tool": {{"name": "start-log-scan", "version": "2.1.1"}},
        "reports": {{
            "org_xls": {{"sha256": org_sha}},
            "final_xls": {{"sha256": final_sha}},
        }},
    }},
    "rows": rows_out,
    "groups": groups,
}}

mut = {mutate_members_mode!r}
if mut == "bad_schema":
    payload["schema"] = "stp.wrong_schema"
elif mut == "bad_sha256":
    payload["meta"]["reports"]["org_xls"]["sha256"] = "0" * 64
elif mut == "bad_path":
    payload["rows"][0]["path"] = "/tampered/wrong/path"
elif mut == "bad_branch":
    payload["rows"][0]["branch"] = "unknown_branch"
elif mut == "bad_group_mismatch":
    payload["groups"][0]["group"] = 999
elif mut == "bad_member_index_type":
    payload["groups"][0]["member_rows"] = ["not-an-int"]
elif mut == "bad_final_row_oob":
    payload["groups"][0]["final_row"] = 99

if {emit_members!r}:
    members_json = org_path.with_name(org_path.stem + "_dedup_org_20261127_110000_members.json")
    members_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
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


# ── 反例 1：覆盖率硬门禁（缺主机、坏表/错表头空表/缺必需列、非期望 host，无放行开关）


def test_b11_u1_01_hard_coverage_gate_missing_corrupt_bad_header_and_unexpected_host(tmp_path):
    storage = tmp_path / "center"
    tool = _create_stub_dedup_org_tool(tmp_path)

    # 1a) 缺主机：硬阻断 409，无 allow_incomplete_coverage 参数
    db = _setup_base_db(run_id=101, hosts=("host-a", "host-b"))
    t0 = db.runs[101].started_at
    dle_id = uuid4()
    db.dles.append(
        SimpleNamespace(
            id=dle_id, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a",
            occurrence_key="occ-key-001", detected_at=t0 + timedelta(minutes=10),
            local_path="/mnt/hdd/aee_events/1120/SN001/aee_exp/2026_1120_181000_db.00.JE",
            remote_path="/mnt/stp-aee/devices/101/SN001/2026_1120_181000_db.00.JE",
        )
    )
    org_a = storage / "dedup" / "101" / "mtk" / "host-a_Result_org.xls"
    row_path = "/mnt/hdd/.stp-scan/pr101/SN001/aee_exp/2026_1120_181000_db.00.JE/db.00.JE.dbg.DEC/__exp_main.txt"
    _write_org_xls(org_a, [{"Path": row_path, "DeviceId": "SN001", "ExpTime": "2026-11-20 18:10:00"}])
    _write_mapping_json(org_a, [{"sheet": "aeeexp", "row": 2, "path": row_path, "mapped": True, "dle_id": str(dle_id), "occurrence_key": "occ-key-001"}])
    db.artifacts.append(
        SimpleNamespace(id=1, plan_run_id=101, host_id="host-a", storage_uri=str(org_a), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=2))
    )

    preflight = run_handoff_preflight(db, plan_run_ids=[101], platform="mtk", storage_root=str(storage))
    assert preflight["gates"]["coverage_complete"] is False
    assert preflight["runs"][0]["missing_hosts"] == ["host-b"]

    with pytest.raises(JiraHandoffError) as exc_info:
        create_handoff_bundle(db, plan_run_ids=[101], platform="mtk", storage_root=str(storage), scan_tool=tool)
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "handoff_coverage_incomplete"
    assert not (storage / "_meta" / "handoff").exists()
    with pytest.raises(TypeError):
        create_handoff_bundle(  # type: ignore[call-arg]
            db, plan_run_ids=[101], platform="mtk", allow_incomplete_coverage=True,
            storage_root=str(storage), scan_tool=tool,
        )

    # 1b) 错表头空表（0 数据行但表头错误）→ host-b 不得计为覆盖，仍 409
    org_b_bad_hdr = storage / "dedup" / "101" / "mtk" / "host-b_Result_org.xls"
    _write_org_xls(org_b_bad_hdr, [], headers=("Foo", "Bar"))
    db.artifacts.append(
        SimpleNamespace(id=2, plan_run_id=101, host_id="host-b", storage_uri=str(org_b_bad_hdr), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=2))
    )
    pre_bad_hdr = run_handoff_preflight(db, plan_run_ids=[101], platform="mtk", storage_root=str(storage))
    assert pre_bad_hdr["gates"]["coverage_complete"] is False
    assert "host-b" in pre_bad_hdr["runs"][0]["missing_hosts"]

    # 1c) 缺必需列（有 Path 但缺 DeviceId / CausedBy）→ 仍不得计为覆盖
    _write_org_xls(org_b_bad_hdr, [{"Path": "/x"}], headers=("Id", "Path", "Version"))
    pre_missing_col = run_handoff_preflight(db, plan_run_ids=[101], platform="mtk", storage_root=str(storage))
    assert pre_missing_col["gates"]["coverage_complete"] is False
    assert "host-b" in pre_missing_col["runs"][0]["missing_hosts"]

    # 1d) 补齐合法空表后覆盖完整，但若多出非期望 host-extra → 仍 409 阻塞
    _write_org_xls(org_b_bad_hdr, [])
    org_extra = storage / "dedup" / "101" / "mtk" / "host-extra_Result_org.xls"
    _write_org_xls(org_extra, [])
    db.artifacts.append(
        SimpleNamespace(id=3, plan_run_id=101, host_id="host-extra", storage_uri=str(org_extra), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=2))
    )
    pre_extra = run_handoff_preflight(db, plan_run_ids=[101], platform="mtk", storage_root=str(storage))
    assert pre_extra["gates"]["coverage_complete"] is False
    assert pre_extra["runs"][0]["unexpected_hosts"] == ["host-extra"]
    with pytest.raises(JiraHandoffError) as exc_extra:
        create_handoff_bundle(db, plan_run_ids=[101], platform="mtk", storage_root=str(storage), scan_tool=tool)
    assert exc_extra.value.status_code == 409


# ── 反例 2：发生身份严格绑定（跨 run 归并 + 错绑/冲突/无 key 拦截 + 历史 CURRENT 唯一回退）


def test_b11_u1_02_strict_occurrence_identity_binding_and_narrowed_compat(tmp_path):
    storage = tmp_path / "center"
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    db = _setup_base_db(run_id=101, hosts=("host-a", "host-b"), started_at=t0)
    db.runs[102] = SimpleNamespace(
        id=102, plan_id=1, status="FAILED",
        started_at=t0 + timedelta(hours=3), ended_at=t0 + timedelta(hours=5),
        project_id=10, build_version="MLD-NX2-16-261117V108",
        run_context={"device_builds": {"1001": "MLD-NX2-16-261117V108"}},
    )
    db.jobs.append(SimpleNamespace(plan_run_id=102, device_id=1001, host_id="host-a"))
    db.pr_hosts.append(SimpleNamespace(plan_run_id=102, host_id="host-a"))

    dle_r101_ok, dle_r101_host_b, dle_r101_nokey = uuid4(), uuid4(), uuid4()
    dle_r102_shared, dle_r102_compat = uuid4(), uuid4()
    dle_r102_ambig_1, dle_r102_ambig_2 = uuid4(), uuid4()
    db.dles.extend([
        SimpleNamespace(
            id=dle_r101_ok, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a",
            occurrence_key="occ-shared-999", detected_at=t0 + timedelta(minutes=20),
            local_path="/hdd/SN001/aee_exp/e_ok", remote_path="/mnt/stp-aee/devices/101/SN001/e_ok",
        ),
        SimpleNamespace(
            id=dle_r101_host_b, serial="SN002", platform="mtk", plan_run_id=101, host_id="host-b",
            occurrence_key="occ-host-b", detected_at=t0 + timedelta(minutes=21),
            local_path="/hdd/SN002/aee_exp/e_hb", remote_path=None,
        ),
        SimpleNamespace(
            id=dle_r101_nokey, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a",
            occurrence_key=None, detected_at=t0 + timedelta(minutes=22),
            local_path="/hdd/SN001/aee_exp/e_nokey", remote_path=None,
        ),
        SimpleNamespace(
            id=dle_r102_shared, serial="SN001", platform="mtk", plan_run_id=102, host_id="host-a",
            occurrence_key="occ-shared-999", detected_at=t0 + timedelta(hours=3, minutes=5),
            local_path="/hdd/SN001/aee_exp/e_ok_r102", remote_path="/mnt/stp-aee/evidence/101_MLD/SN001/e_ok_r102",
        ),
        SimpleNamespace(
            id=dle_r102_compat, serial="SN001", platform="mtk", plan_run_id=102, host_id="host-a",
            occurrence_key=None, detected_at=t0 + timedelta(hours=4),
            local_path="/hdd/SN001/aee_exp/e_compat_unique", remote_path=None,
        ),
        SimpleNamespace(
            id=dle_r102_ambig_1, serial="SN001", platform="mtk", plan_run_id=102, host_id="host-a",
            occurrence_key=None, detected_at=t0 + timedelta(hours=4, minutes=1),
            local_path="/hdd/SN001/aee_exp/e_ambig", remote_path=None,
        ),
        SimpleNamespace(
            id=dle_r102_ambig_2, serial="SN001", platform="mtk", plan_run_id=102, host_id="host-a",
            occurrence_key=None, detected_at=t0 + timedelta(hours=4, minutes=2),
            local_path="/hdd/SN001/aee_exp/e_ambig", remote_path=None,
        ),
    ])

    # Run 101 host-a（含 1 合法行 + 5 种错绑反例：缺 DLE、key 冲突、跨 run、跨 host、有伴随却无 key）
    org101_a = storage / "dedup" / "101" / "mtk" / "host-a_Result_org.xls"
    r101_rows = [
        {"Path": "/s/SN001/aee_exp/e_ok/d.DEC/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "NPE-A"},
        {"Path": "/s/SN001/aee_exp/e_missing_dle/d.DEC/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "Bad1"},
        {"Path": "/s/SN001/aee_exp/e_conflict/d.DEC/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "Bad2"},
        {"Path": "/s/SN001/aee_exp/e_cross_run/d.DEC/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "Bad3"},
        {"Path": "/s/SN002/aee_exp/e_cross_host/d.DEC/__exp_main.txt", "DeviceId": "SN002", "CausedBy": "Bad4"},
        {"Path": "/s/SN001/aee_exp/e_nokey/d.DEC/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "Bad5"},
    ]
    _write_org_xls(org101_a, r101_rows)
    _write_mapping_json(org101_a, [
        {"sheet": "aeeexp", "row": 2, "path": r101_rows[0]["Path"], "mapped": True, "dle_id": str(dle_r101_ok), "occurrence_key": "occ-shared-999"},
        {"sheet": "aeeexp", "row": 3, "path": r101_rows[1]["Path"], "mapped": True, "dle_id": str(uuid4()), "occurrence_key": "occ-ghost"},
        {"sheet": "aeeexp", "row": 4, "path": r101_rows[2]["Path"], "mapped": True, "dle_id": str(dle_r101_ok), "occurrence_key": "occ-CONFLICT"},
        {"sheet": "aeeexp", "row": 5, "path": r101_rows[3]["Path"], "mapped": True, "dle_id": str(dle_r102_shared), "occurrence_key": "occ-shared-999"},
        {"sheet": "aeeexp", "row": 6, "path": r101_rows[4]["Path"], "mapped": True, "dle_id": str(dle_r101_host_b), "occurrence_key": "occ-host-b"},
        {"sheet": "aeeexp", "row": 7, "path": r101_rows[5]["Path"], "mapped": True, "dle_id": str(dle_r101_nokey), "occurrence_key": ""},
    ])

    org101_b = storage / "dedup" / "101" / "mtk" / "host-b_Result_org.xls"
    _write_org_xls(org101_b, [])

    # Run 102 host-a（无伴随历史 CURRENT：跨 run 同 key + 唯一锚定 compat + 无 key 多候选歧义）
    org102_a = storage / "dedup" / "102" / "mtk" / "host-a_Result_org.xls"
    r102_rows = [
        {"Path": "/s/SN001/aee_exp/e_ok_r102/d.DEC/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "NPE-A"},
        {"Path": "/s/SN001/aee_exp/e_compat_unique/d.DEC/__exp_main.txt", "DeviceId": "SN001", "ExpClass": "ANR", "ExpType": "ANR", "CausedBy": "ANR-B"},
        {"Path": "/s/SN001/aee_exp/e_ambig/d.DEC/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "AmbigErr"},
    ]
    _write_org_xls(org102_a, r102_rows)

    db.artifacts.extend([
        SimpleNamespace(id=1, plan_run_id=101, host_id="host-a", storage_uri=str(org101_a), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=2)),
        SimpleNamespace(id=2, plan_run_id=101, host_id="host-b", storage_uri=str(org101_b), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=2)),
        SimpleNamespace(id=3, plan_run_id=102, host_id="host-a", storage_uri=str(org102_a), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=5)),
    ])

    manifest = create_handoff_bundle(
        db, plan_run_ids=[101, 102], platform="mtk",
        storage_root=str(storage), scan_tool=_create_stub_dedup_org_tool(tmp_path),
    )
    assert manifest["identity_gate"]["unique_events"] == 2
    assert manifest["identity_gate"]["keyed_rows"] == 2
    assert manifest["identity_gate"]["compat_fallback_rows"] == 1
    assert manifest["identity_gate"]["unbound_by_reason"] == {
        "dle_host_mismatch": 1,
        "dle_not_found": 1,
        "dle_plan_run_mismatch": 1,
        "missing_mapping_ambiguous_dle": 1,
        "missing_occurrence_key": 1,
        "occurrence_key_conflict": 1,
    }
    by_cause = {iss["form_fields"]["CausedBy"]: iss for iss in manifest["builds"][0]["issues"]}
    assert by_cause["NPE-A"]["count"] == 1
    assert by_cause["NPE-A"]["source_runs"] == [101, 102]
    assert by_cause["NPE-A"]["representative"]["evidence_tier"] == "evidence"
    assert by_cause["ANR-B"]["representative"]["identity_source"] == "compat_serial_entry"

    # DEDUP_FIRST 缺伴随映射不得走推断回退（必须全部记入 unbound_rows）
    db_df = _setup_base_db(run_id=103, hosts=("host-a",), started_at=t0)
    db_df.partition_modes.append(SimpleNamespace(plan_run_id=103, platform="mtk", mode="DEDUP_FIRST"))
    dle_df = uuid4()
    db_df.dles.append(
        SimpleNamespace(
            id=dle_df, serial="SN001", platform="mtk", plan_run_id=103, host_id="host-a",
            occurrence_key=None, detected_at=t0 + timedelta(minutes=10),
            local_path="/hdd/SN001/aee_exp/e_df_nomap", remote_path=None,
        )
    )
    org_df = storage / "dedup" / "103" / "mtk" / "host-a_Result_org.xls"
    _write_org_xls(org_df, [{"Path": "/s/SN001/aee_exp/e_df_nomap/d.DEC/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "DFNoMap"}])
    db_df.scan_rounds.append(
        SimpleNamespace(
            id=910, plan_run_id=103, platform="mtk", round_no=1, kind="FINAL", status="CLOSED",
            closed_at=t0 + timedelta(hours=1),
            expected_set=[{"host_id": "host-a", "platform": "mtk", "reports": [{"storage_uri": str(org_df)}]}],
        )
    )
    pre_df = run_handoff_preflight(db_df, plan_run_ids=[103], platform="mtk", storage_root=str(storage))
    assert pre_df["identity_gate"]["unique_events"] == 0
    assert pre_df["identity_gate"]["compat_fallback_rows"] == 0
    assert pre_df["identity_gate"]["unbound_by_reason"] == {"dedup_first_missing_mapping": 1}


# ── 反例 3：CurProcessNone / *_temp / 未绑定全入 manifest + 全未绑定预检与 422 明细


def test_b11_u1_03_placeholders_unbound_and_all_unbound_detail_export(tmp_path):
    assert is_placeholder_org_row({"Path": "/a/db.00.JE_temp/x", "CurProcess": "app"}) == "temp_path_placeholder"
    assert is_placeholder_org_row({"Path": "/a/db.00.JE/x", "CurProcess": "CurProcessNone"}) == "vendor_parse_placeholder"

    storage = tmp_path / "center"
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
    org = storage / "dedup" / "101" / "mtk" / "host-a_Result_org.xls"
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
        storage_root=str(storage), scan_tool=_create_stub_dedup_org_tool(tmp_path),
    )
    assert manifest["identity_gate"]["total_org_rows"] == 4
    assert manifest["identity_gate"]["unique_events"] == 1
    assert manifest["identity_gate"]["unbound_by_reason"] == {
        "outside_scope": 1,
        "temp_path_placeholder": 1,
        "vendor_parse_placeholder": 1,
    }
    assert len(manifest["unbound_rows"]) == 3

    # 全未绑定（0 有效事件）：preflight 与 422 均返回完整 unbound_rows 明细
    db_zero = _setup_base_db(run_id=102, hosts=("host-a",), started_at=t0)
    org_zero = storage / "dedup" / "102" / "mtk" / "host-a_Result_org.xls"
    rows_zero = [
        {"Path": "/scan/SN001/aee_exp/db.01.JE_temp/db.rand/__exp_main.txt", "DeviceId": "SN001"},
        {"Path": "/scan/SN001/aee_exp/db.02.JE/db.02.JE.dbg.DEC/__exp_main.txt", "DeviceId": "SN001", "CurProcess": "CurProcessNone"},
    ]
    _write_org_xls(org_zero, rows_zero)
    _write_mapping_json(org_zero, [
        {"sheet": "aeeexp", "row": 2, "path": rows_zero[0]["Path"], "mapped": False, "unmapped_reason": "no_source"},
        {"sheet": "aeeexp", "row": 3, "path": rows_zero[1]["Path"], "mapped": False, "unmapped_reason": "no_source"},
    ])
    db_zero.artifacts.append(
        SimpleNamespace(id=2, plan_run_id=102, host_id="host-a", storage_uri=str(org_zero), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=1))
    )
    pre_zero = run_handoff_preflight(db_zero, plan_run_ids=[102], platform="mtk", storage_root=str(storage))
    assert pre_zero["identity_gate"]["unique_events"] == 0
    assert len(pre_zero["unbound_rows"]) == 2
    assert {r["reason"] for r in pre_zero["unbound_rows"]} == {"temp_path_placeholder", "vendor_parse_placeholder"}

    with pytest.raises(JiraHandoffError) as exc_422:
        create_handoff_bundle(
            db_zero, plan_run_ids=[102], platform="mtk",
            storage_root=str(storage), scan_tool=_create_stub_dedup_org_tool(tmp_path),
        )
    assert exc_422.value.status_code == 422
    assert exc_422.value.code == "handoff_no_input"
    assert exc_422.value.payload is not None
    assert len(exc_422.value.payload["unbound_rows"]) == 2
    assert exc_422.value.payload["identity_gate"]["unbound_rows"] == 2


# ── 反例 4：DEDUP_FIRST 多轮有效分片并集 + 未关闭轮次不计入有效覆盖 ─────────────


def test_b11_u1_04_dedup_first_multi_round_union_and_unclosed_round_excluded(tmp_path):
    storage = tmp_path / "center"
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    db = _setup_base_db(run_id=301, hosts=("host-a", "host-b"), started_at=t0)
    db.partition_modes.append(SimpleNamespace(plan_run_id=301, platform="mtk", mode="DEDUP_FIRST"))

    dle_final, dle_supp = uuid4(), uuid4()
    db.dles.extend([
        SimpleNamespace(
            id=dle_final, serial="SN001", platform="mtk", plan_run_id=301, host_id="host-a",
            occurrence_key="occ-df-final", detected_at=t0 + timedelta(minutes=10),
            local_path="/hdd/SN001/aee_exp/e_final", remote_path="/mnt/stp-aee/evidence/301/SN001/e_final",
        ),
        SimpleNamespace(
            id=dle_supp, serial="SN001", platform="mtk", plan_run_id=301, host_id="host-a",
            occurrence_key="occ-df-supp", detected_at=t0 + timedelta(minutes=40),
            local_path="/hdd/SN001/aee_exp/e_supp", remote_path="/mnt/stp-aee/evidence/301/SN001/e_supp",
        ),
    ])
    org_final = storage / "dedup" / "301" / "mtk" / "r1_host-a_Result_org.xls"
    p_final = "/s/SN001/aee_exp/e_final/d.DEC/__exp_main.txt"
    _write_org_xls(org_final, [{"Path": p_final, "DeviceId": "SN001", "CausedBy": "FinalCrash"}])
    map_final = _write_mapping_json(org_final, [{"sheet": "aeeexp", "row": 2, "path": p_final, "mapped": True, "dle_id": str(dle_final), "occurrence_key": "occ-df-final"}])

    org_supp = storage / "dedup" / "301" / "mtk" / "r2_host-a_Result_org.xls"
    p_supp = "/s/SN001/aee_exp/e_supp/d.DEC/__exp_main.txt"
    _write_org_xls(org_supp, [{"Path": p_supp, "DeviceId": "SN001", "CausedBy": "SuppCrash"}])
    map_supp = _write_mapping_json(org_supp, [{"sheet": "aeeexp", "row": 2, "path": p_supp, "mapped": True, "dle_id": str(dle_supp), "occurrence_key": "occ-df-supp"}])

    db.scan_rounds.extend([
        SimpleNamespace(
            id=901, plan_run_id=301, platform="mtk", round_no=1, kind="FINAL", status="CLOSED",
            closed_at=t0 + timedelta(hours=1),
            expected_set=[
                {"host_id": "host-a", "platform": "mtk", "coverage": "RECEIVED", "reports": [{"storage_uri": str(org_final), "mapping_uri": str(map_final)}]},
                {"host_id": "host-b", "platform": "mtk", "coverage": "EMPTY", "reports": [{"empty_receipt": True}]},
            ],
        ),
        SimpleNamespace(
            id=902, plan_run_id=301, platform="mtk", round_no=2, kind="SUPPLEMENT", status="CLOSED",
            closed_at=t0 + timedelta(hours=2),
            expected_set=[
                {"host_id": "host-a", "platform": "mtk", "coverage": "RECEIVED", "reports": [{"storage_uri": str(org_supp), "mapping_uri": str(map_supp)}]},
            ],
        ),
    ])

    manifest = create_handoff_bundle(
        db, plan_run_ids=[301], platform="mtk",
        storage_root=str(storage), scan_tool=_create_stub_dedup_org_tool(tmp_path),
    )
    assert manifest["gates"]["coverage_complete"] is True
    assert manifest["identity_gate"]["unique_events"] == 2
    causes = {iss["form_fields"]["CausedBy"] for iss in manifest["builds"][0]["issues"]}
    assert causes == {"FinalCrash", "SuppCrash"}

    # 未关闭轮次不计入有效覆盖
    db_open = _setup_base_db(run_id=302, hosts=("host-a",), started_at=t0)
    db_open.partition_modes.append(SimpleNamespace(plan_run_id=302, platform="mtk", mode="DEDUP_FIRST"))
    db_open.scan_rounds.append(
        SimpleNamespace(
            id=903, plan_run_id=302, platform="mtk", round_no=1, kind="FINAL", status="RUNNING",
            closed_at=None,
            expected_set=[{"host_id": "host-a", "platform": "mtk", "reports": [{"storage_uri": str(org_final), "mapping_uri": str(map_final)}]}],
        )
    )
    pre_open = run_handoff_preflight(db_open, plan_run_ids=[302], platform="mtk", storage_root=str(storage))
    assert pre_open["gates"]["coverage_complete"] is False
    assert pre_open["runs"][0]["missing_hosts"] == ["host-a"]


# ── 反例 5：snapshot_cutoff 双冻结（DLE 观测 + 报表分片选择同时冻结）────────────


def test_b11_u1_05_snapshot_cutoff_freezes_both_reports_and_dles(tmp_path):
    storage = tmp_path / "center"
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    cutoff = t0 + timedelta(hours=2)

    db = _setup_base_db(run_id=401, hosts=("host-a",), started_at=t0)
    dle_old, dle_new, dle_late = uuid4(), uuid4(), uuid4()
    db.dles.extend([
        SimpleNamespace(id=dle_old, serial="SN001", platform="mtk", plan_run_id=401, host_id="host-a", occurrence_key="occ-old", detected_at=t0 + timedelta(minutes=10), local_path="/hdd/SN001/aee_exp/e_old", remote_path=None),
        SimpleNamespace(id=dle_new, serial="SN001", platform="mtk", plan_run_id=401, host_id="host-a", occurrence_key="occ-new", detected_at=t0 + timedelta(minutes=20), local_path="/hdd/SN001/aee_exp/e_new", remote_path=None),
        SimpleNamespace(id=dle_late, serial="SN001", platform="mtk", plan_run_id=401, host_id="host-a", occurrence_key="occ-late", detected_at=cutoff + timedelta(minutes=30), local_path="/hdd/SN001/aee_exp/e_late", remote_path=None),
    ])

    org_before = storage / "dedup" / "401" / "mtk" / "host-a_v1_org.xls"
    rows_before = [
        {"Path": "/s/SN001/aee_exp/e_old/d.DEC/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "BeforeCutoff"},
        {"Path": "/s/SN001/aee_exp/e_late/d.DEC/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "LateDLE"},
    ]
    _write_org_xls(org_before, rows_before)
    _write_mapping_json(org_before, [
        {"sheet": "aeeexp", "row": 2, "path": rows_before[0]["Path"], "mapped": True, "dle_id": str(dle_old), "occurrence_key": "occ-old"},
        {"sheet": "aeeexp", "row": 3, "path": rows_before[1]["Path"], "mapped": True, "dle_id": str(dle_late), "occurrence_key": "occ-late"},
    ])

    org_after = storage / "dedup" / "401" / "mtk" / "host-a_v2_org.xls"
    rows_after = [{"Path": "/s/SN001/aee_exp/e_new/d.DEC/__exp_main.txt", "DeviceId": "SN001", "CausedBy": "AfterCutoffReport"}]
    _write_org_xls(org_after, rows_after)
    _write_mapping_json(org_after, [{"sheet": "aeeexp", "row": 2, "path": rows_after[0]["Path"], "mapped": True, "dle_id": str(dle_new), "occurrence_key": "occ-new"}])

    db.artifacts.extend([
        SimpleNamespace(id=1, plan_run_id=401, host_id="host-a", storage_uri=str(org_before), artifact_type="scan_result_xls", created_at=cutoff - timedelta(minutes=5)),
        SimpleNamespace(id=2, plan_run_id=401, host_id="host-a", storage_uri=str(org_after), artifact_type="scan_result_xls", created_at=cutoff + timedelta(minutes=5)),
    ])

    manifest = create_handoff_bundle(
        db, plan_run_ids=[401], platform="mtk", snapshot_cutoff=cutoff,
        storage_root=str(storage), scan_tool=_create_stub_dedup_org_tool(tmp_path),
    )
    assert manifest["identity_gate"]["unique_events"] == 1
    assert manifest["identity_gate"]["after_cutoff_skipped_rows"] == 1
    assert manifest["identity_gate"]["keyed_rows"] == 1
    assert manifest["builds"][0]["issues"][0]["form_fields"]["CausedBy"] == "BeforeCutoff"


# ── 反例 6：按源 run build 归属 + 同基础 slug 防碰撞 ───────────────────────────


def test_b11_u1_06_source_run_build_ownership_and_slug_collision_prevention(tmp_path):
    storage = tmp_path / "center"
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    # 两个不同 build 名称规范化后会得到同一基础 slug（"/" 与 "_" 均规范化为 "_"）
    build_a = "MLD-NX2/V108"
    build_b = "MLD-NX2_V108"
    db = _setup_base_db(run_id=501, hosts=("host-a",), started_at=t0, build=build_a)
    db.runs[502] = SimpleNamespace(
        id=502, plan_id=1, status="SUCCESS",
        started_at=t0 + timedelta(hours=3), ended_at=t0 + timedelta(hours=5),
        project_id=10, build_version=build_b,
        run_context={"device_builds": {"1001": build_b}},
    )
    db.jobs.append(SimpleNamespace(plan_run_id=502, device_id=1001, host_id="host-a"))
    db.pr_hosts.append(SimpleNamespace(plan_run_id=502, host_id="host-a"))

    d1, d2 = uuid4(), uuid4()
    db.dles.extend([
        SimpleNamespace(id=d1, serial="SN001", platform="mtk", plan_run_id=501, host_id="host-a", occurrence_key="occ-ba", detected_at=t0 + timedelta(minutes=10), local_path="/hdd/SN001/aee_exp/e_ba", remote_path=None),
        SimpleNamespace(id=d2, serial="SN001", platform="mtk", plan_run_id=502, host_id="host-a", occurrence_key="occ-bb", detected_at=t0 + timedelta(hours=3, minutes=10), local_path="/hdd/SN001/aee_exp/e_bb", remote_path=None),
    ])
    org501 = storage / "dedup" / "501" / "mtk" / "host-a_Result_org.xls"
    p1 = "/s/SN001/aee_exp/e_ba/d.DEC/__exp_main.txt"
    _write_org_xls(org501, [{"Path": p1, "DeviceId": "SN001", "Version": build_a, "CausedBy": "CrashOnBuildA"}])
    _write_mapping_json(org501, [{"sheet": "aeeexp", "row": 2, "path": p1, "mapped": True, "dle_id": str(d1), "occurrence_key": "occ-ba"}])

    org502 = storage / "dedup" / "502" / "mtk" / "host-a_Result_org.xls"
    p2 = "/s/SN001/aee_exp/e_bb/d.DEC/__exp_main.txt"
    _write_org_xls(org502, [{"Path": p2, "DeviceId": "SN001", "Version": build_b, "CausedBy": "CrashOnBuildB"}])
    _write_mapping_json(org502, [{"sheet": "aeeexp", "row": 2, "path": p2, "mapped": True, "dle_id": str(d2), "occurrence_key": "occ-bb"}])

    db.artifacts.extend([
        SimpleNamespace(id=1, plan_run_id=501, host_id="host-a", storage_uri=str(org501), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=2)),
        SimpleNamespace(id=2, plan_run_id=502, host_id="host-a", storage_uri=str(org502), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=5)),
    ])

    manifest = create_handoff_bundle(
        db, plan_run_ids=[501, 502], platform="mtk",
        storage_root=str(storage), scan_tool=_create_stub_dedup_org_tool(tmp_path),
    )
    by_build = {b["build"]: b for b in manifest["builds"]}
    assert set(by_build) == {build_a, build_b}
    assert by_build[build_a]["issues"][0]["form_fields"]["CausedBy"] == "CrashOnBuildA"
    assert by_build[build_b]["issues"][0]["form_fields"]["CausedBy"] == "CrashOnBuildB"
    assert by_build[build_a]["build_slug"] != by_build[build_b]["build_slug"]
    assert by_build[build_a]["artifacts"]["preview_xls"]["rel_path"] != by_build[build_b]["artifacts"]["preview_xls"]["rel_path"]
    assert by_build[build_a]["artifacts"]["review_workbook"]["rel_path"] != by_build[build_b]["artifacts"]["review_workbook"]["rel_path"]

    pa, _, _ = resolve_handoff_download_file(manifest["handoff_id"], "preview_xls", build=build_a, storage_root=str(storage))
    pb, _, _ = resolve_handoff_download_file(manifest["handoff_id"], "preview_xls", build=build_b, storage_root=str(storage))
    assert pa != pb
    assert xlrd.open_workbook(str(pa)).sheet_by_name("aeeexp").row_values(1)[2] == build_a
    assert xlrd.open_workbook(str(pb)).sheet_by_name("aeeexp").row_values(1)[2] == build_b


# ── 反例 7：发布工具只读隔离（§7① 回归钉子）与 release.json 校验 ───────────────


def test_b11_u1_07_published_tool_readonly_isolation_and_release_identity(tmp_path):
    storage = tmp_path / "center"
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    db = _setup_base_db(run_id=601, hosts=("host-a",), started_at=t0)
    d1 = uuid4()
    db.dles.append(
        SimpleNamespace(id=d1, serial="SN001", platform="mtk", plan_run_id=601, host_id="host-a", occurrence_key="occ-iso-1", detected_at=t0 + timedelta(minutes=5), local_path="/hdd/SN001/aee_exp/e1", remote_path=None)
    )
    org = storage / "dedup" / "601" / "mtk" / "host-a_Result_org.xls"
    p1 = "/s/SN001/aee_exp/e1/d.DEC/__exp_main.txt"
    _write_org_xls(org, [{"Path": p1, "DeviceId": "SN001", "CausedBy": "IsoErr"}])
    _write_mapping_json(org, [{"sheet": "aeeexp", "row": 2, "path": p1, "mapped": True, "dle_id": str(d1), "occurrence_key": "occ-iso-1"}])
    db.artifacts.append(
        SimpleNamespace(id=1, plan_run_id=601, host_id="host-a", storage_uri=str(org), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=1))
    )

    # 置入 23 字节 config/blacklist.txt 并模拟 BaseScan.py:2006 写空 cwd/config/blacklist.txt
    blacklist_23 = b"com.example.rule.entry\n"
    assert len(blacklist_23) == 23
    expected_bl_sha = hashlib.sha256(blacklist_23).hexdigest()

    tool = _create_stub_dedup_org_tool(
        tmp_path / "pub_tool",
        truncate_blacklist_in_cwd=True,
        blacklist_bytes=blacklist_23,
    )
    pub_dir = Path(tool["script"]).parent
    before_files = sorted(
        (str(p.relative_to(pub_dir)), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in pub_dir.rglob("*")
        if p.is_file()
    )

    # 将原发布目录与 config 目录设为只读权限（若进程试图写原目录会立刻失败）
    os.chmod(pub_dir / "config", 0o555)
    os.chmod(pub_dir, 0o555)
    try:
        manifest = create_handoff_bundle(
            db, plan_run_ids=[601], platform="mtk",
            storage_root=str(storage), scan_tool=tool,
        )
    finally:
        os.chmod(pub_dir, 0o755)
        os.chmod(pub_dir / "config", 0o755)

    assert manifest["status"] == HANDOFF_STATUS_PENDING_EXPORT
    assert manifest["scan_tool_release"]["version"] == "2.1.1-runtime-20261106"
    bl_after = (pub_dir / "config" / "blacklist.txt").read_bytes()
    assert len(bl_after) == 23
    assert hashlib.sha256(bl_after).hexdigest() == expected_bl_sha
    after_files = sorted(
        (str(p.relative_to(pub_dir)), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in pub_dir.rglob("*")
        if p.is_file()
    )
    assert after_files == before_files
    assert not (pub_dir / "logs").exists()
    assert not (pub_dir / "tmp").exists()

    # release.json 缺失 / 版本过旧 / 缺 git_commit 均 fail-closed 返回 503
    for idx, kwargs in enumerate([
        {"write_release": False},
        {"release_version": "2.1.0"},
        {"release_commit": ""},
    ]):
        bad_rel_tool = _create_stub_dedup_org_tool(tmp_path / f"bad_rel_{idx}", **kwargs)
        with pytest.raises(JiraHandoffError) as exc_rel:
            create_handoff_bundle(db, plan_run_ids=[601], platform="mtk", storage_root=str(storage), scan_tool=bad_rel_tool)
        assert exc_rel.value.status_code == 503
        assert exc_rel.value.code == "handoff_scan_tool_release_invalid"


# ── 反例 8：工具输出强契约校验（schema/sha256/path/branch/group/终表缺失或损坏）


@pytest.mark.parametrize(
    "tool_kwargs,expected_code",
    [
        ({"emit_members": False}, "handoff_members_json_missing"),
        ({"mutate_members_mode": "bad_schema"}, "handoff_members_json_invalid"),
        ({"mutate_members_mode": "bad_sha256"}, "handoff_members_sha256_mismatch"),
        ({"mutate_members_mode": "bad_path"}, "handoff_members_path_mismatch"),
        ({"mutate_members_mode": "bad_branch"}, "handoff_members_json_invalid"),
        ({"mutate_members_mode": "bad_group_mismatch"}, "handoff_members_json_invalid"),
        ({"mutate_members_mode": "bad_member_index_type"}, "handoff_members_json_invalid"),
        ({"mutate_members_mode": "bad_final_row_oob"}, "handoff_members_json_invalid"),
        ({"emit_final_xls": False}, "handoff_dedup_xls_missing"),
        ({"corrupt_final_xls": True}, "handoff_dedup_xls_invalid"),
    ],
)
def test_b11_u1_08_strict_tool_output_contract_fail_closed(tmp_path, tool_kwargs, expected_code):
    storage = tmp_path / "center"
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    db = _setup_base_db(run_id=701, hosts=("host-a",), started_at=t0)
    d1 = uuid4()
    db.dles.append(
        SimpleNamespace(id=d1, serial="SN001", platform="mtk", plan_run_id=701, host_id="host-a", occurrence_key="occ-c1", detected_at=t0 + timedelta(minutes=5), local_path="/hdd/SN001/aee_exp/e1", remote_path=None)
    )
    org = storage / "dedup" / "701" / "mtk" / "host-a_Result_org.xls"
    p1 = "/s/SN001/aee_exp/e1/d.DEC/__exp_main.txt"
    _write_org_xls(org, [{"Path": p1, "DeviceId": "SN001", "CausedBy": "ErrContract"}])
    _write_mapping_json(org, [{"sheet": "aeeexp", "row": 2, "path": p1, "mapped": True, "dle_id": str(d1), "occurrence_key": "occ-c1"}])
    db.artifacts.append(
        SimpleNamespace(id=1, plan_run_id=701, host_id="host-a", storage_uri=str(org), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=1))
    )

    bad_tool = _create_stub_dedup_org_tool(tmp_path / "bad_tool", **tool_kwargs)
    with pytest.raises(JiraHandoffError) as exc_info:
        create_handoff_bundle(db, plan_run_ids=[701], platform="mtk", storage_root=str(storage), scan_tool=bad_tool)
    assert exc_info.value.status_code == 502
    assert exc_info.value.code == expected_code
    handoff_root = storage / "_meta" / "handoff"
    assert not handoff_root.exists() or list(handoff_root.iterdir()) == []


# ── 反例 9：坏钟（2010 占位）回退 DLE.detected_at + 仅基线只标注不自动剔除 ────


def test_b11_u1_09_bad_clock_fallback_and_baseline_annotation_not_dropped(tmp_path):
    storage = tmp_path / "center"
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    cls_bad_cur, fb_cur = classify_event_baseline("2010-01-01 00:00:00", t0 + timedelta(minutes=5), t0)
    assert (cls_bad_cur, fb_cur) == ("current", True)
    cls_bad_base, fb_base = classify_event_baseline("2010-01-01 00:00:00", t0 - timedelta(hours=1), t0)
    assert (cls_bad_base, fb_base) == ("baseline", True)
    assert classify_issue_baseline(["baseline", "baseline"]) == "baseline_only"
    assert classify_issue_baseline(["baseline", "current"]) == "mixed"
    assert classify_issue_baseline(["current"]) == "current_only"

    db = _setup_base_db(run_id=101, hosts=("host-a",), started_at=t0)
    d1, d2, d3 = uuid4(), uuid4(), uuid4()
    db.dles.extend([
        SimpleNamespace(id=d1, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a", occurrence_key="occ-b1", detected_at=t0 - timedelta(hours=2), local_path="/hdd/SN001/aee_exp/e1", remote_path=None),
        SimpleNamespace(id=d2, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a", occurrence_key="occ-m1", detected_at=t0 - timedelta(minutes=30), local_path="/hdd/SN001/aee_exp/e2", remote_path=None),
        SimpleNamespace(id=d3, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a", occurrence_key="occ-m2", detected_at=t0 + timedelta(minutes=30), local_path="/hdd/SN001/aee_exp/e3", remote_path=None),
    ])
    org = storage / "dedup" / "101" / "mtk" / "host-a_Result_org.xls"
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
        storage_root=str(storage), scan_tool=_create_stub_dedup_org_tool(tmp_path),
    )
    b = manifest["builds"][0]
    assert b["baseline_distribution"] == {"current_only": 0, "mixed": 1, "baseline_only": 1}
    by_cause = {i["form_fields"]["CausedBy"]: i for i in b["issues"]}
    assert by_cause["OldBaselineIssue"]["baseline_class"] == "baseline_only"
    assert by_cause["OldBaselineIssue"]["clock_fallback_count"] == 1
    assert by_cause["MixedIssue"]["baseline_class"] == "mixed"
    assert by_cause["MixedIssue"]["representative"]["entry"] == "e3"


# ── 反例 10 & 12：15 列契约 + PENDING_EXPORT + B3 公式注入中和 + 零 aee_extract ─


def test_b11_u1_10_and_12_15col_pending_export_formula_neutralization_and_zero_aee_extract(tmp_path, monkeypatch):
    for ch in ("=", "+", "-", "@", "\t", "\r", "\n", "\x00", "＝", "＋", "－", "＠"):
        assert neutralize_spreadsheet_cell_text(f"{ch}cmd") == f"'{ch}cmd"
    assert safe_xls_cell(-5) == -5
    assert isinstance(safe_xls_cell(3), int)
    assert safe_xls_cell('=HYPERLINK("http://evil")').startswith("'=HYPERLINK")

    storage = tmp_path / "center"
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    db = _setup_base_db(run_id=101, hosts=("host-a",), started_at=t0)
    d1, d_filt = uuid4(), uuid4()
    db.dles.extend([
        SimpleNamespace(id=d1, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a", occurrence_key="occ-f1", detected_at=t0 + timedelta(minutes=10), local_path="/hdd/SN001/aee_exp/e1", remote_path=None),
        SimpleNamespace(id=d_filt, serial="SN001", platform="mtk", plan_run_id=101, host_id="host-a", occurrence_key="occ-f2", detected_at=t0 + timedelta(minutes=12), local_path="/hdd/SN001/aee_exp/e2", remote_path=None),
    ])
    org = storage / "dedup" / "101" / "mtk" / "host-a_Result_org.xls"
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

    recorded_argvs: list[list[str]] = []
    real_run = subprocess.run

    def _spy_run(argv, *args, **kwargs):
        recorded_argvs.append([str(a) for a in argv])
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr("backend.services.jira_handoff_builder.subprocess.run", _spy_run)
    manifest = create_handoff_bundle(
        db, plan_run_ids=[101], platform="mtk",
        storage_root=str(storage), scan_tool=_create_stub_dedup_org_tool(tmp_path),
    )
    assert manifest["schema"] == HANDOFF_MANIFEST_SCHEMA
    assert manifest["status"] == HANDOFF_STATUS_PENDING_EXPORT
    assert manifest["manifest_sha256"] == compute_manifest_sha256(manifest)
    assert len(recorded_argvs) == 1
    assert Path(recorded_argvs[0][1]).name == "start_log_scan.py"
    cli_flags = recorded_argvs[0][2:]
    assert "-dedup_org" in cli_flags
    assert "-m" not in cli_flags
    assert all("aee_extract" not in a for a in cli_flags)
    assert all("/mnt/jxtinno-sonic" not in a for a in recorded_argvs[0])

    preview_path, _, _ = resolve_handoff_download_file(manifest["handoff_id"], "preview_xls", storage_root=str(storage))
    sh = xlrd.open_workbook(str(preview_path)).sheet_by_name("aeeexp")
    assert tuple(sh.row_values(0)) == FORM_COLUMNS
    assert sh.row_values(0)[5] == "ExpType "
    row1 = sh.row_values(1)
    assert str(row1[1]).startswith("PENDING_EXPORT://")
    assert "/home/jxtinno/sonic_tinno/" not in str(row1[1])
    assert row1[7] == "'=CMD|' /C calc'!A0"
    assert row1[8] == "'＠inject"
    assert row1[9] == "'+1+1"
    assert int(row1[11]) == 1
    assert int(row1[13]) == 1

    review_path, _, _ = resolve_handoff_download_file(manifest["handoff_id"], "review_workbook", storage_root=str(storage))
    rwb = xlrd.open_workbook(str(review_path))
    assert rwb.sheet_names() == ["概览", "问题汇总", "代表日志清单", "全部唯一事件", "未绑定与口径说明"]
    assert rwb.sheet_by_name("未绑定与口径说明").nrows == 2


# ── 反例 11：平台字面契约（非字面 "mtk" 一律 400）──────────────────────────────


@pytest.mark.parametrize("bad_platform", ["unisoc", "MTK", "MTK ", " mtk", "", "qcom"])
def test_b11_u1_11_platform_literal_mtk_contract(bad_platform):
    with pytest.raises(JiraHandoffError) as exc_info:
        validate_platform(bad_platform)
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "handoff_unsupported_platform"
    assert validate_platform("mtk") == "mtk"


# ── 反例 13：受控根祖先 symlink、bundle 越根/非普通文件及输入分片归档根校验 ────


def test_b11_u1_13_controlled_root_ancestor_symlink_and_archive_root_guards(tmp_path):
    storage = tmp_path / "center"
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    db = _setup_base_db(run_id=801, hosts=("host-a",), started_at=t0)
    d1 = uuid4()
    db.dles.append(
        SimpleNamespace(id=d1, serial="SN001", platform="mtk", plan_run_id=801, host_id="host-a", occurrence_key="occ-sec-1", detected_at=t0 + timedelta(minutes=5), local_path="/hdd/SN001/aee_exp/e1", remote_path=None)
    )

    # 13a) 输入 _org.xls 越出受控中心根 → 覆盖率阻塞 409
    outside_org = tmp_path / "outside_root" / "host-a_Result_org.xls"
    p1 = "/s/SN001/aee_exp/e1/d.DEC/__exp_main.txt"
    _write_org_xls(outside_org, [{"Path": p1, "DeviceId": "SN001", "CausedBy": "SecErr"}])
    _write_mapping_json(outside_org, [{"sheet": "aeeexp", "row": 2, "path": p1, "mapped": True, "dle_id": str(d1), "occurrence_key": "occ-sec-1"}])
    db.artifacts.append(
        SimpleNamespace(id=1, plan_run_id=801, host_id="host-a", storage_uri=str(outside_org), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=1))
    )
    pre_out = run_handoff_preflight(db, plan_run_ids=[801], platform="mtk", storage_root=str(storage))
    assert pre_out["gates"]["coverage_complete"] is False
    assert "host-a" in pre_out["runs"][0]["missing_hosts"]

    # 13a-2) 同目录 .mapping.json 为 symlink 指向根外 → 必须拒绝（不得回退 compat_serial_entry）
    org = storage / "dedup" / "801" / "mtk" / "host-a_Result_org.xls"
    _write_org_xls(org, [{"Path": p1, "DeviceId": "SN001", "CausedBy": "SecErr"}])
    outside_map = tmp_path / "outside_root" / "evil.mapping.json"
    outside_map.write_text(
        json.dumps({"rows": [{"sheet": "aeeexp", "row": 2, "path": p1, "mapped": True, "dle_id": str(d1), "occurrence_key": "occ-sec-1"}]}),
        encoding="utf-8",
    )
    sym_map = org.with_name(org.name + ".mapping.json")
    sym_map.symlink_to(outside_map)
    db.artifacts[0].storage_uri = str(org)
    pre_sym_map = run_handoff_preflight(db, plan_run_ids=[801], platform="mtk", storage_root=str(storage))
    assert pre_sym_map["identity_gate"]["unique_events"] == 0
    assert pre_sym_map["identity_gate"]["compat_fallback_rows"] == 0
    assert pre_sym_map["identity_gate"]["unbound_by_reason"] == {"mapping_input_symlink_rejected": 1}
    sym_map.unlink()

    # 13b) 移入受控中心根并写合法普通伴随映射后正常生成
    _write_mapping_json(org, [{"sheet": "aeeexp", "row": 2, "path": p1, "mapped": True, "dle_id": str(d1), "occurrence_key": "occ-sec-1"}])

    tool = _create_stub_dedup_org_tool(tmp_path)
    manifest = create_handoff_bundle(db, plan_run_ids=[801], platform="mtk", storage_root=str(storage), scan_tool=tool)
    hid = manifest["handoff_id"]
    assert len(list_handoff_bundles(storage_root=str(storage))) == 1
    assert get_handoff_bundle(hid, storage_root=str(storage))["manifest_sha256"] == manifest["manifest_sha256"]

    # 13c) 受控根祖先含 symlink → 创建/查询/下载全部拒绝（400）
    link_ancestor = tmp_path / "symlink_parent"
    link_ancestor.symlink_to(storage)
    with pytest.raises(JiraHandoffError) as exc_anc:
        get_handoff_bundle(hid, storage_root=str(link_ancestor))
    assert exc_anc.value.status_code == 400
    assert exc_anc.value.code == "handoff_symlink_rejected"

    with pytest.raises(JiraHandoffError) as exc_anc_create:
        create_handoff_bundle(db, plan_run_ids=[801], platform="mtk", storage_root=str(link_ancestor), scan_tool=tool)
    assert exc_anc_create.value.status_code == 400
    assert exc_anc_create.value.code == "handoff_symlink_rejected"

    # 13d) _meta 为 symlink 拒绝
    meta_link_root = tmp_path / "meta_link_root"
    meta_link_root.mkdir()
    (meta_link_root / "_meta").symlink_to(storage / "_meta")
    with pytest.raises(JiraHandoffError) as exc_meta_link:
        resolve_handoff_download_file(hid, "manifest", storage_root=str(meta_link_root))
    assert exc_meta_link.value.status_code == 400
    assert exc_meta_link.value.code == "handoff_symlink_rejected"

    # 13e) 越根 ID / 非法类型 / 叶子 symlink / 非普通文件（目录伪装文件）全部拒绝
    with pytest.raises(JiraHandoffError) as exc_id:
        resolve_handoff_download_file("../escape", "manifest", storage_root=str(storage))
    assert exc_id.value.status_code == 400

    with pytest.raises(JiraHandoffError) as exc_kind:
        resolve_handoff_download_file(hid, "evil_kind", storage_root=str(storage))
    assert exc_kind.value.status_code == 400

    preview_rel = manifest["builds"][0]["artifacts"]["preview_xls"]["rel_path"]
    preview_file = storage / "_meta" / "handoff" / hid / preview_rel
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    preview_file.unlink()
    preview_file.symlink_to(outside)
    with pytest.raises(JiraHandoffError) as exc_sym:
        resolve_handoff_download_file(hid, "preview_xls", storage_root=str(storage))
    assert exc_sym.value.status_code == 400
    assert exc_sym.value.code == "handoff_symlink_rejected"

    # 非普通文件（同名目录替代产物文件）
    preview_file.unlink()
    preview_file.mkdir()
    with pytest.raises(JiraHandoffError) as exc_nonreg:
        resolve_handoff_download_file(hid, "preview_xls", storage_root=str(storage))
    assert exc_nonreg.value.status_code == 400
    assert exc_nonreg.value.code == "handoff_not_regular_file"
