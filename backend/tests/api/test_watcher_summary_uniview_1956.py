"""#1956 — 异常仪表盘必须计入 UNIVIEW（展锐）信号。

背景：采集侧修好（#1946）后真机事件已进 ``job_log_signal``，但仪表盘取数硬编码
``category IN ('AEE', 'VENDOR_AEE', 'ANR')``，于是出现「DLE 有行、异常仪表盘为 0」。
本用例锁住三件事：

1. UNIVIEW 信号计入 ``current_run.total_events`` / 细分类型占比 / 包名榜；
2. UNIVIEW 的 ``ANR`` **不并入** MTK 的 ``AEE/ANR`` 桶（分组独立，混平台时不失真）；
3. 同一物理事件被重复拉取时按 ``nfs_path`` 去重，不重复计数。

另有一条防线用例：仪表盘的类别口径必须来自
``log_observation.ANOMALY_SIGNAL_CATEGORIES``（曾经两处各写一份清单 → 漂移）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.api.routes import plan_runs as plan_runs_module
from backend.models.enums import JobStatus, PlanRunStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance, JobLogSignal
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def uniview_setup(db_session):
    """一台展锐设备 + 一台 MTK 设备各一个 job，混合 UNIVIEW / AEE 信号。"""
    host = Host(
        id="host-uniview", hostname="host-uniview",
        status="ONLINE", ip="10.9.0.1",
        ssh_user="root", ssh_port=22, extra={},
        last_heartbeat=_now(),
    )
    db_session.add(host)

    dev_uni = Device(
        serial="62002360", host_id="host-uniview", status="BUSY",
        adb_connected=True, adb_state="device", platform="UNISOC",
    )
    dev_mtk = Device(
        serial="AYCGNX6728006525", host_id="host-uniview", status="BUSY",
        adb_connected=True, adb_state="device", platform="MTK",
    )
    db_session.add_all([dev_uni, dev_mtk])
    db_session.commit()

    plan = Plan(name="#1956 展锐仪表盘", failure_threshold=0.05)
    db_session.add(plan)
    db_session.commit()

    run = PlanRun(
        plan_id=plan.id, status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.05,
        plan_snapshot={"plan": {"id": plan.id, "name": plan.name}, "steps": []},
        run_type="MANUAL", triggered_by="tester",
        started_at=_now() - timedelta(minutes=10),
    )
    db_session.add(run)
    db_session.commit()

    job_uni = JobInstance(
        plan_run_id=run.id, plan_id=plan.id,
        device_id=dev_uni.id, host_id=host.id,
        status=JobStatus.COMPLETED.value,
        pipeline_def={"lifecycle": {}},
        started_at=_now() - timedelta(minutes=8),
        ended_at=_now() - timedelta(minutes=1),
    )
    job_mtk = JobInstance(
        plan_run_id=run.id, plan_id=plan.id,
        device_id=dev_mtk.id, host_id=host.id,
        status=JobStatus.COMPLETED.value,
        pipeline_def={"lifecycle": {}},
        started_at=_now() - timedelta(minutes=8),
        ended_at=_now() - timedelta(minutes=1),
    )
    db_session.add_all([job_uni, job_mtk])
    db_session.commit()

    def _sig(sig_id, job, serial, seq, category, path, extra, minutes_ago):
        return JobLogSignal(
            id=sig_id, job_id=job.id, host_id=host.id, device_serial=serial,
            seq_no=seq, category=category, source="reconciler",
            path_on_device=path,
            detected_at=_now() - timedelta(minutes=minutes_ago),
            extra=extra,
        )

    # UNIVIEW（真机形态的 extra：event_subtype 取自 unievent_info 的 event_name）
    uniview_jc = {
        "aee_ts": "2026-09-08_06:59:12.031", "aee_ts_utc": None,
        "event_type": "UNIVIEW", "event_subtype": "Java Crash",
        "package_name": "com.android.camera2", "entry_origin": "runtime",
        "nfs_path": "/mnt/hdd/aee_events/uniview_watcher/0914/62002360/JE.103000004",
        "schema_version": 2,
    }
    uniview_anr = dict(
        uniview_jc,
        event_subtype="ANR", package_name="com.android.nfc",
        nfs_path="/mnt/hdd/aee_events/uniview_watcher/0914/62002360/ANR.103000005",
    )
    aee_je = {
        "event_type": "JE", "event_subtype": "JE",
        "package_name": "com.android.settings", "entry_origin": "runtime",
        "nfs_path": "/mnt/hdd/aee_events/run/62002360/aee_exp/db.01.JE",
        "schema_version": 2,
    }
    aee_anr = {
        "event_type": "ANR", "event_subtype": "ANR",
        "package_name": "com.android.settings", "entry_origin": "runtime",
        "nfs_path": "/mnt/hdd/aee_events/run/62002360/aee_exp/db.02.ANR",
        "schema_version": 2,
    }

    db_session.add_all([
        # 同一物理事件（同 nfs_path）被拉取两次 → 只应计 1 次
        _sig(20001, job_uni, dev_uni.serial, 1, "UNIVIEW", "JE.103000004", uniview_jc, 6),
        _sig(20002, job_uni, dev_uni.serial, 2, "UNIVIEW", "JE.103000004", dict(uniview_jc), 4),
        _sig(20003, job_uni, dev_uni.serial, 3, "UNIVIEW", "ANR.103000005", uniview_anr, 3),
        # 对照：MTK 的 AEE 家族不应受影响，且 ANR 与展锐 ANR 分属不同分组
        _sig(20004, job_mtk, dev_mtk.serial, 1, "AEE", "db.01.JE", aee_je, 5),
        _sig(20005, job_mtk, dev_mtk.serial, 2, "ANR", "db.02.ANR", aee_anr, 2),
    ])
    db_session.commit()

    return {"run": run, "uniview_serial": dev_uni.serial, "mtk_serial": dev_mtk.serial}


def test_uniview_signals_enter_anomaly_dashboard(client, auth_headers, uniview_setup):
    run = uniview_setup["run"]
    resp = client.get(
        f"/api/v1/plan-runs/{run.id}/watcher-summary",
        params={"time_scope": "all"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    section = resp.json()["data"]["current_run"]

    # 去重后：展锐 2 条（Java Crash + ANR，重复拉取合并）+ MTK 2 条（JE + ANR）
    assert section["total_events"] == 4, section
    dist = {(item["group"], item["subtype"]): item["count"] for item in section["subtype_distribution"]}
    assert dist[("UNIVIEW", "Java Crash")] == 1, dist
    assert dist[("UNIVIEW", "ANR")] == 1, dist
    # 关键：同名 subtype 走两个分组，互不合并
    assert dist[("AEE", "ANR")] == 1, dist
    assert dist[("AEE", "JE")] == 1, dist

    packages = {row["package_name"]: row for row in section["package_ranking"]}
    assert packages["com.android.camera2"]["total_count"] == 1, packages
    assert packages["com.android.nfc"]["total_count"] == 1, packages
    # 包名细分组带 group，供前端上色/去重
    nfc_breakdown = packages["com.android.nfc"]["subtype_breakdown"]
    assert nfc_breakdown[0]["subtype"] == "ANR"
    assert nfc_breakdown[0]["group"] == "UNIVIEW", nfc_breakdown


def test_dashboard_category_source_is_shared_with_risk_summary():
    """防线：口径必须来自单一真源，不要再在仪表盘里硬编码类别清单。"""
    from backend.services.log_observation import ANOMALY_SIGNAL_CATEGORIES

    assert "UNIVIEW" in ANOMALY_SIGNAL_CATEGORIES

    src = Path(plan_runs_module.__file__).read_text(encoding="utf-8")
    assert "ANOMALY_SIGNAL_CATEGORIES" in src
    # 曾经漏掉 UNIVIEW 的那份硬编码三元组不得再出现
    assert 'category.in_(["AEE", "VENDOR_AEE", "ANR"])' not in src
