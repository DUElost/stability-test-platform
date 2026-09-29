"""#3482：fleet gauge 的 Device 陈旧判据必须每拍现算（cutoff 不得冻结在 import 时）。

#2962 A 给 `stability_device_online{status="offline"}` 加的新鲜度剔除里，7d cutoff
是**现算事实**：`_FLEET_GAUGES` 若在模块 import 时把 `not_stale_condition()` 求值一次，
cutoff 被烤成 SQL 字面量，此后每次 /metrics 拉取复用启动时刻的判据——进程运行中
新转陈旧的设备继续计入 offline 桶，同一拍又按当下判据落 `stability_device_stale`，
一台双计、随 uptime 漂移，重启才自愈。

本文件用可控假钟在同一进程内推进时钟越过 7 天界（不重启、不重新 import），
验证 offline 桶与 stale gauge 都按当下重算、无一台双计。单拍内的分桶正确性
（同一时刻 seeded 新鲜/陈旧/退役）已由 test_metrics_device_lifecycle_2962.py
覆盖，此处不重复。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import backend.services.device_lifecycle as device_lifecycle
from backend.models.enums import DeviceStatus, HostStatus
from backend.models.host import Device, Host
from sqlalchemy import func

#: 跨拍推进量：种子 last_seen 距 T0 有 1 天新鲜余量，推进后距当下陈旧余量同为 1 天。
_CLOCK_ADVANCE = timedelta(days=2)


def _install_fake_clock(monkeypatch) -> dict:
    """把判据模块的 `normalize_now` 换成可控假钟。

    `stale_condition` / `not_stale_condition` 在**调用时**从本模块 globals 解析
    `normalize_now`——patch 这里即覆盖 /metrics 全部陈旧判据求值点（fleet 桶、
    stale_count、per-host adb 分桶），与生产「每拍取真实当下」同构。
    """
    t0 = datetime.now(timezone.utc)
    clock = {"now": t0}

    def fake_normalize_now(now: datetime | None = None) -> datetime:
        return now or clock["now"]

    monkeypatch.setattr(device_lifecycle, "normalize_now", fake_normalize_now)
    return clock


def _seed_one_recently_offline_device(db, t0: datetime) -> None:
    db.add(Host(
        id="f3482-h1", hostname="f3482-h1", status=HostStatus.ONLINE.value,
        ip="10.77.1.1", ssh_user="root", ssh_port=22, extra={},
        last_heartbeat=t0, created_at=t0,
    ))
    # OFFLINE 且 last_seen = T0 − 6 天：当下是「近期掉线」（7 天界内 1 天余量）。
    db.add(Device(serial="f3482-off", host_id="f3482-h1",
                  status=DeviceStatus.OFFLINE.value, adb_state="offline",
                  tags=[], created_at=t0, last_seen=t0 - timedelta(days=6)))
    db.commit()


def _series_value(body: str, series: str) -> float:
    for line in body.splitlines():
        if line.startswith(series + "{") or line.startswith(series + " "):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"series not exposed: {series}")


def test_offline_bucket_and_stale_gauge_recompute_across_ticks(
    client, db_session, monkeypatch,
):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    clock = _install_fake_clock(monkeypatch)
    _seed_one_recently_offline_device(db_session, clock["now"])

    # 拍 1：cutoff = T0 − 7d，设备（6 天未见）是「近期掉线」。
    body1 = client.get("/metrics").text
    assert _series_value(body1, 'stability_device_online{status="offline"}') == 1.0
    assert _series_value(body1, "stability_device_stale") == 0.0

    # 同一进程推进时钟越过 7 天界：判据消费方之外无任何状态变化。
    clock["now"] = clock["now"] + _CLOCK_ADVANCE

    # 拍 2：cutoff = T0 − 5d，同一设备按当下判为陈旧——offline 桶让出、stale 接管，
    # 一台只落一个桶（冻结 cutoff 的缺陷形态是 offline 1.0 ∧ stale 1.0 双计）。
    body2 = client.get("/metrics").text
    assert _series_value(body2, 'stability_device_online{status="offline"}') == 0.0
    assert _series_value(body2, "stability_device_stale") == 1.0


def test_device_online_buckets_plus_stale_reconcile_capacity(
    client, db_session, monkeypatch,
):
    """对账断言（#3482 验收可选项）：Σ device_online 各桶 + stale = 在役非陈旧容量。

    offline 桶与 stale 的判据互补（陈旧度与其补集），任一拍都不该有设备同时
    落进两边、也不该有在役设备两边都不落。
    """
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    clock = _install_fake_clock(monkeypatch)
    _seed_one_recently_offline_device(db_session, clock["now"])

    for _tick in range(2):
        body = client.get("/metrics").text
        bucket_sum = sum(
            _series_value(body, f'stability_device_online{{status="{member}"}}')
            for member in ("online", "offline", "busy", "error")
        )
        # 对账基数 = 非退役设备总数（DB 直查，不带陈旧谓词）：陈旧度与其补集
        # 互补，任一拍 Σ桶 + stale 都必须恰好等于它——双计（2>1）或漏计（0<1）当场红。
        non_retired_total = (
            db_session.query(func.count())
            .select_from(Device)
            .filter(Device.retired_at.is_(None))
            .scalar()
        )
        assert bucket_sum + _series_value(body, "stability_device_stale") == float(non_retired_total)
        # 推进时钟再对账一拍：陈旧化发生后两桶之和仍守恒。
        clock["now"] = clock["now"] + _CLOCK_ADVANCE
