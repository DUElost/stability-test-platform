"""PlanRun 读侧共享辅助（#1520 timeline / events / 路由壳）。

时间格式化与终态集合原先散落在 ``plan_runs`` 路由与各读侧 service；
本模块收成单一真源。``require_plan_run`` 供多条读端点共用 404 门禁。
**#3350（ADR-0023 D2）**：``snapshot_step_scripts`` 把 ``plan_snapshot.steps``
折成脚本身份查表——timeline / events / devices 三条读端点共用同一份派生规则。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.services.errors import NotFound
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.enums import PlanRunStatus
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun

LIVE_PATROL_HEARTBEAT_WINDOW = timedelta(seconds=180)
TERMINAL_PR_STATUSES = {
    PlanRunStatus.SUCCESS.value,
    PlanRunStatus.PARTIAL_SUCCESS.value,
    PlanRunStatus.FAILED.value,
}


def snapshot_step_scripts(
    plan_snapshot: dict | None,
) -> dict[tuple[str, str], tuple[str, str | None]]:
    """``plan_snapshot.steps`` → ``{(stage, step_key): (script_name, script_version)}``。

    ADR-0023 D2 的取值规则：**从快照查表派生**（不新增查询、不回落当前 Plan 定义——
    排查要回答的是「当时跑的是哪个版本」，现行 PlanStep 已被改指别的版本时那个答案
    会骗人）。快照缺失/结构不合法/字段为空 → 查不到即 None，不抛错（旧 PlanRun
    常见：迁移前快照只有部分键；D2 验收要求这类 PlanRun 全字段为 None）。
    """
    snapshot = plan_snapshot if isinstance(plan_snapshot, dict) else {}
    index: dict[tuple[str, str], tuple[str, str | None]] = {}
    for step in snapshot.get("steps") or []:
        if not isinstance(step, dict):
            continue
        stage = str(step.get("stage") or "")
        step_key = str(step.get("step_key") or "")
        if not stage or not step_key:
            continue
        script_name = str(step.get("script_name") or "").strip()
        if not script_name:
            continue
        version = str(step.get("script_version") or "").strip()
        index[(stage, step_key)] = (script_name, version or None)
    return index


def resolve_step_script(
    index: dict[tuple[str, str], tuple[str, str | None]],
    *,
    stage: str | None,
    step_key: str | None,
) -> tuple[str | None, str | None]:
    """把 ``(stage, step_key)`` 解析成 ``(script_name, script_version)``；查不到 → (None, None)。

    devices 端点的 ``current_step`` 来自 ``JobInstance.current_patrol_step``（巡检心跳
    写入，语义上属 patrol 段）——故调用方传 ``stage="patrol"``；events 端点按
    ``StepTrace.stage`` 原样传。
    """
    if not stage or not step_key:
        return None, None
    hit = index.get((stage, step_key))
    if hit is None:
        return None, None
    return hit


def aware(ts: datetime | None) -> datetime | None:
    """Normalise naive datetimes to UTC（SQLite 测试库无 tzinfo）。"""
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat()


def duration_seconds(start, end) -> float | None:
    if start is None:
        return None
    if end is None:
        end = datetime.now(timezone.utc)
    try:
        return max(0.0, (aware(end) - aware(start)).total_seconds())
    except TypeError:
        return None


def min_aware_dt(*values: datetime | None) -> datetime | None:
    present = [aware(v) for v in values if v is not None]
    return min(present) if present else None


def max_aware_dt(*values: datetime | None) -> datetime | None:
    present = [aware(v) for v in values if v is not None]
    return max(present) if present else None


def resolve_plan_name(db: Session, pr: PlanRun) -> str | None:
    """run 归属 Plan 的名字——**detail 与 summary 必须同源**（#2623）。

    权威源是 `Plan.name`（按 `plan_id` 现查），**不是** `plan_snapshot["name"]`：
    快照不跟随改名，两处各取一份就会造出「同一字段两个口径」。detail 一直用的是
    前者，本函数只是把那段查询提出来复用，不改变任何既有行为。

    `plan_id` 可空（历史/异常行）→ 返回 `None`；字段按可空下发，前端不假设必有。
    """
    if pr.plan_id is None:
        return None
    return db.execute(
        select(Plan.name).where(Plan.id == pr.plan_id)
    ).scalar_one_or_none()


def require_plan_run(db: Session, run_id: int) -> PlanRun:
    """Load PlanRun or raise 404 — shared by plan_runs read endpoints."""
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise NotFound("plan run not found")
    return pr
