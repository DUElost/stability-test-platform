"""audit_logs 分层保留期裁剪（#2741 / ADR-0049）。

APScheduler sync job（threadpool 执行、``SessionLocal`` 独立会话），与
``revoked_token_cleanup`` 同族。与 PlanRun retention
（``cron_scheduler.run_retention_cleanup``）的差别：audit_logs 无 FK 子树、
无引用闭包，删除谓词纯 ``timestamp + action 分层``，结构上不存在 #1827
那类「引用闭包保留整批 ⇒ 饿死后续清理」的形态——按 id 升序批量删即可，
每轮自然推进。

分层保留期（owner 裁决 2026-09-19，ADR-0049 D1）：

- security 180d：认证成败、账号/凭据管理、主机密钥更换——安全事件链；
- session 30d：例行会话心跳（``refresh``/``login`` 成功/``logout``），
  量最大、取证价值最低（#2694：生产占 3%、dev 占 62%，短保留在两种
  环境下都成立）；
- business 90d：**默认桶**——未显式归入上面两层的 action 一律落这里。

自免环（ADR-0049 D5）：裁剪自身**仅当确有删除时**每 tick 写一条汇总审计
（``audit_retention_pruned``，落 business 默认桶）——该行不豁免于裁剪
谓词，90 天后同样被清，不形成「审计行阻止自身裁剪」的自持环。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.core.audit import record_audit
from backend.core.database import SessionLocal
from backend.core.metrics import audit_retention_pruned_total
from backend.core.settings.scheduler import get_scheduler_settings
from backend.models.audit import AuditLog

logger = logging.getLogger(__name__)

#: 会话类：例行会话心跳（ADR-0049 D3：同表 + 分层保留期消化，不拆表不折叠）。
SESSION_ACTIONS: frozenset[str] = frozenset({
    "refresh",
    "login",
    "logout",
})

#: 安全类：安全事件链的最小事实集。**新增安全相关 action 必须显式加进来**
#: （ADR-0049 D2）——默认桶是 business 90d，静默漏登会把安全事件降到 90d。
SECURITY_ACTIONS: frozenset[str] = frozenset({
    "login_failed",
    "login_locked",
    "change_password",
    "change_password_failed",
    "initial_admin_created",
    "token_issued",
    "token_failed",
    "token_locked",
    "refresh_rejected",
    "user_created",
    "user_updated",
    "user_deleted",
    "user_active_toggled",
    "host_key_replaced",
})

#: 汇总审计自身的 action。不在上面两个显式集里 ⇒ 落 business 默认桶
#: ⇒ 90d 后自清，不豁免（自免环的关键）。
SUMMARY_ACTION = "audit_retention_pruned"

#: business 默认桶的**显式登记**（#3017 / ADR-0049 D2）。
#: 裁剪谓词仍用 NOT IN（不枚举 business）——本集合**只**给守卫用：新 action
#: 要么进 SESSION/SECURITY，要么在这里点名「故意留 90d」，否则 AST 守卫判红。
#: 漏登安全类会静默降到 90d；漏登业务类则无人确认「90d 是本意」。
BUSINESS_ACTIONS_ALLOWLIST: frozenset[str] = frozenset({
    "admission_shrink",
    "agent_config_reload",
    "ai_assistant_abort_plan_run",
    "ai_assistant_action_approve",
    "ai_assistant_action_auto_approved",
    "ai_assistant_action_cancel",
    "ai_assistant_action_executing",
    "ai_assistant_action_finished",
    "ai_assistant_action_proposed",
    "ai_assistant_action_reject",
    "ai_assistant_config_update",
    "ai_assistant_dispatch_plan_run",
    "ai_assistant_session_delete",
    "ai_assistant_trigger_plan_run_archive",
    "abort_jobs_for_host_update",
    "abort_plan_run",
    "apply_project_model",
    "archive_project",
    "audit_retention_pruned",  # SUMMARY_ACTION；字面量/常量两条路径都覆盖
    "bulk_assign_project_models",
    "create",
    "create_project",
    "create_version",
    "deactivate",
    "dead_letter_replay",
    "delete",
    "export",
    "host_retired_log_tail",
    "hot_update",
    "hot_update_result",
    "import",
    "install_agent",
    "install_agent_cancel",
    "install_agent_request",
    "jira_run_cancel",
    "job_batch_terminalized",
    "job_running_timeout",
    "job_terminalization_failed",
    "job_terminalized",
    "patrol_manual_exit",
    "patrol_manual_retry",
    "patrol_stall_detected",
    "plan_admission_failed",
    "plan_admission_requeued",
    "plan_created",
    "plan_deleted",
    "plan_dispatch_failed",
    "plan_dispatch_gate_failed",
    "plan_dispatch_retry_requested",
    "plan_run_archive_scan_trigger",
    "plan_run_scan_trigger",
    "plan_updated",
    "promote_seed_project",
    "register",
    "remove_project_model",
    "rename_project",
    "retire_host",
    "scan",
    "scan_rebaseline",
    "stale_job_completion_rejected",
    "terminal_payload_conflict",
    "unarchive_project",
    "unretire_host",
    "update",
    "update_project",
    "update_tags",
    "update_watcher_admin_state",
    "upgrade_gate_acquire",
    "upgrade_gate_release",
})

#: 显式层（business 是 NOT IN 兜底，不枚举——对新 action 封闭，ADR-0049 D2）。
_EXPLICIT_LAYERS: tuple[tuple[str, frozenset[str]], ...] = (
    ("session", SESSION_ACTIONS),
    ("security", SECURITY_ACTIONS),
)


def _prune_layer(
    db: Session, *, actions: frozenset[str] | None, cutoff: datetime, limit: int
) -> int:
    """删单层一批到期行（按 id 升序），返回删除数。

    ``actions=None`` 表示 business 默认桶：用 NOT IN（显式集的并）而不是
    枚举「business 有哪些」——后者会随词表演化漏项，前者对新 action 封闭。
    """
    stmt = select(AuditLog.id).where(AuditLog.timestamp < cutoff)
    if actions is None:
        stmt = stmt.where(
            AuditLog.action.notin_(SESSION_ACTIONS | SECURITY_ACTIONS)
        )
    else:
        stmt = stmt.where(AuditLog.action.in_(actions))
    ids = db.execute(stmt.order_by(AuditLog.id).limit(limit)).scalars().all()
    if not ids:
        return 0
    return db.execute(delete(AuditLog).where(AuditLog.id.in_(ids))).rowcount


def audit_log_cleanup_job() -> dict[str, int]:
    """单 tick：三层各删至多 ``audit_log_retention_batch_size`` 行。

    单 tick 工作量上界 = 3 × batch（可预期，与 ``plan_run_retention_batch_size``
    同一杠杆语义；这里没有行锁窗口顾虑——audit_logs 没有并发 updater，
    DELETE 走主键）。

    ``AUDIT_LOG_*_RETENTION_DAYS=0`` 与 PlanRun 家族同义：cutoff=now，
    该层全量到期（清库/排障场景）。彻底停用裁剪走
    ``AUDIT_LOG_RETENTION_INTERVAL_SECONDS=0``（app_scheduler 不注册本作业）。
    """
    settings = get_scheduler_settings()
    batch = settings.audit_log_retention_batch_size
    days = {
        "session": settings.audit_log_session_retention_days,
        "business": settings.audit_log_business_retention_days,
        "security": settings.audit_log_security_retention_days,
    }
    now = datetime.now(timezone.utc)
    cutoffs = {name: now - timedelta(days=d) for name, d in days.items()}
    pruned: dict[str, int] = {"session": 0, "business": 0, "security": 0}

    session = SessionLocal()
    try:
        with session.begin():
            for name, actions in _EXPLICIT_LAYERS:
                pruned[name] = _prune_layer(
                    session, actions=actions, cutoff=cutoffs[name], limit=batch
                )
            pruned["business"] = _prune_layer(
                session, actions=None, cutoff=cutoffs["business"], limit=batch
            )
        # 出了 `with session.begin()` 就是**已提交的既成事实**：下面任何失败都不能
        # 再把返回值改写成「没删」。这是 #2789 记的那处对账矛盾的根——旧实现让
        # 汇总审计的写入失败落到同一个外层 except，于是「行已删、指标已自增、
        # 返回值与日志却说零」。
        committed = dict(pruned)
        for name, count in committed.items():
            if count:
                audit_retention_pruned_total.labels(layer=name).inc(count)
        summary_audit_failed = False
        if sum(committed.values()):
            # ADR-0049 D5：汇总审计写在裁剪事务之后，失败只丢这一条审计，
            # **不回滚**已完成的裁剪。所以它的异常必须就地接住，且要有自己的名字。
            try:
                record_audit(
                    session,
                    action=SUMMARY_ACTION,
                    resource_type="audit_log",
                    details={"pruned": dict(committed), "days": days},
                )
                session.commit()
            except Exception:
                session.rollback()   # 只回滚这一笔审计；删除在前一个事务里已提交
                summary_audit_failed = True
                logger.exception("audit_retention_summary_audit_failed")
        logger.info(
            "audit_retention_pruned session=%d business=%d security=%d "
            "summary_audit_failed=%s",
            committed["session"], committed["business"], committed["security"],
            int(summary_audit_failed),
        )
        return committed
    except Exception:
        # 走到这里说明**裁剪事务本身**没提交成功（已回滚）——此时报零才是诚实的。
        # 已提交的删除永远不会走到这条分支。
        logger.exception("audit_log_cleanup_failed")
        return {"session": 0, "business": 0, "security": 0}
    finally:
        session.close()
