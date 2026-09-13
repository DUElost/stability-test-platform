"""主机维护窗口（#960 · R04-F17）单元测试。

覆盖：窗口判据（NULL / 已过期 / 未来 / naive 输入）、acquire 互斥与崩溃兜底、
release 的持有者校验、contextmanager 异常路径也释放。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from backend.models.enums import HostStatus
from backend.models.host import Host
from backend.services.host_maintenance import (
    HostMaintenanceConflict,
    acquire_maintenance_window,
    in_maintenance_window,
    maintenance_window,
    release_maintenance_window,
)


@pytest.fixture
def host_row(db_session):
    host = Host(
        id="h-maint-1",
        hostname="h-maint-1",
        status=HostStatus.ONLINE.value,
        ip="10.0.0.91",
    )
    db_session.add(host)
    db_session.commit()
    return host


class TestInMaintenanceWindow:
    def test_none_means_no_window(self):
        assert in_maintenance_window(None) is False

    def test_future_is_in_window(self):
        assert in_maintenance_window(
            datetime.now(timezone.utc) + timedelta(seconds=60)
        ) is True

    def test_past_is_out_of_window(self):
        assert in_maintenance_window(
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ) is False

    def test_naive_value_treated_as_utc(self):
        # SQLite 等不带时区的驱动会回传 naive datetime —— 按 UTC 解释，
        # 不能因为 tzinfo 缺失就把窗口判成失效
        assert in_maintenance_window(
            datetime.utcnow() + timedelta(seconds=60)
        ) is True


class TestAcquireRelease:
    def test_cached_host_cannot_overwrite_another_session_window(self, db_session, host_row):
        with Session(db_session.get_bind(), expire_on_commit=False) as contender:
            cached = contender.get(Host, host_row.id)
            assert cached.maintenance_until is None
            assert acquire_maintenance_window(db_session, host_row.id, "ui:owner")
            assert not acquire_maintenance_window(contender, host_row.id, "ui:contender")
            assert cached.maintenance_holder == "ui:owner"
        db_session.refresh(host_row)
        assert host_row.maintenance_holder == "ui:owner"

    def test_cached_holder_cannot_release_successor_window(self, db_session, host_row):
        acquire_maintenance_window(db_session, host_row.id, "ui:first")
        with Session(db_session.get_bind(), expire_on_commit=False) as stale_session:
            cached = stale_session.get(Host, host_row.id)
            assert cached.maintenance_holder == "ui:first"
            release_maintenance_window(db_session, host_row.id, "ui:first")
            acquire_maintenance_window(db_session, host_row.id, "ui:second")
            release_maintenance_window(stale_session, host_row.id, "ui:first")
        db_session.refresh(host_row)
        assert host_row.maintenance_holder == "ui:second"
        assert in_maintenance_window(host_row.maintenance_until)

    def test_holderless_window_is_not_owned_by_arbitrary_caller(self, db_session, host_row):
        host_row.maintenance_until = datetime.now(timezone.utc) + timedelta(seconds=60)
        host_row.maintenance_holder = ""
        db_session.commit()
        release_maintenance_window(db_session, host_row.id, "ui:other")
        db_session.refresh(host_row)
        assert in_maintenance_window(host_row.maintenance_until)

    def test_acquire_sets_window_and_release_clears(self, db_session, host_row):
        assert acquire_maintenance_window(db_session, host_row.id, "ui:tester") is True
        db_session.refresh(host_row)
        assert in_maintenance_window(host_row.maintenance_until) is True
        assert host_row.maintenance_holder == "ui:tester"

        release_maintenance_window(db_session, host_row.id, "ui:tester")
        db_session.refresh(host_row)
        assert host_row.maintenance_until is None

    def test_second_acquire_rejected_while_window_live(self, db_session, host_row):
        assert acquire_maintenance_window(db_session, host_row.id, "ui:a") is True
        assert acquire_maintenance_window(db_session, host_row.id, "ui:b") is False
        db_session.refresh(host_row)
        assert host_row.maintenance_holder == "ui:a"

    def test_expired_window_can_be_taken_over(self, db_session, host_row):
        host_row.maintenance_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        host_row.maintenance_holder = "ui:crashed"
        db_session.commit()
        assert acquire_maintenance_window(db_session, host_row.id, "ui:b") is True

    def test_acquire_unknown_host(self, db_session):
        assert acquire_maintenance_window(db_session, "nope", "ui:a") is False

    def test_release_by_wrong_holder_is_ignored(self, db_session, host_row):
        acquire_maintenance_window(db_session, host_row.id, "ui:a")
        release_maintenance_window(db_session, host_row.id, "ui:b")
        db_session.refresh(host_row)
        assert in_maintenance_window(host_row.maintenance_until) is True


class TestMaintenanceWindowContext:
    def test_releases_on_success(self, db_session, host_row):
        with maintenance_window(db_session, host_row.id, "ui:a"):
            pass
        db_session.refresh(host_row)
        assert host_row.maintenance_until is None

    def test_releases_on_exception(self, db_session, host_row):
        with pytest.raises(RuntimeError):
            with maintenance_window(db_session, host_row.id, "ui:a"):
                raise RuntimeError("boom")
        db_session.refresh(host_row)
        assert host_row.maintenance_until is None

    def test_conflict_raises(self, db_session, host_row):
        with maintenance_window(db_session, host_row.id, "ui:a"):
            with pytest.raises(HostMaintenanceConflict):
                with maintenance_window(db_session, host_row.id, "ui:b"):
                    pass
