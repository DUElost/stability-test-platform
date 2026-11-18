"""MTK 扫描轮的能力证据（ADR-0062 v1.2 D3 第 1 条、D5 第 1 条；#3800 §2.1 第 1 项）。

Owner 裁定 6975368043 要求「模式准入」与「轮次下发守卫」共用**同一份可核验的能力证据**。
本模块是那份证据的唯一判定入口：

- 下发守卫：``backend/services/shadow_scan_rounds.py`` 的 ``_shadow_capability_verified``
  委托到 :func:`host_scan_round_capability`，预筛与实际拒绝下发读同一条规则；
- 模式准入：``backend/services/partition_mode_admission.py`` 在首次写入 ``DEDUP_FIRST``
  之前用 :func:`verify_expected_mtk_hosts` 核验该实际分区**全部**预期 MTK host，
  任一未证实即硬阻断（不得写入 ``DEDUP_FIRST``）。

现行判据是 #3875 v1.23 的过渡预筛（在线 + 心跳 + 制品摘要 matched）。#4044 把终态判据换成
「Agent 当前心跳显式上报的能力」时，只需要改 :func:`host_scan_round_capability` 的证据来源
（以及必要时的 :func:`expected_mtk_host_ids`），两个调用方都不必重复实现规则，也不会出现
「准入放行而下发拒绝」或反之的口径分叉。

能力属于**该 host 当前 Agent 实例**：离线、缺心跳、摘要上报早于当前心跳、或摘要与期望制品
不一致，一律按未证实处理（fail-closed）。未证实的原因码随返回值给出，供调用方记审计线索。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.dedup_platform import DEDUP_PLATFORM_MTK
from backend.models.host import Host
from backend.services.agent_version_info import resolve_agent_code_sync_status
from backend.services.artifact_digest import ARTIFACT_KIND_CODE, compute_desired_artifact_digest
from backend.services.partition_scope import normalize_platform
from backend.services.plan_run_scan_scope import (
    iter_plan_run_scan_hosts,
    load_expected_scan_platforms,
)

logger = logging.getLogger(__name__)

#: 已核验（唯一表示「能力成立」的原因码）。
CAPABILITY_VERIFIED = "verified"
#: 未证实的统称，与影子轮覆盖里既有的 ``reason`` 取值保持一致。
CAPABILITY_UNVERIFIED = "capability_unverified"

REASON_HOST_MISSING = "host_missing"
REASON_HOST_OFFLINE = "host_offline"
REASON_HEARTBEAT_MISSING = "heartbeat_missing"
REASON_DIGEST_REPORT_MISSING = "digest_report_missing"
REASON_DIGEST_REPORT_STALE = "digest_report_stale"
REASON_DIGEST_MISMATCH = "digest_mismatch"


@dataclass(frozen=True)
class HostCapability:
    """单个 host 当前 Agent 的能力证据结论。"""

    host_id: str
    verified: bool
    reason: str

    @property
    def evidence(self) -> dict[str, Any]:
        return {"host_id": self.host_id, "verified": self.verified, "reason": self.reason}


def desired_scan_round_digest() -> str:
    """当前期望的 Agent 制品摘要；取不到时返回空串（判据随即按未证实处理）。"""
    try:
        return compute_desired_artifact_digest(kind=ARTIFACT_KIND_CODE)
    except OSError:
        logger.exception("scan_round_desired_digest_unavailable")
        return ""


def host_scan_round_capability(host: Any, desired_digest: str) -> HostCapability:
    """判定该 host 当前 Agent 是否能执行绑定轮次的扫描。

    过渡判据（#3875 v1.23，#4044 收口前）：在线、有心跳、制品摘要**上报时间不早于当前
    心跳**、且摘要与期望制品一致。旧摘要不是当前心跳的证明，故时间关系必须核对（6704562784）。
    原因码只用于审计与告警文案，不改变结论方向：任何非 :data:`CAPABILITY_VERIFIED` 都是
    「未证实」。
    """
    host_id = str(getattr(host, "id", "") or "")
    if host is None:
        return HostCapability(host_id, False, REASON_HOST_MISSING)
    if getattr(host, "status", None) != "ONLINE":
        return HostCapability(host_id, False, REASON_HOST_OFFLINE)
    last_heartbeat = getattr(host, "last_heartbeat", None)
    if last_heartbeat is None:
        return HostCapability(host_id, False, REASON_HEARTBEAT_MISSING)
    reported_at = getattr(host, "agent_artifact_digest_reported_at", None)
    if reported_at is None:
        return HostCapability(host_id, False, REASON_DIGEST_REPORT_MISSING)
    if reported_at < last_heartbeat:
        return HostCapability(host_id, False, REASON_DIGEST_REPORT_STALE)
    if resolve_agent_code_sync_status(
        agent_artifact_digest=getattr(host, "agent_artifact_digest", None),
        desired_artifact_digest=desired_digest,
    ) != "matched":
        return HostCapability(host_id, False, REASON_DIGEST_MISMATCH)
    return HostCapability(host_id, True, CAPABILITY_VERIFIED)


def expected_mtk_host_ids(db: Session, plan_run_id: int) -> list[str]:
    """该实际分区的全部**预期** MTK host（与 D5 第 1 条的期望集同源）。

    与 ``shadow_scan_rounds._expected_units`` / ``scan_completeness`` 共用派生入口
    （``iter_plan_run_scan_hosts`` + ``load_expected_scan_platforms``）：准入核验的 host 集合
    必须等于轮次下发与覆盖判定使用的 host 集合，否则会放行一台从来不下发的机器。
    离线 host **不**从期望集里删掉——它的能力证据按未证实处理，正是硬阻断要的语义。
    """
    host_rows = iter_plan_run_scan_hosts(db, plan_run_id)
    expected = load_expected_scan_platforms(
        db, plan_run_id, [host for host, _status, _retired in host_rows],
    )
    return sorted(
        str(host)
        for host, platforms in expected.items()
        if normalize_platform(DEDUP_PLATFORM_MTK) in {
            normalize_platform(value) for value in platforms
        }
    )


def verify_expected_mtk_hosts(
    db: Session,
    plan_run_id: int,
    *,
    host_ids: Optional[Iterable[str]] = None,
    desired_digest: Optional[str] = None,
) -> list[HostCapability]:
    """按当前证据核验给定（默认=全部预期 MTK 主机）host，逐台给出结论。

    ``host_ids=None`` 时取 :func:`expected_mtk_host_ids`；显式传入让影子下发守卫复用同一判定。
    host 表中不存在的 id 仍然产出一条 ``host_missing`` 结论，不静默跳过。
    """
    wanted = sorted(
        {
            str(host)
            for host in (
                list(host_ids) if host_ids is not None
                else expected_mtk_host_ids(db, plan_run_id)
            )
            if host
        }
    )
    if not wanted:
        return []
    digest = desired_scan_round_digest() if desired_digest is None else desired_digest
    rows = {
        str(row.id): row
        for row in db.execute(
            select(
                Host.id, Host.status, Host.last_heartbeat,
                Host.agent_artifact_digest, Host.agent_artifact_digest_reported_at,
            ).where(Host.id.in_(wanted))
        ).all()
    }
    verdicts = [_verdict(host_id, rows.get(host_id), digest) for host_id in wanted]
    rejected = [item for item in verdicts if not item.verified]
    if rejected:
        logger.info(
            "partition_capability_unverified plan_run=%d platform=%s reasons=%s",
            plan_run_id, DEDUP_PLATFORM_MTK,
            ",".join(f"{item.host_id}:{item.reason}" for item in rejected),
        )
    return verdicts


def _verdict(host_id: str, row: Any, desired_digest: str) -> HostCapability:
    if row is None:
        return HostCapability(host_id, False, REASON_HOST_MISSING)
    return host_scan_round_capability(
        SimpleNamespace(
            id=host_id,
            status=row.status,
            last_heartbeat=row.last_heartbeat,
            agent_artifact_digest=row.agent_artifact_digest,
            agent_artifact_digest_reported_at=row.agent_artifact_digest_reported_at,
        ),
        desired_digest,
    )


def unverified_hosts(verdicts: Iterable[HostCapability]) -> list[str]:
    return sorted(item.host_id for item in verdicts if not item.verified)


def all_verified(verdicts: Iterable[HostCapability]) -> bool:
    """空集合返回 False：没有可核验的 host 就等于能力不可证明，不得当作放行。"""
    items = list(verdicts)
    return bool(items) and not unverified_hosts(items)


__all__ = [
    "CAPABILITY_UNVERIFIED",
    "CAPABILITY_VERIFIED",
    "HostCapability",
    "all_verified",
    "desired_scan_round_digest",
    "expected_mtk_host_ids",
    "host_scan_round_capability",
    "unverified_hosts",
    "verify_expected_mtk_hosts",
]
