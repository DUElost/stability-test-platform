"""PlanRun scan_now payload is scoped to this run's devices."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from backend.agent.aee.paths import shanghai_mmdd
from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.services.plan_run_scan_scope import (
    build_scan_now_payload,
    iter_plan_run_scan_hosts,
    load_plan_run_device_serials,
    load_plan_run_scan_scope,
    run_date_stamp_from_started_at,
    xls_row_matches_serials,
)


def test_run_date_stamp_uses_shanghai_mmdd():
    started = datetime(2026, 8, 8, 21, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert run_date_stamp_from_started_at(started) == "0808"
    utc = datetime(2026, 8, 8, 13, 0, tzinfo=timezone.utc)
    assert run_date_stamp_from_started_at(utc) == "0808"


def test_run_date_stamp_alignment_across_shanghai_midnight():
    """控制面 scan 范围与 Agent AEE 目录使用同一 Shanghai MMDD，跨午夜不漂移。"""
    cases = [
        datetime(2026, 8, 8, 15, 59, tzinfo=timezone.utc),  # 23:59 CST → 0808
        datetime(2026, 8, 8, 16, 0, tzinfo=timezone.utc),   # 00:00 CST → 0809
        datetime(2026, 8, 8, 16, 30, tzinfo=timezone.utc),  # 00:30 CST → 0809
    ]
    for dt in cases:
        assert run_date_stamp_from_started_at(dt) == shanghai_mmdd(dt)


def test_build_scan_now_payload_includes_serials_and_stamp(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host,
):
    job = JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status=JobStatus.COMPLETED.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        started_at=datetime(2026, 8, 8, 21, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    db_session.add(job)
    db_session.commit()

    payload = build_scan_now_payload(
        db_session, sample_plan_run.id, sample_host.id, is_final=True,
    )
    assert payload == {
        "plan_run_id": sample_plan_run.id,
        "is_final": True,
        "device_serials": [sample_device.serial],
        "run_date_stamps": ["0808"],
    }
    assert load_plan_run_device_serials(db_session, sample_plan_run.id) == [
        sample_device.serial,
    ]


def test_scan_scope_is_plan_run_wide_across_hosts(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host,
):
    host_b = Host(
        id="201",
        hostname="test-host-201",
        name="test-host-b",
        ip="192.0.2.201",
        ip_address="192.0.2.201",
        status=HostStatus.ONLINE.value,
        last_heartbeat=datetime.now(timezone.utc),
    )
    device_b = Device(
        serial="test-device-b",
        host_id=host_b.id,
        status="ONLINE",
    )
    db_session.add_all([host_b, device_b])
    db_session.flush()
    db_session.add_all([
        JobInstance(
            plan_run_id=sample_plan_run.id,
            plan_id=sample_plan.id,
            device_id=sample_device.id,
            host_id=sample_host.id,
            status=JobStatus.COMPLETED.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            started_at=datetime(2026, 8, 8, 21, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        ),
        JobInstance(
            plan_run_id=sample_plan_run.id,
            plan_id=sample_plan.id,
            device_id=device_b.id,
            host_id=host_b.id,
            status=JobStatus.COMPLETED.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            started_at=datetime(2026, 8, 7, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        ),
    ])
    db_session.commit()

    expected_serials = {sample_device.serial, device_b.serial}
    payload_a = build_scan_now_payload(db_session, sample_plan_run.id, sample_host.id)
    payload_b = build_scan_now_payload(db_session, sample_plan_run.id, host_b.id)
    assert set(payload_a["device_serials"]) == expected_serials
    assert payload_a["device_serials"] == payload_b["device_serials"]
    assert set(payload_a["run_date_stamps"]) == {"0808", "0807"}
    assert payload_a["run_date_stamps"] == payload_b["run_date_stamps"]
    assert load_plan_run_device_serials(db_session, sample_plan_run.id) == list(
        payload_a["device_serials"]
    )
    host_ids = {
        hid for hid, _status, _retired in iter_plan_run_scan_hosts(
            db_session, sample_plan_run.id,
        )
    }
    assert host_ids == {sample_host.id, host_b.id}


def test_xls_row_matches_serials_path_or_detail():
    serials = ["0000NX2622000670"]
    assert xls_row_matches_serials(
        "/hdd/f/0000NX2622000670/db.00.ANR/__exp_main.txt", "", serials,
    )
    assert xls_row_matches_serials(
        "/tmp/flat/db.00.ANR/__exp_main.txt",
        "Device_id: 0000NX2622000670\nver",
        serials,
    )
    assert not xls_row_matches_serials(
        "/hdd/f/0000NX2622000662/db.00.ANR/__exp_main.txt",
        "Device_id: 0000NX2622000662\nver",
        serials,
    )
    assert not xls_row_matches_serials(
        "/hdd/f/0000NX2622000670/db.00.ANR/__exp_main.txt", "", [],
    )


def test_scan_scope_falls_back_to_today_when_started_at_missing(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host,
):
    db_session.add(JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status=JobStatus.COMPLETED.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    ))
    db_session.commit()

    serials, stamps = load_plan_run_scan_scope(db_session, sample_plan_run.id)
    assert serials == [sample_device.serial]
    assert stamps == [datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%m%d")]


def test_iter_scan_hosts_carries_retired_flag(
    db_session, sample_plan_run, sample_plan, sample_host, sample_device,
):
    """ADR-0038 D5：历史证据集合必须可见退役标记（182d4e-R02）。"""
    from datetime import datetime, timezone

    from backend.models.host import Device, Host
    from backend.models.job import JobInstance
    from backend.models.enums import JobStatus

    retired_host = Host(
        id="h-retired-scan", hostname="h-retired-scan", status="ONLINE",
        retired_at=datetime.now(timezone.utc),
    )
    db_session.add(retired_host)
    db_session.flush()
    # uq_job_instance_plan_run_device：同一 Run 同一设备只允许一个 Job
    retired_device = Device(
        serial="dev-retired-scan", host_id=retired_host.id, status="OFFLINE",
    )
    db_session.add(retired_device)
    db_session.flush()
    for host_id, device_id in (
        (sample_host.id, sample_device.id),
        (retired_host.id, retired_device.id),
    ):
        db_session.add(JobInstance(
            plan_run_id=sample_plan_run.id,
            plan_id=sample_plan.id,
            device_id=device_id,
            host_id=host_id,
            status=JobStatus.COMPLETED.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        ))
    db_session.commit()

    rows = {
        hid: (status, retired)
        for hid, status, retired in iter_plan_run_scan_hosts(db_session, sample_plan_run.id)
    }
    assert rows[retired_host.id] == ("ONLINE", True)
    assert rows[sample_host.id] == ("ONLINE", False)


def test_classify_recycle_targets_policy_matrix():
    """ADR-0038 D5：退役仅显式 admin 触发可触达；skipped_retired 不虚报完整。"""
    from backend.services.plan_run_scan_scope import classify_recycle_targets

    rows = [
        ("h-on", "ONLINE", False),
        ("h-off", "OFFLINE", False),
        ("h-ret-on", "ONLINE", True),
        ("h-ret-off", "OFFLINE", True),
    ]

    targets, off, ret = classify_recycle_targets(rows, allow_retired=False)
    assert targets == ["h-on"]
    assert [r["host_id"] for r in off] == ["h-off"]
    assert [r["host_id"] for r in ret] == ["h-ret-on", "h-ret-off"]

    targets, off, ret = classify_recycle_targets(rows, allow_retired=True)
    assert targets == ["h-on", "h-ret-on"]
    assert [r["host_id"] for r in off] == ["h-off"]
    assert [r["host_id"] for r in ret] == ["h-ret-off"]


def test_load_expected_scan_platforms_derives_from_host_device_mix(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host,
):
    """ADR-0032 B1：完备性期望按 host 的**设备平台构成**派生。

    不是「每个 host 都要所有平台」——那是 #1071 收紧过头的语义，会让纯平台 host
    每轮都判不齐。
    """
    from backend.services.plan_run_scan_scope import load_expected_scan_platforms

    host_b = Host(
        id="205",
        hostname="test-host-205",
        name="test-host-205",
        ip="192.0.2.205",
        ip_address="192.0.2.205",
        status=HostStatus.ONLINE.value,
    )
    db_session.add(host_b)
    db_session.flush()
    extra_devices = [
        Device(
            serial="dev-unisoc-1", host_id=host_b.id,
            platform="UNISOC", status="ONLINE",
        ),
        Device(
            serial="dev-qcom-1", host_id=host_b.id,
            platform="QCOM", status="ONLINE",
        ),
    ]
    db_session.add_all(extra_devices)
    db_session.flush()
    # sample_device 未设 platform（NULL）→ 与 Agent 路由一致兜底到 mtk。
    for device in [sample_device, *extra_devices]:
        db_session.add(JobInstance(
            plan_run_id=sample_plan_run.id,
            plan_id=sample_plan.id,
            device_id=device.id,
            host_id=device.host_id,
            status=JobStatus.COMPLETED.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        ))
    db_session.commit()

    expected = load_expected_scan_platforms(
        db_session, sample_plan_run.id, [sample_host.id, host_b.id],
    )
    assert expected[sample_host.id] == {"mtk"}
    # UNISOC 设备 → unisoc；QCOM 无采集/扫描实现 → 不计入期望。
    assert expected[host_b.id] == {"unisoc"}


def test_load_expected_scan_platforms_ignores_hosts_without_device_evidence(
    db_session, sample_plan_run,
):
    """无设备证据的 host 不产生期望——它没有可预期产物，不该拖住屏障。"""
    from backend.services.plan_run_scan_scope import load_expected_scan_platforms

    assert load_expected_scan_platforms(
        db_session, sample_plan_run.id, ["host-x"],
    ) == {}
    assert load_expected_scan_platforms(db_session, sample_plan_run.id, []) == {}
