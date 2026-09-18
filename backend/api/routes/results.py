# -*- coding: utf-8 -*-
"""
Results summary API — aggregated test run statistics for the dashboard.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from backend.api.routes.auth import get_current_active_user, User
from backend.core.database import get_db
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.project import Specialty, TestProject
from backend.core.metrics import risk_jobs_by_level
from backend.services.log_observation import (
    aggregate_risk_levels_by_job,
    aggregate_risk_summary,
)

router = APIRouter(prefix="/api/v1/results", tags=["results"])


# ---------- Response schemas ----------

class RunsByStatus(BaseModel):
    finished: int = 0
    failed: int = 0
    canceled: int = 0
    running: int = 0
    total: int = 0


#: #2631：`specialty`（专项）未设置时的**显式**桶名。
#: 旧实现在这里回落到 `Plan.name`——那正是「一个概念两套口径」的成因：
#: 图上写「按测试类型统计」，轴上却是用户自由输入、只增不减的 Plan 代号。
UNSPECIFIED_SPECIALTY_LABEL = "未设专项"

#: 未设专项恒排最后（字典表 sort_order 是整数，取一个不可能胜出的哨兵值）
_UNSPECIFIED_SPECIALTY_SORT = 10 ** 9


class TestTypeStat(BaseModel):
    """「测试类型」统计行。

    `type` = `specialty.display_name`（ADR-0029 D6：专项＝测试类型维度标签，有界字典），
    不是 Plan 名；Plan 未设专项时归入 `UNSPECIFIED_SPECIALTY_LABEL`（#2631）。
    """

    type: str
    finished: int = 0
    failed: int = 0
    total: int = 0


class RiskDistribution(BaseModel):
    """对外风险分布桶（ADR-0045 D2：值域就是判定级别，不再翻成 HIGH/MEDIUM/LOW）。

    `unknown` = 该窗口内没有任何异常事件的 job —— **不是**"低风险"，也不是"查过没风险"，
    这个区别是 #2365 覆盖率观测的对象（D4：不许把 UNKNOWN 压进 B）。
    """

    s: int = 0
    a: int = 0
    b: int = 0
    unknown: int = 0


class RecentRun(BaseModel):
    run_id: int
    task_name: str
    task_type: str
    status: str
    # ADR-0045 D2：对外值域 = 判定级别本身（S/A/B/UNKNOWN），后端不做展示层翻译。
    risk_level: str = "UNKNOWN"
    # ADR-0029：归属项目（plan_run 快照，F2 口径）
    project_key: Optional[str] = None
    duration_seconds: Optional[float] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class ResultsSummary(BaseModel):
    runs_by_status: RunsByStatus
    test_type_stats: List[TestTypeStat]
    risk_distribution: RiskDistribution
    recent_runs: List[RecentRun]


class RiskTrendBucket(BaseModel):
    """单日 S/A/B/UNKNOWN 风险计数（项目维度，v2.5 D13）。

    ADR-0045 D2：第四态由 `NONE` 并入 `UNKNOWN` —— 二者语义相同「没有可判定的事件」。
    D4 同时守住方向：**不得**反过来把零事件算进 `B`（"B=有事件但非 S/A"，零不是低风险）。
    """

    date: str  # YYYY-MM-DD（run 起始日）
    S: int = 0
    A: int = 0
    B: int = 0
    UNKNOWN: int = 0
    runs: int = 0


class RiskTrendOut(BaseModel):
    """项目级风险趋势——按天的 S/A/B 计数。

    数据源：DLE 权威聚合（aggregate_risk_summary，与 /results 同口径），
    run 级风险按 plan_run.started_at 归日。plan_run.project_id 快照过滤
    （P0-1 起新 Run 有真实项目归属）。
    """

    project_key: Optional[str] = None
    days: int
    buckets: List[RiskTrendBucket] = []
    # v2.5 D13：近窗口汇总（详情页「最近跑得怎么样」KPI + S 级清单）
    total_runs: int = 0
    success_runs: int = 0
    success_rate: float = 0.0
    s_runs: List[dict] = []


# ---------- Helpers ----------

_JOB_STATUS_TO_RUN_STATUS = {
    "PENDING": "QUEUED",
    "RUNNING": "RUNNING",
    "COMPLETED": "FINISHED",
    "FAILED": "FAILED",
    "ABORTED": "CANCELED",
    "UNKNOWN": "RUNNING",
}


def _normalize_job_status(job_status: Any) -> str:
    raw = str(job_status or "").upper()
    return _JOB_STATUS_TO_RUN_STATUS.get(raw, raw or "RUNNING")


# ADR-0045 D1/D2：**这里不再有任何映射表**。判定级别（S/A/B）由 log_observation 的活链
# 单源产出，API 出的是**同一级别**；"高/中/低"是前端文案（D3），不是对外词表。
# 曾经这里有 `_RISK_LABEL_BY_LEVEL = {"S": "HIGH", ...}` 与桶名 `high/medium/low`，
# 于是同一个概念在四个面有四种形状（报告 DTO 出 S/A/B、列表出 HIGH、分布出 high、
# 趋势又用 NONE），徽标查不到键就恒显"未知"——即 #2494/#2418 那一族。
_RISK_LEVELS = ("S", "A", "B")


def _risk_level_or_unknown(level: Optional[str]) -> str:
    """判定级别 → 对外值；无判定依据 → UNKNOWN。

    **不压成 B**：没有采到异常 ≠ 查过且没风险，这个区别正是 #2365 覆盖率观测的对象。
    """
    value = (level or "").strip().upper()
    return value if value in _RISK_LEVELS else "UNKNOWN"


# ---------- Endpoint ----------

@router.get("/summary", response_model=ResultsSummary)
def get_results_summary(
    limit: int = Query(20, ge=1, le=100, description="Number of recent runs"),
    project_key: Optional[str] = Query(None, description="ADR-0029: filter by project key"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> ResultsSummary:
    """Return aggregated test run statistics."""

    def _empty_summary() -> ResultsSummary:
        return ResultsSummary(
            runs_by_status=RunsByStatus(),
            test_type_stats=[],
            risk_distribution=RiskDistribution(),
            recent_runs=[],
        )

    def _is_missing_orchestration_table(exc: Exception) -> bool:
        message = str(exc).lower()
        table_hit = any(
            t in message for t in (
                "job_instance",
                "step_trace",
                "plan_run",
                "plan",
            )
        )
        return (
            table_hit and (
                "does not exist" in message
                or "undefinedtable" in message
                or "no such table" in message
                or "不存在" in message
            )
        )

    try:

        # ADR-0029 P2：project_key 可选过滤（D9 挂起，缺省 = 全量）。
        # 过滤统一经 PlanRun.project_id——Plan.project_id 是可变当前归属，
        # 历史 Run 的归属以 plan_run 快照为准（D5 快照语义；M-b 已冻结）。
        # JobInstance 无 project 列，经 plan_run_id 一次 join 即可。
        target_project_id: Optional[int] = None
        if project_key:
            project = (
                db.query(TestProject)
                .filter(TestProject.project_key == project_key)
                .first()
            )
            if project is None:
                raise HTTPException(status_code=404, detail="project not found")
            target_project_id = project.id

        def _scope_by_project(query):
            """按 project 过滤（若指定）；调用方须已 join PlanRun。"""
            if target_project_id is not None:
                query = query.filter(PlanRun.project_id == target_project_id)
            return query

        # --- runs_by_status (新链路：JobInstance) ---
        status_query = db.query(JobInstance.status, func.count(JobInstance.id))
        if target_project_id is not None:
            status_query = status_query.join(PlanRun, JobInstance.plan_run_id == PlanRun.id)
        status_query = _scope_by_project(status_query)
        status_counts = status_query.group_by(JobInstance.status).all()
        runs_by_status = RunsByStatus()
        for raw_status, count in status_counts:
            normalized = _normalize_job_status(raw_status)
            cnt = int(count or 0)
            runs_by_status.total += cnt
            if normalized == "FINISHED":
                runs_by_status.finished += cnt
            elif normalized == "FAILED":
                runs_by_status.failed += cnt
            elif normalized == "CANCELED":
                runs_by_status.canceled += cnt
            else:
                # QUEUED/RUNNING/UNKNOWN 都视作运行态
                runs_by_status.running += cnt

        # --- test_type_stats (按 specialty/专项 聚合 = 「测试类型」的权威定义) ---
        # ADR-0029 D6 与 new-specialty-onboarding-runbook.md 都把「测试类型」指派给
        # specialty（有界字典）；旧实现按 Plan.name 聚合并自证于注释与变量名
        # (`template_name`)，于是同一专项被拆成 N 条、每新增一个 Plan 图表就恶化一次（#2631）。
        type_query = db.query(
            Specialty.id,
            Specialty.display_name,
            Specialty.sort_order,
            JobInstance.status,
            func.count(JobInstance.id),
        ).join(Plan, JobInstance.plan_id == Plan.id)
        # 未设专项的 Plan 也必须出现在图上（LEFT JOIN + 显式桶），不得回落到 Plan 名
        type_query = type_query.outerjoin(Specialty, Plan.specialty_id == Specialty.id)
        # 按专项分组需保留 Plan join；project 过滤额外 join PlanRun（快照语义）
        if target_project_id is not None:
            type_query = type_query.join(PlanRun, JobInstance.plan_run_id == PlanRun.id)
        type_query = _scope_by_project(type_query)
        type_rows = type_query.group_by(
            Specialty.id,
            Specialty.display_name,
            Specialty.sort_order,
            JobInstance.status,
        ).all()
        # 键取**标签**（轴的 identity 就是图上那格文字）：`display_name` 无唯一约束，
        # 两个 key 撞同名时按 id 聚合会画出两条一模一样的图例，按标签聚合才是对的读数。
        type_agg: Dict[str, Dict[str, Any]] = {}
        for spec_id, display_name, sort_order, raw_status, cnt in type_rows:
            label = (str(display_name) if display_name
                     else UNSPECIFIED_SPECIALTY_LABEL)
            slot = type_agg.setdefault(label, {
                "sort": (_UNSPECIFIED_SPECIALTY_SORT if spec_id is None
                         else int(sort_order or 0)),
                "finished": 0,
                "failed": 0,
                "total": 0,
            })
            count_i = int(cnt or 0)
            slot["total"] += count_i
            normalized = _normalize_job_status(raw_status)
            if normalized == "FINISHED":
                slot["finished"] += count_i
            elif normalized == "FAILED":
                slot["failed"] += count_i
        # 出图顺序 = 字典表 sort_order（与 /orchestration/plans 那排专项 chip 同序）
        test_type_stats = [
            TestTypeStat(
                type=label,
                finished=slot["finished"],
                failed=slot["failed"],
                total=slot["total"],
            )
            for label, slot in sorted(type_agg.items(),
                                      key=lambda kv: (kv[1]["sort"], kv[0]))
        ]

        # --- recent_runs (ADR-0020: Plan-based) ---
        # Plan join 供 name 展示；outerjoin PlanRun+TestProject 取归属 key
        # （快照语义）；project 过滤走 _scope_by_project（filter 复用已 join 表）。
        recent_query = (
            db.query(JobInstance, Plan.name, TestProject.project_key)
            .join(Plan, JobInstance.plan_id == Plan.id)
            .outerjoin(PlanRun, JobInstance.plan_run_id == PlanRun.id)
            .outerjoin(TestProject, PlanRun.project_id == TestProject.id)
            .order_by(JobInstance.id.desc())
        )
        recent_query = _scope_by_project(recent_query)
        recent_rows = recent_query.limit(limit).all()
        recent_job_ids = [job.id for job, _plan_name, _project_key in recent_rows]
        # #2365：风险级别改由**活链**判定（log_observation：DLE 权威 + 未链接信号），
        # 不再读 RUN_COMPLETE 快照里 log_summary 的 `risk=` —— 自 ADR-0025 起 Agent
        # 侧 `log_summary` 恒为 None（`pipeline_runner` 硬编码），生产实测 15118 条
        # 快照里 0 条带 `risk=`，所以这一列此前恒为 UNKNOWN。
        recent_risk = aggregate_risk_levels_by_job(db, recent_job_ids)

        recent_runs: List[RecentRun] = []
        for job, plan_name, project_key in recent_rows:
            risk = _risk_level_or_unknown(recent_risk.get(job.id))
            duration = None
            if job.started_at and job.ended_at:
                duration = (job.ended_at - job.started_at).total_seconds()
            plan_name_norm = str(plan_name or "unknown")
            recent_runs.append(
                RecentRun(
                    run_id=job.id,
                    task_name=plan_name_norm,
                    task_type=plan_name_norm,
                    status=_normalize_job_status(job.status),
                    risk_level=risk,
                    project_key=project_key,
                    duration_seconds=duration,
                    started_at=job.started_at,
                    finished_at=job.ended_at,
                )
            )

        # --- risk_distribution ---
        total_jobs_query = db.query(func.count(JobInstance.id))
        if target_project_id is not None:
            total_jobs_query = total_jobs_query.join(PlanRun, JobInstance.plan_run_id == PlanRun.id)
        total_jobs_query = _scope_by_project(total_jobs_query)
        total_jobs = int(total_jobs_query.scalar() or 0)
        risk_counts = {"s": 0, "a": 0, "b": 0, "unknown": 0}
        if total_jobs > 0:
            scoped_job_query = db.query(JobInstance.id)
            if target_project_id is not None:
                scoped_job_query = scoped_job_query.join(PlanRun, JobInstance.plan_run_id == PlanRun.id)
            scoped_job_query = _scope_by_project(scoped_job_query)
            scoped_job_ids = [int(job_id) for (job_id,) in scoped_job_query.all()]
            levels = aggregate_risk_levels_by_job(db, scoped_job_ids)
            for job_id in scoped_job_ids:
                # 桶名就是级别本身（小写只是 JSON 字段风格，不再换词）
                level = _risk_level_or_unknown(levels.get(job_id, ""))
                risk_counts[level.lower()] += 1

        # #2365 重开后的两处收口（覆盖率指标本身与活链接线保留，这里只补它的口径）：
        #
        # 1. **写 gauge 必须在 `if total_jobs > 0` 之外**。原先四桶的 .set() 全在块内，
        #    「job 被清空」时整段跳过 → gauge 停在上一轮的非零值，读起来像「还有风险
        #    分布」。这与本单的目标（让「无判定依据」与「判据坏了」可区分）正好相反，
        #    也违反仓库自定纪律（`cron_scheduler.py` 里「清空后 gauge 必须回到 0」）。
        # 2. **只有全局口径写 gauge**。带 `project_key` 的请求算的是作用域分布，而该
        #    gauge 只有 `level` 标签，一写就把全局值覆盖了——两个语义共用一个序列。
        #    刻意**不加 `scope` 标签**：`project_key` 是用户数据，拿它当标签等于把基数
        #    交给项目登记簿；而覆盖率要观测的本来就是全局分布。
        if target_project_id is None:
            for bucket, count in risk_counts.items():
                risk_jobs_by_level.labels(level=bucket).set(count)

        runs_by_status = RunsByStatus(
            finished=int(runs_by_status.finished),
            failed=int(runs_by_status.failed),
            canceled=int(runs_by_status.canceled),
            running=int(runs_by_status.running),
            total=int(runs_by_status.total),
        )

        return ResultsSummary(
            runs_by_status=runs_by_status,
            test_type_stats=test_type_stats,
            risk_distribution=RiskDistribution(**risk_counts),
            recent_runs=recent_runs,
        )
    except ProgrammingError as exc:
        if not _is_missing_orchestration_table(exc):
            raise
        db.rollback()
        return _empty_summary()


@router.get("/risk-trend", response_model=RiskTrendOut)
def get_risk_trend(
    project_key: Optional[str] = Query(
        None, description="ADR-0029 P2: filter by project key（缺省 = 全量）"
    ),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """项目级风险趋势：按天的 S/A/B 计数（run 级 DLE 权威聚合）。

    过滤统一经 PlanRun.project_id 快照（与 /summary 同口径，P0-1 后新 Run
    有真实项目归属）。run 的风险 = 其全部 job 的 aggregate_risk_summary，
    按 run 起始日归桶。project_key 未知 → 404（与 /summary 同语义）。
    """
    target_project_id: Optional[int] = None
    if project_key:
        project = (
            db.query(TestProject)
            .filter(TestProject.project_key == project_key)
            .first()
        )
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        target_project_id = project.id

    since = datetime.now(timezone.utc) - timedelta(days=days)
    run_query = db.query(PlanRun).filter(
        PlanRun.started_at >= since,
        # 终态才有风险判定；枚举为 SUCCESS/PARTIAL_SUCCESS/FAILED
        PlanRun.status.in_(("SUCCESS", "PARTIAL_SUCCESS", "FAILED")),
    )
    if target_project_id is not None:
        run_query = run_query.filter(PlanRun.project_id == target_project_id)

    buckets: Dict[str, Dict[str, int]] = {}
    total_runs = 0
    success_runs = 0
    s_runs: list[dict] = []
    for run in run_query.all():
        job_ids = [
            jid for (jid,) in db.query(JobInstance.id)
            .filter(JobInstance.plan_run_id == run.id)
            .all()
        ]
        summary = aggregate_risk_summary(db, job_ids) or {}
        # v2.5 D13 + ADR-0045 D2/D4：零事件走第四态 UNKNOWN（原 NONE，二者语义相同），
        # 并且**不**落进 B（「B：其余非零」口径；零不是低风险）
        level = (
            str(summary.get("risk_level"))
            if summary
            else "UNKNOWN"
        )
        total_runs += 1
        if run.status == "SUCCESS":
            success_runs += 1
        if level == "S":
            s_runs.append({
                "run_id": run.id,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "status": run.status,
            })
        if run.started_at is None:
            continue
        day = run.started_at.date().isoformat()
        bucket = buckets.setdefault(day, {"S": 0, "A": 0, "B": 0, "UNKNOWN": 0, "runs": 0})
        if level in ("S", "A", "B", "UNKNOWN"):
            bucket[level] += 1
        bucket["runs"] += 1

    return RiskTrendOut(
        project_key=project_key,
        days=days,
        buckets=[RiskTrendBucket(date=d, **buckets[d]) for d in sorted(buckets)],
        total_runs=total_runs,
        success_runs=success_runs,
        success_rate=round(success_runs / total_runs, 2) if total_runs else 0.0,
        s_runs=s_runs,
    )
