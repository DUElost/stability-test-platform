"""Log observation layer — ADR-0028 authority vs UI aggregation (#519 / #527).

``device_log_event`` is the upload/extract authority; ``job_log_signal`` remains
the PlanRun watcher-summary transport. Risk rating merges both without
double-counting linked rows (signals with ``device_log_event_id`` are excluded).

Follow-up (#519 remaining): migrate watcher-summary UI to read DLE-backed
aggregates — tracked separately from this module; do not delete ``job_log_signal``
until that UI migration lands.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.core import metrics
from backend.models.job import JobLogSignal
from backend.services.report_service import (
    _DEFAULT_RISK_LEVEL,
    _RISK_SEVERITY_ORDER,
    _classify_subtype,
)

# Legacy family labels in device_log_event.event_type (#519) plus concrete types
# written by resolve_device_log_event_type (#215 / #1054). Placeholders
# (UNKNOWN/CRASH/AEE/其他) still match when event_subtype carries the subtype.
_DLE_RISK_FAMILY_EVENT_TYPES = frozenset({"AEE", "VENDOR_AEE", "ANR", "CRASH", "UNIVIEW"})
_DLE_RISK_CONCRETE_EVENT_TYPES = frozenset({
    "ANR",
    "JE",
    "NE",
    "KE",
    "SWT",
    "HWT",
    "HANG",
    "FATAL JE",
    "FATAL NE",
    "COMBO EE",
    "KERNEL API DUMP",
    "SYSTEM API DUMP",
    "MODEM EE",
    "OCP REBOOT",
    "HW REBOOT",
})
_DLE_RISK_PLACEHOLDER_EVENT_TYPES = frozenset(
    {"", "UNKNOWN", "CRASH", "AEE", "VENDOR_AEE", "其他"},
)
_DLE_RISK_EVENT_TYPES = tuple(
    sorted(_DLE_RISK_FAMILY_EVENT_TYPES | _DLE_RISK_CONCRETE_EVENT_TYPES),
)
# #1956：异常类信号的类别**真源**（风险汇总 / 异常仪表盘共用）。
# 曾经两处各写一份清单，结果仪表盘漏掉 UNIVIEW——事件采到了却不进仪表盘。
# 现由此单一常量派生，并有防漂移测试守卫（见 tests 里的 sync 用例）。
ANOMALY_SIGNAL_CATEGORIES = ("AEE", "VENDOR_AEE", "ANR", "UNIVIEW")
_SIGNAL_RISK_CATEGORIES = ANOMALY_SIGNAL_CATEGORIES
# Reconciler registers DLE for crash-family signals; MOBILELOG is signal-only (#528).
_LINK_RATE_CATEGORIES = ("AEE", "VENDOR_AEE", "UNIVIEW")
_SIGNAL_ONLY_CATEGORIES = ("MOBILELOG",)


def _rows_from_device_log_events(
    db: Session, job_ids: list[int], *, by_job: bool = False,
) -> list[tuple]:
    """DLE-backed subtype counts (authority per ADR-0028).

    Ruled semantics (#783, 2026-09-12): the count is **distinct event artifact
    paths** per subtype (``COUNT(DISTINCT COALESCE(remote_path, local_path))``),
    not raw event rows. This matches the DLE model where a path identifies one
    uploaded event artifact; multiple signals referencing the same artifact are
    the same event and must not inflate the risk bucket. ``_classify_subtype``
    thresholds therefore read "distinct event artifacts", not "event
    occurrences". Changing this needs a product ruling (see Revisit).

    ``by_job=True`` 时按 job 分桶（``(job_id, subtype, count)``，供 #2365 的
    逐 job 判定用）；两种形态共用同一 SELECT/WHERE 字面量，过滤器不会各写一份
    而漂移。
    """
    prefix = "job_id, " if by_job else ""
    sql = text(f"""
        SELECT
            {prefix}COALESCE(NULLIF(event_subtype, ''), event_type) AS subtype,
            COUNT(DISTINCT COALESCE(remote_path, local_path)) AS dedup_count
        FROM device_log_event
        WHERE job_id = ANY(:job_ids)
          AND (
            upper(event_type) = ANY(:event_types)
            OR (
              upper(event_type) = ANY(:placeholder_types)
              AND upper(COALESCE(NULLIF(event_subtype, ''), '___')) = ANY(:concrete_subtypes)
            )
          )
        GROUP BY {prefix}subtype
    """)
    rows = db.execute(
        sql,
        {
            "job_ids": list(job_ids),
            "event_types": [t.upper() for t in _DLE_RISK_EVENT_TYPES],
            "placeholder_types": [t.upper() for t in _DLE_RISK_PLACEHOLDER_EVENT_TYPES],
            "concrete_subtypes": [t.upper() for t in _DLE_RISK_CONCRETE_EVENT_TYPES],
        },
    ).all()
    if by_job:
        return [(int(job_id), str(subtype), int(count)) for job_id, subtype, count in rows]
    return [(str(subtype), int(count)) for subtype, count in rows]


def _rows_from_unlinked_signals(
    db: Session, job_ids: list[int], *, by_job: bool = False,
) -> list[tuple]:
    """未链接信号（尚未归档成 DLE 的那部分）的子类型计数；``by_job`` 语义同
    :func:`_rows_from_device_log_events`（#2365）。"""
    prefix = "job_id, " if by_job else ""
    sql = text(f"""
        SELECT
            {prefix}COALESCE(extra->>'event_subtype', category) AS subtype,
            COUNT(DISTINCT extra->>'nfs_path') AS dedup_count
        FROM job_log_signal
        WHERE job_id = ANY(:job_ids)
          AND device_log_event_id IS NULL
          AND category = ANY(:categories)
        GROUP BY {prefix}subtype
    """)
    rows = db.execute(
        sql,
        {
            "job_ids": list(job_ids),
            "categories": list(_SIGNAL_RISK_CATEGORIES),
        },
    ).all()
    if by_job:
        return [(int(job_id), str(subtype), int(count)) for job_id, subtype, count in rows]
    return [(str(subtype), int(count)) for subtype, count in rows]


def _subtype_levels(subtype_counts: dict[str, int]) -> Dict[str, str]:
    """子类型 → S/A/B（判据唯一实现，全局汇总与逐 job 判定共用，#2365）。"""
    return {
        subtype: _classify_subtype(subtype, count)
        for subtype, count in subtype_counts.items()
    }


def _worst_level(levels: Dict[str, str]) -> str:
    """最严重级别；无输入时返回默认级（调用方负责把「无信号」表达成 UNKNOWN）。"""
    worst = _DEFAULT_RISK_LEVEL
    for level in levels.values():
        if _RISK_SEVERITY_ORDER.get(level, 0) > _RISK_SEVERITY_ORDER.get(worst, 0):
            worst = level
    return worst


def _build_risk_summary(subtype_counts: dict[str, int]) -> Optional[Dict[str, Any]]:
    if not subtype_counts:
        return None

    by_type: Dict[str, int] = {}
    by_severity: Dict[str, int] = {"S": 0, "A": 0, "B": 0}
    events_total = 0
    aee_entries = 0

    for subtype, count in subtype_counts.items():
        by_type[subtype] = count
        events_total += count
        upper = subtype.upper()
        if upper != "ANR":
            aee_entries += count

    levels = _subtype_levels(subtype_counts)
    for level in levels.values():
        by_severity[level] = by_severity.get(level, 0) + 1
    worst_level = _worst_level(levels)

    return {
        "risk_level": worst_level,
        "counts": {
            "by_type": by_type,
            "by_severity": by_severity,
            "events_total": events_total,
            "aee_entries": aee_entries,
        },
    }


def aggregate_signal_link_stats(db: Session, job_ids: list[int]) -> Dict[str, Any]:
    """PlanRun-scoped signal↔DLE link metrics (#528).

    ``link_rate`` is computed over AEE/VENDOR_AEE only (categories that should
    register DLE on the reconciler path). MOBILELOG is excluded from the rate.

    A single ratio cannot answer "is the link logic broken?", because the
    unlinked set mixes three very different states. Per ADR-0028, events stay
    on the Agent's local disk until an archive report names them or the disk
    fills, so a signal with no DLE usually means "not archived yet" — not a
    failure. The unlinked linkable set is therefore split three ways:

    - ``not_yet_archived``: the job has no ``device_log_event`` row at all.
    - ``unlinkable``: the job has DLE rows, but none carrying this signal's
      ``seq_no`` (e.g. UNKNOWN events reconciled without a originating signal).
    - ``unlinked_fixable``: a matching DLE exists yet the link is missing —
      the only bucket that is a genuine failure, and the only one the
      ``signal_link_reconcile`` sweep can repair.

    ``fixable_link_rate`` is the alert-able number: it ignores the two buckets
    that cannot be fixed by construction.
    """
    if not job_ids:
        return {
            "total_signals": 0,
            "linked_signals": 0,
            "unlinked_linkable": 0,
            "signal_only_signals": 0,
            "link_rate": 1.0,
            "not_yet_archived": 0,
            "unlinkable": 0,
            "unlinked_fixable": 0,
            "fixable_link_rate": 1.0,
        }

    total = int(
        db.execute(
            select(func.count(JobLogSignal.id)).where(
                JobLogSignal.job_id.in_(job_ids),
            )
        ).scalar()
        or 0
    )
    linked = int(
        db.execute(
            select(func.count(JobLogSignal.id)).where(
                JobLogSignal.job_id.in_(job_ids),
                JobLogSignal.device_log_event_id.isnot(None),
            )
        ).scalar()
        or 0
    )
    linkable_total = int(
        db.execute(
            select(func.count(JobLogSignal.id)).where(
                JobLogSignal.job_id.in_(job_ids),
                JobLogSignal.category.in_(_LINK_RATE_CATEGORIES),
            )
        ).scalar()
        or 0
    )
    linkable_linked = int(
        db.execute(
            select(func.count(JobLogSignal.id)).where(
                JobLogSignal.job_id.in_(job_ids),
                JobLogSignal.category.in_(_LINK_RATE_CATEGORIES),
                JobLogSignal.device_log_event_id.isnot(None),
            )
        ).scalar()
        or 0
    )
    signal_only = int(
        db.execute(
            select(func.count(JobLogSignal.id)).where(
                JobLogSignal.job_id.in_(job_ids),
                JobLogSignal.category.in_(_SIGNAL_ONLY_CATEGORIES),
            )
        ).scalar()
        or 0
    )
    unlinked_linkable = max(0, linkable_total - linkable_linked)
    link_rate = (linkable_linked / linkable_total) if linkable_total else 1.0

    # Three-way split of the unlinked linkable set. Both EXISTS probes ride
    # idx_device_log_event_job_signal_seq (job_id, signal_seq_no).
    split = db.execute(
        text(
            """
            SELECT
              count(*) FILTER (
                WHERE s.device_log_event_id IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM device_log_event e WHERE e.job_id = s.job_id)
              ) AS not_yet_archived,
              count(*) FILTER (
                WHERE s.device_log_event_id IS NULL
                  AND EXISTS (
                    SELECT 1 FROM device_log_event e WHERE e.job_id = s.job_id)
                  AND NOT EXISTS (
                    SELECT 1 FROM device_log_event e
                     WHERE e.job_id = s.job_id AND e.signal_seq_no = s.seq_no)
              ) AS unlinkable,
              count(*) FILTER (
                WHERE s.device_log_event_id IS NULL
                  AND EXISTS (
                    SELECT 1 FROM device_log_event e
                     WHERE e.job_id = s.job_id AND e.signal_seq_no = s.seq_no)
              ) AS unlinked_fixable
            FROM job_log_signal s
            WHERE s.job_id = ANY(:job_ids)
              AND s.category = ANY(:categories)
            """
        ),
        {"job_ids": list(job_ids), "categories": list(_LINK_RATE_CATEGORIES)},
    ).one()
    not_yet_archived = int(split.not_yet_archived or 0)
    unlinkable = int(split.unlinkable or 0)
    unlinked_fixable = int(split.unlinked_fixable or 0)

    # #528 链接健康：unlinked_fixable 稳态不该出现 → 非零即告警。
    # 注意本函数唯一调用点是 GET watcher-summary 路由，计数随前端轮询重复自增，
    # 绝对值无意义，只用于 increase(...)>0（见 metrics.py 同名计数器注释）。
    if unlinked_fixable > 0:
        metrics.unlinked_fixable_total.inc()

    fixable_total = linkable_linked + unlinked_fixable
    fixable_link_rate = (
        linkable_linked / fixable_total if fixable_total else 1.0
    )
    return {
        "total_signals": total,
        "linked_signals": linked,
        "unlinked_linkable": unlinked_linkable,
        "signal_only_signals": signal_only,
        "link_rate": round(link_rate, 4),
        "not_yet_archived": not_yet_archived,
        "unlinkable": unlinkable,
        "unlinked_fixable": unlinked_fixable,
        "fixable_link_rate": round(fixable_link_rate, 4),
    }


def aggregate_risk_summary(db: Session, job_ids: list[int]) -> Optional[Dict[str, Any]]:
    """PlanRun-scoped risk rollup: DLE authority + legacy unlinked signals."""
    if not job_ids:
        return None

    merged: dict[str, int] = {}
    for subtype, count in _rows_from_device_log_events(db, job_ids):
        merged[subtype] = merged.get(subtype, 0) + count
    for subtype, count in _rows_from_unlinked_signals(db, job_ids):
        merged[subtype] = merged.get(subtype, 0) + count

    return _build_risk_summary(merged)


def aggregate_risk_summary_from_signals(
    db: Session, job_ids: list[int]
) -> Optional[Dict[str, Any]]:
    """Backward-compatible alias — prefer :func:`aggregate_risk_summary`."""
    return aggregate_risk_summary(db, job_ids)


def aggregate_risk_levels_by_job(db: Session, job_ids: list[int]) -> Dict[int, str]:
    """逐 job 风险级别（S/A/B）——**只含有异常信号的 job**，无信号者不在返回里。

    #2365：与 :func:`aggregate_risk_summary` **同判据同数据源**（DLE 权威 +
    未链接信号，经 :func:`_subtype_levels` / :func:`_worst_level`），只是按 job
    分桶且一次查询覆盖全部 job——调用方（`/results/summary` 的风险分布与
    recent_runs）因此不必逐 job 往返。

    「有信号」与「无信号」必须由调用方区分：`None` 表示**没有可判定的事件**
    （风险未知），不是「低风险」——这是 #2365 覆盖率观测的口径，别把它压成默认级。
    """
    if not job_ids:
        return {}

    per_job: Dict[int, Dict[str, int]] = {}
    for job_id, subtype, count in _rows_from_device_log_events(db, job_ids, by_job=True):
        bucket = per_job.setdefault(job_id, {})
        bucket[subtype] = bucket.get(subtype, 0) + count
    for job_id, subtype, count in _rows_from_unlinked_signals(db, job_ids, by_job=True):
        bucket = per_job.setdefault(job_id, {})
        bucket[subtype] = bucket.get(subtype, 0) + count

    return {job_id: _worst_level(_subtype_levels(counts)) for job_id, counts in per_job.items()}
