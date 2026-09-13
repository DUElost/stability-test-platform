"""#1804 / ADR-0038 ③：退役主机在读面的过滤（列表 / 统计 / metrics / AI 读面）。

覆盖 issue 验收：
1. 每面反例实证（临时移除过滤 → 对应用例转红，命令与结果见 PR/Agent Note）；
2. 统计面断言**具体计数值**（非泛型 group_by 存在性）；
3. `include_retired` 契约（默认隐藏 / 显式显示 / 分页计数一致）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.models.enums import JobStatus
from backend.models.host import Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services.ai_assistant.tools import _q_hosts, _q_platform_health

_NOW = datetime.now(timezone.utc)


def _host(
    db_session,
    host_id: str,
    *,
    status: str = "ONLINE",
    retired: bool = False,
    heartbeat_age_s: int = 5,
) -> Host:
    host = Host(
        id=host_id,
        hostname=host_id,
        status=status,
        last_heartbeat=_NOW - timedelta(seconds=heartbeat_age_s),
        retired_at=_NOW if retired else None,
        retired_by="admin" if retired else None,
        retire_reason="样机报废" if retired else None,
        watcher_admin_active=True,
    )
    db_session.add(host)
    db_session.commit()
    return host


class TestHostList:
    def test_retired_hidden_by_default(self, client, db_session, admin_headers):
        _host(db_session, "rf-active")
        _host(db_session, "rf-retired", retired=True)

        resp = client.get(
            "/api/v1/hosts", params={"skip": 0, "limit": 50}, headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [item["id"] for item in body["items"]] == ["rf-active"]
        assert body["total"] == 1

    def test_include_retired_shows_both(self, client, db_session, admin_headers):
        _host(db_session, "rf2-active")
        _host(db_session, "rf2-retired", retired=True)

        resp = client.get(
            "/api/v1/hosts",
            params={"skip": 0, "limit": 50, "include_retired": "true"},
            headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert {item["id"] for item in body["items"]} == {"rf2-active", "rf2-retired"}
        assert body["total"] == 2
        retired = next(i for i in body["items"] if i["id"] == "rf2-retired")
        assert retired["retired_at"] is not None
        assert retired["retire_reason"] == "样机报废"

    def test_pagination_total_matches_filter(self, client, db_session, admin_headers):
        _host(db_session, "rf3-a")
        _host(db_session, "rf3-r1", retired=True)
        _host(db_session, "rf3-r2", retired=True)

        resp = client.get(
            "/api/v1/hosts", params={"skip": 0, "limit": 1}, headers=admin_headers,
        )

        body = resp.json()
        assert body["total"] == 1, "total 必须与默认过滤同口径（否则分页器谎报）"
        assert len(body["items"]) == 1

    def test_detail_stays_visible_when_retired(self, client, db_session, admin_headers):
        """详情面保留可见（含 D6 身份字段）——退役是历史终态，要能查痕迹。"""
        _host(db_session, "rf-detail", retired=True)

        resp = client.get("/api/v1/hosts/rf-detail", headers=admin_headers)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["retired_at"] is not None
        assert "boot_id" in body and "agent_instance_id" in body


class TestStatsFaces:
    def test_file_server_active_hosts_exclude_retired(
        self, client, db_session, admin_headers, monkeypatch,
    ):
        """file-server 的 active_hosts 口径 = 在线 ∧ 心跳新鲜 ∧ 未退役。

        以「捕获传给 monitor 的主机列表」为断言对象；stub 抛 RuntimeError 让
        端点走 503 分支（本用例不关心响应，只验过滤）。
        """
        from backend.api.routes import stats as stats_module

        _host(db_session, "fs-active")
        _host(db_session, "fs-retired", retired=True)
        captured: list[str] = []

        def _capture(hosts, *, hours=6):
            captured.extend(h.id for h in hosts)
            raise RuntimeError("stub")

        monkeypatch.setattr(stats_module, "collect_file_server_overview", _capture)
        resp = client.get("/api/v1/stats/file-server", headers=admin_headers)
        assert resp.status_code == 503  # stub 产物，非被测行为

        assert captured == ["fs-active"]

    def test_dashboard_summary_counts_exclude_retired(
        self, client, db_session, admin_headers,
    ):
        _host(db_session, "ds-active-online")
        _host(db_session, "ds-active-offline", status="OFFLINE")
        _host(db_session, "ds-retired-online", retired=True)

        resp = client.get("/api/v1/stats/dashboard-summary", headers=admin_headers)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        hosts = body["hosts"]
        assert hosts["total"] == 2, "退役主机不得计入仪表板容量口径"
        assert hosts["online"] == 1
        assert hosts["offline"] == 1

    def test_host_failure_rate_keeps_retired_history(
        self, client, db_session, admin_headers,
    ):
        """裁决 D-5：历史 KPI 保留退役主机（历史事实 ≠ 当前容量）。"""
        from backend.models.host import Device

        host = _host(db_session, "fr-retired", retired=True)
        device = Device(serial="fr-dev", host_id=host.id, status="OFFLINE")
        db_session.add(device)
        db_session.flush()
        plan = Plan(name="fr-plan")
        db_session.add(plan)
        db_session.flush()
        run = PlanRun(
            plan_id=plan.id, status="FAILED", plan_snapshot={}, run_type="MANUAL",
            started_at=_NOW - timedelta(days=1),
        )
        db_session.add(run)
        db_session.flush()
        db_session.add(JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
            host_id=host.id, status=JobStatus.FAILED.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            started_at=_NOW - timedelta(days=1),
        ))
        db_session.commit()

        resp = client.get("/api/v1/stats/host-failure-rate", headers=admin_headers)

        assert resp.status_code == 200, resp.text
        assert "fr-retired" in [item["host_id"] for item in resp.json()["items"]]


class TestPrometheusGauge:
    def test_fleet_gauge_excludes_retired(self, db_session, monkeypatch):
        import backend.api.routes.metrics as metrics_module

        _host(db_session, "pg-active")
        _host(db_session, "pg-retired", retired=True)

        monkeypatch.setattr(metrics_module, "is_prometheus_available", lambda: True)
        metrics_module._refresh_fleet_gauges(db_session)

        assert metrics_module.host_online.labels(status="online")._value.get() == 1


class TestAiReadFaces:
    def test_platform_health_counts_exclude_retired(self, db_session):
        _host(db_session, "ai-active")
        _host(db_session, "ai-retired", retired=True)

        output = _q_platform_health(db_session, {})

        assert "'ONLINE': 1" in output, output

    def test_host_list_excludes_retired(self, db_session):
        _host(db_session, "ail-active")
        _host(db_session, "ail-retired", retired=True)

        output = _q_hosts(db_session, {})

        assert "ail-active" in output
        assert "ail-retired" not in output
