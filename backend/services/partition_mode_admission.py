"""首次分区模式写入（ADR-0062 v1.2 D3 第 1 条、D13；#3800 §2.1 第 1 项；#4043）。

「运行模式」的作用域是**实际 PlanRun × 平台**，而决定它的地方只有本模块的
:func:`fixate_partition_mode`。控制面必须在该分区**第一个受运行模式影响的动作之前**调用它
（D3 第 1 条）：判定结果一旦落库就永久固定，之后的所有入口只读这条记录分流（D13 第 2 条），
不再各自问「开关现在是开还是关」。

判据（Owner 裁定 6975368043 + #3800 §2.1 第 1 项）：

1. 只有 MTK 分区可以是「先去重」（D13 第 3 条）。其它平台**不写记录**，由消费方按 D13 第 2 条
   读作「现行」——顺带避免给展锐分区凭空造一条 CURRENT 记录。
2. 已有记录 → 原样返回，永不改写。已固定 DEDUP_FIRST 的分区在运行中失去 Agent 能力时同样
   不回退（记缺口与补充轮恢复属 #4044）。
3. 阶段二开关默认关闭；关闭开关只阻止**之后**的入场（D13 第 5 条），不影响已固定分区的回执、
   读取、下载与回收。
4. Owner 白名单按**实际 PlanRun id** 指定，**不按 Plan 扩展**：同一 Plan 的兄弟 run 各自判定。
5. 白名单命中后仍要核验该分区**全部**预期 MTK host 的能力证据（与影子下发守卫同源，见
   :mod:`backend.services.partition_capability`）。任一未证实 → **硬阻断**：不写
   ``DEDUP_FIRST``。能力是瞬时事实（一次心跳抖动、一次 Agent 回退），不能据此把 run 永久锁死在
   旧链上，因此阻断本身**不写记录**；期间消费方按 D13 第 2 条走现行，旧链若真的发生副作用，
   下一次入场会按第 6 条固化为 CURRENT。
6. 写 ``DEDUP_FIRST`` 前必须确认旧链还没在本分区留下事实（在途旧 run 沿用 D13）。判定信号是
   旧链**专有**的库内痕迹：本分区（MTK 或归属不可确认）的 scan/merge 报表产物，以及旧链写的
   ``run_context`` 段 ``archive`` / ``upload_mark`` / ``merge_platforms`` / ``extract``。发现
   任一信号 → 固化为 CURRENT 并记原因，绝不两套链混跑。「归属不可确认」按 MTK 计
   （fail-closed）：路径段缺失时宁可拒绝入场，也不把可能的旧链 MTK 产物当作与本分区无关；
   展锐产物（路径含 ``/unisoc/``）不算本分区副作用。

**落库范围**（D13 第 2 条：不得因开关当前值的回读改写没有记录的存量分区）：

- 获批（白名单命中）的分区：无论最终是 ``DEDUP_FIRST`` 还是被拒的 ``CURRENT``，都在第一个受
  影响动作处落库——被拒原因（旧链已有副作用、阶段一未开）必须留下决定，之后该 run 继续在或
  离开白名单都不会翻转它。
- 未获批 / 开关关闭的分区：**只有**影子轮入口（``record_current_fallback=True``）写 CURRENT。
  这不是「按开关值改写历史」，而是主干既有行为：#3875 的影子 settled 出口把
  ``shadow_settled_at`` 记在 ``partition_mode`` 行上，取消这次写入会让已观察分区失去影子终态、
  每个检查周期重复处理。其余入口（scan/upload/merge/extract）在这种情况下不落库，消费方继续
  按 D13 第 2 条解释为现行。

并发与幂等：首写在 ``plan_run`` 行锁（与影子轮同口径：先 PlanRun，再按 round_no 锁
``scan_round``）内完成，落库走 ``uq_partition_mode_run_platform`` 的 ON CONFLICT DO NOTHING 再
回读。同一分区并发入场只留下**一条**记录，任务重试与进程重建读到同一决定。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.database import SessionLocal
from backend.core.dedup_platform import DEDUP_PLATFORM_MTK, DEDUP_PLATFORM_UNISOC
from backend.core.settings.scheduler import SchedulerSettings, get_scheduler_settings
from backend.models.plan_run import PlanRun
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services import issue_fact_store as facts
from backend.services.partition_capability import (
    CAPABILITY_UNVERIFIED,
    expected_mtk_host_ids,
    unverified_hosts,
    verify_expected_mtk_hosts,
)
from backend.services.partition_scope import normalize_platform

logger = logging.getLogger(__name__)

# ── 入口标识（写进日志，供「哪个入口先固化了模式」可核对）──
ENTRY_SHADOW_SWEEP = "shadow_scan_rounds._advance_run"
ENTRY_SHADOW_PREPARE = "shadow_scan_rounds.prepare_shadow_scan"
ENTRY_SCAN_TASK = "saq_tasks.scan_task"
ENTRY_UPLOAD_TASK = "saq_tasks.upload_task"
ENTRY_MERGE = "dedup_scan.merge"
ENTRY_EXTRACT = "dedup_extract.extract"
ENTRY_MERGE_ROUTE = "dedup.trigger_merge_route"

# ── 决定原因码 ──
REASON_ALREADY_RECORDED = "already_recorded"
REASON_NON_MTK_PARTITION = "non_mtk_partition"
REASON_PLAN_RUN_MISSING = "plan_run_missing"
REASON_SWITCH_DISABLED = "dedup_first_switch_disabled"
REASON_NOT_WHITELISTED = "not_in_owner_whitelist"
REASON_PHASE_ONE_DISABLED = "shadow_phase_disabled"
REASON_NO_EXPECTED_HOSTS = "no_expected_mtk_hosts"
REASON_CAPABILITY_UNVERIFIED = CAPABILITY_UNVERIFIED
REASON_OLD_CHAIN_EFFECTS = "old_chain_side_effects"
REASON_OWNER_ADMITTED = "owner_admitted"

#: 旧链（先上传后去重）独有的 run_context 段：新链只写事实表与证据区，不写这四段。
_OLD_CHAIN_CONTEXT_SECTIONS = ("archive", "upload_mark", "merge_platforms", "extract")
#: 旧链按 PlanRun 产物登记的报表类型（与 D10 的既有报表入口同源）。
_OLD_CHAIN_REPORT_ARTIFACT_TYPES = ("scan_result_xls", "merge_result_xls")


@dataclass(frozen=True)
class PartitionModeDecision:
    """一次准入判定。``mode=None`` 表示**本次不固化**（条件瞬时成立，留待下一次入场）。"""

    mode: Optional[str]
    reason: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def defers(self) -> bool:
        return self.mode is None


def _mtk_report_artifact_count(db: Session, plan_run_id: int) -> int:
    """本分区可确认或无法确认归属的旧链报表产物数（展锐分区不计入）。"""
    rows = db.execute(
        select(PlanRunArtifact.storage_uri).where(
            PlanRunArtifact.plan_run_id == plan_run_id,
            PlanRunArtifact.artifact_type.in_(_OLD_CHAIN_REPORT_ARTIFACT_TYPES),
        )
    ).scalars()
    count = 0
    for uri in rows:
        norm = (uri or "").replace("\\", "/").lower()
        if f"/{DEDUP_PLATFORM_UNISOC}/" in norm:
            continue
        count += 1
    return count


def _old_chain_context_sections(db: Session, plan_run_id: int) -> list[str]:
    run = db.get(PlanRun, plan_run_id)
    context = getattr(run, "run_context", None) if run is not None else None
    if not isinstance(context, dict):
        return []
    return [section for section in _OLD_CHAIN_CONTEXT_SECTIONS if section in context]


def old_chain_effects(db: Session, plan_run_id: int) -> dict[str, Any]:
    """旧链在本分区已留下的库内痕迹；空 dict 表示还没有任何副作用发生过。"""
    artifacts = _mtk_report_artifact_count(db, plan_run_id)
    sections = _old_chain_context_sections(db, plan_run_id)
    effects: dict[str, Any] = {}
    if artifacts:
        effects["mtk_report_artifacts"] = artifacts
    if sections:
        effects["run_context_sections"] = sections
    return effects


def _recorded_mode(db: Session, plan_run_id: int, platform: str):
    """分区已记录的模式（只读既有事实，不做任何写入）。"""
    return facts.get_partition_mode(db, plan_run_id=plan_run_id, platform=platform)


def decide_partition_mode(
    db: Session,
    plan_run_id: int,
    *,
    platform: str = DEDUP_PLATFORM_MTK,
    settings: Optional[SchedulerSettings] = None,
    record_current_fallback: bool = False,
) -> PartitionModeDecision:
    """**只判定、不落库**：给出该分区应有的模式与原因。

    ``record_current_fallback=True`` 只用于影子轮入口（保留 #3875 settled 记账所需的 CURRENT
    行）；其余入口对未获批分区不写记录。已固定分区永远返回它自己的模式，因此本函数同时是
    「开关翻转后仍按既有模式处理回执 / 读取 / 下载 / 回收」这条判据的读接口。
    """
    scope = normalize_platform(platform)
    recorded = _recorded_mode(db, plan_run_id, scope)
    if recorded is not None:
        return PartitionModeDecision(recorded.mode, REASON_ALREADY_RECORDED, {"platform": scope})
    if scope != DEDUP_PLATFORM_MTK:
        return PartitionModeDecision(None, REASON_NON_MTK_PARTITION, {"platform": scope})
    if db.get(PlanRun, plan_run_id) is None:
        return PartitionModeDecision(None, REASON_PLAN_RUN_MISSING, {"platform": scope})

    conf = settings or get_scheduler_settings()
    if not conf.stp_dedup_first_enabled:
        return PartitionModeDecision(
            facts.PARTITION_MODE_CURRENT if record_current_fallback else None,
            REASON_SWITCH_DISABLED,
            {"platform": scope},
        )
    if int(plan_run_id) not in conf.dedup_first_plan_run_whitelist:
        return PartitionModeDecision(
            facts.PARTITION_MODE_CURRENT if record_current_fallback else None,
            REASON_NOT_WHITELISTED,
            {"platform": scope, "whitelist": sorted(conf.dedup_first_plan_run_whitelist)},
        )
    if not conf.stp_scan_round_shadow_enabled:
        # 获批分区在阶段一未开时也必须落库为 CURRENT：这是 Owner 点名 run 的拒绝记录。
        return PartitionModeDecision(
            facts.PARTITION_MODE_CURRENT, REASON_PHASE_ONE_DISABLED, {"platform": scope},
        )

    hosts = expected_mtk_host_ids(db, plan_run_id)
    if not hosts:
        return PartitionModeDecision(None, REASON_NO_EXPECTED_HOSTS, {"platform": scope})
    verdicts = verify_expected_mtk_hosts(db, plan_run_id, host_ids=hosts)
    rejected = unverified_hosts(verdicts)
    if rejected:
        return PartitionModeDecision(
            None,
            REASON_CAPABILITY_UNVERIFIED,
            {
                "platform": scope,
                "unverified_hosts": rejected,
                "reasons": {item.host_id: item.reason for item in verdicts if not item.verified},
                "expected_hosts": hosts,
            },
        )

    effects = old_chain_effects(db, plan_run_id)
    if effects:
        return PartitionModeDecision(
            facts.PARTITION_MODE_CURRENT, REASON_OLD_CHAIN_EFFECTS,
            {"platform": scope, **effects},
        )
    return PartitionModeDecision(
        facts.PARTITION_MODE_DEDUP_FIRST, REASON_OWNER_ADMITTED,
        {"platform": scope, "expected_hosts": hosts},
    )


def fixate_partition_mode(
    db: Session,
    plan_run_id: int,
    *,
    entrypoint: str,
    platform: str = DEDUP_PLATFORM_MTK,
    settings: Optional[SchedulerSettings] = None,
    record_current_fallback: bool = False,
) -> Optional[str]:
    """在分区第一个受模式影响的动作前固化模式，返回该分区**已提交**的模式。

    ``None`` 表示本次没有可固化的决定（非 MTK、run 不存在、能力未证实、期望集为空，或未获批
    且调用方不需要 CURRENT 兜底记录）：调用方继续按「无模式记录」处理，消费侧读作现行。
    """
    scope = normalize_platform(platform)
    locked = db.execute(
        select(PlanRun).where(PlanRun.id == plan_run_id).with_for_update()
    ).scalar_one_or_none()
    if locked is None:
        _log_decision(plan_run_id, scope, None, REASON_PLAN_RUN_MISSING, entrypoint, {})
        return None

    decision = decide_partition_mode(
        db, plan_run_id, platform=scope, settings=settings,
        record_current_fallback=record_current_fallback,
    )
    if decision.mode is None:
        _log_decision(plan_run_id, scope, None, decision.reason, entrypoint, decision.detail)
        return None

    row, created = facts.insert_partition_mode_if_absent(
        db, plan_run_id=plan_run_id, platform=scope, mode=decision.mode,
    )
    _log_decision(
        plan_run_id, scope, row.mode,
        decision.reason if created else REASON_ALREADY_RECORDED,
        entrypoint, decision.detail,
    )
    return row.mode


def fixate_partition_mode_at_entry(
    plan_run_id: int,
    *,
    entrypoint: str,
    platform: str = DEDUP_PLATFORM_MTK,
    settings: Optional[SchedulerSettings] = None,
    record_current_fallback: bool = False,
) -> Optional[str]:
    """自开会话的入口版：任务 / 服务入口在动作前把决定**独立提交**。

    单独一个短事务并立即 commit，保证「模式已提交」先于分流；不把决定绑在调用方的事务里，
    调用方后续失败也不会让已提交的模式回滚成未固化状态。
    """
    db = SessionLocal()
    try:
        mode = fixate_partition_mode(
            db, plan_run_id, platform=platform, entrypoint=entrypoint, settings=settings,
            record_current_fallback=record_current_fallback,
        )
        db.commit()
        return mode
    except Exception:
        db.rollback()
        logger.exception(
            "partition_mode_admission_failed plan_run=%s platform=%s entrypoint=%s",
            plan_run_id, normalize_platform(platform), entrypoint,
        )
        raise
    finally:
        db.close()


def _log_decision(
    plan_run_id: int,
    platform: str,
    mode: Optional[str],
    reason: str,
    entrypoint: str,
    detail: Mapping[str, Any],
) -> None:
    """决定必须可从日志回溯：哪个入口、什么模式、因为什么（或为什么没写）。"""
    unverified = detail.get("unverified_hosts") if isinstance(detail, Mapping) else None
    if reason == REASON_CAPABILITY_UNVERIFIED:
        logger.warning(
            "dedup_first_entry_blocked plan_run=%s platform=%s reason=%s entrypoint=%s "
            "unverified_hosts=%s",
            plan_run_id, platform, reason, entrypoint, ",".join(unverified or []),
        )
        return
    if reason == REASON_OLD_CHAIN_EFFECTS:
        logger.warning(
            "dedup_first_admission_denied plan_run=%s platform=%s reason=%s entrypoint=%s "
            "mtk_report_artifacts=%s run_context_sections=%s",
            plan_run_id, platform, reason, entrypoint,
            detail.get("mtk_report_artifacts", 0),
            ",".join(detail.get("run_context_sections", []) or []),
        )
        return
    logger.info(
        "partition_mode_fixated plan_run=%s platform=%s mode=%s reason=%s entrypoint=%s",
        plan_run_id, platform, mode or "-", reason, entrypoint,
    )


__all__ = [
    "ENTRY_EXTRACT",
    "ENTRY_MERGE",
    "ENTRY_SCAN_TASK",
    "ENTRY_SHADOW_PREPARE",
    "ENTRY_SHADOW_SWEEP",
    "ENTRY_UPLOAD_TASK",
    "PartitionModeDecision",
    "REASON_ALREADY_RECORDED",
    "REASON_CAPABILITY_UNVERIFIED",
    "REASON_NOT_WHITELISTED",
    "REASON_OLD_CHAIN_EFFECTS",
    "REASON_OWNER_ADMITTED",
    "REASON_PHASE_ONE_DISABLED",
    "REASON_PLAN_RUN_MISSING",
    "REASON_SWITCH_DISABLED",
    "decide_partition_mode",
    "fixate_partition_mode",
    "fixate_partition_mode_at_entry",
    "old_chain_effects",
]
