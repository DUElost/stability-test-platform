"""#3723 U2：disarm_results 迁移挂在 head 链上。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "backend" / "alembic" / "versions" / "c3d4e5f6a7b8_add_job_disarm_results_3727.py"


def test_migration_file_exists_and_revisions():
    text = MIG.read_text(encoding="utf-8")
    assert 'revision = "c3d4e5f6a7b8"' in text
    assert 'down_revision = "b2c3d4e5f6a7"' in text
    assert "disarm_results" in text
    assert "job_instance" in text
    assert "downgrade" in text


def test_model_has_disarm_results_column():
    from backend.models.job import JobInstance
    assert "disarm_results" in JobInstance.__table__.c
