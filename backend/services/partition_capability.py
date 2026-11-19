"""MTK 扫描轮的能力证据（ADR-0062 v1.2 D3 第 1 条、D5 第 1 条；#3800 §2.1 第 1 项）。

Owner 裁定 6975368043 要求「模式准入」与「轮次下发守卫」共用**同一份可核验的能力证据**。
本模块是 #4043 准入侧的唯一判定入口，证据来源已收口到 #4044/#4049 的
:mod:`backend.services.agent_capabilities`：

- 下发守卫：``backend/services/shadow_scan_rounds.py`` 的预筛与
  ``verify_shadow_scan_dispatch`` 直接调用同模块的 ``evaluate_host_capability`` /
  ``load_host_capability_evidence``（便于探针 monkeypatch 该命名空间）；
- 模式准入：``backend/services/partition_mode_admission.py`` 在首次写入 ``DEDUP_FIRST``
  之前用 :func:`verify_expected_mtk_hosts` 核验该实际分区**全部**预期 MTK host，
  任一未证实即硬阻断（不得写入 ``DEDUP_FIRST``）。

两侧最终都落到 :func:`backend.services.agent_capabilities.evaluate_host_capability`，
因此不会出现「准入放行而下发拒绝」或反之的口径分叉。

能力属于**该 host 当前 Agent 实例**：离线、退役、缺心跳、能力未由当前心跳上报、
缺 ``scan_round_v1``、或实例未绑定，一律按未证实处理（fail-closed）。未证实的原因码
随返回值给出，供调用方记审计线索。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from backend.core.dedup_platform import DEDUP_PLATFORM_MTK
from backend.models.host import Host
from backend.services import agent_capabilities as caps
from backend.services.partition_scope import normalize_platform
from backend.services.plan_run_scan_scope import (
    iter_plan_run_scan_hosts,
    load_expected_scan_platforms,
)

logger = logging.getLogger(__name__)

#: 已核验（唯一表示「能力成立」的原因码；#4044 证据对象在 verified 时 reason=None，
#: 这里归一成稳定字符串，方便准入台账与既有探针断言）。
CAPABILITY_VERIFIED = "verified"
#: 未证实的统称，与影子轮覆盖里既有的 ``reason`` 取值保持一致。
CAPABILITY_UNVERIFIED = "capability_unverified"

# 与 agent_capabilities 对齐的原因码（再导出，供准入/探针按名引用）。
REASON_HOST_MISSING = caps.REASON_HOST_MISSING
REASON_HOST_OFFLINE = caps.REASON_HOST_OFFLINE
REASON_HOST_RETIRED = caps.REASON_HOST_RETIRED
REASON_HEARTBEAT_MISSING = caps.REASON_HEARTBEAT_MISSING
REASON_CAPABILITY_NEVER_REPORTED = caps.REASON_CAPABILITY_NEVER_REPORTED
REASON_CAPABILITY_NOT_CURRENT = caps.REASON_CAPABILITY_NOT_CURRENT
REASON_CAPABILITY_MISSING = caps.REASON_CAPABILITY_MISSING
REASON_INSTANCE_UNBOUND = caps.REASON_INSTANCE_UNBOUND


@dataclass(frozen=True)
class HostCapability:
    """单个 host 当前 Agent 的能力证据结论（#4043 准入侧稳定形状）。"""

    host_id: str
    verified: bool
    reason: str

    @property
    def evidence(self) -> dict[str, Any]:
        return {"host_id": self.host_id, "verified": self.verified, "reason": self.reason}


def desired_scan_round_digest() -> str:
    """兼容保留：#4044 收口后摘要不再参与能力判定。

    仍可能被旧探针取用；返回空串表示「摘要不是本接缝的证据」。新测试应通过
    :func:`host_scan_round_capability` / :func:`verify_expected_mtk_hosts` 读心跳能力。
    """
    return ""


