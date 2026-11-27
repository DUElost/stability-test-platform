"""B11-U1（#4199 / #4174）阶段 1：A 段发生身份归并、覆盖率双门禁与本次/基线判定。

本模块只读消费已登记的 PlanRun、DLE、ScanRound 与已上送 Agent ``*_org.xls``
及伴随 ``.mapping.json``，不写外部 15.4 共享根、不调用 ``aee_extract`` / ``-m 0``，
不导入 FastAPI（C2 分层约束）。

核心不变量（#4199 §3 B11-U1 / §4.2）：
1. 跨 run 重复观测归并优先按 ``occurrence_key``（``backend/agent/contracts/occurrence_key.py``）；
   ``(serial, entry)`` 仅用于无伴随映射或 ``occurrence_key`` 缺失且可由 DLE 唯一锚定的历史兼容路径。
2. 未绑定 DLE 行与 ``*_temp`` / ``CurProcessNone`` 等解析占位行不得静默混入计数或静默丢弃，
   全部显式记入 ``unbound_rows``。
3. 本次/基线判定以 ``<首轮 started_at>`` 为界，2010 类坏钟占位值回退 ``DLE.detected_at``（#4078），
   且抽为纯函数供未来 ``#4033`` 复用；「仅基线」问题只标注、不自动剔除。
4. 电子表格文本单元按 B3（#3561 G1 / #3237）规则中和公式注入。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session
import xlrd

from backend.core.dedup_platform import scan_artifact_uri_platform
from backend.models.device_log_event import DeviceLogEvent
from backend.models.host import Device
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun, PlanRunTargetDevice
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.models.project import TestProject
from backend.services import issue_fact_store as facts
from backend.services.plan_run_scan_scope import (
    load_expected_scan_platforms,
    load_plan_run_scan_host_ids,
)

SUPPORTED_HANDOFF_PLATFORMS = frozenset({"mtk"})
HANDOFF_STATUS_PENDING_EXPORT = "PENDING_EXPORT"

# 15 列厂商提单表头（逐字对齐 .claude/skills/monkey-aee-dedup-handoff/SKILL.md；
# 注意第 6 列 "ExpType " 含尾空格，第 14 列为整数 DeviceCount）。
FORM_COLUMNS: tuple[str, ...] = (
    "Id",
    "Path",
    "Version",
    "ExpTime",
    "ExpClass",
    "ExpType ",
    "CurProcess",
    "Package",
    "Detail",
    "CausedBy",
    "extraTag",
    "Count",
    "Activity",
    "DeviceCount",
    "Rom_Ram",
)

# 15 列合并输入表头（喂给 start_log_scan.py -dedup_org；第 14 列为串号 DeviceId）。
ORG_COLUMNS: tuple[str, ...] = (
    "Id",
    "Path",
    "Version",
    "ExpTime",
    "ExpClass",
    "ExpType ",
    "CurProcess",
    "Package",
    "Detail",
    "CausedBy",
    "extraTag",
    "Count",
    "Activity",
    "DeviceId",
    "Rom_Ram",
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_BAD_CLOCK_FLOOR_UTC = datetime(2020, 1, 1, tzinfo=timezone.utc)

# 对齐 B3（#3561 G1 / #3237 `frontend/src/utils/spreadsheet.ts`）：
# 首字符命中 = + - @ TAB CR LF NUL 与全角 ＝ ＋ － ＠ 时前置单引号。
_FORMULA_TRIGGER_PREFIXES = frozenset(
    {
        "=",
        "+",
        "-",
        "@",
        "\t",
        "\r",
        "\n",
        "\x00",
        "\uff1d",
        "\uff0b",
        "\uff0d",
        "\uff20",
    }
)

# 扫描工具解析失败写入的占位串（docs/notes/process/2026-11-25-curprocessnone-temp-org-rows.md）。
_VENDOR_PLACEHOLDER_VALUES = frozenset(
    {
        "curprocessnone",
        "expclassnone",
        "exptypenone",
        "packagenone",
    }
)

_AEE_EXP_ENTRY_RE = re.compile(r"/([^/]+)/aee_exp/([^/]+)(?:/|$)")
_EXP_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y_%m%d_%H%M%S",
    "%Y%m%d_%H%M%S",
)
_ARTIFACT_TYPE_SCAN = "scan_result_xls"
_UNKNOWN_BUILD = "UNKNOWN_BUILD"


def neutralize_spreadsheet_cell_text(value: str) -> str:
    """对齐 B3（#3561 G1 / #3237）：对可能触发电子表格公式注入的首字符前置单引号。"""
    if not value:
        return value
    if value[0] in _FORMULA_TRIGGER_PREFIXES:
        return "'" + value
    return value


