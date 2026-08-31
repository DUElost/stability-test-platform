# -*- coding: utf-8 -*-
"""AI 助手工具注册表（ADR-0031 D1/D4）。

- T0 观测查询：服务层直读（不经 HTTP 自调）。
- T1/T2 执行类：**一律 RunConsole**（argv 服务端模板拼装，LLM 只能填参数；
  禁 shell、禁路径穿越、枚举参数白名单）。
- T3（hot-update/生产库写/任意 shell）不注册——无入口即无暗门。
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
from backend.models.audit import AuditLog
from backend.models.enums import DeviceStatus, HostStatus, PlanRunStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.notification import AlertRule
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.project import Specialty, TestProject

REPO_ROOT = Path(__file__).resolve().parents[3]

# 与 scripts/run_gates.py AGENT_TEST_ENV 同源语义（run_gates.py:35-41）：
# 既避免 agent 测试收集期 resolve_database_url RuntimeError，也显式覆盖
# backend 进程 env 中可能指向生产库的 DATABASE_URL（H1，防生产串透传）。
AGENT_TEST_ENV_OVERRIDE = {
    "TESTING": "1",
    "JWT_SECRET_KEY": "ci-test-secret-key",
    "DATABASE_URL": "postgresql+psycopg://postgres:postgres@localhost:5432/stability_test",
    "TEST_DATABASE_URL": "postgresql+psycopg://postgres:postgres@localhost:5432/stability_test",
}

AGENT_TESTS_DIR = REPO_ROOT / "backend" / "agent" / "tests"


class ToolValidationError(ValueError):
    """工具参数校验失败——报错回填给模型重试（占轮次预算，不静默修正）。"""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    tier: str  # "T0" | "T1" | "T2"
    kind: str  # "query" | "runconsole" | "service"
    run_key: str | None = None
    timeout_seconds: int = 300
    # 仅 T2 低危工具可加入免确认白名单（前端勾选与此处对齐）
    whitelistable: bool = False
    # 镜像 admin-only 端点的工具（audit/settings 路由均 require_admin）：
    # 助手工具面不得宽于用户自身 API 权限（PR-Agent gate 越权发现）
    admin_only: bool = False


@dataclass
class RunConsolePlan:
    cmd: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    run_key: str = ""
    timeout_seconds: int = 300


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


# ─────────────────────────── 参数校验小工具 ───────────────────────────

def _clamp_int(value: Any, name: str, default: int, lo: int, hi: int) -> int:
    if value in (None, ""):
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ToolValidationError(f"{name} must be an integer") from None
    return max(lo, min(hi, n))


def _enum(value: Any, name: str, allowed: tuple[str, ...]) -> str:
    v = str(value or "").strip()
    if v not in allowed:
        raise ToolValidationError(f"{name} must be one of {list(allowed)}")
    return v


def _opt_str(value: Any, name: str, max_len: int = 100) -> str | None:
    if value in (None, ""):
        return None
    v = str(value).strip()
    if not v:
        return None
    if len(v) > max_len:
        raise ToolValidationError(f"{name} too long (>{max_len})")
    return v


def _parse_device_ids(value: Any, name: str = "device_ids") -> list[int]:
    if not isinstance(value, list) or not value:
        raise ToolValidationError(f"{name} must be a non-empty array of integers")
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            raise ToolValidationError(f"{name} must contain integers only") from None
    if len(out) > 50:
        raise ToolValidationError(f"{name} too many devices (max 50)")
    return out


def _plan_hidden_by_legacy(steps: list) -> bool:
    return any(s.script_name in LEGACY_AEE_SCRIPT_NAMES for s in steps)


def _format_plan_summary(plan: Plan, steps: list) -> str:
    spec = plan.specialty.key if getattr(plan, "specialty", None) else None
    proj = plan.project.project_key if getattr(plan, "project", None) else None
    return (
        f"#{plan.id} name={plan.name!r} project={proj} specialty={spec} "
        f"steps={len(steps)} failure_threshold={plan.failure_threshold}"
    )


# ─────────────────────────── T0 查询实现 ───────────────────────────

def _q_platform_health(db: Session, args: dict) -> str:
    db.execute(text("SELECT 1"))
    hosts = dict(db.query(Host.status, func.count(Host.id)).group_by(Host.status).all())
    devices = dict(db.query(Device.status, func.count(Device.id)).group_by(Device.status).all())
    # 状态分布全量 group_by——不枚举具体状态值（plan_run_status 枚举与
    # job_status 枚举值集不同，猜测会 InvalidTextRepresentation，线上实测）
    run_dist = dict(db.query(PlanRun.status, func.count(PlanRun.id)).group_by(PlanRun.status).all())
    lines = [
        "数据库连接正常。",
        f"主机：{dict(hosts) or '无记录'}",
        f"设备：{dict(devices) or '无记录'}",
        f"PlanRun 状态分布：{run_dist or '无记录'}",
    ]
    return "\n".join(lines)


def _enum_values(enum_cls) -> tuple[str, ...]:
    return tuple(m.value if hasattr(m, "value") else str(m) for m in enum_cls)


def _filter_status(q, column, raw: str | None, enum_cls, name: str):
    """M1：状态过滤按枚举成员校验，非法值把合法列表回给模型（零枚举猜测）。"""
    if not raw:
        return q
    value = str(raw).strip().upper()
    legal = _enum_values(enum_cls)
    if value not in legal:
        raise ToolValidationError(f"{name} 合法值：{list(legal)}")
    return q.filter(column == value)


def _q_plan_runs(db: Session, args: dict) -> str:
    limit = _clamp_int(args.get("limit"), "limit", 10, 1, 20)
    q = _filter_status(
        db.query(PlanRun), PlanRun.status,
        _opt_str(args.get("status"), "status", 32), PlanRunStatus, "status",
    )
    project_id = args.get("project_id")
    if project_id not in (None, ""):
        q = q.filter(PlanRun.project_id == int(project_id))
    specialty_id = args.get("specialty")
    if specialty_id not in (None, ""):
        # specialty 在 plan 表（models/plan.py specialty_id）——join 过滤（M6）
        q = q.join(Plan, PlanRun.plan_id == Plan.id).filter(
            Plan.specialty_id == int(specialty_id)
        )
    runs = q.order_by(PlanRun.id.desc()).limit(limit).all()
    if not runs:
        return "没有匹配的执行记录。"
    lines = [f"共 {len(runs)} 条（最多展示 {limit}）："]
    for r in runs:
        lines.append(
            f"#{r.id} status={r.status} project_id={r.project_id} "
            f"started={getattr(r, 'started_at', None)}"
        )
    return "\n".join(lines)


def _q_plan_run_detail(db: Session, args: dict) -> str:
    run_id = _clamp_int(args.get("run_id"), "run_id", 0, 1, 10**9)
    run = db.get(PlanRun, run_id)
    if not run:
        return f"PlanRun #{run_id} 不存在。"
    jobs = dict(
        db.query(JobInstance.status, func.count(JobInstance.id))
        .filter(JobInstance.plan_run_id == run_id)
        .group_by(JobInstance.status)
        .all()
    )
    ctx = run.run_context or {}
    ctx_keys = list(ctx.keys())[:20]
    return (
        f"PlanRun #{run.id}\nstatus={run.status}\nproject_id={run.project_id}\n"
        f"job 状态分布={jobs or '无 job'}\nrun_context 键={ctx_keys}"
    )


def _q_list_plans(db: Session, args: dict) -> str:
    limit = _clamp_int(args.get("limit"), "limit", 10, 1, 20)
    q = db.query(Plan)
    project_key = _opt_str(args.get("project_key"), "project_key", 64)
    if project_key:
        proj = db.query(TestProject).filter(TestProject.project_key == project_key).first()
        if proj is None:
            return f"项目 key {project_key!r} 不存在。"
        q = q.filter(Plan.project_id == proj.id)
    specialty_key = _opt_str(args.get("specialty_key"), "specialty_key", 64)
    if specialty_key:
        spec = db.query(Specialty).filter(Specialty.key == specialty_key).first()
        if spec is None:
            return f"专项 key {specialty_key!r} 不存在。"
        q = q.filter(Plan.specialty_id == spec.id)
    plans = q.order_by(Plan.created_at.desc()).limit(limit).all()
    if not plans:
        return "没有匹配的 Plan。"
    plan_ids = [p.id for p in plans]
    all_steps = (
        db.query(PlanStep)
        .filter(PlanStep.plan_id.in_(plan_ids))
        .order_by(PlanStep.stage, PlanStep.sort_order)
        .all()
    )
    steps_by: dict[int, list] = {}
    for step in all_steps:
        steps_by.setdefault(step.plan_id, []).append(step)
    visible = [p for p in plans if not _plan_hidden_by_legacy(steps_by.get(p.id, []))]
    if not visible:
        return "没有匹配的 Plan。"
    lines = [f"共 {len(visible)} 条 Plan："]
    for plan in visible:
        lines.append(_format_plan_summary(plan, steps_by.get(plan.id, [])))
    return "\n".join(lines)


def _q_get_plan_detail(db: Session, args: dict) -> str:
    plan_id = _clamp_int(args.get("plan_id"), "plan_id", 0, 1, 10**9)
    plan = db.get(Plan, plan_id)
    if plan is None:
        return f"Plan #{plan_id} 不存在。"
    steps = (
        db.query(PlanStep)
        .filter(PlanStep.plan_id == plan_id)
        .order_by(PlanStep.stage, PlanStep.sort_order)
        .all()
    )
    if _plan_hidden_by_legacy(steps):
        return f"Plan #{plan_id} 不存在。"
    lines = [_format_plan_summary(plan, steps), "步骤："]
    for idx, step in enumerate(steps, 1):
        lines.append(
            f"  {idx}. stage={step.stage} sort={step.sort_order} "
            f"script={step.script_name}:{step.script_version}"
        )
    return "\n".join(lines)


def _q_preview_plan_dispatch(db: Session, args: dict) -> str:
    from backend.services.plan_dispatcher_sync import PlanDispatchError, preview_plan_dispatch_sync

    plan_id = _clamp_int(args.get("plan_id"), "plan_id", 0, 1, 10**9)
    device_ids = _parse_device_ids(args.get("device_ids"))
    try:
        preview = preview_plan_dispatch_sync(plan_id, device_ids, db)
    except PlanDispatchError as exc:
        return f"预检失败：{exc}"
    lifecycle = preview.get("lifecycle") or {}
    stage_keys = list(lifecycle.keys())[:10]
    return (
        f"预检通过（未创建 PlanRun）。\n"
        f"plan_id={preview.get('plan_id')} name={preview.get('plan_name')!r}\n"
        f"device_ids={preview.get('device_ids')} total_steps={preview.get('total_steps')}\n"
        f"lifecycle 顶层键={stage_keys}"
    )


def _q_plan_run_jobs(db: Session, args: dict) -> str:
    run_id = _clamp_int(args.get("run_id"), "run_id", 0, 1, 10**9)
    if db.get(PlanRun, run_id) is None:
        return f"PlanRun #{run_id} 不存在。"
    jobs = (
        db.query(JobInstance)
        .filter(JobInstance.plan_run_id == run_id)
        .order_by(JobInstance.id)
        .all()
    )
    if not jobs:
        return f"PlanRun #{run_id} 尚无 job。"
    device_ids = list({j.device_id for j in jobs})
    serials: dict[int, str] = {}
    if device_ids:
        for dev in db.query(Device).filter(Device.id.in_(device_ids)).all():
            serials[dev.id] = dev.serial
    lines = [f"PlanRun #{run_id} 共 {len(jobs)} 个 job："]
    for job in jobs:
        lines.append(
            f" job#{job.id} device_id={job.device_id} serial={serials.get(job.device_id, '?')} "
            f"host={job.host_id} status={job.status} manual_action={job.manual_action}"
        )
    return "\n".join(lines)


def _q_plan_run_watcher_summary(db: Session, args: dict) -> str:
    from backend.services.log_observation import aggregate_risk_summary, aggregate_signal_link_stats

    run_id = _clamp_int(args.get("run_id"), "run_id", 0, 1, 10**9)
    pr = db.get(PlanRun, run_id)
    if pr is None:
        return f"PlanRun #{run_id} 不存在。"
    job_ids = [
        row[0]
        for row in db.query(JobInstance.id).filter(JobInstance.plan_run_id == run_id).all()
    ]
    if not job_ids:
        return f"PlanRun #{run_id} 无 job，watcher 无数据。"
    link = aggregate_signal_link_stats(db, job_ids)
    risk = aggregate_risk_summary(db, job_ids)
    jobs_status = dict(
        db.query(JobInstance.status, func.count(JobInstance.id))
        .filter(JobInstance.plan_run_id == run_id)
        .group_by(JobInstance.status)
        .all()
    )
    lines = [
        f"PlanRun #{run_id} status={pr.status} failure_threshold={pr.failure_threshold}",
        f"job 状态分布={jobs_status}",
        (
            f"signal 链接 linked={link.get('linked', 0)} "
            f"unlinked_fixable={link.get('unlinked_fixable', 0)} "
            f"fixable_link_rate={link.get('fixable_link_rate')}"
        ),
    ]
    lines.append(f"风险摘要={risk if risk else '暂无'}")
    return "\n".join(lines)


def _q_plan_run_log_events(db: Session, args: dict) -> str:
    from backend.services.device_log_event import list_plan_run_device_log_events

    run_id = _clamp_int(args.get("run_id"), "run_id", 0, 1, 10**9)
    if db.get(PlanRun, run_id) is None:
        return f"PlanRun #{run_id} 不存在。"
    limit = _clamp_int(args.get("limit"), "limit", 20, 1, 50)
    state = _opt_str(args.get("state"), "state", 32)
    rows, total = list_plan_run_device_log_events(
        db, run_id, skip=0, limit=limit, state=state,
    )
    if not rows:
        return f"PlanRun #{run_id} 无 device log events（total=0）。"
    lines = [f"PlanRun #{run_id} log events（展示 {len(rows)}/{total}）："]
    for row in rows:
        path = row.remote_path or row.local_path or "-"
        lines.append(
            f" {row.serial} type={row.event_type}/{row.event_subtype} "
            f"state={row.state} path={path}"
        )
    return "\n".join(lines)


def _q_hosts(db: Session, args: dict) -> str:
    limit = _clamp_int(args.get("limit"), "limit", 20, 1, 50)
    q = _filter_status(
        db.query(Host), Host.status,
        _opt_str(args.get("status"), "status", 32), HostStatus, "status",
    )
    keyword = _opt_str(args.get("keyword"), "keyword", 64)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter((Host.id.ilike(like)) | (Host.name.ilike(like)))
    hosts = q.order_by(Host.id).limit(limit).all()
    if not hosts:
        return "没有匹配的主机。"
    return "\n".join(
        f"{h.id} status={h.status} name={h.name}" for h in hosts
    )


def _q_devices(db: Session, args: dict) -> str:
    limit = _clamp_int(args.get("limit"), "limit", 20, 1, 50)
    q = _filter_status(
        db.query(Device), Device.status,
        _opt_str(args.get("status"), "status", 32), DeviceStatus, "status",
    )
    host_id = _opt_str(args.get("host_id"), "host_id", 64)
    if host_id:
        q = q.filter(Device.host_id == host_id)
    devices = q.order_by(Device.id).limit(limit).all()
    if not devices:
        return "没有匹配的设备。"
    return "\n".join(
        f"{d.serial} status={d.status} host={d.host_id} model={d.model}" for d in devices
    )


def _q_audit_logs(db: Session, args: dict) -> str:
    limit = _clamp_int(args.get("limit"), "limit", 20, 1, 50)
    q = db.query(AuditLog)
    action = _opt_str(args.get("action"), "action", 64)
    if action:
        q = q.filter(AuditLog.action.ilike(f"%{action}%"))
    resource_type = _opt_str(args.get("resource_type"), "resource_type", 64)
    if resource_type:
        q = q.filter(AuditLog.resource_type == resource_type)
    logs = q.order_by(AuditLog.id.desc()).limit(limit).all()
    if not logs:
        return "没有匹配的审计记录。"
    return "\n".join(
        f"#{l.id} {l.created_at} {l.action} {l.resource_type}:{l.resource_id}"
        for l in logs
    )


def _search_docs(db: Session, args: dict) -> str:
    """docs/ 轻量检索：查询按空白**分词**，任一词命中即算（按命中词数排序）。

    线上实测（用户会话 8）：LLM 倾向发带空格的长短语，整句子串匹配在中文
    文档上几乎必空，模型会反复重试烧穿 max_turns——分词 + 指引双管齐下。
    """
    query = _opt_str(args.get("query"), "query", 200)
    if not query:
        raise ToolValidationError("query is required")
    limit = _clamp_int(args.get("limit"), "limit", 5, 1, 10)
    tokens = [t.lower() for t in query.split() if len(t.strip()) >= 2] or [query.lower()]
    hits: list[tuple[int, str]] = []
    docs_root = REPO_ROOT / "docs"
    for path in docs_root.rglob("*.md"):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        name_hit = sum(1 for t in tokens if t in path.name.lower())
        best_line: tuple[int, str] | None = None
        for lineno, line in enumerate(content.splitlines(), start=1):
            score = sum(1 for t in tokens if t in line.lower())
            if score and (best_line is None or score > best_line[0]):
                best_line = (score, f"{lineno} {line.strip()[:120]}")
        score = name_hit * 2 + (best_line[0] if best_line else 0)
        if score:
            rel = path.relative_to(REPO_ROOT)
            detail = f"（文件名匹配）{best_line[1]}" if name_hit and best_line else (
                best_line[1] if best_line else "（文件名匹配）"
            )
            hits.append((score, f"{rel}:{detail}"))
    if not hits:
        return (
            f"docs/ 中未检索到「{query}」。"
            "建议：改用 1-2 个更短的英文/编号关键词重试（如「MTBF」「suite_id」「ADR-0030」）。"
        )
    hits.sort(key=lambda x: -x[0])
    return "\n".join(h[1] for h in hits[:limit])


def _q_settings_overview(db: Session, args: dict) -> str:
    db.execute(text("SELECT 1"))
    enabled_rules = (
        db.query(func.count(AlertRule.id)).filter(AlertRule.enabled.is_(True)).scalar()
    )
    return (
        f"platform_name={os.getenv('STP_PLATFORM_NAME', 'Stability Test Platform')}\n"
        f"timezone={os.getenv('STP_TIMEZONE', 'Asia/Shanghai')}\n"
        f"database={db.get_bind().dialect.name}\n"
        f"启用的告警规则数={enabled_rules or 0}"
    )


QUERY_IMPLEMENTATIONS = {
    "get_platform_health": _q_platform_health,
    "query_plan_runs": _q_plan_runs,
    "get_plan_run_detail": _q_plan_run_detail,
    "list_plans": _q_list_plans,
    "get_plan_detail": _q_get_plan_detail,
    "preview_plan_dispatch": _q_preview_plan_dispatch,
    "get_plan_run_jobs": _q_plan_run_jobs,
    "get_plan_run_watcher_summary": _q_plan_run_watcher_summary,
    "get_plan_run_log_events": _q_plan_run_log_events,
    "query_hosts": _q_hosts,
    "query_devices": _q_devices,
    "query_recent_audit_logs": _q_audit_logs,
    "search_docs": _search_docs,
    "get_settings_overview": _q_settings_overview,
}


def execute_query(db: Session, name: str, args: dict) -> str:
    impl = QUERY_IMPLEMENTATIONS.get(name)
    if impl is None:
        raise ToolValidationError(f"unknown query tool: {name}")
    return impl(db, args or {})


# ─────────────────────────── T1/T2 执行计划 ───────────────────────────

def _plan_quality_gate(args: dict) -> RunConsolePlan:
    profile = _enum(args.get("profile"), "profile", ("quick", "pr"))
    timeout = 900 if profile == "quick" else 1800
    return RunConsolePlan(
        cmd=[sys.executable, "scripts/run_gates.py", f"check:{profile}"],
        cwd=REPO_ROOT,
        # check:pr 含 agent-tests（run_gates.py:104）——同款四键覆盖，
        # 防 backend 进程 env 中生产 DATABASE_URL 透传（PR-Agent gate 复评发现）
        env=dict(AGENT_TEST_ENV_OVERRIDE),
        run_key=f"ai-gate:{profile}",
        timeout_seconds=timeout,
    )


def _plan_agent_tests(args: dict) -> RunConsolePlan:
    rel = _opt_str(args.get("file_path"), "file_path", 256)
    target = AGENT_TESTS_DIR
    if rel:
        candidate = (AGENT_TESTS_DIR / rel).resolve()
        # 防穿越：解析后必须仍在 agent 测试目录内
        if not candidate.is_relative_to(AGENT_TESTS_DIR.resolve()):
            raise ToolValidationError("file_path escapes backend/agent/tests/")
        if not candidate.exists():
            raise ToolValidationError(f"file not found under backend/agent/tests/: {rel}")
        target = candidate
    cmd = [sys.executable, "-m", "pytest", str(target), "-q"]
    return RunConsolePlan(
        cmd=cmd,
        cwd=REPO_ROOT,
        env=dict(AGENT_TEST_ENV_OVERRIDE),
        run_key="ai-agent-tests",
        timeout_seconds=900,
    )


def _plan_gov_checks(args: dict) -> RunConsolePlan:
    # v1 仅 surface（pollution 门禁的 git-ls-files|xargs 管道非纯 argv，见实施计划风险表）
    check = _enum(args.get("check"), "check", ("surface",))
    return RunConsolePlan(
        cmd=[sys.executable, "tools/dev/check_governance_surface.py", "--check"],
        cwd=REPO_ROOT,
        run_key=f"ai-gov:{check}",
        timeout_seconds=300,
    )


EXEC_PLANS = {
    "run_quality_gate": _plan_quality_gate,
    "run_agent_tests": _plan_agent_tests,
    "run_gov_checks": _plan_gov_checks,
}


def build_runconsole_plan(name: str, args: dict) -> RunConsolePlan:
    builder = EXEC_PLANS.get(name)
    if builder is None:
        raise ToolValidationError(f"unknown runconsole tool: {name}")
    return builder(args or {})


# ─────────────────────────── 注册表 ───────────────────────────

TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        ToolSpec(
            name="get_platform_health",
            description="获取平台健康概览：数据库连通、主机/设备状态分布、进行中的 PlanRun 数量。",
            parameters=_schema({}),
            tier="T0", kind="query",
        ),
        ToolSpec(
            name="query_plan_runs",
            description="查询执行记录（PlanRun）列表，可按状态/项目/专项过滤。",
            parameters=_schema({
                "status": {"type": "string", "description": "如 RUNNING / SUCCESS / FAILED"},
                "project_id": {"type": "integer"},
                "specialty": {"type": "integer", "description": "专项 id"},
                "limit": {"type": "integer", "description": "1-20，默认 10"},
            }),
            tier="T0", kind="query",
        ),
        ToolSpec(
            name="get_plan_run_detail",
            description="获取某个 PlanRun 的详情：状态、job 状态分布、run_context 摘要。",
            parameters=_schema({"run_id": {"type": "integer"}}, ["run_id"]),
            tier="T0", kind="query",
        ),
        ToolSpec(
            name="list_plans",
            description="列出 Plan（可按项目 key / 专项 key 过滤），含步骤数与归属。",
            parameters=_schema({
                "project_key": {"type": "string"},
                "specialty_key": {"type": "string", "description": "如 GPU / MTBF"},
                "limit": {"type": "integer", "description": "1-20，默认 10"},
            }),
            tier="T0", kind="query",
        ),
        ToolSpec(
            name="get_plan_detail",
            description="获取单个 Plan 的详情与全部 PlanStep（脚本名与版本）。",
            parameters=_schema({"plan_id": {"type": "integer"}}, ["plan_id"]),
            tier="T0", kind="query",
        ),
        ToolSpec(
            name="preview_plan_dispatch",
            description="预检 Plan 派发（只读，不创建 PlanRun）：校验脚本/套件并组装 lifecycle。",
            parameters=_schema({
                "plan_id": {"type": "integer"},
                "device_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "目标设备 id 列表",
                },
            }, ["plan_id", "device_ids"]),
            tier="T0", kind="query",
        ),
        ToolSpec(
            name="get_plan_run_jobs",
            description="列出 PlanRun 下全部 Job：设备 serial、host、状态、manual_action。",
            parameters=_schema({"run_id": {"type": "integer"}}, ["run_id"]),
            tier="T0", kind="query",
        ),
        ToolSpec(
            name="get_plan_run_watcher_summary",
            description="PlanRun watcher 摘要：job 分布、signal 链接健康、风险评级。",
            parameters=_schema({"run_id": {"type": "integer"}}, ["run_id"]),
            tier="T0", kind="query",
        ),
        ToolSpec(
            name="get_plan_run_log_events",
            description="PlanRun 设备日志事件（DLE 权威列表，终态为主）。",
            parameters=_schema({
                "run_id": {"type": "integer"},
                "state": {"type": "string", "description": "按 DLE state 过滤"},
                "limit": {"type": "integer", "description": "1-50，默认 20"},
            }, ["run_id"]),
            tier="T0", kind="query",
        ),
        ToolSpec(
            name="query_hosts",
            description="查询主机列表，可按状态/关键词过滤。",
            parameters=_schema({
                "status": {"type": "string", "description": "ONLINE / OFFLINE / DEGRADED"},
                "keyword": {"type": "string", "description": "匹配主机 id 或名称"},
                "limit": {"type": "integer"},
            }),
            tier="T0", kind="query",
        ),
        ToolSpec(
            name="query_devices",
            description="查询设备列表，可按状态/主机过滤。",
            parameters=_schema({
                "status": {"type": "string", "description": "ONLINE / OFFLINE / BUSY / ERROR"},
                "host_id": {"type": "string"},
                "limit": {"type": "integer"},
            }),
            tier="T0", kind="query",
        ),
        ToolSpec(
            name="query_recent_audit_logs",
            description="检索最近的平台审计日志（操作留痕）。",
            parameters=_schema({
                "action": {"type": "string", "description": "动作关键词（模糊）"},
                "resource_type": {"type": "string"},
                "limit": {"type": "integer", "description": "1-50，默认 20"},
            }),
            tier="T0", kind="query", admin_only=True,
        ),
        ToolSpec(
            name="search_docs",
            description="检索仓库 docs/ 目录的文档（文件名+内容），返回相对路径与行号出处。",
            parameters=_schema({"query": {"type": "string"}}, ["query"]),
            tier="T0", kind="query",
        ),
        ToolSpec(
            name="get_settings_overview",
            description="获取平台设置概览（平台名/时区/数据库类型/告警规则数）。",
            parameters=_schema({}),
            tier="T0", kind="query", admin_only=True,
        ),
        ToolSpec(
            name="run_quality_gate",
            description="运行平台质量门禁（本地 gate 矩阵）。quick=纯静态最快；pr=PR 级全量（含 agent 测试）。",
            parameters=_schema({"profile": {"type": "string", "enum": ["quick", "pr"]}}, ["profile"]),
            tier="T1", kind="runconsole",
        ),
        ToolSpec(
            name="run_agent_tests",
            description="运行 Agent 测试套件（约 30s，不依赖数据库）；可指定 backend/agent/tests/ 下的单个文件。",
            parameters=_schema({
                "file_path": {"type": "string", "description": "相对 backend/agent/tests/ 的文件名"},
            }),
            tier="T1", kind="runconsole",
        ),
        ToolSpec(
            name="run_gov_checks",
            description="运行治理面结构检查（check_governance_surface --check）。",
            parameters=_schema({"check": {"type": "string", "enum": ["surface"]}}, ["check"]),
            tier="T1", kind="runconsole",
        ),
        ToolSpec(
            name="scan_script_catalog",
            description="触发脚本目录扫描（对账 STP_SCRIPT_ROOT 与 script 表；不含 force）。",
            parameters=_schema({}),
            tier="T2", kind="service", admin_only=True,
        ),
        ToolSpec(
            name="test_notification_channel",
            description="向指定通知渠道发送一条测试消息，验证通道连通性。",
            parameters=_schema({"channel_id": {"type": "integer"}}, ["channel_id"]),
            tier="T2", kind="service", whitelistable=True, admin_only=True,
        ),
        ToolSpec(
            name="reload_agent_config",
            description="让指定 ONLINE 主机上的 Agent 重读 .env 并热刷新运行时配置（不重启进程）。",
            parameters=_schema({"host_id": {"type": "string"}}, ["host_id"]),
            tier="T2", kind="service",
        ),
        ToolSpec(
            name="dispatch_plan_run",
            description=(
                "发起 Plan 手动执行（创建 QUEUED PlanRun 并入准入队列）。"
                "建议先调用 preview_plan_dispatch 预检。"
            ),
            parameters=_schema({
                "plan_id": {"type": "integer"},
                "device_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "目标设备 id 列表（须唯一）",
                },
                "note": {"type": "string", "description": "可选备注，写入 run_context.note"},
                "wifi_pool_id": {
                    "type": "integer",
                    "description": "含 connect_wifi/monkey_setup 步骤时可选 WiFi 资源池 id",
                },
            }, ["plan_id", "device_ids"]),
            tier="T2", kind="service",
        ),
    ]
}


def normalize_tool_params(name: str, args: dict | None) -> dict:
    """执行类工具参数归一化（校验失败抛 ToolValidationError）。"""
    if name == "dispatch_plan_run":
        from backend.services.ai_assistant.dispatch import normalize_dispatch_params

        return normalize_dispatch_params(args)
    return dict(args or {})


def describe_tool_action_preview(db: Session, tool_name: str, params: dict) -> str | None:
    if tool_name == "dispatch_plan_run":
        from backend.services.ai_assistant.dispatch import describe_dispatch_preview

        return describe_dispatch_preview(db, params)
    return None


def to_openai_tools(allowed_names: set[str] | None = None) -> list[dict[str, Any]]:
    """生成 OpenAI chat/completions 的 tools 载荷（allowed_names 非空时按其过滤）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in TOOLS.values()
        if allowed_names is None or spec.name in allowed_names
    ]


def allowed_tool_names(is_admin: bool) -> set[str]:
    """按用户角色裁剪工具集：助手工具面不得宽于用户自身 API 权限。

    admin-only 端点的镜像工具（audit / settings 路由均 require_admin）
    仅 admin 会话可见；非 admin 调用此类工具名走「未知工具」分支回填。
    """
    if is_admin:
        return set(TOOLS)
    return {n for n, s in TOOLS.items() if not s.admin_only}
