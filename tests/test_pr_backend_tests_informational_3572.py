"""#3572：`backend-test` 的信息性 PR 侧 job 的接线契约。

背景：#1525 的前移触发规则（同类夜间红灯 ≥2 次 → 评估前移）已响 4 次且全部经重跑
判定为确定性缺陷（#2628/#2970/#3060/#3247）。2026-10-01 评估结论是**信息性前移**、
**不转 required**——决定性理由是 #2333 事实 3（backend-test 类红灯混有 flake）与其
边界第三条（flake 占比至今无法断言）：让 flake 率未知的检查阻塞合入＝把 flakiness
引进合入路径。

本 job 的存在意义是**采集 PR 路径上的 flake 率**（转 required 的必要输入，现在没有）。
故除了「它在」，更要钉住「它没被误升格为阻塞」「它没把红吞掉」「它不会静默无结论」。

这些是**接线契约**断言——真跑起来要几十分钟，CI 不适合验证；但「形态被改坏」是
随时可能发生且后果安静的（信息性变阻塞＝PR 路径随机红；continue-on-error 加上去
＝核心指标永久失真），故用静态断言钉住。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CI = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_JOB = "pr-backend-tests-informational"

# main 分支保护的 required 集合（`gh api .../branches/main/protection` 实测）。
# 本 job **不得**进入这个集合——那是 #3572 评估的核心结论。
_REQUIRED_BASELINE = {
    "lint", "CodeQL", "pr-typecheck", "pr-compileall",
    "pr-agent-tests", "pr-migrate-empty-db",
}


@pytest.fixture(scope="module")
def ci() -> dict:
    return yaml.safe_load(_CI.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def job(ci: dict) -> dict:
    assert _JOB in ci["jobs"], f"ci.yml 缺信息性 job {_JOB}"
    return ci["jobs"][_JOB]


def test_job_exists_and_is_pr_only(job: dict):
    """只跑 PR 路径；夜间全量已有 backend-test，不重复。"""
    assert job.get("if") == "github.event_name == 'pull_request'"


def test_job_runs_the_same_suite_as_backend_test(ci: dict, job: dict):
    """跑的命令必须与全量 backend-test 的 backend 步骤同形，否则采到的不是同一个数据。"""
    full = [
        s for s in ci["jobs"]["backend-test"]["steps"]
        if s.get("name") == "Run backend tests"
    ]
    info = [s for s in job["steps"] if s.get("name") == "Run backend tests"]
    assert full and info, "两侧都必须有 Run backend tests 步骤"
    assert full[0]["run"].strip() == info[0]["run"].strip(), (
        f"信息性 job 与全量跑的集合不一致：{info[0]['run']!r} vs {full[0]['run']!r}"
    )


def test_job_is_not_required(job: dict):
    """**不设** continue-on-error：信息性体现在它不在 required 集合里，不体现为把红吞掉。

    吞掉会让「PR 路径上到底红过几次」这个核心指标永久失真——而它正是本 job 存在的理由。
    """
    assert "continue-on-error" not in job, "信息性 job 不得吞掉红灯"
    for step in job["steps"]:
        assert "continue-on-error" not in step, "信息性 job 的步骤不得吞掉红灯"


def test_job_is_not_in_required_baseline():
    """required 基线是分支保护的事实；本 job 不在其中（静态对照，防误升格）。"""
    assert _JOB not in _REQUIRED_BASELINE
    assert not (_REQUIRED_BASELINE & {_JOB})


def test_timeout_has_headroom_for_observed_jitter(job: dict):
    """超时取 90min 而非 60min：#3576 实测存在 3.2 倍环境抖动。

    09-26 backend tests 50m32s vs 常态 16m00s（测试数基本相同），60 会把 job 顶穿并
    让后续步骤静默不跑。锁 60 会在抖动复现时重新踩坑。
    """
    assert job.get("timeout-minutes") == 90, (
        f"信息性 job 超时应为 90（#3576 实测 3.2 倍抖动），实为 {job.get('timeout-minutes')}"
    )


def test_database_url_not_at_job_level(job: dict):
    """job 级**不得**设 DATABASE_URL（#1547 的 db_url_guard 会在导入期拒绝同库配置）。"""
    env = job.get("env", {})
    assert "DATABASE_URL" not in env, "DATABASE_URL 必须步骤级注入，见 backend-test 同款注释"
    assert "TEST_DATABASE_URL" in env


def test_migration_step_injects_database_url(job: dict):
    """迁移步骤必须显式注入 DATABASE_URL，否则 alembic 无库可迁。"""
    steps = {s.get("name"): s for s in job["steps"]}
    migrate = steps.get("Migrate empty PostgreSQL database")
    assert migrate is not None, "缺迁移步骤"
    assert migrate.get("env", {}).get("DATABASE_URL"), "迁移步骤未注入 DATABASE_URL"
