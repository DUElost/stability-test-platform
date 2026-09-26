"""#3159 / ADR-0038 v0.3 D9：设备面意图位（置位 / 清除 / 互斥 / 指标面）。

覆盖 §7.3 验收的可机检项：
1. 置位写状态 + fail-closed 审计；幂等（重复置位不重写 who/when/reason）；
2. 与退役互斥（D9.2）：退役机置位 409；置位机退役 409（取向=拒绝并提示）；
3. 清除 = ``emptied_at=NULL``，``emptied_by``/``emptied_reason`` 保留最近一次；
4. 指标面（D9.3）：置位 host 落 ``stability_host_device_intent{intent="emptied"} 1``；
   清除后 series 移除（#2791：label child 常驻，停刷新=冻结值）；OFFLINE host
   也暴露（意图主场景是关机 / 移机）；词表与告警选择器双向绑定。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

import backend.services.host_retirement as retirement
from backend.models.audit import AuditLog
from backend.models.host import Host

RULES = (
    Path(__file__).resolve().parents[3]
    / "deploy" / "prometheus" / "alerts-stability-platform.yml"
)


def _host(db_session, host_id: str, *, retired: bool = False, status: str = "OFFLINE") -> Host:
    host = Host(id=host_id, hostname=host_id, status=status)
    if retired:
        from datetime import datetime, timezone

        host.retired_at = datetime.now(timezone.utc)
    db_session.add(host)
    db_session.commit()
    return host


def _audit_actions(db_session, host_id: str) -> list[str]:
    db_session.expire_all()
    return [
        row.action
        for row in db_session.execute(
            select(AuditLog).where(AuditLog.resource_id == host_id)
        ).scalars()
    ]


class TestSetDeviceIntent:
    def test_set_writes_state_and_fail_closed_audit(
        self, client, db_session, admin_headers, admin_user,
    ):
        _host(db_session, "intent-h1")

        resp = client.post(
            "/api/v1/hosts/intent-h1/device-intent",
            json={"reason": "机柜撤线，设备已移机"},
            headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["emptied_at"] is not None
        assert body["emptied_by"] == admin_user.username
        assert body["emptied_reason"] == "机柜撤线，设备已移机"
        assert "set_device_intent" in _audit_actions(db_session, "intent-h1")

    def test_set_is_idempotent(self, client, db_session, admin_headers):
        _host(db_session, "intent-h2")
        first = client.post(
            "/api/v1/hosts/intent-h2/device-intent",
            json={"reason": "第一次"}, headers=admin_headers,
        ).json()

        second = client.post(
            "/api/v1/hosts/intent-h2/device-intent",
            json={"reason": "第二次"}, headers=admin_headers,
        ).json()

        assert second["emptied_at"] == first["emptied_at"], "重复置位不得重写时间戳"
        assert second["emptied_reason"] == "第一次", "重复置位不得重写 reason"
        assert _audit_actions(db_session, "intent-h2").count("set_device_intent") == 1

    def test_set_on_retired_host_is_409(self, client, db_session, admin_headers):
        _host(db_session, "intent-ret", retired=True)

        resp = client.post(
            "/api/v1/hosts/intent-ret/device-intent",
            json={"reason": "x"}, headers=admin_headers,
        )

        assert resp.status_code == 409, resp.text

    def test_set_requires_reason(self, client, db_session, admin_headers):
        _host(db_session, "intent-h3")

        resp = client.post(
            "/api/v1/hosts/intent-h3/device-intent",
            json={"reason": ""}, headers=admin_headers,
        )

        assert resp.status_code == 422, "D9.5：reason 必填（空串同样拒绝）"

    def test_set_requires_admin(self, client, db_session, auth_headers):
        _host(db_session, "intent-h4")

        resp = client.post(
            "/api/v1/hosts/intent-h4/device-intent",
            json={"reason": "x"}, headers=auth_headers,
        )

        assert resp.status_code == 403, resp.text

    def test_audit_failure_rolls_back_set(
        self, client, db_session, admin_headers, monkeypatch,
    ):
        """审计写失败 = 事务失败：意图不得落库（D9.4 fail-closed，与 retire 同级）。"""
        _host(db_session, "intent-h5")

        def _boom(*args, **kwargs):
            raise RuntimeError("audit write failed")

        monkeypatch.setattr(retirement, "record_audit", _boom)

        with pytest.raises(RuntimeError):
            client.post(
                "/api/v1/hosts/intent-h5/device-intent",
                json={"reason": "x"}, headers=admin_headers,
            )

        db_session.rollback()
        db_session.expire_all()
        assert db_session.get(Host, "intent-h5").emptied_at is None, (
            "审计失败后意图状态不得提交（fail-closed）"
        )


class TestClearDeviceIntent:
    def test_clear_keeps_last_set_trace(self, client, db_session, admin_headers):
        _host(db_session, "intent-h6")
        client.post(
            "/api/v1/hosts/intent-h6/device-intent",
            json={"reason": "撤线"}, headers=admin_headers,
        )

        resp = client.delete(
            "/api/v1/hosts/intent-h6/device-intent",
            headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["emptied_at"] is None, "清除 = emptied_at=NULL（豁免解除）"
        assert body["emptied_by"] is not None, "置位痕迹保留（与 unretire 同惯例）"
        assert body["emptied_reason"] == "撤线"
        actions = _audit_actions(db_session, "intent-h6")
        assert "clear_device_intent" in actions

    def test_clear_is_idempotent(self, client, db_session, admin_headers):
        _host(db_session, "intent-h7")
        client.post(
            "/api/v1/hosts/intent-h7/device-intent",
            json={"reason": "撤线"}, headers=admin_headers,
        )

        first = client.delete(
            "/api/v1/hosts/intent-h7/device-intent", headers=admin_headers,
        )
        second = client.delete(
            "/api/v1/hosts/intent-h7/device-intent", headers=admin_headers,
        )

        assert first.status_code == 200 and second.status_code == 200
        # 首次清除留痕；对已无意图 host 的再清除是 no-op（服务层幂等，不重复审计）
        assert _audit_actions(db_session, "intent-h7").count("clear_device_intent") == 1
        db_session.expire_all()
        assert db_session.get(Host, "intent-h7").emptied_at is None


class TestRetireIntentMutex:
    def test_retire_refuses_intent_set_host(self, client, db_session, admin_headers):
        """D9.2：置位机走 retire 取向=拒绝并提示（不隐式吃掉人工意图）。"""
        _host(db_session, "intent-h8")
        client.post(
            "/api/v1/hosts/intent-h8/device-intent",
            json={"reason": "撤线"}, headers=admin_headers,
        )

        resp = client.post(
            "/api/v1/hosts/intent-h8/retire",
            json={"retire_reason": "报废"}, headers=admin_headers,
        )

        assert resp.status_code == 409, resp.text
        db_session.expire_all()
        assert db_session.get(Host, "intent-h8").emptied_at is not None, (
            "退役被拒后意图不得被改写"
        )


class TestIntentGauge:
    def test_set_host_exposed_and_cleared_host_removed(
        self, client, db_session, admin_headers, monkeypatch,
    ):
        """D9.3 + #2791：置位 host 落值 1；清除意图后 series 必须**移除**——
        只停刷新会把陈旧豁免值冻结在 registry（与退役 host 同款冻结形态）。
        """
        monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
        _host(db_session, "intent-g1", status="ONLINE")
        client.post(
            "/api/v1/hosts/intent-g1/device-intent",
            json={"reason": "撤线"}, headers=admin_headers,
        )

        first = client.get("/metrics").text
        assert (
            'stability_host_device_intent{host_id="intent-g1",intent="emptied"} 1.0' in first
        )

        client.delete("/api/v1/hosts/intent-g1/device-intent", headers=admin_headers)

        second = client.get("/metrics").text
        assert 'stability_host_device_intent{host_id="intent-g1"' not in second, (
            "清除意图后 intent series 仍暴露——陈旧豁免值冻结（#2791）"
        )

    def test_offline_host_intent_still_exposed(self, client, db_session, monkeypatch):
        """意图主场景是关机 / 移机（host 常为 OFFLINE）——不过滤 status；
        消费方的第一子句都要求 adb series（仅 ONLINE 存在），多暴露不产生误豁免。
        """
        monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
        host = _host(db_session, "intent-g2", status="OFFLINE")
        host.emptied_at = host.created_at
        host.emptied_by = "tester"
        host.emptied_reason = "关机"
        db_session.commit()

        body = client.get("/metrics").text

        assert 'stability_host_device_intent{host_id="intent-g2",intent="emptied"} 1.0' in body

    def test_vocab_dual_binds_rule_selectors(self):
        """D9.3：词表封闭且与规则选择器双向绑定——告警里出现词表外的 intent 值
        （或词表有、规则没消费）都是漂移，当场红。
        """
        from backend.api.routes.metrics import _DEVICE_INTENTS

        assert _DEVICE_INTENTS == ("emptied",), "词表扩展必须同 PR 改规则与测试"
        rules = yaml.safe_load(RULES.read_text())
        used: set[str] = set()
        for group in rules["groups"]:
            for rule in group["rules"]:
                for match in re.finditer(
                    r"stability_host_device_intent\{[^}]*intent=\"([^\"]+)\"",
                    rule.get("expr", ""),
                ):
                    used.add(match.group(1))
        assert used == set(_DEVICE_INTENTS), (
            f"规则选择器与 _DEVICE_INTENTS 漂移：rules={sorted(used)} vocab={_DEVICE_INTENTS}"
        )
