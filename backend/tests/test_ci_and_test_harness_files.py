from __future__ import annotations

from pathlib import Path

from tools.dev.source_anchor import SourceGuard

ROOT = Path(__file__).resolve().parents[2]

#: 锚点编在**取代被禁形态的那一样东西**上（#2639 第七批）：真容器夹具取代 sqlite 兜底、
#: 人工测试清单取代已下线的 PR Agent 文案。
_CONFTEST_REL = "backend/tests/conftest.py"
_POSTGRES_CONTAINER_ANCHOR = "PostgresContainer"
_PR_TEMPLATE_REL = ".github/pull_request_template.md"
_MANUAL_TEST_CHECKLIST_ANCHOR = "## 测试"


def test_ci_workflow_runs_agent_tests_frontend_vitest_and_uses_lockfile():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "npm ci" in workflow
    assert "python -m pytest backend/tests/" in workflow
    assert "python -m pytest backend/agent/tests/" in workflow
    assert "npx vitest run" in workflow


def test_backend_conftest_uses_postgres_testcontainers_not_sqlite_fallback():
    conftest = (ROOT / "backend" / "tests" / "conftest.py").read_text(encoding="utf-8")

    SourceGuard.of_repo_path(_CONFTEST_REL).anchored(_POSTGRES_CONTAINER_ANCHOR).assert_absent(
        "ALLOW_SQLITE_TESTS",
        why="sqlite 兜底会让 PG 专属行为（JSONB/表达式索引/约束名）在 CI 里静默不测",
    )
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


def test_pr_template_does_not_advertise_retired_pr_agent():
    """PR Agent advisory review 已下线：模板不得再引导 /review 或承诺 AI 审查。"""
    guard = SourceGuard.of_repo_path(_PR_TEMPLATE_REL).anchored(_MANUAL_TEST_CHECKLIST_ANCHOR)
    for retired in ("PR-Agent", "PR Agent", "/review", "security concerns 会阻断合入"):
        guard.assert_absent(
            retired,
            why="PR Agent advisory review 已下线：模板再引导它会让作者把安全性交给不存在的审查",
        )
