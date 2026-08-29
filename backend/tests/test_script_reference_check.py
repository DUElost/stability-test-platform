"""Tests for backend/scripts/check_unreferenced_script_versions.py."""

from __future__ import annotations

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