def host_scan_round_capability(host: Any, desired_digest: str = "") -> HostCapability:
    """判定该 host 当前 Agent 是否能执行绑定轮次的扫描。

    ``desired_digest`` 是 #3875 过渡期形参，#4044 后忽略。证据来自
    :func:`agent_capabilities.evaluate_host_capability`。
    """
    del desired_digest  # 摘要列只服务 ADR-0040 展示，不再参与准入。
    now = datetime.now(timezone.utc)
    if host is None:
        evidence = caps.evaluate_host_capability(None, now=now)
        return HostCapability("", False, evidence.reason or REASON_HOST_MISSING)
    host_id = str(getattr(host, "id", "") or "")
    retired = bool(getattr(host, "retired_at", None) is not None)
    # 行可能是 select 投影的 Row/SimpleNamespace；缺列时按「未上报」fail-closed。
    evidence_host: Any = host if isinstance(host, Host) else _coerce_host_row(host)
    evidence = caps.evaluate_host_capability(
        evidence_host, now=now, retired=retired,
    )
    return HostCapability(
        host_id or evidence.host_id,
        evidence.verified,
        CAPABILITY_VERIFIED if evidence.verified else (evidence.reason or CAPABILITY_UNVERIFIED),
    )


def _coerce_host_row(row: Any) -> SimpleNamespace:
    """把测试/守卫传入的轻量 host 投影抬成可被 evaluate 读取的对象。"""
    return SimpleNamespace(
        id=str(getattr(row, "id", "") or ""),
        status=getattr(row, "status", None),
        last_heartbeat=getattr(row, "last_heartbeat", None),
        agent_capabilities=getattr(row, "agent_capabilities", None),
        agent_capabilities_reported_at=getattr(
            row, "agent_capabilities_reported_at", None,
        ),
        agent_capabilities_instance_id=getattr(
            row, "agent_capabilities_instance_id", None,
        ),
        retired_at=getattr(row, "retired_at", None),
    )


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
    """按当前心跳能力证据核验给定（默认=全部预期 MTK 主机）host，逐台给出结论。

    ``host_ids=None`` 时取 :func:`expected_mtk_host_ids`；显式传入让影子下发守卫复用同一判定。
    host 表中不存在的 id 仍然产出一条 ``host_missing`` 结论，不静默跳过。
    ``desired_digest`` 兼容保留，忽略。
    """
    del desired_digest
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
    # 走 #4044 交给 #4043 的全量分区证据接口，再按 wanted 过滤——期望集推导与
    # shadow_scan_rounds / partition_capability_evidence 同源。
    partition = caps.partition_capability_evidence(
        db, plan_run_id=plan_run_id, platform=DEDUP_PLATFORM_MTK,
    )
    by_id = {item.host_id: item for item in partition.hosts}
    now = datetime.now(timezone.utc)
    verdicts: list[HostCapability] = []
    for host_id in wanted:
        evidence = by_id.get(host_id)
        if evidence is None:
            # 调用方显式传入了期望集之外的 id：仍按单机证据 fail-closed。
            evidence = caps.load_host_capability_evidence(db, host_id, now=now)
        verdicts.append(HostCapability(
            host_id,
            evidence.verified,
            CAPABILITY_VERIFIED if evidence.verified else (
                evidence.reason or CAPABILITY_UNVERIFIED
            ),
        ))
    rejected = [item for item in verdicts if not item.verified]
    if rejected:
        logger.info(
            "partition_capability_unverified plan_run=%d platform=%s reasons=%s",
            plan_run_id, DEDUP_PLATFORM_MTK,
            ",".join(f"{item.host_id}:{item.reason}" for item in rejected),
        )
    return verdicts


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
    "REASON_CAPABILITY_MISSING",
    "REASON_CAPABILITY_NEVER_REPORTED",
    "REASON_CAPABILITY_NOT_CURRENT",
    "REASON_HEARTBEAT_MISSING",
    "REASON_HOST_MISSING",
    "REASON_HOST_OFFLINE",
    "REASON_HOST_RETIRED",
    "REASON_INSTANCE_UNBOUND",
    "all_verified",
    "desired_scan_round_digest",
    "expected_mtk_host_ids",
    "host_scan_round_capability",
    "unverified_hosts",
    "verify_expected_mtk_hosts",
]
