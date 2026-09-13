"""#1801 / ADR-0038 ②：退役 / 解除退役 API（Cordon 前置 + 审计 fail-closed）。

覆盖 issue 验收：
1. 活跃 Job / QUEUED 引用 → 409；幂等；unretire 写回；reason 校验；审计失败回滚；
2. retire × claim 同行锁序（用「他人持 host 行锁 → retire 阻塞」确定性验证）；
3. 反例实证见 PR/Agent Note（移除前置、移除 strict 审计各使对应用例转红）。
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session as SASession

from backend.models.audit import AuditLog
from backend.models.enums import JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.services.host_retirement import retire_host

_PIPELINE = {"lifecycle": {"init": [], "teardown": []}}


def _host(db_session, host_id: str, *, status: str = "OFFLINE") -> Host:
    host = Host(id=host_id, hostname=host_id, status=status)
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


def _inflight_run(db_session, host_id: str, *, status: str = "QUEUED") -> PlanRun:
    plan = Plan(name=f"plan-{host_id}-{status}")
    db_session.add(plan)
    db_session.flush()
    run = PlanRun(plan_id=plan.id, status=status, plan_snapshot={}, run_type="MANUAL")
    db_session.add(run)
    db_session.flush()
    db_session.add(PlanRunHost(plan_run_id=run.id, host_id=host_id))
    db_session.commit()
    return run


def _active_job(db_session, host_id: str) -> JobInstance:
    """造一个活跃 Job；PlanRun 用 SUCCESS 以免同时触发「在途 Run」判据（隔离断言）。"""
    plan = Plan(name=f"plan-job-{host_id}")
    db_session.add(plan)
    db_session.flush()
    run = PlanRun(
        plan_id=plan.id, status="SUCCESS", plan_snapshot={}, run_type="MANUAL",
    )
    db_session.add(run)
    db_session.flush()
    device = Device(serial=f"dev-{host_id}", host_id=host_id, status="ONLINE")
    db_session.add(device)
    db_session.flush()
    job = JobInstance(
        plan_run_id=run.id, plan_id=plan.id, device_id=device.id, host_id=host_id,
        status=JobStatus.RUNNING.value, pipeline_def=_PIPELINE,
    )
    db_session.add(job)
    db_session.commit()
    return job


class TestRetire:
    def test_retire_writes_state_and_fail_closed_audit(
        self, client, db_session, admin_headers, admin_user,
    ):
        host = _host(db_session, "ret-h1")

        resp = client.post(
            "/api/v1/hosts/ret-h1/retire",
            json={"retire_reason": "样机报废"},
            headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["retired_at"] is not None
        assert body["retired_by"] == admin_user.username
        assert body["retire_reason"] == "样机报废"

        db_session.expire_all()
        row = db_session.get(Host, host.id)
        assert row.retired_at is not None
        assert row.retire_reason == "样机报废"
        # 审计是事件真源：who/when/reason + before/after 快照
        audit = db_session.execute(
            select(AuditLog).where(AuditLog.resource_id == host.id)
        ).scalars().one()
        assert audit.action == "retire_host"
        assert audit.details["reason"] == "样机报废"
        assert audit.details["before"]["retired_at"] is None
        assert audit.details["after"]["retired_at"] is not None
        assert set(audit.details["before"]) >= {"boot_id", "agent_instance_id"}

    def test_retire_blocked_by_active_job(self, client, db_session, admin_headers):
        _host(db_session, "ret-h2")
        _active_job(db_session, "ret-h2")

        resp = client.post(
            "/api/v1/hosts/ret-h2/retire",
            json={"retire_reason": "报废"}, headers=admin_headers,
        )

        assert resp.status_code == 409, resp.text
        assert "活跃 Job" in resp.json()["detail"]
        db_session.expire_all()
        assert db_session.get(Host, "ret-h2").retired_at is None

    @pytest.mark.parametrize("run_status", ["QUEUED", "PRECHECK", "RUNNING"])
    def test_retire_blocked_by_inflight_plan_run(
        self, client, db_session, admin_headers, run_status,
    ):
        host_id = f"ret-{run_status.lower()}"
        _host(db_session, host_id)
        _inflight_run(db_session, host_id, status=run_status)

        resp = client.post(
            f"/api/v1/hosts/{host_id}/retire",
            json={"retire_reason": "报废"}, headers=admin_headers,
        )

        assert resp.status_code == 409, resp.text
        assert "在途 PlanRun" in resp.json()["detail"]

    def test_retire_is_idempotent(self, client, db_session, admin_headers):
        host = _host(db_session, "ret-h3")

        first = client.post(
            "/api/v1/hosts/ret-h3/retire",
            json={"retire_reason": "第一次"}, headers=admin_headers,
        )
        second = client.post(
            "/api/v1/hosts/ret-h3/retire",
            json={"retire_reason": "第二次"}, headers=admin_headers,
        )

        assert first.status_code == second.status_code == 200
        assert first.json()["retired_at"] == second.json()["retired_at"]
        # 幂等不重写历史：reason 保持首次值，且只有一条审计
        db_session.expire_all()
        assert db_session.get(Host, host.id).retire_reason == "第一次"
        assert _audit_actions(db_session, host.id) == ["retire_host"]

    def test_retire_reason_required(self, client, db_session, admin_headers):
        _host(db_session, "ret-h4")

        resp = client.post(
            "/api/v1/hosts/ret-h4/retire",
            json={"retire_reason": ""}, headers=admin_headers,
        )

        assert resp.status_code == 422, resp.text

    def test_retire_requires_admin(self, client, db_session, auth_headers):
        _host(db_session, "ret-h5")

        resp = client.post(
            "/api/v1/hosts/ret-h5/retire",
            json={"retire_reason": "报废"}, headers=auth_headers,
        )

        assert resp.status_code == 403, resp.text


class TestUnretire:
    def test_unretire_clears_flag_and_keeps_last_trace(
        self, client, db_session, admin_headers, admin_user,
    ):
        host = _host(db_session, "unret-h1")
        client.post(
            "/api/v1/hosts/unret-h1/retire",
            json={"retire_reason": "误退役"}, headers=admin_headers,
        )

        resp = client.post(
            "/api/v1/hosts/unret-h1/unretire",
            json={"retire_reason": "恢复使用"}, headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["retired_at"] is None
        # 最近一次退役痕迹保留（谁/为何）
        assert body["retired_by"] == admin_user.username
        assert body["retire_reason"] == "误退役"
        assert _audit_actions(db_session, host.id) == ["retire_host", "unretire_host"]

    def test_unretire_has_no_precondition(self, client, db_session, admin_headers):
        """unretire 无前置：在途 Run 也要能解除（退役→恢复是纠错路径）。"""
        host_id = "unret-h2"
        _host(db_session, host_id)
        client.post(
            f"/api/v1/hosts/{host_id}/retire",
            json={"retire_reason": "退役"}, headers=admin_headers,
        )
        _inflight_run(db_session, host_id, status="RUNNING")

        resp = client.post(
            f"/api/v1/hosts/{host_id}/unretire",
            json={"retire_reason": "恢复"}, headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["retired_at"] is None

    def test_unretire_is_idempotent(self, client, db_session, admin_headers):
        host = _host(db_session, "unret-h3")

        resp = client.post(
            "/api/v1/hosts/unret-h3/unretire",
            json={"retire_reason": "本就在用"}, headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["retired_at"] is None
        assert _audit_actions(db_session, host.id) == []


class TestAuditFailClosed:
    def test_audit_failure_rolls_back_retire(
        self, client, db_session, admin_headers, monkeypatch,
    ):
        """审计写失败 = 事务失败：退役状态不得落库（D2 fail-closed）。"""
        host = _host(db_session, "ret-h6")

        import backend.services.host_retirement as retirement

        def _boom(*args, **kwargs):
            raise RuntimeError("audit write failed")

        monkeypatch.setattr(retirement, "record_audit", _boom)

        with pytest.raises(RuntimeError):
            client.post(
                "/api/v1/hosts/ret-h6/retire",
                json={"retire_reason": "报废"}, headers=admin_headers,
            )

        db_session.rollback()
        db_session.expire_all()
        assert db_session.get(Host, host.id).retired_at is None, (
            "审计失败后退役状态不得提交（fail-closed）"
        )


class TestRecordAuditStrict:
    """`record_audit(strict=True)` 通道：缺表不再降级（#1801 的 fail-closed 底座）。"""

    class _FakeSession:
        def __init__(self) -> None:
            self.expunged = False

        def begin_nested(self):
            class _Ctx:
                def __enter__(self_):
                    return self_

                def __exit__(self_, *exc):
                    return False

            return _Ctx()

        def add(self, entry) -> None:
            self.entry = entry

        def flush(self) -> None:
            from sqlalchemy.exc import ProgrammingError

            raise ProgrammingError(
                "SELECT", {},
                Exception('relation "audit_logs" does not exist'),
            )

        def expunge(self, entry) -> None:
            self.expunged = True

    def test_strict_reraises_missing_table(self):
        from backend.core.audit import record_audit

        with pytest.raises(Exception, match="audit_logs"):
            record_audit(
                self._FakeSession(), action="retire_host", resource_type="host",
                resource_id="h1", strict=True,
            )

    def test_default_still_degrades_on_missing_table(self):
        from backend.core.audit import record_audit

        session = self._FakeSession()
        assert record_audit(
            session, action="retire_host", resource_type="host", resource_id="h1",
        ) is None


