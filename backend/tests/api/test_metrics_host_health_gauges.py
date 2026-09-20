"""#2900/#2957：``/metrics`` 把 agent 上报的 host 健康 reason 与判据通道折成 series。

#2900 的失效形状是「agent 已经知道、平台已经收到、但没有任何人会被告知」：
`api/routes/heartbeat.py` 把 `payload.health` 塞进 `host.extra` 这个 JSON 就停了。
本文件锁住那最后一跳的口径——不是「有没有指标」，而是**指标会不会骗人**：

- 每台落值 host 的**全词表**都要有 series（含 0）：缺 0 就没有基线；
- **没上报 health 的 host 一个 series 都不产**（未知 ≠ 干净）；
- agent 先发了新 reason 时落 `other` 兜底桶，而不是静默消失；
- 退役 / OFFLINE host 不进指标，且**上一轮暴露过的 child 必须被 remove**
  （停刷新 ≠ 停暴露：冻结在 registry 里的故障值是一台已失联机器的永久红灯，#2791）；
- reason 与通道两个 gauge 的差集**各自独立**——「通道有值但 reason 未上报」的 host
  不能把上一轮的 reason 留在表上（本单作者自己第一版就犯了这个错，用例即为其守卫）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.api.routes import metrics as metrics_route
from backend.models.enums import HostStatus
from backend.models.host import Host

REASON_HC_DEAD = "usb_host_controller_dead"
REASON_LINK_DEGRADED = "usb_link_degraded"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _host(db, host_id: str, *, extra: dict, status: str = HostStatus.ONLINE.value,
          retired: bool = False) -> Host:
    octet = (sum(ord(ch) for ch in host_id) % 200) + 10
    host = Host(
        id=host_id, hostname=host_id, status=status, ip=f"10.44.1.{octet}",
        ssh_user="root", ssh_port=22, extra=extra, last_heartbeat=_now(),
        created_at=_now(),
    )
    if retired:
        host.retired_at = _now()
    db.add(host)
    return host


def _health(reasons):  # noqa: ANN001 - 直给 dict，形状与 capacity_reporter 一致
    return {"health": {"status": "DEGRADED" if reasons else "HEALTHY", "reasons": reasons}}


def _line(reason: str, host_id: str) -> str:
    return f'stability_host_health_reason{{host_id="{host_id}",reason="{reason}"}}'


def _channel_line(host_id: str, state: str) -> str:
    return f'stability_host_kernel_log_channel{{host_id="{host_id}",state="{state}"}}'


def _values(body: str, prefix: str) -> float | None:
    for line in body.splitlines():
        if line.startswith(prefix + " "):
            return float(line.rsplit(" ", 1)[1])
    return None


def test_reason_present_sets_one_and_full_vocabulary_keeps_zero_baseline(
    client, db_session, monkeypatch
):
    """命中 reason 落 1，同 host 其余词表值落 0（不是缺 series）。"""
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _host(db_session, "usb-h1", extra={**_health([REASON_HC_DEAD]), "capacity": {}})
    _host(db_session, "usb-h2", extra={**_health([]), "capacity": {}})
    db_session.commit()

    body = client.get("/metrics").text
    assert _values(body, _line(REASON_HC_DEAD, "usb-h1")) == 1.0
    assert _values(body, _line(REASON_LINK_DEGRADED, "usb-h1")) == 0.0
    for reason in metrics_route._HEALTH_REASONS:
        assert _values(body, _line(reason, "usb-h2")) == 0.0, f"健康 host 的 {reason} 桶缺 0 基线"


def test_host_without_health_block_produces_no_reason_series(
    client, db_session, monkeypatch
):
    """未知 ≠ 干净：没有 health.reasons 就**不产 series**，绝不写一串 0 冒充「查过且没问题」。"""
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _host(db_session, "usb-h3", extra={"capacity": {"active_jobs": 0}})
    # **对照组同一台抓取里必须落值**：少了它，「刷新函数整段抛错」也表现为 series 缺失，
    # 本断言就会为错误的理由变绿（M2 变异实测到这个假阴性，故钉子写进用例而不是只写在注释里）。
    _host(db_session, "usb-h3-ctl", extra={**_health([REASON_HC_DEAD]), "capacity": {}})
    db_session.commit()

    body = client.get("/metrics").text
    assert _values(body, _line(REASON_HC_DEAD, "usb-h3-ctl")) == 1.0, "对照组没落值 = 刷新根本没跑"
    for reason in metrics_route._HEALTH_REASONS:
        assert _line(reason, "usb-h3") not in body


def test_unknown_agent_reason_lands_in_the_catch_all_bucket(
    client, db_session, monkeypatch
):
    """agent 先于控制面发新 reason ⇒ 进 `other`（可见），而不是因为没分桶就蒸发。"""
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _host(db_session, "usb-h4", extra={**_health(["brand_new_reason_from_agent"]), "capacity": {}})
    db_session.commit()

    body = client.get("/metrics").text
    assert _values(body, _line("other", "usb-h4")) == 1.0
    assert _values(body, _line(REASON_HC_DEAD, "usb-h4")) == 0.0


def test_kernel_log_channel_buckets_are_exclusive(client, db_session, monkeypatch):
    """#2957：ok / unavailable 各落一个 1、其余 0；**缺字段 = unknown**（老 agent ≠ 通道正常）。"""
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _host(db_session, "usb-h5", extra={**_health([]), "capacity": {"usb_kernel_log": "ok"}})
    _host(db_session, "usb-h6", extra={**_health([]), "capacity": {"usb_kernel_log": "unavailable"}})
    _host(db_session, "usb-h7", extra={**_health([]), "capacity": {}})
    db_session.commit()

    body = client.get("/metrics").text
    for host_id, wanted in (("usb-h5", "ok"), ("usb-h6", "unavailable"), ("usb-h7", "unknown")):
        for state in metrics_route._KERNEL_LOG_STATES:
            value = _values(body, _channel_line(host_id, state))
            assert value == (1.0 if state == wanted else 0.0), f"{host_id} 的 {state} 桶落值错"


