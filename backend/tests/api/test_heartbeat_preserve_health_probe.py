"""回归测试：Agent 心跳重建 host.extra 时必须保留控制面写入的 health_probe。

背景：#2983 控制面 SSH 拓扑对账探针（host_health_probe_sweep）每 10 分钟写入
``host.extra["health_probe"]``（含 ``recent_verdicts`` 与 ``strike_open``）。
此前 ``POST /api/v1/heartbeat`` 每 5 秒重建 ``host_extra = {}`` 时只保留了
6 个 ``agent_*`` 键，导致 ``health_probe`` 在下次心跳即被擦除：
- ``recent_verdicts`` 永远凑不够 ``strike_need=2``，``strike_open`` 结构性恒为 0；
- ``stability_host_health_probe_strike`` 仅在探针后 5 秒窗内偶发出现，随后消失。
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.api.routes.metrics import _refresh_host_health_probe_strike_gauges
from backend.core.metrics import host_health_probe_strike
from backend.services.host_health_probe import (
    HEALTH_PROBE_EXTRA_KEY,
    ProbeVerdict,
    TOPOLOGY_BLIND,
    apply_probe_result_to_extra,
)


class TestHeartbeatPreserveHealthProbe:
    def test_heartbeat_preserves_control_plane_health_probe(
        self, client, sample_host, db_session,
    ):
        """探针写入后经历连续心跳，health_probe 连续窗与 strike_open 不得丢失。"""
        now = datetime(2026, 11, 23, 7, 0, tzinfo=timezone.utc)
        sample_host.extra = apply_probe_result_to_extra(
            sample_host.extra,
            verdict=ProbeVerdict.AGENT_MUTE.value,
            signals=("topology_blind",),
            topology=TOPOLOGY_BLIND,
            now=now,
            strike_need=2,
        )
        db_session.commit()

        # 第一次心跳不得擦除第 1 轮 probe 结果
        resp1 = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "extra": {"cpu_load": 0.2},
            },
        )
        assert resp1.status_code == 200, resp1.text
        db_session.refresh(sample_host)
        probe_after_hb1 = (sample_host.extra or {}).get(HEALTH_PROBE_EXTRA_KEY)
        assert isinstance(probe_after_hb1, dict)
        assert probe_after_hb1["recent_verdicts"] == [ProbeVerdict.AGENT_MUTE.value]
        assert probe_after_hb1["strike_open"] is False

        # 第二轮探针到来后可正常累积到 strike_need=2 并触发 strike_open=True
        sample_host.extra = apply_probe_result_to_extra(
            sample_host.extra,
            verdict=ProbeVerdict.AGENT_MUTE.value,
            signals=("topology_blind",),
            topology=TOPOLOGY_BLIND,
            now=now,
            strike_need=2,
        )
        db_session.commit()

        # 再次经历心跳，strike_open=True 仍须保留，且 metrics gauge 正常产出 1.0
        resp2 = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "extra": {"cpu_load": 0.3},
            },
        )
        assert resp2.status_code == 200, resp2.text
        db_session.refresh(sample_host)
        probe_after_hb2 = (sample_host.extra or {}).get(HEALTH_PROBE_EXTRA_KEY)
        assert isinstance(probe_after_hb2, dict)
        assert probe_after_hb2["recent_verdicts"] == [
            ProbeVerdict.AGENT_MUTE.value,
            ProbeVerdict.AGENT_MUTE.value,
        ]
        assert probe_after_hb2["strike_open"] is True

        _refresh_host_health_probe_strike_gauges(db_session)
        assert (
            host_health_probe_strike.labels(host_id=str(sample_host.id))._value.get()
            == 1.0
        )

    def test_agent_payload_cannot_spoof_or_overwrite_health_probe(
        self, client, sample_host, db_session,
    ):
        """health_probe 归属控制面：Agent payload 既不能伪造新值，也不能篡改既有值。"""
        # 1) 库里尚无 health_probe 时，Agent 在 extra 里注入的值不得落库
        resp1 = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "extra": {
                    HEALTH_PROBE_EXTRA_KEY: {
                        "verdict": "aligned",
                        "strike_open": False,
                    }
                },
            },
        )
        assert resp1.status_code == 200, resp1.text
        db_session.refresh(sample_host)
        assert HEALTH_PROBE_EXTRA_KEY not in (sample_host.extra or {})

        # 2) 库里已有控制面写入的 strike_open=True 时，Agent 同名字段不得覆盖
        now = datetime(2026, 11, 23, 7, 10, tzinfo=timezone.utc)
        extra = apply_probe_result_to_extra(
            sample_host.extra,
            verdict=ProbeVerdict.AGENT_MUTE.value,
            signals=("hc_dead",),
            topology=TOPOLOGY_BLIND,
            now=now,
            strike_need=1,
        )
        sample_host.extra = extra
        db_session.commit()

        resp2 = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "extra": {
                    HEALTH_PROBE_EXTRA_KEY: {
                        "verdict": "aligned",
                        "strike_open": False,
                        "recent_verdicts": ["aligned"],
                    }
                },
            },
        )
        assert resp2.status_code == 200, resp2.text
        db_session.refresh(sample_host)
        probe = (sample_host.extra or {}).get(HEALTH_PROBE_EXTRA_KEY)
        assert probe["verdict"] == ProbeVerdict.AGENT_MUTE.value
        assert probe["strike_open"] is True
