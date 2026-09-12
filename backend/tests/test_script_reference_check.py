"""Tests for backend/scripts/check_unreferenced_script_versions.py."""

from __future__ import annotations

import pytest

from backend.models.plan import Plan, PlanStep
from backend.models.script import Script
from backend.scripts.check_unreferenced_script_versions import compute_reference_counts


def _seed(db_session) -> None:
    """flash_firmware: v1.3.5 被 plan_step 引用；v1.3.6 零引用；v1.3.4 零引用且已退役。"""
    db_session.add_all(
        [
            Script(
                name="flash_firmware",
                script_type="python",
                version="v1.3.4",
                nfs_path="/s/ff",
                content_sha256="a",
                is_active=False,
            ),
            Script(
                name="flash_firmware",
                script_type="python",
                version="v1.3.5",
                nfs_path="/s/ff",
                content_sha256="b",
                is_active=True,
            ),
            Script(
                name="flash_firmware",
                script_type="python",
                version="v1.3.6",
                nfs_path="/s/ff",
                content_sha256="c",
                is_active=True,
            ),
        ]
    )
    plan = Plan(name="ref-check")
    db_session.add(plan)
    db_session.flush()
    db_session.add(
        PlanStep(
            plan_id=plan.id,
            step_key="init_flash",
            script_name="flash_firmware",
            script_version="v1.3.5",
            stage="init",
            sort_order=0,
            retry=0,
        )
    )
    db_session.commit()


def test_counts_by_script_version(db_session):
    _seed(db_session)
    rows = {(r["name"], r["version"]): r for r in compute_reference_counts(db_session)}
    assert rows[("flash_firmware", "v1.3.5")]["refs"] == 1
    assert rows[("flash_firmware", "v1.3.6")]["refs"] == 0
    assert rows[("flash_firmware", "v1.3.4")]["refs"] == 0
    assert rows[("flash_firmware", "v1.3.4")]["is_active"] is False


def test_retirement_candidates_only_active_zero_ref(db_session):
    _seed(db_session)
    candidates = {
        (r["name"], r["version"])
        for r in compute_reference_counts(db_session)
        if r["refs"] == 0 and r["is_active"]
    }
    assert ("flash_firmware", "v1.3.6") in candidates
    assert ("flash_firmware", "v1.3.4") not in candidates  # 已退役的不重复报
    assert ("flash_firmware", "v1.3.5") not in candidates  # 有引用不报


def test_main_normalizes_async_url_before_sync_engine(monkeypatch):
    """#735 §1.3 回归：main() 必须把异步驱动 URL 归一化后再交给同步 create_engine。

    未归一化时生产库形态（postgresql+asyncpg://）会在首次连接抛 MissingGreenlet——
    诊断工具自身崩溃，退役闭环无从开始。此处只断言交给 create_engine 的 URL，
    不建立真实连接。
    """
    from backend.scripts import check_unreferenced_script_versions as mod

    captured: dict[str, str] = {}

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *a, **kw):
            return None

    class _FakeEngine:
        def connect(self):
            return _FakeConn()

        def dispose(self):
            pass

    def _fake_create_engine(url):
        captured["url"] = url
        return _FakeEngine()

    monkeypatch.setattr(mod, "create_engine", _fake_create_engine)
    monkeypatch.setattr(
        mod,
        "resolve_database_url",
        lambda: ("postgresql+asyncpg://u:p@h:5432/db", "test"),
    )
    monkeypatch.setattr(mod, "compute_reference_counts", lambda conn: [])

    assert mod.main([]) == 0
    assert captured["url"] == "postgresql+psycopg://u:p@h:5432/db"
    assert "+asyncpg" not in captured["url"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("postgresql+asyncpg://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("sqlite+aiosqlite:///:memory:", "sqlite:///:memory:"),
    ],
)
def test_normalize_sync_database_url_covers_production_forms(raw, expected):
    from backend.core.database import normalize_sync_database_url

    assert normalize_sync_database_url(raw) == expected
