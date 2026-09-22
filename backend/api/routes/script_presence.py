"""host 脚本在位矩阵：只读查询 + 单机按需刷新（#2958 第五道闸）。

- ``GET /script-presence/summary``：fleet 汇总（六态计数、缺口 host 数、新鲜度）；
- ``GET /script-presence/hosts/{host_id}``：单机矩阵（族 × 版本 × 态 + detail）；
- ``POST /script-presence/refresh?host_id=…``：**单机**按需重核（一轮 verify_scripts RPC，
  10s 超时内）——全 fleet 刷新由每日 timer 承担（`script_presence_sweep_cron`），
  避免把 48 台 × 10s 的墙钟搬进请求路径。

数据由常设 sweep 落库（`backend/services/script_presence.py`），本层不自己算目标集。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import User, get_current_active_user, require_admin
from backend.api.schemas.script_presence import (
    HostScriptPresenceOut,
    ScriptPresenceCounts,
    ScriptPresenceItem,
    ScriptPresenceSummaryOut,
    ScriptPresenceSweepOut,
)
from backend.core.database import get_db
from backend.core.settings.scheduler import get_scheduler_settings
from backend.models.host import Host
from backend.services import script_presence as presence

router = APIRouter(prefix="/api/v1/script-presence", tags=["script-presence"])

#: 新鲜度阈值：>2× 日周期（24h × 2）即视为账本陈旧（告警侧另有 PromQL 版本）。
STALE_AFTER = timedelta(hours=48)


def _counts_from_rows(rows) -> ScriptPresenceCounts:
    counts = {state: 0 for state in presence.PRESENCE_STATES}
    for r in rows:
        state = getattr(r, "state", None) or (r["state"] if isinstance(r, dict) else None)
        if state in counts:
            counts[state] += 1
    return ScriptPresenceCounts(**counts)


@router.get("/summary", response_model=ApiResponse[ScriptPresenceSummaryOut])
def script_presence_summary(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """fleet 汇总：六态计数 + 缺口 host 数 + 完整 sweep 的新鲜度（min(checked_at)）。"""
    rows = presence.presence_counts_by_host(db)
    counts = ScriptPresenceCounts()
    hosts_with_gap: set[str] = set()
    for r in rows:
        state, n = str(r["state"]), int(r["n"])
        if state in presence.PRESENCE_STATES:
            setattr(counts, state, getattr(counts, state) + n)
        if state in presence.GAP_STATES:
            hosts_with_gap.add(str(r["host_id"]))
    covered, uncovered = _coverage(db)
    fresh_min, fresh_max = presence.sweep_freshness_range(db)
    now = datetime.now(timezone.utc)
    if fresh_min is not None and fresh_min.tzinfo is None:
        fresh_min = fresh_min.replace(tzinfo=timezone.utc)
    if fresh_max is not None and fresh_max.tzinfo is None:
        fresh_max = fresh_max.replace(tzinfo=timezone.utc)
    payload = ScriptPresenceSummaryOut(
        counts=counts,
        hosts_total=len({str(r["host_id"]) for r in rows}),
        hosts_with_gap=len(hosts_with_gap),
        full_versions=covered,
        uncovered_active_versions=uncovered,
        checked_at_min=fresh_min,
        checked_at_max=fresh_max,
        stale=(fresh_min is None) or (now - fresh_min > STALE_AFTER),
    )
    return ok(payload)


@router.get("/hosts/{host_id}", response_model=ApiResponse[HostScriptPresenceOut])
def host_script_presence(
    host_id: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """单台 host 的矩阵（未跑过 sweep 的 host 返回空 items，不是 404——host 存在即可）。"""
    if db.get(Host, host_id) is None:
        raise HTTPException(status_code=404, detail="host not found")
    rows = presence.host_presence_rows(db, host_id)
    payload = HostScriptPresenceOut(
        host_id=host_id,
        checked_at=max((r.checked_at for r in rows), default=None),
        sweep_id=rows[0].sweep_id if rows else "",
        counts=_counts_from_rows(rows),
        items=[
            ScriptPresenceItem(name=r.name, version=r.version, state=r.state, detail=r.detail or "")
            for r in rows if r.state != presence.STATE_N_A
        ],
    )
    return ok(payload)


@router.post("/refresh", response_model=ApiResponse[ScriptPresenceSweepOut])
async def refresh_script_presence(
    host_id: str = Query(..., description="要重核的 host_id（单机）"),
    days: Optional[int] = Query(
        None, ge=1, le=365,
        description="历史可达窗口天数；缺省取 SCRIPT_PRESENCE_HISTORY_DAYS（默认 30）",
    ),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """单机按需重核：一轮 verify_scripts RPC + 该机整行 upsert（bounded by 10s/次）。

    **写端点**：除账本 upsert 外还会触发 agent 侧 RPC，权限面与同域其它写端点一致
    （`require_admin`，#3091——此前误用 `get_current_active_user`，非管理员可经 UI 触发）。
    退役 host 明确 404（#3089：退役机不在账本射程，之前的 200 + 全零是静默空转——
    告警的 Runbook 会指向一个看似成功、实则什么都没做的动作）。
    """
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="host not found")
    if host.retired_at is not None:
        raise HTTPException(
            status_code=404,
            detail="host retired — 不在账本射程（退役机不再核验，无需刷新）",
        )
    effective_days = days or _history_days_default()
    result = await presence.run_sweep(days=effective_days, host_ids=[host_id])
    counts = ScriptPresenceCounts(**{
        state: int(result.get("counts", {}).get(state, 0))
        for state in presence.PRESENCE_STATES
    })
    payload = ScriptPresenceSweepOut(
        sweep_id=result.get("sweep_id", ""), host_id=host_id,
        rows=int(result.get("rows", 0)), counts=counts,
    )
    return ok(payload)


def _history_days_default() -> int:
    """历史可达窗口的权威默认值 = `SCRIPT_PRESENCE_HISTORY_DAYS`（#3089：该旋钮此前是死的）。"""
    return int(get_scheduler_settings().script_presence_history_days)


def _coverage(db: Session) -> tuple[int, int]:
    """`(账本覆盖的版本数, 覆盖不到的 active 版本数)`——一次 facts 加载算两侧（#3111）。

    两侧互补：`|active| = 覆盖 + 未覆盖`。后者只计数不逐条返回：生产实测未覆盖集有
    47 个（`|active|=97`），列出来是噪声，而它要纠正的是「`missing=0` 是否等于全部在位」
    这个读法——计数足以纠正，具体是哪些直接查 `GET /api/v1/scripts`。
    """
    facts = presence.load_facts(db, days=presence.DEFAULT_HISTORY_DAYS)
    return (
        len(presence.build_full_target_set(facts["steps"], facts["scripts"])),
        len(presence.active_unreferenced_versions(facts["steps"], facts["scripts"])),
    )
