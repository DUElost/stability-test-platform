"""后端静态守卫必须在 PR 路径真跑（接线结构守卫，2026-09-26 PR 审计后续）。

背景：`backend-test` 只在夜间兜底跑；#3421 新增审计 action 未登记保留分层，
`backend/tests/test_audit_action_retention_guard_3017.py` 在 main 上红到人工发现（#3423）。
owner 裁决：PR 上不跑全量 backend/tests，只把**静态守卫**（AST / 结构 / 登记表断言）
接进有 PG service 的 PR job（`pr-migrate-empty-db`，conftest 会话级夹具需要 PG）。

覆盖判据：`backend/tests/` 下文件名含 `guard` 的 `test_*.py`——新增同名守卫却不接线，
PR 即红。与 `tests/test_lock_order_pr_path_contract.py` 同模式，共用
`tests/ci_workflow_probe.py` 的「真实执行者」判据（注释行不算）。

纯离线：只读文本，不起容器、不连库（`tests/` 准入判据）。
"""
from __future__ import annotations

from pathlib import Path

from tests import ci_workflow_probe as probe

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_TESTS = REPO_ROOT / "backend" / "tests"

_NAME_MARKER = "guard"


def _static_guard_files() -> list[str]:
    return sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in BACKEND_TESTS.rglob("test_*.py")
        if _NAME_MARKER in p.name
    )


def _pr_pytest_steps() -> list[tuple[str, dict, dict]]:
    jobs = probe.load_jobs()
    return [
        (name, jobs[name], step)
        for name, _index, step in probe.steps_running("pytest", only_pr=True)
    ]


def test_static_guard_files_are_found():
    files = _static_guard_files()
    assert "backend/tests/test_audit_action_retention_guard_3017.py" in files, (
        f"按文件名含 {_NAME_MARKER!r} 未找到 #3017 保留分层守卫（得到 {files}）——"
        "命名约定或扫描逻辑已被改动，本守卫会静默失去覆盖"
    )


def test_every_static_guard_runs_in_pr_path():
    steps = _pr_pytest_steps()
    assert steps, "PR 路径里没有任何 pytest 步骤，解析器已失效"
    missing = [
        rel for rel in _static_guard_files()
        if not any(rel in probe.code_text(step) for _name, _job, step in steps)
    ]
    assert not missing, (
        f"以下后端静态守卫不在 PR 路径的任何 pytest 命令里：{missing}。"
        "把它们加进 ci.yml `pr-migrate-empty-db` 的「Run backend static guards」步骤"
        "（夜间 backend-test 不拦合入，#3423 即由此漏进 main）。"
    )


def test_runner_step_neutralizes_same_db_database_url():
    """跑守卫的进程不得看到与 TEST_DATABASE_URL 同库的 DATABASE_URL（#1547）。"""
    checked = 0
    for job_name, job, step in _pr_pytest_steps():
        code = probe.code_text(step)
        if not any(rel in code for rel in _static_guard_files()):
            continue
        checked += 1
        job_env = job.get("env") or {}
        if "DATABASE_URL" not in job_env or "env -u DATABASE_URL" in code:
            continue
        step_url = (step.get("env") or {}).get("DATABASE_URL")
        assert step_url and step_url != job_env.get("TEST_DATABASE_URL"), (
            f"{job_name} / {step.get('name')!r} 让 pytest 看到了与 TEST_DATABASE_URL "
            "同库的 DATABASE_URL，backend/tests/conftest.py 导入期会拒载（pytest exit 4）。"
        )
    assert checked, "没有任何 PR 路径步骤被识别为「跑后端静态守卫」"
