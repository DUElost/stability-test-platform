"""#3573：backstop 归因的重跑等待预算必须按失败 job 分级。

缺陷：`wait_conclusion` 轮询的是 **run 级**状态，而等待预算对所有 job 共用一个窗口
（30s × 120 = 1h）。vitest 全套（`frontend-check`）远慢于 backend/agent/docker，共用
1h 窗口时必然跑不完 → 重跑结论为空 → 分类为「未能分类」→ **该类红灯永远进不了 #1525
的前移评估**。#2441 实测即此形态，单内自述「重跑未在预算内完成（120 × 30s），无法
分类……不得作为前移评估的样本」。

修法：失败集合里命中 SLOW_JOBS（默认 `frontend-check`）时改用 WAIT_MAX_SLOW，并把
实际采用的窗口作为 `wait_budget` output 暴露——既可被断言，也让兜底单正文能直接看到
这次等了多久。

与 test_backstop_attribution.py 的分工：那个文件守「分类语义」（flake / 确定性 /
未能分类），本文件守「预算选择」。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "ci" / "backstop-attribution.sh"

_LOG = (
    "2026-08-25T18:28:58.8735367Z FAILED backend/tests/test_x.py::test_y - AssertionError\n"
)


def _jobs(*failed: str) -> dict:
    """构造 jobs payload：给定名字集合标 failure，其余标 success。"""
    names = ["backend-test", "frontend-check", "docker-build", "lint"]
    return {
        "jobs": [
            {
                "id": 100 + i,
                "name": n,
                "conclusion": "failure" if n in failed else "success",
                "steps": [
                    {"name": f"run {n}", "conclusion": "failure" if n in failed else "success"}
                ],
            }
            for i, n in enumerate(names)
        ]
    }


def _run(tmp_path: Path, failed: tuple[str, ...], **overrides: str) -> tuple[str, str]:
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps(_jobs(*failed)), encoding="utf-8")
    log = tmp_path / "job.log"
    log.write_text(_LOG, encoding="utf-8")
    out_file = tmp_path / "gh_output"
    # 必须截断而非 touch()：touch 只改 mtime 不清空，同一 test 内多次 _run 时
    # 输出会被追加，_field 取到首个匹配而误判（本次就踩过）。
    out_file.write_text("", encoding="utf-8")

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "REPO": "owner/name",
        "CI_RUN_ID": "12345",
        "FIRST_CONCLUSION": "failure",
        "DRY_RUN": "1",
        "DRY_RUN_JOBS_JSON": str(jobs),
        "DRY_RUN_LOG_FILE": str(log),
        "GITHUB_OUTPUT": str(out_file),
    }
    env.update(overrides)
    proc = subprocess.run(
        ["bash", str(_SCRIPT)], env=env, capture_output=True, text=True, check=False
    )
    return proc.stdout, out_file.read_text(encoding="utf-8")


def _field(outputs: str, name: str) -> str:
    for line in outputs.splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
    raise AssertionError(f"output 缺字段 {name}；实得：{outputs!r}")


def test_fast_jobs_only_use_base_budget(tmp_path: Path):
    """只有快 job 红 → 沿用原预算，不因新逻辑变慢。"""
    _, out = _run(tmp_path, failed=("backend-test",))
    # 默认 WAIT_INTERVAL=30 × WAIT_MAX=120 = 3600s
    assert _field(out, "wait_budget") == "3600"


def test_frontend_check_failure_escalates_budget(tmp_path: Path):
    """frontend-check 红 → 用更长的慢 job 预算（本条即缺陷修复的核心判据）。"""
    _, out = _run(tmp_path, failed=("frontend-check",))
    # 默认 WAIT_MAX_SLOW=360 × 30 = 10800s（3h），显著大于原 1h
    assert _field(out, "wait_budget") == "10800"


def test_mixed_failure_escalates_budget(tmp_path: Path):
    """快慢混合红 → 取大者，不能因为有快 job 就退回短预算。"""
    _, out = _run(tmp_path, failed=("backend-test", "frontend-check"))
    assert _field(out, "wait_budget") == "10800"


def test_budget_env_overrides_are_respected(tmp_path: Path):
    """WAIT_MAX_SLOW / WAIT_INTERVAL 可覆盖，便于按仓调参而不改脚本。"""
    _, out = _run(tmp_path, failed=("frontend-check",),
                  WAIT_INTERVAL="10", WAIT_MAX_SLOW="60")
    assert _field(out, "wait_budget") == "600"


def test_slow_jobs_list_is_configurable(tmp_path: Path):
    """SLOW_JOBS 可配置：换白名单后行为随之改变（不把 job 名硬编码死）。"""
    _, out = _run(tmp_path, failed=("docker-build",),
                  SLOW_JOBS="docker-build")
    assert _field(out, "wait_budget") == "10800"
    _, out2 = _run(tmp_path, failed=("frontend-check",),
                   SLOW_JOBS="docker-build")
    # 白名单里没有 frontend-check → 走基础预算
    assert _field(out2, "wait_budget") == "3600"


def test_budget_is_summarized_in_stdout_for_log_trail(tmp_path: Path):
    """stdout 摘要带上本次预算，留痕可查（不只存在于 output）。"""
    stdout, _ = _run(tmp_path, failed=("frontend-check",))
    assert "wait_budget=10800s" in stdout
