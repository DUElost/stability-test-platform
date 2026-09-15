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


def test_testing_doc_does_not_advertise_sqlite_fallback():
    """#1299/#2041: 开发文档不得宣传 ALLOW_SQLITE_TESTS 退路——conftest 已无该开关。

    #2041 前只守 `testing.md`（#1299 修了它却漏掉同族的 local-development /
    environment-variables），故范围扩到整个 `docs/development/`。
    """
    docs_dir = ROOT / "docs" / "development"
    offending = sorted(
        str(p.relative_to(ROOT))
        for p in docs_dir.rglob("*.md")
        if "ALLOW_SQLITE_TESTS" in p.read_text(encoding="utf-8")
    )
    assert offending == []
    assert "testcontainers" in (docs_dir / "testing.md").read_text(encoding="utf-8")


def test_pr_template_pr_agent_wording_matches_advisory_semantics():
    """#1299: PR 模板必须与 pr-agent.yml 顾问语义一致（非 required check、不阻断合入）。"""
    template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")

    assert "不阻断合入" in template
    assert "security concerns 会阻断合入" not in template
