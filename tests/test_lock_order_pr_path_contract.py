"""#1999：共享行加锁顺序回归必须在 PR 路径上真跑（接线结构守卫）。

背景：#1959 / #1980 / #1985 三处锁序修复各自带了一条 PostgreSQL 并发回归，但它们是
PostgreSQL-only（`DATABASE_URL` 为 sqlite 时整组 skip），而 PR 阶段此前**没有任何 job
会跑它们**：`pr-agent-tests` 无 docker/无 PG（testcontainers 路径会硬失败，见
`tests/test_offline_subset_guard.py`），`pr-migrate-empty-db` 有 PG service 却不跑 pytest。
于是「共享行加锁全序」这一不变量在**合入门禁里零覆盖**——只能等夜间 `backend-test`，
而那条路径本身经历过 #1547 整段 ImportError 跳过的窗口期。

更早的远因：2026-09 那轮 R01–R15 全面审查漏掉 `job_instance` / `device_leases` 锁序环，
死锁复发四周无人发现（根因见 `docs/notes/architecture/2026-09-14-shared-row-lock-table.md`）。

本文件把「接线」锚成结构断言，与 `tests/test_ci_test_db_guard_wiring.py` 同模式：

1. **发现式覆盖**：`backend/tests/` 下凡文件名含 `lock_order` 的测试文件，必须出现在
   `ci.yml` 中 **PR 路径 job** 的 pytest 命令里——新增第四个锁序回归却不接线，PR 即红；
2. **护栏语义**：跑它们的那个步骤不得让 pytest 进程看到「与 `TEST_DATABASE_URL` 同库的
   `DATABASE_URL`」，否则 `backend/tests/conftest.py` 导入期 `db_url_guard` 拒载
   （pytest exit 4，#1547 原样复发）。

纯离线：只读文本，不起容器、不连库（`tests/` 准入判据）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
BACKEND_TESTS = REPO_ROOT / "backend" / "tests"

# 文件名判据（非路径白名单）：新增锁序回归只要按命名约定落位，就会被本守卫要求接线。
_NAME_MARKER = "lock_order"


def _ci() -> dict:
    return yaml.safe_load(CI_YML.read_text(encoding="utf-8"))


def _lock_order_test_files() -> list[str]:
    """`backend/tests/` 下的锁序回归文件（仓库相对路径）。"""
    return sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in BACKEND_TESTS.rglob("*.py")
        if _NAME_MARKER in p.name
    )


def _pr_stage_steps() -> list[tuple[str, dict, dict]]:
    """(job 名, job, step) —— 只取**会在 PR 事件上运行**的 job。

    判据取「排除了 `!= 'pull_request'`」而不是「等于 `== 'pull_request'`」：
    后者会漏掉无 `if` 的 job（如 `lint`，PR 与全量都跑），把正确接线误判为未接线。
    """
    out: list[tuple[str, dict, dict]] = []
    for name, job in (_ci().get("jobs") or {}).items():
        if "!= 'pull_request'" in str(job.get("if", "")):
            continue
        for step in job.get("steps") or []:
            out.append((name, job, step))
    return out


def _run_text(step: dict) -> str:
    run = step.get("run", "")
    return run if isinstance(run, str) else "\n".join(run)


class TestDiscovery:
    """防解析失效把断言变成永真。"""

    def test_lock_order_files_are_found(self):
        files = _lock_order_test_files()
        assert len(files) >= 3, (
            f"按文件名含 {_NAME_MARKER!r} 只找到 {files}；少于已知的 3 条"
            "（#1959 / #1980 / #1985），命名约定或扫描逻辑已被改动"
        )

    def test_pr_stage_jobs_are_detected(self):
        jobs = {name for name, _job, _step in _pr_stage_steps()}
        assert "pr-migrate-empty-db" in jobs, (
            f"未从 ci.yml 解析出 PR 路径 job（得到 {sorted(jobs)}）——"
            "`if` 形态改动后本守卫会静默失去覆盖"
        )
        full_only = {"backend-test", "frontend-check", "docker-build"}
        assert not (jobs & full_only), (
            f"仅全量 job 被误判为 PR 路径：{sorted(jobs & full_only)}"
        )


class TestPrPathWiring:
    def test_every_lock_order_file_runs_in_pr_path(self):
        """核心断言：锁序回归不得只活在夜间全量里。"""
        runs = [
            (name, _run_text(step))
            for name, _job, step in _pr_stage_steps()
            if "pytest" in _run_text(step)
        ]
        assert runs, "PR 路径里没有任何 pytest 步骤，解析器已失效"

        missing = [
            rel for rel in _lock_order_test_files()
            if not any(rel in text for _name, text in runs)
        ]
        assert not missing, (
            f"以下锁序回归不在 PR 路径的任何 pytest 命令里：{missing}。"
            "它们是 PostgreSQL-only，夜间 `backend-test` 是唯一防线，而该防线曾是"
            "「合入前不跑、夜间又整段跳过」——请把它们加进有 PG service 的 PR job"
            "（当前是 pr-migrate-empty-db），或说明为何该文件不需要 PR 覆盖。"
        )

    def test_runner_step_neutralizes_same_db_database_url(self):
        """护栏语义：跑锁序回归的进程不得看到与 TEST_DATABASE_URL 同库的 DATABASE_URL。"""
        checked = 0
        for job_name, job, step in _pr_stage_steps():
            run = _run_text(step)
            if not any(rel in run for rel in _lock_order_test_files()):
                continue
            checked += 1
            job_env = job.get("env") or {}
            if "DATABASE_URL" not in job_env:
                continue  # 无 job 级 DATABASE_URL，无需剥离
            if "env -u DATABASE_URL" in run:
                continue
            step_url = (step.get("env") or {}).get("DATABASE_URL")
            assert step_url and step_url != job_env.get("TEST_DATABASE_URL"), (
                f"{job_name} / {step.get('name')!r} 让 pytest 进程看到了与 "
                "TEST_DATABASE_URL 同库的 DATABASE_URL —— backend/tests/conftest.py "
                "导入期 db_url_guard 会拒载（pytest exit 4，#1547 原始成因）。"
                "用 `env -u DATABASE_URL` 剥离该变量，或指向一个不同的库。"
            )
        assert checked, (
            "没有任何 PR 路径步骤被识别为「跑锁序回归」——测试接线断言未生效"
        )
