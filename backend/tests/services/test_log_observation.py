"""#519 — unified risk aggregation (DLE authority + unlinked signals)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from backend.core import metrics
from backend.models.device_log_event import DeviceLogEvent
from backend.models.job import JobInstance, JobLogSignal
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services.log_observation import (
    aggregate_risk_summary,
    aggregate_signal_link_stats,
)


def _seed_job(db_session, sample_device, status="RUNNING"):
    now = datetime.now(timezone.utc)
    plan = Plan(name="risk-obs-plan", failure_threshold=0.05)
    db_session.add(plan)
    db_session.flush()
    pr = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        failure_threshold=0.05,
        plan_snapshot={},
        run_type="MANUAL",
    )
    db_session.add(pr)
    db_session.flush()
    job = JobInstance(
        plan_run_id=pr.id,
        plan_id=plan.id,
        device_id=sample_device.id,
        host_id=sample_device.host_id,
        status=status,
        pipeline_def={"lifecycle": {}},
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(job)
    db_session.flush()
    return job, now


def test_risk_summary_counts_unlinked_signals_only(db_session, sample_device):
    job, now = _seed_job(db_session, sample_device)
    for i in range(3):
        db_session.add(JobLogSignal(
            job_id=job.id,
            host_id=str(sample_device.host_id),
            device_serial=sample_device.serial,
            seq_no=i,
            category="ANR",
            source="inotifyd",
            path_on_device=f"/data/anr/{i}",
            detected_at=now,
            received_at=now,
            extra={"event_subtype": "ANR", "nfs_path": f"/nfs/anr/{i}"},
        ))
    db_session.commit()

    summary = aggregate_risk_summary(db_session, [job.id])
    assert summary is not None
    assert summary["counts"]["by_type"]["ANR"] == 3


def test_risk_summary_prefers_dle_and_skips_linked_signals(db_session, sample_device):
    job, now = _seed_job(db_session, sample_device)
    dle = DeviceLogEvent(
        id=uuid4(),
        serial=sample_device.serial,
        platform="MTK",
        event_type="ANR",
        event_subtype="ANR",
        detected_at=now,
        state="REMOTE",
        local_path="/local/anr/1",
        remote_path="/nfs/devices/1/anr/1",
        host_id=str(sample_device.host_id),
        job_id=job.id,
        plan_run_id=job.plan_run_id,
    )
    db_session.add(dle)
    db_session.flush()

    db_session.add(JobLogSignal(
        job_id=job.id,
        host_id=str(sample_device.host_id),
        device_serial=sample_device.serial,
        device_log_event_id=dle.id,
        seq_no=1,
        category="ANR",
        source="reconciler",
        path_on_device="/data/anr/1",
        detected_at=now,
        received_at=now,
        extra={"event_subtype": "ANR", "nfs_path": "/nfs/devices/1/anr/1"},
    ))
    db_session.add(JobLogSignal(
        job_id=job.id,
        host_id=str(sample_device.host_id),
        device_serial=sample_device.serial,
        seq_no=2,
        category="ANR",
        source="inotifyd",
        path_on_device="/data/anr/legacy",
        detected_at=now,
        received_at=now,
        extra={"event_subtype": "ANR", "nfs_path": "/nfs/anr/legacy"},
    ))
    db_session.commit()

    summary = aggregate_risk_summary(db_session, [job.id])
    assert summary is not None
    # 1 from DLE + 1 legacy unlinked signal (linked signal excluded)
    assert summary["counts"]["by_type"]["ANR"] == 2


def test_signal_link_stats_excludes_mobilelog_from_link_rate(db_session, sample_device):
    job, now = _seed_job(db_session, sample_device)
    db_session.add(JobLogSignal(
        job_id=job.id,
        host_id=str(sample_device.host_id),
        device_serial=sample_device.serial,
        seq_no=1,
        category="AEE",
        source="reconciler",
        path_on_device="/data/aee/1",
        detected_at=now,
        received_at=now,
    ))
    db_session.add(JobLogSignal(
        job_id=job.id,
        host_id=str(sample_device.host_id),
        device_serial=sample_device.serial,
        seq_no=2,
        category="MOBILELOG",
        source="reconciler",
        path_on_device="/data/mobilelog",
        detected_at=now,
        received_at=now,
    ))
    db_session.commit()

    stats = aggregate_signal_link_stats(db_session, [job.id])
    assert stats["total_signals"] == 2
    assert stats["unlinked_linkable"] == 1
    assert stats["signal_only_signals"] == 1
    assert stats["link_rate"] == 0.0


def test_signal_link_stats_counts_linked_aee(db_session, sample_device):
    job, now = _seed_job(db_session, sample_device)
    dle = DeviceLogEvent(
        id=uuid4(),
        serial=sample_device.serial,
        platform="MTK",
        event_type="AEE",
        event_subtype="KE",
        detected_at=now,
        state="LOCAL",
        local_path="/local/aee/1",
        host_id=str(sample_device.host_id),
        job_id=job.id,
        plan_run_id=job.plan_run_id,
        signal_seq_no=1,
    )
    db_session.add(dle)
    db_session.flush()
    db_session.add(JobLogSignal(
        job_id=job.id,
        host_id=str(sample_device.host_id),
        device_serial=sample_device.serial,
        device_log_event_id=dle.id,
        seq_no=1,
        category="AEE",
        source="reconciler",
        path_on_device="/data/aee/1",
        detected_at=now,
        received_at=now,
    ))
    db_session.commit()

    stats = aggregate_signal_link_stats(db_session, [job.id])
    assert stats["linked_signals"] == 1
    assert stats["link_rate"] == 1.0


def _add_aee_signal(db_session, sample_device, job, seq, now):
    db_session.add(JobLogSignal(
        job_id=job.id,
        host_id=str(sample_device.host_id),
        device_serial=sample_device.serial,
        seq_no=seq,
        category="AEE",
        source="reconciler",
        path_on_device=f"/data/aee/{seq}",
        detected_at=now,
        received_at=now,
    ))


def _add_dle(db_session, sample_device, job, now, signal_seq_no):
    db_session.add(DeviceLogEvent(
        id=uuid4(),
        serial=sample_device.serial,
        platform="MTK",
        event_type="AEE",
        event_subtype="KE",
        detected_at=now,
        state="LOCAL",
        local_path=f"/local/aee/{job.id}",
        host_id=str(sample_device.host_id),
        job_id=job.id,
        plan_run_id=job.plan_run_id,
        signal_seq_no=signal_seq_no,
    ))


def test_signal_link_stats_splits_unlinked_into_three_buckets(
    db_session, sample_device, monkeypatch,
):
    """#528: 未链接集合必须拆成三类，且三桶之和守恒。"""
    counter = MagicMock()
    monkeypatch.setattr(metrics, "unlinked_fixable_total", counter)
    # uq_job_active_per_device：同一设备只允许一个在途 job，其余用终态
    job_a, now = _seed_job(db_session, sample_device)              # 无任何 DLE
    job_b, _ = _seed_job(db_session, sample_device, "COMPLETED")   # DLE 的 seq 不匹配
    job_c, _ = _seed_job(db_session, sample_device, "COMPLETED")   # seq 匹配但未链接

    _add_aee_signal(db_session, sample_device, job_a, 1, now)
    _add_aee_signal(db_session, sample_device, job_b, 2, now)
    _add_aee_signal(db_session, sample_device, job_c, 3, now)
    _add_dle(db_session, sample_device, job_b, now, 999)
    _add_dle(db_session, sample_device, job_c, now, 3)
    db_session.commit()

    stats = aggregate_signal_link_stats(
        db_session, [job_a.id, job_b.id, job_c.id],
    )

    assert stats["not_yet_archived"] == 1
    assert stats["unlinkable"] == 1
    assert stats["unlinked_fixable"] == 1
    # 不变式：三桶之和 == 旧的粗口径未链接数
    assert stats["unlinked_linkable"] == 3
    assert stats["link_rate"] == 0.0
    # 告警口径：已链接 0 / (已链接 0 + 真故障 1)
    assert stats["fixable_link_rate"] == 0.0
    # P1：unlinked_fixable > 0 → 非零即告警计数器自增
    counter.inc.assert_called_once_with()


def test_fixable_link_rate_ignores_not_yet_archived(db_session, sample_device, monkeypatch):
    """#528 核心：只有「尚未归档」时，告警口径必须仍是 1.0。

    旧口径把这类 signal 算作失败，导致生产 link_rate 被压到 0.575，
    而这个数字无论怎么修链接逻辑都提不上去。
    """
    counter = MagicMock()
    monkeypatch.setattr(metrics, "unlinked_fixable_total", counter)
    job, now = _seed_job(db_session, sample_device)
    _add_aee_signal(db_session, sample_device, job, 1, now)
    db_session.commit()

    stats = aggregate_signal_link_stats(db_session, [job.id])

    assert stats["not_yet_archived"] == 1
    assert stats["unlinkable"] == 0
    assert stats["unlinked_fixable"] == 0
    assert stats["link_rate"] == 0.0          # 旧口径误报为失败
    assert stats["fixable_link_rate"] == 1.0  # 告警口径：没有真故障
    # P1：unlinked_fixable == 0 → 计数器不自增（不该触发告警）
    counter.inc.assert_not_called()
