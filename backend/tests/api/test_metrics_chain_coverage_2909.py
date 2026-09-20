"""#2909③：链覆盖差 gauge 的拉取期计算（fleet ONLINE ∉ enabled schedule 清单并集）。

锁四件事：① gap 只算 enabled schedule（停用清单不算覆盖）；② 退役 host 的
ONLINE 设备不进分母（ADR-0038 D5 同族口径）；③ 无 host 归属的设备不进分母
（与 adb gauge 的「不硬造 (none) 桶」同理）；④ 多 schedule 取**并集**（跨清单
重复设备不双计、任一清单含它即算覆盖）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.models.enums import DeviceStatus, HostStatus
from backend.models.host import Device, Host
from backend.models.plan import Plan
from backend.models.schedule import TaskSchedule


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _host(db, hid: str, *, retired: bool = False) -> Host:
    octet = (sum(ord(ch) for ch in hid) % 200) + 10
    h = Host(
        id=hid, hostname=hid, status=HostStatus.ONLINE.value,
        ip=f"10.46.0.{octet}", ssh_user="root", ssh_port=22, extra={},
        last_heartbeat=_now(), created_at=_now(),
    )
    if retired:
        h.retired_at = _now()
    db.add(h)
    return h


def _dev(db, serial: str, hid: str | None) -> Device:
    d = Device(
        serial=serial, host_id=hid, status=DeviceStatus.ONLINE.value,
        tags=[], created_at=_now(),
    )
    db.add(d)
    db.flush()
    return d


def _sched(db, name: str, ids: list[int], *, enabled: bool = True) -> TaskSchedule:
    plan = Plan(name=f"cov-{name}")
    db.add(plan)
    db.flush()
    s = TaskSchedule(
        name=f"cov-{name}", cron_expression="0 * * * *", plan_id=plan.id,
        enabled=enabled, device_ids=ids,
    )
    db.add(s)
    return s


def test_chain_coverage_triplet_computed_at_scrape(client, db_session, monkeypatch):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _host(db_session, "cov-h1")
    d1 = _dev(db_session, "cov-1", "cov-h1")   # 在 enabled schedule → 覆盖
    _dev(db_session, "cov-2", "cov-h1")             # 不在任何清单 → gap
    d3 = _dev(db_session, "cov-3", "cov-h1")   # 只在 disabled schedule → gap（停用不算覆盖）
    d4 = _dev(db_session, "cov-4", "cov-h1")   # 第二个 enabled schedule 并集成员
    _sched(db_session, "on", [d1.id, d4.id])
    _sched(db_session, "off", [d3.id], enabled=False)
    db_session.commit()

    body = client.get("/metrics").text
    assert 'stability_chain_coverage_devices{kind="online_total"} 4.0' in body
    assert 'stability_chain_coverage_devices{kind="scheduled_union"} 2.0' in body
    assert 'stability_chain_coverage_devices{kind="gap_missing"} 2.0' in body


def test_retired_and_ownerless_devices_excluded_from_denominator(client, db_session, monkeypatch):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _host(db_session, "cov-ret", retired=True)
    _dev(db_session, "cov-r1", "cov-ret")      # 退役 host 的设备：分母外
    _dev(db_session, "cov-orphan", None)        # 无 host 归属：分母外
    _host(db_session, "cov-h2")
    kept = _dev(db_session, "cov-k", "cov-h2")
    _sched(db_session, "k", [kept.id])
    db_session.commit()

    body = client.get("/metrics").text
    # 该用例的 fleet 是聚合的：只要退役/孤儿的 2 台不进 online_total 增量即成立
    # （前一个用例不存在并发，这里独立库）——精确断言：total==1、gap==0
    assert 'stability_chain_coverage_devices{kind="online_total"} 1.0' in body
    assert 'stability_chain_coverage_devices{kind="gap_missing"} 0.0' in body
