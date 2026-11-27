"""B11-U1（#4199 v1.1 / #4174）阶段 1：A 段发生身份归并、覆盖率硬门禁与本次/基线判定。

本模块只读消费已登记的 PlanRun、DLE、ScanRound 与已上送 Agent ``*_org.xls``
及伴随 ``.mapping.json``，不写外部 15.4 共享根、不调用 ``aee_extract`` / ``-m 0``，
不导入 FastAPI（C2 分层约束）。

核心不变量（#4199 v1.1 §3 B11-U1 / §4.2）：
1. 覆盖率门禁为硬阻断：双向核验期望主机集与有效报表/合法空回执集合；坏表、错表头、
   缺必需列、越根/软链输入、非期望 host、未关闭轮次均不得计入有效覆盖。
2. ``snapshot_cutoff`` 同时冻结事件观测（``DLE.detected_at <= cutoff``）与分片选择
   （``CURRENT`` ``created_at <= cutoff``；``DEDUP_FIRST`` 已关闭 ``FINAL`` + ``SUPPLEMENT``
   且 ``closed_at <= cutoff`` 有效轮次分片并集）。
3. 发生身份按分片归属（``plan_run_id`` + ``host_id`` + ``serial``）与 ``occurrence_key``
   一致性严格核验 DLE；``compat_serial_entry`` 仅限无伴随映射的历史 ``CURRENT`` 分片
   且唯一锚定（多候选歧义入 ``unbound_rows``；``DEDUP_FIRST`` 缺伴随不得回退）。
4. 未绑定 DLE 行与 ``*_temp`` / ``CurProcessNone`` 等解析占位行全部显式记入 ``unbound_rows``。
5. 每条发生的 build 严格按源 run 的 ``device_builds`` 事实归属（同串号跨 run 换 build 不错归）。
6. 本次/基线判定以 ``<首轮 started_at>`` 为界，2010 类坏钟占位值回退 ``DLE.detected_at``（#4078），
   且抽为纯函数供未来 ``#4033`` 复用；「仅基线」问题只标注、不自动剔除。
7. 电子表格文本单元按 B3（#3561 G1 / #3237）规则中和公式注入。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session
import xlrd

from backend.core.artifact_paths import (
    ArtifactPathError,
    coerce_local_artifact_path,
    get_local_artifact_roots,
)
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

# 读入 Agent 上送 *_org.xls 时必须存在的表头列（大小写不敏感、去首尾空白）。
_REQUIRED_ORG_COLUMNS = frozenset(
    {
        "id",
        "path",
        "version",
        "exptime",
        "expclass",
        "exptype",
        "curprocess",
        "package",
        "detail",
        "causedby",
        "count",
        "deviceid",
    }
)

_DEDUP_FIRST_COVERED_STATES = frozenset({"RECEIVED", "EMPTY"})
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
        for idx, part in enumerate(parts):
            if part.endswith(".DEC") and idx >= 2:
                entry = parts[idx - 1].strip()
                serial = (device_id or parts[idx - 2]).strip()
                if serial and entry and not entry.endswith("_temp"):
                    return serial, entry
        if len(parts) >= 2 and device_id:
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


def _has_symlink_ancestor(path: Path) -> bool:
    """检查 ``path`` 自身及全部既有祖先目录中是否包含符号链接。"""
    cur = path
    while True:
        try:
            if cur.is_symlink():
                return True
        except OSError:
            return True
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return False


def _resolve_allowed_input_roots(storage_root: Optional[str]) -> tuple[Path, ...]:
    """收集允许读取输入分片的受控中心存储/归档根列表（按配置原样保留信任锚）。"""
    raw_roots: list[Path] = []
    if storage_root and storage_root.strip():
        raw_roots.append(Path(storage_root.strip()))
    for configured in get_local_artifact_roots():
        raw_roots.append(configured)
    unique: list[Path] = []
    seen: set[str] = set()
    for root in raw_roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return tuple(unique)


def _validate_controlled_input_file(
    raw_uri: str,
    allowed_roots: Sequence[Path],
    *,
    missing_code: str = "input_file_not_found",
) -> tuple[Optional[Path], Optional[str]]:
    """校验输入分片/映射文件落在受控存储根内、祖先及自身无 symlink 且为普通文件。"""
    if not raw_uri or not str(raw_uri).strip():
        return None, missing_code
    if not allowed_roots:
        return None, "storage_root_unconfigured"
    raw_str = str(raw_uri).strip()
    if "\0" in raw_str:
        return None, "input_path_rejected"
    try:
        # 使用既有路径契约校验 scheme 与 '..' 穿越（先不做 symlink 解析）
        coerced = coerce_local_artifact_path(raw_str)
    except ArtifactPathError:
        return None, "input_path_rejected"

    # 原始词法路径（用于逐段检测 symlink，不被 resolve() 吞掉）
    lexical = Path(raw_str[7:] if raw_str.lower().startswith("file://") else raw_str)
    if not lexical.is_absolute() or ".." in lexical.parts:
        return None, "input_path_rejected"

    matched_root: Optional[Path] = None
    for root in allowed_roots:
        if not root.is_absolute() or ".." in root.parts:
            continue
        if _has_symlink_ancestor(root):
            continue
        try:
            lexical.relative_to(root)
        except ValueError:
            continue
        matched_root = root
        break

    if matched_root is None:
        return None, "input_outside_controlled_root"

    # 从受控根向下逐段拒绝 symlink
    rel = lexical.relative_to(matched_root)
    cursor = matched_root
    for part in rel.parts:
        cursor = cursor / part
        try:
            st = os.lstat(cursor)
        except FileNotFoundError:
            return None, missing_code
        except OSError:
            return None, "input_unreadable"
        if stat.S_ISLNK(st.st_mode):
            return None, "input_symlink_rejected"

    try:
        final_st = os.lstat(lexical)
    except FileNotFoundError:
        return None, missing_code
    except OSError:
        return None, "input_unreadable"
    if stat.S_ISLNK(final_st.st_mode):
        return None, "input_symlink_rejected"
    if not stat.S_ISREG(final_st.st_mode):
        return None, "input_not_regular_file"

    try:
        resolved = lexical.resolve(strict=True)
        resolved_root = matched_root.resolve(strict=True)
    except OSError:
        return None, "input_unreadable"
    if resolved != coerced or resolved != lexical or not resolved.is_relative_to(resolved_root):
        return None, "input_symlink_rejected"

    return resolved, None


@dataclass(frozen=True)
class ReportSourceRef:
    plan_run_id: int
    host_id: str
    storage_uri: str
    mapping_uri: Optional[str]
    empty_receipt: bool = False
    round_id: Optional[int] = None


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
    unexpected_hosts: list[str] = field(default_factory=list)
    reports: list[ReportSourceRef] = field(default_factory=list)
    run_blocking_issues: list[str] = field(default_factory=list)


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
) -> tuple[dict[tuple[int, str], str], dict[str, str], dict[str, str], dict[str, set[str]]]:
    """按 ``(plan_run_id, serial)`` 派生源 run 的 build 归属与串号 product/兜底 build。

    严禁跨 run 用单一串号字典互相覆写（#4199 v1.1 §3⑥ / B5）。
    """
    run_ids = [r.id for r in runs]
    if not run_ids:
        return {}, {}, {}, {}

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

    run_serial_build: dict[tuple[int, str], str] = {}
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
            run_serial_build[(run.id, s)] = btext
            build_devices.setdefault(btext, set()).add(s)
        run_bv = str(run.build_version or "").strip()
        for s in run_serials.get(run.id, set()):
            resolved_b = run_serial_build.get((run.id, s)) or run_bv or serial_fallback_build.get(s) or _UNKNOWN_BUILD
            run_serial_build.setdefault((run.id, s), resolved_b)
            build_devices.setdefault(resolved_b, set()).add(s)

    return run_serial_build, serial_product, serial_fallback_build, build_devices


def _load_run_coverage(
    db: Session,
    run: PlanRun,
    *,
    platform: str,
    cutoff: datetime,
) -> RunCoverageSummary:
    """按 ``snapshot_cutoff`` 冻结加载单轮次报表分片候选集与期望主机集。

    注意：本阶段只收集冻结候选分片，实际 ``hosts_with_reports`` / ``missing_hosts`` 需在
    后续校验每个分片路径与 ``*_org.xls`` 表头完整性后最终定账（坏表不得计为有效覆盖）。
    """
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
    base_expected_hosts = {
        host_id for host_id, plats in expected_map.items() if platform in plats
    }

    reports: list[ReportSourceRef] = []
    unexpected_hosts: set[str] = set()
    run_blocking_issues: list[str] = []

    if partition_mode == facts.PARTITION_MODE_DEDUP_FIRST:
        rounds = facts.list_scan_rounds_for_read(db, plan_run_id=run.id, platform=platform)
        # 严格只纳入截止时间前已关闭的 FINAL + SUPPLEMENT 轮次（未关闭与 PERIODIC 轮次不得纳入）
        eligible_rounds = []
        for rnd in rounds:
            if rnd.kind not in (facts.SCAN_ROUND_KIND_FINAL, facts.SCAN_ROUND_KIND_SUPPLEMENT):
                continue
            if rnd.status != facts.SCAN_ROUND_STATUS_CLOSED:
                continue
            closed_utc = ensure_utc(getattr(rnd, "closed_at", None))
            if closed_utc is None or closed_utc > cutoff:
                continue
            eligible_rounds.append(rnd)

        has_closed_final = any(r.kind == facts.SCAN_ROUND_KIND_FINAL for r in eligible_rounds)
        if not has_closed_final:
            run_blocking_issues.append(
                f"plan_run {run.id} 在快照截止时间前无已关闭的 FINAL 轮次"
            )

        round_expected_hosts: set[str] = set(base_expected_hosts)
        for rnd in eligible_rounds:
            for unit in (rnd.expected_set or []):
                if not isinstance(unit, dict):
                    continue
                unit_plat = str(unit.get("platform") or platform).lower()
                if unit_plat != platform:
                    continue
                hid = str(unit.get("host_id") or "").strip()
                if not hid:
                    continue
                if unit.get("expected", True):
                    round_expected_hosts.add(hid)

        seen_report_keys: set[tuple[str, str]] = set()
        seen_empty_hosts: set[str] = set()
        for rnd in eligible_rounds:
            for unit in (rnd.expected_set or []):
                if not isinstance(unit, dict):
                    continue
                unit_plat = str(unit.get("platform") or platform).lower()
                if unit_plat != platform:
                    continue
                hid = str(unit.get("host_id") or "").strip()
                if not hid:
                    continue
                unit_expected = bool(unit.get("expected", True))
                unit_coverage = unit.get("coverage")
                coverage_allowed = (
                    unit_coverage is None
                    or str(unit_coverage).upper() in _DEDUP_FIRST_COVERED_STATES
                )
                unit_reports = unit.get("reports") or []
                if not unit_expected and hid not in round_expected_hosts:
                    if unit_reports:
                        unexpected_hosts.add(hid)
                    continue
                if not coverage_allowed:
                    continue
                for rep in unit_reports:
                    if not isinstance(rep, dict):
                        continue
                    if rep.get("empty_receipt"):
                        if hid not in seen_empty_hosts:
                            seen_empty_hosts.add(hid)
                            reports.append(
                                ReportSourceRef(
                                    plan_run_id=run.id,
                                    host_id=hid,
                                    storage_uri="",
                                    mapping_uri=None,
                                    empty_receipt=True,
                                    round_id=getattr(rnd, "id", None),
                                )
                            )
                        continue
                    uri = str(rep.get("storage_uri") or "").strip()
                    if not uri or not uri.endswith("_org.xls"):
                        continue
                    dedup_rep_key = (hid, uri)
                    if dedup_rep_key in seen_report_keys:
                        continue
                    seen_report_keys.add(dedup_rep_key)
                    mapping_uri = str(rep.get("mapping_uri") or "").strip() or None
                    reports.append(
                        ReportSourceRef(
                            plan_run_id=run.id,
                            host_id=hid,
                            storage_uri=uri,
                            mapping_uri=mapping_uri,
                            empty_receipt=False,
                            round_id=getattr(rnd, "id", None),
                        )
                    )
        expected_hosts = sorted(round_expected_hosts)
    else:
        # CURRENT 模式：按 snapshot_cutoff 冻结，取每个期望 host 截止时刻前最新已登记的 *_org.xls
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
            art_created = ensure_utc(getattr(art, "created_at", None))
            if art_created is not None and art_created > cutoff:
                continue
            uri = str(art.storage_uri or "").strip()
            if not uri or not uri.endswith("_org.xls"):
                continue
            if scan_artifact_uri_platform(uri) != platform:
                continue
            hid = str(art.host_id or "").strip()
            if not hid:
                run_blocking_issues.append(f"plan_run {run.id} 存在缺失 host_id 的扫描报表记录")
                continue
            if hid not in base_expected_hosts:
                unexpected_hosts.add(hid)
                continue
            if hid in by_host:
                continue
            by_host[hid] = ReportSourceRef(
                plan_run_id=run.id,
                host_id=hid,
                storage_uri=uri,
                mapping_uri=None,
                empty_receipt=False,
            )
        expected_hosts = sorted(base_expected_hosts)
        reports = [by_host[h] for h in sorted(by_host)]

    if not expected_hosts:
        run_blocking_issues.append(f"plan_run {run.id} 无期望 {platform} 主机")
    if unexpected_hosts:
        run_blocking_issues.append(
            f"plan_run {run.id} 存在非期望主机报表: {', '.join(sorted(unexpected_hosts))}"
        )

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
        hosts_with_reports=[],
        missing_hosts=list(expected_hosts),
        mapping_status="empty",
        unexpected_hosts=sorted(unexpected_hosts),
        reports=reports,
        run_blocking_issues=run_blocking_issues,
    )


def _read_org_xls_rows(report_path: Path) -> list[dict[str, str]]:
    """严格读取单份 ``*_org.xls`` 的 ``aeeexp`` 工作表（空表也必须校验表头与必需列）。"""
    book = xlrd.open_workbook(str(report_path), on_demand=True)
    try:
        if "aeeexp" not in book.sheet_names():
            raise ValueError(f"missing 'aeeexp' sheet in {report_path.name}")
        sheet = book.sheet_by_name("aeeexp")
        if sheet.nrows < 1:
            raise ValueError(f"empty 'aeeexp' sheet (missing header row) in {report_path.name}")
        headers = [str(v).strip() for v in sheet.row_values(0)]
        col_map = {h.lower(): idx for idx, h in enumerate(headers) if h}
        missing_cols = sorted(_REQUIRED_ORG_COLUMNS - set(col_map))
        if missing_cols:
            raise ValueError(
                f"missing required columns in {report_path.name}: {', '.join(missing_cols)}"
            )
        if sheet.nrows == 1:
            return []

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
    report_path: Path,
    explicit_mapping_uri: Optional[str],
    org_rows: Sequence[Mapping[str, str]],
    allowed_roots: Sequence[Path],
) -> tuple[Optional[list[dict[str, Any]]], Optional[str]]:
    """按受控存储根与无 symlink 边界加载并校验伴随 ``.mapping.json``（对齐 ``scan_membership.py:51-64``）。"""
    candidate_uri = explicit_mapping_uri
    probed_sibling = False
    if not candidate_uri:
        candidate_uri = str(report_path.with_name(report_path.name + ".mapping.json"))
        probed_sibling = True

    validated_path, path_err = _validate_controlled_input_file(
        candidate_uri,
        allowed_roots,
        missing_code="missing_mapping",
    )
    if path_err is not None or validated_path is None:
        if path_err == "missing_mapping":
            return None, "missing_mapping"
        # 若显式 mapping_uri 或同目录探针指向越根/符号链接/非普通文件，绝不允许退回历史回退分支
        return None, "mapping_path_rejected" if not probed_sibling else f"mapping_{path_err}"

    try:
        payload = json.loads(validated_path.read_text(encoding="utf-8"))
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
    storage_root: Optional[str] = None,
) -> IdentityCollectionResult:
    """执行双门禁检查 + 跨 run 发生身份归并（纯只读，零外部写盘、零 ``aee_extract``）。"""
    cutoff = ensure_utc(snapshot_cutoff) or datetime.now(timezone.utc)
    allowed_input_roots = _resolve_allowed_input_roots(storage_root)

    ordered_ids = list(dict.fromkeys(int(rid) for rid in plan_run_ids))
    run_rows = db.execute(
        select(PlanRun).where(PlanRun.id.in_(ordered_ids))
    ).scalars().all()
    by_id = {r.id: r for r in run_rows}
    missing_run_ids = [rid for rid in ordered_ids if rid not in by_id]
    if missing_run_ids:
        raise LookupError(f"plan_run not found: {missing_run_ids}")

    runs_ordered = [by_id[rid] for rid in ordered_ids]
    run_summaries = [
        _load_run_coverage(db, run, platform=platform, cutoff=cutoff)
        for run in runs_ordered
    ]
    start_times = [s.started_at for s in run_summaries if s.started_at is not None]
    first_run_started_at = min(start_times) if start_times else None

    (
        run_serial_build,
        serial_product,
        serial_fallback_build,
        build_devices,
    ) = _load_device_build_and_product(db, runs_ordered)
    run_build_version = {
        r.id: str(r.build_version or "").strip() for r in runs_ordered
    }
    run_started_map = {
        r.id: ensure_utc(r.started_at) for r in runs_ordered
    }

    # 一次性拉取选中 run 集合的 DLE 台账，按 id 与分片级 (plan_run_id, host_id, serial, entry) 建索引
    dle_rows = db.execute(
        select(DeviceLogEvent)
        .where(
            DeviceLogEvent.plan_run_id.in_(ordered_ids),
            func.lower(DeviceLogEvent.platform) == platform.lower(),
        )
        .order_by(DeviceLogEvent.detected_at.asc(), DeviceLogEvent.id.asc())
    ).scalars().all()
    dle_by_id: dict[str, DeviceLogEvent] = {}
    dle_by_shard_serial_entry: dict[tuple[int, str, str, str], list[DeviceLogEvent]] = {}
    for dle in dle_rows:
        dle_by_id[str(dle.id)] = dle
        entry_name = _dle_entry_name(dle)
        dle_host = str(getattr(dle, "host_id", "") or "").strip()
        dle_serial = str(dle.serial or "").strip()
        if dle.plan_run_id is not None and dle_host and dle_serial and entry_name:
            dle_by_shard_serial_entry.setdefault(
                (int(dle.plan_run_id), dle_host, dle_serial, entry_name), []
            ).append(dle)

    unbound_rows: list[UnboundRowRecord] = []
    unbound_counter: Counter[str] = Counter()
    deduped_by_key: dict[str, DedupedEvent] = {}
    event_origin_started_at: dict[str, Optional[datetime]] = {}
    total_org_rows = 0
    keyed_rows = 0
    compat_fallback_rows = 0
    after_cutoff_skipped_rows = 0
    warnings: list[str] = []
    blocking_issues: list[str] = []

    def _record_unbound(
        *,
        plan_run_id: int,
        host_id: str,
        report_file: str,
        row_no: int,
        serial: str,
        raw_path: str,
        reason: str,
    ) -> None:
        unbound_rows.append(
            UnboundRowRecord(
                plan_run_id=plan_run_id,
                host_id=host_id,
                report_file=report_file,
                row_no=row_no,
                serial=serial,
                raw_path=raw_path,
                reason=reason,
            )
        )
        unbound_counter[reason] += 1

    for rs in run_summaries:
        blocking_issues.extend(rs.run_blocking_issues)
        valid_covered_hosts: set[str] = set()
        invalid_hosts: set[str] = set()
        non_empty_valid_reports = 0
        mapped_valid_reports = 0

        for rep in rs.reports:
            if rep.empty_receipt:
                valid_covered_hosts.add(rep.host_id)
                continue

            validated_report, path_err = _validate_controlled_input_file(
                rep.storage_uri,
                allowed_input_roots,
                missing_code="report_not_found",
            )
            if path_err is not None or validated_report is None:
                invalid_hosts.add(rep.host_id)
                blocking_issues.append(
                    f"plan_run {rs.plan_run_id} host {rep.host_id} 报表路径校验失败({path_err}): {Path(rep.storage_uri).name}"
                )
                continue

            try:
                org_rows = _read_org_xls_rows(validated_report)
            except (OSError, ValueError, KeyError, TypeError, IndexError, xlrd.XLRDError) as exc:
                invalid_hosts.add(rep.host_id)
                blocking_issues.append(
                    f"plan_run {rs.plan_run_id} host {rep.host_id} 解析报表失败: {validated_report.name} ({exc})"
                )
                continue

            valid_covered_hosts.add(rep.host_id)
            non_empty_valid_reports += 1

            mapping_rows, mapping_err = _load_and_validate_mapping(
                validated_report,
                rep.mapping_uri,
                org_rows,
                allowed_input_roots,
            )
            if mapping_rows is not None:
                mapped_valid_reports += 1
            elif mapping_err and mapping_err != "missing_mapping":
                warnings.append(
                    f"plan_run {rs.plan_run_id} host {rep.host_id} 伴随映射校验失败({mapping_err})，该分片行记入未绑定清单"
                )
            elif mapping_err == "missing_mapping" and rs.partition_mode == facts.PARTITION_MODE_DEDUP_FIRST:
                warnings.append(
                    f"plan_run {rs.plan_run_id} host {rep.host_id}（DEDUP_FIRST）缺少伴随映射，不允许兼容回退，行记入未绑定清单"
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
                    _record_unbound(
                        plan_run_id=rs.plan_run_id,
                        host_id=rep.host_id,
                        report_file=validated_report.name,
                        row_no=row_no,
                        serial=serial,
                        raw_path=raw_path,
                        reason=placeholder_reason,
                    )
                    continue

                if mapping_err is not None and mapping_err != "missing_mapping":
                    _record_unbound(
                        plan_run_id=rs.plan_run_id,
                        host_id=rep.host_id,
                        report_file=validated_report.name,
                        row_no=row_no,
                        serial=serial,
                        raw_path=raw_path,
                        reason=mapping_err,
                    )
                    continue

                dedup_key: Optional[str] = None
                identity_source = ""
                occ_key: Optional[str] = None
                matched_dle: Optional[DeviceLogEvent] = None
                entry_name = parsed_entry

                if mapping_rows is not None:
                    m_row = mapping_rows[idx]
                    mapped_flag = bool(m_row.get("mapped"))
                    if not mapped_flag:
                        reason = str(m_row.get("unmapped_reason") or "mapping_unmapped")
                        _record_unbound(
                            plan_run_id=rs.plan_run_id,
                            host_id=rep.host_id,
                            report_file=validated_report.name,
                            row_no=row_no,
                            serial=serial,
                            raw_path=raw_path,
                            reason=reason,
                        )
                        continue

                    raw_dle_id = str(m_row.get("dle_id") or "").strip()
                    raw_occ = str(m_row.get("occurrence_key") or "").strip()
                    matched_dle = dle_by_id.get(raw_dle_id) if raw_dle_id else None
                    if matched_dle is None:
                        _record_unbound(
                            plan_run_id=rs.plan_run_id,
                            host_id=rep.host_id,
                            report_file=validated_report.name,
                            row_no=row_no,
                            serial=serial,
                            raw_path=raw_path,
                            reason="dle_not_found",
                        )
                        continue

                    # 严格校验分片归属（plan_run_id + host_id + serial）
                    if matched_dle.plan_run_id != rs.plan_run_id:
                        _record_unbound(
                            plan_run_id=rs.plan_run_id,
                            host_id=rep.host_id,
                            report_file=validated_report.name,
                            row_no=row_no,
                            serial=serial,
                            raw_path=raw_path,
                            reason="dle_plan_run_mismatch",
                        )
                        continue
                    dle_host = str(getattr(matched_dle, "host_id", "") or "").strip()
                    if dle_host != rep.host_id:
                        _record_unbound(
                            plan_run_id=rs.plan_run_id,
                            host_id=rep.host_id,
                            report_file=validated_report.name,
                            row_no=row_no,
                            serial=serial,
                            raw_path=raw_path,
                            reason="dle_host_mismatch",
                        )
                        continue
                    dle_serial = str(matched_dle.serial or "").strip()
                    if serial and dle_serial and serial != dle_serial:
                        _record_unbound(
                            plan_run_id=rs.plan_run_id,
                            host_id=rep.host_id,
                            report_file=validated_report.name,
                            row_no=row_no,
                            serial=serial,
                            raw_path=raw_path,
                            reason="dle_serial_mismatch",
                        )
                        continue
                    if not serial and dle_serial:
                        serial = dle_serial
                    if not entry_name:
                        entry_name = _dle_entry_name(matched_dle)

                    dle_occ = str(matched_dle.occurrence_key or "").strip()
                    if raw_occ and dle_occ and raw_occ != dle_occ:
                        _record_unbound(
                            plan_run_id=rs.plan_run_id,
                            host_id=rep.host_id,
                            report_file=validated_report.name,
                            row_no=row_no,
                            serial=serial,
                            raw_path=raw_path,
                            reason="occurrence_key_conflict",
                        )
                        continue

                    effective_occ = raw_occ or dle_occ
                    if not effective_occ:
                        # 有伴随映射却缺 occurrence_key：不得走历史无映射 compat 回退
                        _record_unbound(
                            plan_run_id=rs.plan_run_id,
                            host_id=rep.host_id,
                            report_file=validated_report.name,
                            row_no=row_no,
                            serial=serial,
                            raw_path=raw_path,
                            reason="missing_occurrence_key",
                        )
                        continue

                    occ_key = effective_occ
                    dedup_key = f"occ:{occ_key}"
                    identity_source = "occurrence_key"
                else:
                    # 缺伴随映射分支：DEDUP_FIRST 严禁推断回退；仅历史 CURRENT 允许本分片唯一 DLE 锚定
                    if rs.partition_mode != facts.PARTITION_MODE_CURRENT:
                        _record_unbound(
                            plan_run_id=rs.plan_run_id,
                            host_id=rep.host_id,
                            report_file=validated_report.name,
                            row_no=row_no,
                            serial=serial,
                            raw_path=raw_path,
                            reason="dedup_first_missing_mapping",
                        )
                        continue

                    candidates = (
                        dle_by_shard_serial_entry.get(
                            (rs.plan_run_id, rep.host_id, serial, entry_name), []
                        )
                        if (serial and entry_name)
                        else []
                    )
                    if not candidates:
                        _record_unbound(
                            plan_run_id=rs.plan_run_id,
                            host_id=rep.host_id,
                            report_file=validated_report.name,
                            row_no=row_no,
                            serial=serial,
                            raw_path=raw_path,
                            reason="missing_mapping_no_dle",
                        )
                        continue

                    if len(candidates) == 1:
                        matched_dle = candidates[0]
                        dle_occ = str(matched_dle.occurrence_key or "").strip()
                        if dle_occ:
                            occ_key = dle_occ
                            dedup_key = f"occ:{occ_key}"
                            identity_source = "occurrence_key"
                        else:
                            dedup_key = f"compat:{serial}:{entry_name}"
                            identity_source = "compat_serial_entry"
                    else:
                        occ_keys = {
                            str(c.occurrence_key).strip()
                            for c in candidates
                            if c.occurrence_key and str(c.occurrence_key).strip()
                        }
                        all_have_key = all(
                            bool(c.occurrence_key and str(c.occurrence_key).strip())
                            for c in candidates
                        )
                        if all_have_key and len(occ_keys) == 1:
                            matched_dle = candidates[0]
                            occ_key = next(iter(occ_keys))
                            dedup_key = f"occ:{occ_key}"
                            identity_source = "occurrence_key"
                        else:
                            # 多候选且无唯一 occurrence_key（含全部无 key 多候选）→ 歧义入未绑定
                            _record_unbound(
                                plan_run_id=rs.plan_run_id,
                                host_id=rep.host_id,
                                report_file=validated_report.name,
                                row_no=row_no,
                                serial=serial,
                                raw_path=raw_path,
                                reason="missing_mapping_ambiguous_dle",
                            )
                            continue

                # 快照截止时间过滤（基于 DLE detected_at）
                detected_utc = ensure_utc(matched_dle.detected_at) if matched_dle is not None else None
                if detected_utc is not None and detected_utc > cutoff:
                    after_cutoff_skipped_rows += 1
                    continue

                if not serial or not dedup_key:
                    _record_unbound(
                        plan_run_id=rs.plan_run_id,
                        host_id=rep.host_id,
                        report_file=validated_report.name,
                        row_no=row_no,
                        serial=serial,
                        raw_path=raw_path,
                        reason="missing_serial_or_identity",
                    )
                    continue

                if identity_source == "occurrence_key":
                    keyed_rows += 1
                else:
                    compat_fallback_rows += 1

                # 按源 run 事实取 build（禁止跨 run 串号覆盖）
                build = (
                    run_serial_build.get((rs.plan_run_id, serial))
                    or run_build_version.get(rs.plan_run_id)
                    or row.get("Version")
                    or serial_fallback_build.get(serial)
                    or _UNKNOWN_BUILD
                )
                product = serial_product.get(serial, "")
                if build == _UNKNOWN_BUILD:
                    msg = f"设备 {serial}（run {rs.plan_run_id}）缺失 build_version 映射，已归入 {_UNKNOWN_BUILD}"
                    if msg not in warnings:
                        warnings.append(msg)

                baseline_cls, clock_fallback = classify_event_baseline(
                    row.get("ExpTime"),
                    detected_utc,
                    first_run_started_at,
                )
                ev_loc, ev_tier = _pick_evidence_location(matched_dle)
                dle_id_str = str(matched_dle.id) if matched_dle is not None else None
                cur_run_started = run_started_map.get(rs.plan_run_id)

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
                    event_origin_started_at[dedup_key] = cur_run_started
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
                    earlier_detected = detected_utc is not None and (
                        existing.detected_at is None or detected_utc < existing.detected_at
                    )
                    if earlier_detected:
                        existing.detected_at = detected_utc
                        existing.build = build
                        event_origin_started_at[dedup_key] = cur_run_started
                    elif (
                        cur_run_started is not None
                        and (
                            event_origin_started_at.get(dedup_key) is None
                            or cur_run_started < event_origin_started_at[dedup_key]
                        )
                        and existing.detected_at == detected_utc
                    ):
                        existing.build = build
                        event_origin_started_at[dedup_key] = cur_run_started
                    if existing.baseline_class == "baseline" and baseline_cls == "current":
                        pass
                    elif existing.clock_fallback and not clock_fallback:
                        existing.baseline_class = baseline_cls
                        existing.clock_fallback = False

        # 回填经真实文件与表头校验后的最终主机覆盖口径
        effective_covered = (valid_covered_hosts - invalid_hosts) & set(rs.hosts_expected)
        rs.hosts_with_reports = sorted(effective_covered)
        rs.missing_hosts = [h for h in rs.hosts_expected if h not in effective_covered]
        if non_empty_valid_reports == 0:
            rs.mapping_status = "empty"
        elif mapped_valid_reports == non_empty_valid_reports:
            rs.mapping_status = "complete"
        elif mapped_valid_reports == 0:
            rs.mapping_status = "missing"
        else:
            rs.mapping_status = "partial"

        if rs.missing_hosts:
            blocking_issues.append(
                f"plan_run {rs.plan_run_id} 缺少 {len(rs.missing_hosts)} 台主机有效报表: {', '.join(rs.missing_hosts)}"
            )
        if rs.mapping_status in ("partial", "missing"):
            warnings.append(
                f"plan_run {rs.plan_run_id} 伴随映射状态={rs.mapping_status}（缺映射分片仅历史 CURRENT 唯一锚定走兼容回退）"
            )

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