def test_retired_and_offline_hosts_are_swept_out(client, db_session, monkeypatch):
    """移出在册/转 OFFLINE 后 child 必须被 **remove**（不是停刷新）。

    这正是 #2791 的形态：prometheus_client 的 label child 一旦创建就常驻 registry，
    只按当前在册集合刷新会把「主控死亡」的 1.0 冻结到进程重启为止——一台已经不
    存在容量的机器永久 firing，且基数随机器轮换单调增长。
    """
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _host(db_session, "usb-h8", extra={**_health([REASON_HC_DEAD]),
                                       "capacity": {"usb_kernel_log": "unavailable"}})
    db_session.commit()

    body = client.get("/metrics").text
    assert _values(body, _line(REASON_HC_DEAD, "usb-h8")) == 1.0

    host = db_session.get(Host, "usb-h8")
    host.status = HostStatus.OFFLINE.value
    db_session.commit()

    body = client.get("/metrics").text
    assert _line(REASON_HC_DEAD, "usb-h8") not in body, "转 OFFLINE 后故障值被冻结在 registry"
    assert _channel_line("usb-h8", "unavailable") not in body


def test_reason_children_sweep_even_when_channel_still_reports(
    client, db_session, monkeypatch
):
    """两个 gauge 的差集必须各自独立。

    反例形状（本单第一版的真错）：host 仍在 ONLINE、`capacity` 仍上报通道值，但
    `health.reasons` 不再出现——共用一份「在册 host」差集会认为它仍 live，于是上一轮
    的 `usb_host_controller_dead 1.0` 留在表上，比冻结更糟：它看起来像「还在失明」。
    """
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _host(db_session, "usb-h9", extra={**_health([REASON_HC_DEAD]),
                                       "capacity": {"usb_kernel_log": "ok"}})
    db_session.commit()
    assert _values(client.get("/metrics").text, _line(REASON_HC_DEAD, "usb-h9")) == 1.0

    host = db_session.get(Host, "usb-h9")
    host.extra = {"capacity": {"usb_kernel_log": "ok"}}  # health 块整个消失
    db_session.commit()

    body = client.get("/metrics").text
    assert _line(REASON_HC_DEAD, "usb-h9") not in body, "reason child 未随 health 块消失而被 remove"
    assert _values(body, _channel_line("usb-h9", "ok")) == 1.0, "通道 gauge 不应被牵连清空"
