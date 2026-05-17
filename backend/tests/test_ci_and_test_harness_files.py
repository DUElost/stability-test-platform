from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_ci_workflow_runs_agent_tests_frontend_vitest_and_uses_lockfile():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "npm ci" in workflow
    assert "python -m pytest backend/tests/" in workflow
    assert "python -m pytest backend/agent/tests/" in workflow
    assert "npx vitest run" in workflow


def test_backend_conftest_uses_postgres_testcontainers_not_sqlite_fallback():
    conftest = (ROOT / "backend" / "tests" / "conftest.py").read_text(encoding="utf-8")

    assert "ALLOW_SQLITE_TESTS" not in conftest
    assert "PostgresContainer" in conftest
