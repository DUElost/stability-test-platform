"""Agent protocol + code revision helpers for host display and hot-update audit."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm.attributes import flag_modified

from backend.core.audit import record_audit
from backend.core.metrics import hot_update_outcome_total
from backend.services.artifact_digest import (
    ARTIFACT_KIND_CODE,
    compute_desired_artifact_digest,
)
from backend.services.host_updater import get_agent_code_version

logger = logging.getLogger(__name__)

AgentCodeSyncStatus = Literal["unknown", "matched", "drift", "pending"]


def _self_computed_desired_digest() -> str:
    """未显式传 desired 时的自算兜底（#2320）：**IO 失败只降级，不抛**。

    调用面是主机详情/列表等**展示**路径，而 desired digest 要对输入集全量 stat + 逐文件
    读；控制面工作树在部署/checkout 期间会变动（stat 与 open 之间存在竞态窗口）。
    读不出摘要 → 返回空串 → `resolve_agent_code_sync_status` 判 `unknown`，这正是
    该函数已有的「无路可退」表达法。只吃 `OSError`（IO 形态），编程错误继续冒泡。
    """
    try:
        return compute_desired_artifact_digest(kind=ARTIFACT_KIND_CODE) or ""
    except OSError as exc:
        logger.warning(
            "agent_code_desired_digest_failed err=%s "
            "hint=代码同步判据降级 unknown（控制面工作树正在变动？）",
            exc,
        )
        return ""


def resolve_agent_code_sync_status(
    *,
    agent_artifact_digest: str | None,
    desired_artifact_digest: str | None,
) -> AgentCodeSyncStatus:
    """ADR-0040 v1.1：面向运维的动作判据**唯一** = code artifact digest。

    revision（`agent_code_revision` / `expected_code_revision`）**不参与判等**，
    只作溯源文本：`get_agent_code_version()` 取的是仓库 HEAD，任何不动
    `backend/agent/**` 的提交都会让 revision 前进而 digest 不变（#2057）——
    按 revision 判等会让全 fleet 永久假 drift，且无自愈通道。

    - 未上报 digest（#1907 前部署 / 新装未心跳）→ `unknown`（**不是 drift**）：
      运维动作为「等一次心跳」或「首次 `--force` 迁移」，禁止渲染成需更新；
    - `pending` 在 digest 判据下不再产生（枚举保留以兼容既有前端与历史数据）。
    """
    desired = (desired_artifact_digest or "").strip()
    if not desired:
        return "unknown"
    current = (agent_artifact_digest or "").strip()
    if not current:
        return "unknown"
    return "matched" if current == desired else "drift"


def build_host_version_view(
    extra: dict | None,
    *,
    agent_artifact_digest: str | None = None,
    desired_artifact_digest: str | None = None,
) -> dict:
    """Derive top-level HostOut version fields from host.extra.

    `desired_artifact_digest` 由调用方传入以便列表场景只算一次（desired 现算 +
    进程缓存，但缓存键仍需 stat 输入集）。#2320 起**三态分明**：

    - ``None``（未传）→ 本函数自算，且自算失败只降级不抛；
    - ``""``（调用方**显式**说「算不出」，如列表页的降级路径）→ 判据 ``unknown``；
      旧写法 `(desired or "").strip() or 自算` 会把空串当成「没传」而**回头自算**，
      于是列表页的降级意图被下游覆盖成 ``drift``/``matched``——一个假判据；
    - 非空 → 用它比对。
    """
    data = extra if isinstance(extra, dict) else {}
    expected = get_agent_code_version() or None
    if desired_artifact_digest is None:
        desired = _self_computed_desired_digest()
    else:
        desired = desired_artifact_digest.strip()

    def _clean(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text or None

    reported = _clean(data.get("agent_code_revision"))
    deployed = _clean(data.get("agent_code_deployed"))
    protocol = _clean(data.get("agent_version"))
    deployed_at = _clean(data.get("agent_code_deployed_at"))

    return {
        "agent_protocol_version": protocol,
        "agent_code_revision": reported,
        "expected_code_revision": expected,
        "agent_code_deployed": deployed,
        "agent_code_deployed_at": deployed_at,
        "agent_code_sync_status": resolve_agent_code_sync_status(
            agent_artifact_digest=agent_artifact_digest,
            desired_artifact_digest=desired,
        ),
    }


def record_agent_code_deployed(host, code_version: str) -> None:
    """Persist last successful hot-update revision on host.extra."""
    revision = (code_version or "").strip()
    if not revision:
        return
    extra = dict(host.extra or {})
    extra["agent_code_deployed"] = revision
    extra["agent_code_deployed_at"] = datetime.now(timezone.utc).isoformat()
    host.extra = extra
    flag_modified(host, "extra")


def finalize_hot_update_outcome(
    db,
    host,
    result: dict,
    *,
    entry: str,
    code_version: str = "",
    user_id: str | None = None,
    username: str | None = None,
) -> None:
    """ADR-0040 D5/D6：三入口共用的热更新结果记录通道（审计 + deployed_at + 指标）。

    - 审计 ``hot_update_result``：含 converged/reason/digest/per-phase 计时，
      no-op 同样留痕（不再出现「跑了但什么都没记」，§1.2 事实 4 的分叉闭合）；
    - ``agent_code_deployed(_at)`` 仅在**内容实际变更并收敛成功**时刷新——
      no-op 不刷新（D2 语义修订，no-op 由 digest 匹配状态表达）；
    - Prometheus ``hot_update_outcome_total{entry,outcome}``。
    """
    ok = bool(result.get("ok"))
    converged = bool(result.get("converged"))
    if ok and converged:
        outcome = "converged"
    elif ok:
        outcome = "deployed"
    else:
        outcome = "failed"

    record_audit(
        db,
        action="hot_update_result",
        resource_type="host",
        resource_id=None,
        details={
            "host_id": host.id,
            "ip": getattr(host, "ip", None),
            "entry": entry,
            "ok": ok,
            "converged": converged,
            "reason": result.get("reason", ""),
            "artifact_digest": result.get("artifact_digest", ""),
            "phases": result.get("phases", {}),
            "deps_refreshed": bool(result.get("deps_refreshed")),
            "env_keys_synced": result.get("env_keys_synced", []),
            "env_paths_missing": result.get("env_paths_missing", {}),
            "code_version": result.get("code_version", code_version),
            "priv_mode": result.get("priv_mode", ""),
            "duration_ms": result.get("duration_ms"),
            "message": result.get("message", ""),
        },
        user_id=user_id,
        username=username,
    )

    if ok and not converged:
        record_agent_code_deployed(host, code_version)

    try:
        hot_update_outcome_total.labels(entry=entry, outcome=outcome).inc()
    except Exception:  # 指标面永不阻断业务路径
        pass
    db.commit()
