"""终态事实层——设备日志的问题、发生与交付事实（ADR-0056 D1/D2，ADR-0062，B8 G1）。

事实行只追加、不改写；对 ``plan_run`` / ``job_instance`` / ``device_log_event``
只存引用 id，不建级联外键，运行行被保留策略删除后事实行仍完整。

所有表都只经 ``backend/services/issue_fact_store.py`` 读写，其他模块不得直接写表。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from backend.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IssueSignature(Base):
    """问题签名——同一类发生在跨轮次上的稳定身份（ADR-0056 D2）。

    唯一键 ``(platform, event_type, normalized_key)``；``signature_raw`` 保留
    厂商原文，``normalized_key`` 是适配器产出的归一化键。
    """

    __tablename__ = "issue_signature"

    id = Column(Integer, primary_key=True)
    signature_raw = Column(Text, nullable=False)
    normalized_key = Column(String(512), nullable=False)
    platform = Column(String(16), nullable=False)
    event_type = Column(String(32), nullable=False)
    issue_keys = Column(JSONB, nullable=False, default=list)
    first_seen_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=False)

    occurrences = relationship("IssueOccurrence", back_populates="signature")

    __table_args__ = (
        UniqueConstraint(
            "platform",
            "event_type",
            "normalized_key",
            name="uq_issue_signature_platform_event_normalized",
        ),
        Index("idx_issue_signature_normalized_key", "normalized_key"),
    )


class IssueOccurrence(Base):
    """一次发生——同一台设备上的同一次报错（ADR-0056 D2，ADR-0062 I1）。

    ``occurrence_key`` 唯一（I1）；为空表示身份不可证明（fail-open），
    此时允许多行，由后续处理标注来源未证实。``first_seen_plan_run_id`` 与
    ``first_seen_round_id`` 记录首见位置，运行行删除后保留。
    """

    __tablename__ = "issue_occurrence"

    id = Column(Integer, primary_key=True)
    signature_id = Column(
        Integer, ForeignKey("issue_signature.id"), nullable=False,
    )
    occurrence_key = Column(String(64), nullable=True)
    serial = Column(String(128), nullable=False)
    build_version = Column(String(256), nullable=True)
    project_key = Column(String(64), nullable=True)
    specialty_key = Column(String(64), nullable=True)
    risk_level = Column(String(8), nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    first_seen_source = Column(String(16), nullable=False)
    first_seen_plan_run_id = Column(Integer, nullable=True)
    first_seen_round_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    signature = relationship("IssueSignature", back_populates="occurrences")
    event_links = relationship("IssueOccurrenceEvent", back_populates="occurrence")

    __table_args__ = (
        UniqueConstraint("occurrence_key", name="uq_issue_occurrence_key"),
        Index("idx_issue_occurrence_signature", "signature_id"),
        Index("idx_issue_occurrence_serial", "serial"),
        Index("idx_issue_occurrence_plan_run", "first_seen_plan_run_id"),
    )


class IssueOccurrenceEvent(Base):
    """发生 ↔ DLE 观察的映射（ADR-0062 §3 第 1 条）。

    一个发生对应多条观察（重扫、baseline、溢出副本）；一个事件目录可以承载多个
    问题，因此映射带 ``signature_id``。唯一键 ``(occurrence_id, dle_id, signature_id)``。
    """

    __tablename__ = "issue_occurrence_event"

    id = Column(Integer, primary_key=True)
    occurrence_id = Column(
        Integer, ForeignKey("issue_occurrence.id"), nullable=False,
    )
    dle_id = Column(UUID(as_uuid=True), nullable=False)
    signature_id = Column(
        Integer, ForeignKey("issue_signature.id"), nullable=False,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    occurrence = relationship("IssueOccurrence", back_populates="event_links")

    __table_args__ = (
        UniqueConstraint(
            "occurrence_id",
            "dle_id",
            "signature_id",
            name="uq_issue_occurrence_event_mapping",
        ),
        Index("idx_issue_occurrence_event_dle", "dle_id"),
    )


class PartitionMode(Base):
    """实际 PlanRun × 平台的运行模式（ADR-0062 D13 第 1–2 条）。

    写入后不可改；没有模式记录的存量分区按「现行」解释。
    """

    __tablename__ = "partition_mode"

    id = Column(Integer, primary_key=True)
    plan_run_id = Column(Integer, nullable=False)
    platform = Column(String(16), nullable=False)
    mode = Column(String(16), nullable=False)
    written_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    # #3875：影子分区的不可撤销出口；独立于不可变的运行模式。
    shadow_settled_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "plan_run_id", "platform", name="uq_partition_mode_run_platform",
        ),
    )


class ScanRound(Base):
    """轮次记录（ADR-0062 D3 第 2 条）。

    轮次号、种类（周期轮 / 最后一轮 / 补充轮）、期望集、输入截止点与状态。
    最后一轮与补充轮必须持久化，供调度器按检查周期重新入队。
    """

    __tablename__ = "scan_round"

    id = Column(Integer, primary_key=True)
    plan_run_id = Column(Integer, nullable=False)
    platform = Column(String(16), nullable=False)
    round_no = Column(Integer, nullable=False)
    kind = Column(String(16), nullable=False)
    expected_set = Column(JSONB, nullable=False, default=list)
    input_cutoff_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(16), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    merge_status = Column(String(16), nullable=True)
    merge_summary = Column(JSONB, nullable=True)
    merge_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    merge_next_retry_at = Column(DateTime(timezone=True), nullable=True)
    merged_at = Column(DateTime(timezone=True), nullable=True)
    # 进入终态一次加 1；reset 不清零（#3929 补偿判据，与墙钟无关）。
    merge_generation = Column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint(
            "plan_run_id",
            "platform",
            "round_no",
            name="uq_scan_round_run_platform_round",
        ),
        Index("idx_scan_round_run_platform_status", "plan_run_id", "platform", "status"),
    )


class RepresentativeSelection(Base):
    """代表选择（ADR-0062 D7 第 1–2、6 条）。

    范围是实际 PlanRun × 平台 × 问题；选择状态与证据完整度是两个独立字段。
    进入「已选」后 ``dle_id`` 不可改。
    """

    __tablename__ = "representative_selection"

    id = Column(Integer, primary_key=True)
    plan_run_id = Column(Integer, nullable=False)
    platform = Column(String(16), nullable=False)
    signature_id = Column(Integer, nullable=False)
    dle_id = Column(UUID(as_uuid=True), nullable=True)
    selection_status = Column(String(16), nullable=False)
    evidence_status = Column(String(16), nullable=True)
    selected_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint(
            "plan_run_id",
            "platform",
            "signature_id",
            name="uq_representative_selection_run_platform_signature",
        ),
        Index("idx_representative_selection_signature", "signature_id"),
    )


class DeliveryVersion(Base):
    """交付物版本登记（ADR-0062 D9 第 2 条）。

    记录范围、依据的轮次集合与有效覆盖、收录的问题与事件、统计快照版本、
    未完成项、缺口与主文件夹集合。登记本身不表示就绪。
    """

    __tablename__ = "delivery_version"

    id = Column(Integer, primary_key=True)
    plan_run_id = Column(Integer, nullable=False)
    platform = Column(String(16), nullable=False)
    version_no = Column(Integer, nullable=False)
    software_versions = Column(JSONB, nullable=False, default=list)
    round_ids = Column(JSONB, nullable=False, default=list)
    # [{"round_id": int, "merge_generation": int}, ...]，按 round_id 升序。
    basis_rounds = Column(JSONB, nullable=False, default=list, server_default="[]")
    effective_coverage = Column(JSONB, nullable=False, default=list)
    issue_ids = Column(JSONB, nullable=False, default=list)
    event_ids = Column(JSONB, nullable=False, default=list)
    stats_snapshot_version = Column(String(64), nullable=True)
    pending_items = Column(JSONB, nullable=False, default=list)
    gaps = Column(JSONB, nullable=False, default=list)
    primary_folders = Column(JSONB, nullable=False, default=list)
    registered_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint(
            "plan_run_id",
            "platform",
            "version_no",
            name="uq_delivery_version_run_platform_version",
        ),
    )


class SpillLedger(Base):
    """溢出台账（ADR-0062 D2 第 3 条）——每个分区的溢出事件与字节数。"""

    __tablename__ = "spill_ledger"

    id = Column(Integer, primary_key=True)
    plan_run_id = Column(Integer, nullable=False)
    platform = Column(String(16), nullable=False)
    dle_id = Column(UUID(as_uuid=True), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    spilled_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("dle_id", name="uq_spill_ledger_dle_id"),
        Index("idx_spill_ledger_run_platform", "plan_run_id", "platform"),
    )