def safe_xls_cell(value: Any) -> Any:
    """写单元格前按原始类型决定中和策略：数值保留数值语义，字符串走公式中和。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    return neutralize_spreadsheet_cell_text(str(value))


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """统一将 datetime 归一化到 UTC；naive datetime 视为 UTC。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_exp_time_utc(raw: Any) -> Optional[datetime]:
    """解析扫描报表 ``ExpTime`` 文本为 UTC datetime；无法解析返回 None。

    MTK AEE ``ExpTime`` 无时区后缀，按设备本地上海时区（Asia/Shanghai）解释后再转 UTC。
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # 兼容带毫秒的常见格式（如 2026-11-18 20:59:33.123 或 2026_1118_205933_123）
    try:
        iso = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if iso.tzinfo is None:
            return iso.replace(tzinfo=_SHANGHAI).astimezone(timezone.utc)
        return iso.astimezone(timezone.utc)
    except ValueError:
        pass
    trimmed = text.split(".", 1)[0]
    for fmt in _EXP_TIME_FORMATS:
        try:
            parsed = datetime.strptime(trimmed, fmt)
            return parsed.replace(tzinfo=_SHANGHAI).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def classify_event_baseline(
    exp_time_raw: Any,
    detected_at: Optional[datetime],
    first_run_started_at: Optional[datetime],
) -> tuple[str, bool]:
    """判定单条唯一事件归属「本次 (current)」或「基线 (baseline)」（纯函数，供 #4033 复用）。

    规则（#4174 / #4078 / monkey-aee-dedup-handoff §2 第 12 步）：
    - 以 ``first_run_started_at``（选中 run 集合的最早开始时间）为下界；
    - ``ExpTime`` 无法解析或早于 2020-01-01（2010/1970 坏钟占位值）时，回退采用
      ``DLE.detected_at`` 判定，并返回 ``clock_fallback=True``；
    - 两者皆不可得时保守归 ``current`` 并标 ``clock_fallback=True``。
    """
    boundary = ensure_utc(first_run_started_at)
    if boundary is None:
        return "current", False
    parsed_exp = parse_exp_time_utc(exp_time_raw)
    clock_fallback = False
    effective_ts: Optional[datetime] = parsed_exp
    if effective_ts is None or effective_ts < _BAD_CLOCK_FLOOR_UTC:
        clock_fallback = True
        effective_ts = ensure_utc(detected_at)
    if effective_ts is None:
        return "current", True
    return ("current" if effective_ts >= boundary else "baseline"), clock_fallback


def classify_issue_baseline(event_classes: Sequence[str]) -> str:
    """根据问题内全部唯一事件的本次/基线分布输出 ``current_only`` / ``mixed`` / ``baseline_only``。"""
    unique = {cls for cls in event_classes if cls in {"current", "baseline"}}
    if not unique or unique == {"current"}:
        return "current_only"
    if unique == {"baseline"}:
        return "baseline_only"
    return "mixed"


def is_placeholder_org_row(row_fields: Mapping[str, Any]) -> Optional[str]:
    """识别扫描工具解析失败占位行与 ``*_temp`` 预分析目录（不计入真实发生）。"""
    raw_path = str(row_fields.get("Path") or "").replace("\\", "/")
    parts = [part for part in raw_path.split("/") if part]
    if any(part.endswith("_temp") for part in parts):
        return "temp_path_placeholder"
    for key in ("CurProcess", "ExpClass", "ExpType", "Package"):
        val = str(row_fields.get(key) or "").strip().lower()
        if val in _VENDOR_PLACEHOLDER_VALUES:
            return "vendor_parse_placeholder"
    return None


def extract_serial_and_entry(raw_path: str, device_id: str = "") -> tuple[str, str]:
    """从报表 Path 或 DLE 路径提取 ``(serial, event_entry)``；失败返回空串对。"""
    normalized = str(raw_path or "").replace("\\", "/").strip()
    if normalized:
        match = _AEE_EXP_ENTRY_RE.search(normalized)
        if match:
            serial = match.group(1).strip()
            entry = match.group(2).strip()
            if serial and entry and not entry.endswith("_temp"):
                return serial, entry
        parts = [p for p in normalized.split("/") if p]
        # 兼容 .../devices/<run_id>/<serial>/<entry>/... 或 .../<serial>/<entry>/.DEC/...
        for idx, part in enumerate(parts):
            if part.endswith(".DEC") and idx >= 2:
                entry = parts[idx - 1].strip()
                serial = (device_id or parts[idx - 2]).strip()
                if serial and entry and not entry.endswith("_temp"):
                    return serial, entry
        if len(parts) >= 2 and device_id:
            # 兜底：若路径末段本身像 db 目录名（如 2026_1107_..._db.00.JE）
            for part in reversed(parts):
                if "db." in part and not part.endswith(".DEC") and not part.endswith(".dbg"):
                    return device_id.strip(), part.strip()
    return device_id.strip(), ""


def _dle_entry_name(dle: DeviceLogEvent) -> str:
    for raw in (dle.local_path, dle.remote_path):
        if not raw:
            continue
        cleaned = str(raw).replace("\\", "/").rstrip("/")
        if not cleaned:
            continue
        base = cleaned.rsplit("/", 1)[-1]
        if base:
            return base
    return ""


@dataclass(frozen=True)
class ReportSourceRef:
    plan_run_id: int
    host_id: str
    storage_uri: str
    mapping_uri: Optional[str]
    empty_receipt: bool = False


@dataclass
class RunCoverageSummary:
    plan_run_id: int
    plan_name: str
    status: str
    partition_mode: str
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    project_id: Optional[int]
    project_key: Optional[str]
    jira_project_key: Optional[str]
    hosts_expected: list[str]
    hosts_with_reports: list[str]
    missing_hosts: list[str]
    mapping_status: str  # "complete" | "partial" | "missing" | "empty"
    reports: list[ReportSourceRef] = field(default_factory=list)


@dataclass
class UnboundRowRecord:
    plan_run_id: int
    host_id: str
    report_file: str
    row_no: int
    serial: str
    raw_path: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_run_id": self.plan_run_id,
            "host_id": self.host_id,
            "report_file": self.report_file,
            "row_no": self.row_no,
            "serial": self.serial,
            "raw_path": self.raw_path,
            "reason": self.reason,
        }


@dataclass
class DedupedEvent:
    dedup_key: str
    identity_source: str  # "occurrence_key" | "compat_serial_entry"
    occurrence_key: Optional[str]
    serial: str
    entry: str
    build: str
    product: str
    source_runs: list[int]
    dle_ids: list[str]
    primary_dle_id: Optional[str]
    detected_at: Optional[datetime]
    baseline_class: str  # "current" | "baseline"
    clock_fallback: bool
    evidence_location: str
    evidence_tier: str  # "evidence" | "devices" | "local" | "none"
    fields: dict[str, str]  # 原始 15 列归一化字段

    def to_manifest_dict(self) -> dict[str, Any]:
        return {
            "dedup_key": self.dedup_key,
            "identity_source": self.identity_source,
            "occurrence_key": self.occurrence_key,
            "serial": self.serial,
            "entry": self.entry,
            "build": self.build,
            "product": self.product,
            "source_runs": list(self.source_runs),
            "dle_ids": list(self.dle_ids),
            "primary_dle_id": self.primary_dle_id,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "baseline_class": self.baseline_class,
            "clock_fallback": self.clock_fallback,
            "evidence_location": self.evidence_location,
            "evidence_tier": self.evidence_tier,
            "exp_time": self.fields.get("ExpTime", ""),
            "exp_class": self.fields.get("ExpClass", ""),
            "exp_type": self.fields.get("ExpType", ""),
            "cur_process": self.fields.get("CurProcess", ""),
            "package": self.fields.get("Package", ""),
        }


@dataclass
class IdentityCollectionResult:
    snapshot_cutoff: datetime
    first_run_started_at: Optional[datetime]
    runs: list[RunCoverageSummary]
    events: list[DedupedEvent]
    unbound_rows: list[UnboundRowRecord]
    total_org_rows: int
    keyed_rows: int
    compat_fallback_rows: int
    after_cutoff_skipped_rows: int
    unbound_by_reason: dict[str, int]
    builds_meta: dict[str, dict[str, Any]]
    coverage_complete: bool
    identity_degraded: bool
    blocking_issues: list[str]
    warnings: list[str]


def _pick_evidence_location(dle: Optional[DeviceLogEvent]) -> tuple[str, str]:
    if dle is None:
        return "", "none"
    remote = (dle.remote_path or "").strip()
    if remote:
        norm = remote.replace("\\", "/")
        if "/evidence/" in norm or norm.startswith("evidence/"):
            return remote, "evidence"
        return remote, "devices"
    local = (dle.local_path or "").strip()
    if local:
        return local, "local"
    return "", "none"


_EVIDENCE_TIER_RANK = {"evidence": 3, "devices": 2, "local": 1, "none": 0}


def _load_device_build_and_product(
    db: Session,
    runs: Sequence[PlanRun],
) -> tuple[dict[str, str], dict[str, str], dict[str, set[str]]]:
    """从 ``run_context.device_builds`` 与 ``Device`` 派生串号 → build / product 映射。"""
    run_ids = [r.id for r in runs]
    if not run_ids:
        return {}, {}, {}

    # 收集选中 run 涉及的全部 device_id → (serial, product, build_display_id)
    job_rows = db.execute(
        select(JobInstance.plan_run_id, Device.id, Device.serial, Device.product, Device.build_display_id)
        .join(Device, Device.id == JobInstance.device_id)
        .where(JobInstance.plan_run_id.in_(run_ids))
    ).all()
    target_rows = db.execute(
        select(PlanRunTargetDevice.plan_run_id, Device.id, Device.serial, Device.product, Device.build_display_id)
        .join(Device, Device.id == PlanRunTargetDevice.device_id)
        .where(PlanRunTargetDevice.plan_run_id.in_(run_ids))
    ).all()

    device_by_id: dict[int, tuple[str, str, str]] = {}
    serial_product: dict[str, str] = {}
    serial_fallback_build: dict[str, str] = {}
    run_serials: dict[int, set[str]] = {}
    for run_id, dev_id, serial, product, display_id in (*job_rows, *target_rows):
        if not serial:
            continue
        s = str(serial).strip()
        p = str(product or "").strip()
        b = str(display_id or "").strip()
        device_by_id[int(dev_id)] = (s, p, b)
        if p and s not in serial_product:
            serial_product[s] = p
        if b and s not in serial_fallback_build:
            serial_fallback_build[s] = b
        run_serials.setdefault(int(run_id), set()).add(s)

    # 优先取各 run 的 run_context.device_builds[str(device_id)]，其次 run.build_version，再次 device.build_display_id
    serial_build: dict[str, str] = {}
    build_devices: dict[str, set[str]] = {}
    for run in runs:
        ctx = run.run_context if isinstance(run.run_context, dict) else {}
        dev_builds = ctx.get("device_builds") if isinstance(ctx.get("device_builds"), dict) else {}
        for dev_id_str, bval in dev_builds.items():
            btext = str(bval or "").strip()
            if not btext:
                continue
            try:
                info = device_by_id.get(int(dev_id_str))
            except (TypeError, ValueError):
                info = None
            if info is None:
                continue
            s = info[0]
            serial_build.setdefault(s, btext)
            build_devices.setdefault(btext, set()).add(s)
        run_bv = str(run.build_version or "").strip()
        for s in run_serials.get(run.id, set()):
            resolved_b = serial_build.get(s) or run_bv or serial_fallback_build.get(s) or _UNKNOWN_BUILD
            serial_build.setdefault(s, resolved_b)
            build_devices.setdefault(resolved_b, set()).add(s)

    return serial_build, serial_product, build_devices


def _load_run_coverage(
    db: Session,
    run: PlanRun,
    *,
    platform: str,
) -> RunCoverageSummary:
    plan = db.get(Plan, run.plan_id)
    plan_name = str(plan.name) if plan is not None else f"Plan #{run.plan_id}"
    project = db.get(TestProject, run.project_id) if run.project_id else None
    project_key = str(project.project_key) if project is not None else None
    jira_project_key = (
        str(project.jira_project_key)
        if project is not None and project.jira_project_key
        else None
    )

    pm = facts.get_partition_mode(db, plan_run_id=run.id, platform=platform)
    partition_mode = pm.mode if pm is not None else facts.PARTITION_MODE_CURRENT

    all_hosts = load_plan_run_scan_host_ids(db, run.id)
    expected_map = load_expected_scan_platforms(db, run.id, all_hosts)
    expected_hosts = sorted(
        host_id for host_id, plats in expected_map.items() if platform in plats
    )

    reports: list[ReportSourceRef] = []
    covered_hosts: set[str] = set()

    if partition_mode == facts.PARTITION_MODE_DEDUP_FIRST:
        rounds = facts.list_scan_rounds_for_read(db, plan_run_id=run.id, platform=platform)
        # 取最新一轮（优先 FINAL/SUPPLEMENT，再退化至最新 PERIODIC）与已关闭补充轮并集
        selected_rounds = [
            r for r in rounds if r.kind in (facts.SCAN_ROUND_KIND_FINAL, facts.SCAN_ROUND_KIND_SUPPLEMENT)
        ]
        if not selected_rounds and rounds:
            selected_rounds = [rounds[-1]]
        host_latest: dict[str, ReportSourceRef] = {}
        round_expected_hosts: set[str] = set(expected_hosts)
        for rnd in selected_rounds:
            for unit in (rnd.expected_set or []):
                if not isinstance(unit, dict):
                    continue
                unit_plat = str(unit.get("platform") or platform).lower()
                if unit_plat != platform:
                    continue
                hid = str(unit.get("host_id") or "").strip()
                if not hid:
                    continue
                round_expected_hosts.add(hid)
                unit_reports = unit.get("reports") or []
                for rep in unit_reports:
                    if not isinstance(rep, dict):
                        continue
                    if rep.get("empty_receipt"):
                        covered_hosts.add(hid)
                        host_latest.setdefault(
                            hid,
                            ReportSourceRef(
                                plan_run_id=run.id,
                                host_id=hid,
                                storage_uri="",
                                mapping_uri=None,
                                empty_receipt=True,
                            ),
                        )
                        continue
                    uri = str(rep.get("storage_uri") or "").strip()
                    if not uri or not uri.endswith("_org.xls"):
                        continue
                    covered_hosts.add(hid)
                    mapping_uri = str(rep.get("mapping_uri") or "").strip() or None
                    if mapping_uri is None:
                        candidate = Path(uri).with_name(Path(uri).name + ".mapping.json")
                        if candidate.is_file():
                            mapping_uri = str(candidate)
                    host_latest[hid] = ReportSourceRef(
                        plan_run_id=run.id,
                        host_id=hid,
                        storage_uri=uri,
                        mapping_uri=mapping_uri,
                        empty_receipt=False,
                    )
        expected_hosts = sorted(round_expected_hosts)
        reports = [host_latest[h] for h in sorted(host_latest)]
    else:
        # CURRENT 模式：取每个预期 host 最新已登记的 *_org.xls 扫描分片
        artifacts = db.execute(
            select(PlanRunArtifact)
            .where(
                PlanRunArtifact.plan_run_id == run.id,
                PlanRunArtifact.artifact_type == _ARTIFACT_TYPE_SCAN,
            )
            .order_by(PlanRunArtifact.created_at.desc(), PlanRunArtifact.id.desc())
        ).scalars().all()
        by_host: dict[str, ReportSourceRef] = {}
        for art in artifacts:
            uri = str(art.storage_uri or "").strip()
            if not uri or not uri.endswith("_org.xls"):
                continue
            if scan_artifact_uri_platform(uri) != platform:
                continue
            hid = str(art.host_id or "").strip()
            if not hid or hid in by_host:
                continue
            candidate_map = Path(uri).with_name(Path(uri).name + ".mapping.json")
            mapping_uri = str(candidate_map) if candidate_map.is_file() else None
            by_host[hid] = ReportSourceRef(
                plan_run_id=run.id,
                host_id=hid,
                storage_uri=uri,
                mapping_uri=mapping_uri,
                empty_receipt=False,
            )
            covered_hosts.add(hid)
        reports = [by_host[h] for h in sorted(by_host)]

    missing_hosts = [h for h in expected_hosts if h not in covered_hosts]
    non_empty_reports = [r for r in reports if not r.empty_receipt]
    if not non_empty_reports:
        mapping_status = "empty"
    else:
        with_map = sum(1 for r in non_empty_reports if r.mapping_uri and Path(r.mapping_uri).is_file())
        if with_map == len(non_empty_reports):
            mapping_status = "complete"
        elif with_map == 0:
            mapping_status = "missing"
        else:
            mapping_status = "partial"

    return RunCoverageSummary(
        plan_run_id=run.id,
        plan_name=plan_name,
        status=str(run.status),
        partition_mode=partition_mode,
        started_at=ensure_utc(run.started_at),
        ended_at=ensure_utc(run.ended_at),
        project_id=run.project_id,
        project_key=project_key,
        jira_project_key=jira_project_key,
        hosts_expected=expected_hosts,
        hosts_with_reports=sorted(covered_hosts),
        missing_hosts=missing_hosts,
        mapping_status=mapping_status,
        reports=reports,
    )


def _read_org_xls_rows(report_path: Path) -> list[dict[str, str]]:
    """读取单份 ``*_org.xls`` 的 ``aeeexp`` 工作表全部数据行，归一化键名。"""
    book = xlrd.open_workbook(str(report_path), on_demand=True)
    try:
        sheet = (
            book.sheet_by_name("aeeexp")
            if "aeeexp" in book.sheet_names()
            else book.sheet_by_index(0)
        )
        if sheet.nrows <= 1:
            return []
        headers = [str(v).strip() for v in sheet.row_values(0)]
        col_map = {h.lower(): idx for idx, h in enumerate(headers)}
        if "path" not in col_map:
            raise ValueError(f"missing Path column in {report_path}")

        def _cell(row_vals: Sequence[Any], key: str) -> str:
            idx = col_map.get(key.lower())
            if idx is None or idx >= len(row_vals):
                return ""
            val = row_vals[idx]
            if val is None:
                return ""
            if isinstance(val, float) and val.is_integer():
                return str(int(val))
            return str(val).strip()

        rows: list[dict[str, str]] = []
        for r_idx in range(1, sheet.nrows):
            vals = sheet.row_values(r_idx)
            rows.append(
                {
                    "Id": _cell(vals, "Id"),
                    "Path": _cell(vals, "Path"),
                    "Version": _cell(vals, "Version"),
                    "ExpTime": _cell(vals, "ExpTime"),
                    "ExpClass": _cell(vals, "ExpClass"),
                    "ExpType": _cell(vals, "ExpType"),
                    "CurProcess": _cell(vals, "CurProcess"),
                    "Package": _cell(vals, "Package"),
                    "Detail": _cell(vals, "Detail"),
                    "CausedBy": _cell(vals, "CausedBy"),
                    "extraTag": _cell(vals, "extraTag"),
                    "Count": _cell(vals, "Count") or "1",
                    "Activity": _cell(vals, "Activity"),
                    "DeviceId": _cell(vals, "DeviceId"),
                    "Rom_Ram": _cell(vals, "Rom_Ram"),
                }
            )
        return rows
    finally:
        book.release_resources()


def _load_and_validate_mapping(
    mapping_uri: Optional[str],
    org_rows: Sequence[Mapping[str, str]],
) -> tuple[Optional[list[dict[str, Any]]], Optional[str]]:
    """加载并校验伴随 ``.mapping.json``（对齐 ``scan_membership.py:51-64``）。"""
    if not mapping_uri:
        return None, "missing_mapping"
    path = Path(mapping_uri)
    if not path.is_file():
        return None, "missing_mapping"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["rows"]
    except (OSError, ValueError, KeyError, TypeError):
        return None, "mapping_corrupted"
    if not isinstance(rows, list) or len(rows) != len(org_rows):
        return None, "mapping_mismatch"
    for idx, (m_row, o_row) in enumerate(zip(rows, org_rows, strict=True)):
        if (
            not isinstance(m_row, dict)
            or m_row.get("sheet") != "aeeexp"
            or m_row.get("row") != idx + 2
            or str(m_row.get("path") or "") != str(o_row.get("Path") or "")
        ):
            return None, "mapping_mismatch"
    return rows, None


def collect_handoff_identity(
    db: Session,
    *,
    plan_run_ids: Sequence[int],
    platform: str = "mtk",
    snapshot_cutoff: Optional[datetime] = None,
) -> IdentityCollectionResult:
    """执行双门禁检查 + 跨 run 发生身份归并（纯只读，零外部写盘、零 ``aee_extract``）。"""
    cutoff = ensure_utc(snapshot_cutoff) or datetime.now(timezone.utc)
    ordered_ids = list(dict.fromkeys(int(rid) for rid in plan_run_ids))
    run_rows = db.execute(
        select(PlanRun).where(PlanRun.id.in_(ordered_ids))
    ).scalars().all()
    by_id = {r.id: r for r in run_rows}
    missing_run_ids = [rid for rid in ordered_ids if rid not in by_id]
    if missing_run_ids:
        raise LookupError(f"plan_run not found: {missing_run_ids}")

    runs_ordered = [by_id[rid] for rid in ordered_ids]
    run_summaries = [_load_run_coverage(db, run, platform=platform) for run in runs_ordered]
    start_times = [s.started_at for s in run_summaries if s.started_at is not None]
    first_run_started_at = min(start_times) if start_times else None

    serial_build, serial_product, build_devices = _load_device_build_and_product(db, runs_ordered)

    # 一次性拉取选中 run 集合的 DLE 台账，按 id / occurrence_key / (serial, entry) 建只读索引
    dle_rows = db.execute(
        select(DeviceLogEvent)
        .where(
            DeviceLogEvent.plan_run_id.in_(ordered_ids),
            func.lower(DeviceLogEvent.platform) == platform.lower(),
        )
        .order_by(DeviceLogEvent.detected_at.asc(), DeviceLogEvent.id.asc())
    ).scalars().all()
    dle_by_id: dict[str, DeviceLogEvent] = {}
    dle_by_serial_entry: dict[tuple[str, str], list[DeviceLogEvent]] = {}
    for dle in dle_rows:
        dle_by_id[str(dle.id)] = dle
        entry_name = _dle_entry_name(dle)
        if dle.serial and entry_name:
            dle_by_serial_entry.setdefault((dle.serial.strip(), entry_name), []).append(dle)

    unbound_rows: list[UnboundRowRecord] = []
    unbound_counter: Counter[str] = Counter()
    deduped_by_key: dict[str, DedupedEvent] = {}
    total_org_rows = 0
    keyed_rows = 0
    compat_fallback_rows = 0
    after_cutoff_skipped_rows = 0
    warnings: list[str] = []
    blocking_issues: list[str] = []

    for rs in run_summaries:
        if rs.missing_hosts:
            blocking_issues.append(
                f"plan_run {rs.plan_run_id} 缺少 {len(rs.missing_hosts)} 台主机报表: {', '.join(rs.missing_hosts)}"
            )
        if rs.mapping_status in ("partial", "missing"):
            warnings.append(
                f"plan_run {rs.plan_run_id} 伴随映射状态={rs.mapping_status}（缺映射分片仅在 DLE 精确锚定时走兼容回退）"
            )
        for rep in rs.reports:
            if rep.empty_receipt:
                continue
            report_path = Path(rep.storage_uri)
            if not report_path.is_file():
                blocking_issues.append(
                    f"plan_run {rs.plan_run_id} host {rep.host_id} 报表文件不可读: {report_path.name}"
                )
                continue
            try:
                org_rows = _read_org_xls_rows(report_path)
            except Exception as exc:
                blocking_issues.append(
                    f"plan_run {rs.plan_run_id} host {rep.host_id} 解析报表失败: {report_path.name} ({exc})"
                )
                continue

            mapping_rows, mapping_err = _load_and_validate_mapping(rep.mapping_uri, org_rows)
            if mapping_err == "mapping_mismatch" or mapping_err == "mapping_corrupted":
                warnings.append(
                    f"plan_run {rs.plan_run_id} host {rep.host_id} 伴随映射校验失败({mapping_err})，该分片行记入未绑定清单"
                )

            for idx, row in enumerate(org_rows):
                total_org_rows += 1
                row_no = idx + 2  # Excel 1-based，表头为第 1 行
                raw_path = row.get("Path", "")
                serial_hint = row.get("DeviceId", "").strip()
                parsed_serial, parsed_entry = extract_serial_and_entry(raw_path, serial_hint)
                serial = serial_hint or parsed_serial

                placeholder_reason = is_placeholder_org_row(row)
                if placeholder_reason is not None:
                    unbound_rows.append(
                        UnboundRowRecord(
                            plan_run_id=rs.plan_run_id,
                            host_id=rep.host_id,
                            report_file=report_path.name,
                            row_no=row_no,
                            serial=serial,
                            raw_path=raw_path,
                            reason=placeholder_reason,
                        )
                    )
                    unbound_counter[placeholder_reason] += 1
                    continue

                if mapping_err in ("mapping_mismatch", "mapping_corrupted"):
                    unbound_rows.append(
                        UnboundRowRecord(
                            plan_run_id=rs.plan_run_id,
                            host_id=rep.host_id,
                            report_file=report_path.name,
                            row_no=row_no,
                            serial=serial,
                            raw_path=raw_path,
                            reason=mapping_err,
                        )
                    )
                    unbound_counter[mapping_err] += 1
                    continue

                dedup_key: Optional[str] = None
                identity_source = ""
                occ_key: Optional[str] = None
                matched_dle: Optional[DeviceLogEvent] = None
                entry_name = parsed_entry

                if mapping_rows is not None:
                    m_row = mapping_rows[idx]
                    mapped_flag = bool(m_row.get("mapped"))
                    raw_dle_id = str(m_row.get("dle_id") or "").strip()
                    raw_occ = str(m_row.get("occurrence_key") or "").strip()
                    if raw_dle_id and raw_dle_id in dle_by_id:
                        matched_dle = dle_by_id[raw_dle_id]
                        if not raw_occ and matched_dle.occurrence_key:
                            raw_occ = str(matched_dle.occurrence_key).strip()
                        if not serial and matched_dle.serial:
                            serial = matched_dle.serial.strip()
                        if not entry_name:
                            entry_name = _dle_entry_name(matched_dle)
                    if mapped_flag and raw_occ:
                        dedup_key = f"occ:{raw_occ}"
                        identity_source = "occurrence_key"
                        occ_key = raw_occ
                        keyed_rows += 1
                    elif mapped_flag and matched_dle is not None and serial and entry_name:
                        # 已绑定 DLE 但设备侧 occurrence_key 缺失（fail-open），降级为有 DLE 锚定的 (serial, entry)
                        dedup_key = f"compat:{serial}:{entry_name}"
                        identity_source = "compat_serial_entry"
                        compat_fallback_rows += 1
                    else:
                        reason = str(m_row.get("unmapped_reason") or "mapping_unmapped")
                        unbound_rows.append(
                            UnboundRowRecord(
                                plan_run_id=rs.plan_run_id,
                                host_id=rep.host_id,
                                report_file=report_path.name,
                                row_no=row_no,
                                serial=serial,
                                raw_path=raw_path,
                                reason=reason,
                            )
                        )
                        unbound_counter[reason] += 1
                        continue
                else:
                    # 仅历史无伴随映射分片（如 Run 1216）：必须有 DLE 台账精确锚定才允许走 (serial, entry)
                    candidates = (
                        dle_by_serial_entry.get((serial, entry_name), [])
                        if (serial and entry_name)
                        else []
                    )
                    if not candidates:
                        reason = "missing_mapping_no_dle"
                        unbound_rows.append(
                            UnboundRowRecord(
                                plan_run_id=rs.plan_run_id,
                                host_id=rep.host_id,
                                report_file=report_path.name,
                                row_no=row_no,
                                serial=serial,
                                raw_path=raw_path,
                                reason=reason,
                            )
                        )
                        unbound_counter[reason] += 1
                        continue
                    occ_keys = {
                        str(c.occurrence_key).strip()
                        for c in candidates
                        if c.occurrence_key and str(c.occurrence_key).strip()
                    }
                    matched_dle = candidates[0]
                    if len(occ_keys) == 1:
                        occ_key = next(iter(occ_keys))
                        dedup_key = f"occ:{occ_key}"
                        identity_source = "occurrence_key"
                        keyed_rows += 1
                    elif len(occ_keys) > 1:
                        reason = "missing_mapping_ambiguous_dle"
                        unbound_rows.append(
                            UnboundRowRecord(
                                plan_run_id=rs.plan_run_id,
                                host_id=rep.host_id,
                                report_file=report_path.name,
                                row_no=row_no,
                                serial=serial,
                                raw_path=raw_path,
                                reason=reason,
                            )
                        )
                        unbound_counter[reason] += 1
                        continue
                    else:
                        dedup_key = f"compat:{serial}:{entry_name}"
                        identity_source = "compat_serial_entry"
                        compat_fallback_rows += 1

                # 快照截止时间过滤（基于 DLE detected_at）
                detected_utc = ensure_utc(matched_dle.detected_at) if matched_dle is not None else None
                if detected_utc is not None and detected_utc > cutoff:
                    after_cutoff_skipped_rows += 1
                    continue

                if not serial or not dedup_key:
                    reason = "missing_serial_or_identity"
                    unbound_rows.append(
                        UnboundRowRecord(
                            plan_run_id=rs.plan_run_id,
                            host_id=rep.host_id,
                            report_file=report_path.name,
                            row_no=row_no,
                            serial=serial,
                            raw_path=raw_path,
                            reason=reason,
                        )
                    )
                    unbound_counter[reason] += 1
                    continue

                build = serial_build.get(serial) or row.get("Version") or _UNKNOWN_BUILD
                product = serial_product.get(serial, "")
                if build == _UNKNOWN_BUILD:
                    msg = f"设备 {serial} 缺失 build_version 映射，已归入 {_UNKNOWN_BUILD}"
                    if msg not in warnings:
                        warnings.append(msg)

                baseline_cls, clock_fallback = classify_event_baseline(
                    row.get("ExpTime"),
                    detected_utc,
                    first_run_started_at,
                )
                ev_loc, ev_tier = _pick_evidence_location(matched_dle)
                dle_id_str = str(matched_dle.id) if matched_dle is not None else None

                existing = deduped_by_key.get(dedup_key)
                if existing is None:
                    deduped_by_key[dedup_key] = DedupedEvent(
                        dedup_key=dedup_key,
                        identity_source=identity_source,
                        occurrence_key=occ_key,
                        serial=serial,
                        entry=entry_name or f"row_{row_no}",
                        build=build,
                        product=product,
                        source_runs=[rs.plan_run_id],
                        dle_ids=[dle_id_str] if dle_id_str else [],
                        primary_dle_id=dle_id_str,
                        detected_at=detected_utc,
                        baseline_class=baseline_cls,
                        clock_fallback=clock_fallback,
                        evidence_location=ev_loc,
                        evidence_tier=ev_tier,
                        fields=dict(row),
                    )
                else:
                    if rs.plan_run_id not in existing.source_runs:
                        existing.source_runs.append(rs.plan_run_id)
                        existing.source_runs.sort()
                    if dle_id_str and dle_id_str not in existing.dle_ids:
                        existing.dle_ids.append(dle_id_str)
                    if _EVIDENCE_TIER_RANK[ev_tier] > _EVIDENCE_TIER_RANK[existing.evidence_tier]:
                        existing.evidence_location = ev_loc
                        existing.evidence_tier = ev_tier
                        if dle_id_str:
                            existing.primary_dle_id = dle_id_str
                    if detected_utc is not None and (
                        existing.detected_at is None or detected_utc < existing.detected_at
                    ):
                        existing.detected_at = detected_utc
                    if existing.baseline_class == "baseline" and baseline_cls == "current":
                        # 保留最早观测口径；不覆写为 current
                        pass
                    elif existing.clock_fallback and not clock_fallback:
                        existing.baseline_class = baseline_cls
                        existing.clock_fallback = False

    events = sorted(
        deduped_by_key.values(),
        key=lambda ev: (
            ev.build,
            ev.fields.get("ExpTime", ""),
            ev.serial,
            ev.entry,
            ev.dedup_key,
        ),
    )

    builds_meta: dict[str, dict[str, Any]] = {}
    for b, devs in build_devices.items():
        products = sorted({serial_product.get(s, "") for s in devs if serial_product.get(s)})
        builds_meta[b] = {
            "build": b,
            "product": products[0] if products else "",
            "device_count": len(devs),
            "unique_event_count": 0,
        }
    for ev in events:
        entry = builds_meta.setdefault(
            ev.build,
            {
                "build": ev.build,
                "product": ev.product,
                "device_count": 0,
                "unique_event_count": 0,
            },
        )
        entry["unique_event_count"] += 1
        if not entry["product"] and ev.product:
            entry["product"] = ev.product

    coverage_complete = len(blocking_issues) == 0
    identity_degraded = compat_fallback_rows > 0 or len(unbound_rows) > 0

    return IdentityCollectionResult(
        snapshot_cutoff=cutoff,
        first_run_started_at=first_run_started_at,
        runs=run_summaries,
        events=events,
        unbound_rows=unbound_rows,
        total_org_rows=total_org_rows,
        keyed_rows=keyed_rows,
        compat_fallback_rows=compat_fallback_rows,
        after_cutoff_skipped_rows=after_cutoff_skipped_rows,
        unbound_by_reason=dict(sorted(unbound_counter.items())),
        builds_meta=builds_meta,
        coverage_complete=coverage_complete,
        identity_degraded=identity_degraded,
        blocking_issues=blocking_issues,
        warnings=warnings,
    )


__all__ = [
    "FORM_COLUMNS",
    "HANDOFF_STATUS_PENDING_EXPORT",
    "ORG_COLUMNS",
    "SUPPORTED_HANDOFF_PLATFORMS",
    "DedupedEvent",
    "IdentityCollectionResult",
    "ReportSourceRef",
    "RunCoverageSummary",
    "UnboundRowRecord",
    "classify_event_baseline",
    "classify_issue_baseline",
    "collect_handoff_identity",
    "ensure_utc",
    "extract_serial_and_entry",
    "is_placeholder_org_row",
    "neutralize_spreadsheet_cell_text",
    "parse_exp_time_utc",
    "safe_xls_cell",
]