class TestLockOrder:
    def test_retire_waits_on_host_row_lock_like_claim(self, db_session, engine):
        """retire 与 claim 同序：先取 host 行锁。

        用「独立连接持 host 行 FOR UPDATE → retire 在 lock_timeout 内无法完成」
        证明其确实竞争同一把行锁（claim 的 `_claim_jobs_for_host` 同样先锁
        host 行，见 agent_api.py:396-401）；释放后同一调用成功，构成正对照。
        """
        host = _host(db_session, "ret-lock")
        errors: list[Exception] = []

        def _try_retire() -> None:
            with SASession(engine) as session:
                session.execute(text("SET LOCAL lock_timeout = '300ms'"))
                try:
                    retire_host(
                        session, host_id=host.id, reason="并发测试",
                        actor_id=None, actor_username="tester",
                    )
                except Exception as exc:  # noqa: BLE001 - 断言锁超时
                    errors.append(exc)

        with engine.connect() as holder:
            holder.execute(text("BEGIN"))
            holder.execute(
                text("SELECT id FROM host WHERE id = :h FOR UPDATE"), {"h": host.id}
            )
            thread = threading.Thread(target=_try_retire)
            thread.start()
            thread.join(timeout=10)
            holder.execute(text("ROLLBACK"))

        assert errors, "持锁期间 retire 必须阻塞（否则未取 host 行锁，与 claim 不同序）"
        assert "lock" in str(errors[0]).lower() or "timeout" in str(errors[0]).lower()

        # 正对照：锁释放后同一调用成功落库
        with SASession(engine) as session:
            retire_host(
                session, host_id=host.id, reason="并发测试",
                actor_id=None, actor_username="tester",
            )
        db_session.expire_all()
        assert db_session.get(Host, host.id).retired_at is not None
