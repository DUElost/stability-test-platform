"""#3573：backstop 归因必须**只看目标红灯 job**，且等待预算按目标集合里最慢的那个分级。

根因（实测 run 35148237108 attempt 2，对应 #2441）：原 `wait_conclusion` 轮询 **run 级**
`status/conclusion`，而 run 的 completed 要等**所有** job 落定。当时目标
`frontend-check` **2m20s** 就出结论（failure），但同批次 `backend-test` 撞上自身
`timeout-minutes: 60` 被 cancelled，run 直到 60 分钟后才 completed ⇒ 1h 预算在边界
上被这个**不相干的兄弟 job** 吃掉，分类永远拿不到，该类红灯遂永远进不了 #1525 的
前移评估。

实测墙钟（用于定 SLOW_JOBS，别再凭直觉写反）：

    backend-test    16~60 min（自身 timeout-minutes: 60）
    frontend-check  ≈2 min
    docker-build    更短

故 `SLOW_JOBS` 默认列 **backend-test**。本文件同时守两件事：
① 等待口径是「目标 job 自身结论」而非 run 级；② 预算按目标集合分级。

与 test_backstop_attribution.py 的分工：那个守分类语义（flake / 确定性 / 未能分类），
本文件守「等谁」与「等多久」。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "ci" / "backstop-attribution.sh"

_LOG = "2026-08-25T18:28:58.8735367Z FAILED backend/tests/test_x.py::test_y - AssertionError\n"

# 默认预算：WAIT_INTERVAL=30；WAIT_MAX=120 → 3600s；WAIT_MAX_SLOW=240 → 7200s
_BASE = 3600
_SLOW = 7200


def _jobs(*failed: str) -> dict:
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


def _run(tmp_path: Path, failed: tuple[str, ...], conclusion: str = "failure",
         **overrides: str) -> tuple[str, str]:
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps(_jobs(*failed)), encoding="utf-8")
    log = tmp_path / "job.log"
    log.write_text(_LOG, encoding="utf-8")
    out_file = tmp_path / "gh_output"
    # 必须截断而非 touch()：touch 只改 mtime 不清空，同一 test 内多次 _run 时输出会被
    # 追加，_field 取到首个匹配而误判（本次踩过）。
    out_file.write_text("", encoding="utf-8")

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "REPO": "owner/name",
        "CI_RUN_ID": "12345",
        "FIRST_CONCLUSION": "failure",
        "DRY_RUN": "1",
        "DRY_RUN_JOBS_JSON": str(jobs),
        "DRY_RUN_LOG_FILE": str(log),
        "DRY_RUN_RERUN_CONCLUSION": conclusion,
        "GITHUB_OUTPUT": str(out_file),
    }
    env.update(overrides)
    proc = subprocess.run(
        ["bash", str(_SCRIPT)], env=env, capture_output=True, text=True, check=False,
        timeout=60,
    )
    return proc.stdout, out_file.read_text(encoding="utf-8")


def _field(outputs: str, name: str) -> str:
    for line in outputs.splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
    raise AssertionError(f"output 缺字段 {name}；实得：{outputs!r}")


# ── 预算按「目标集合里最慢的那个」分级 ────────────────────────────────────

def test_fast_job_only_uses_base_budget(tmp_path: Path):
    """frontend-check 实测 ≈2min，命中不了慢名单 → 基础预算。"""
    _, out = _run(tmp_path, failed=("frontend-check",))
    assert _field(out, "wait_budget") == str(_BASE)


def test_slow_job_escalates_budget(tmp_path: Path):
    """backend-test 实测 16~60min → 走慢预算（本条即分级生效的核心判据）。"""
    _, out = _run(tmp_path, failed=("backend-test",))
    assert _field(out, "wait_budget") == str(_SLOW)


def test_mixed_targets_take_the_slower_budget(tmp_path: Path):
    """快慢混合 → 取大者，不能因为有快 job 就退回短预算。"""
    _, out = _run(tmp_path, failed=("frontend-check", "backend-test"))
    assert _field(out, "wait_budget") == str(_SLOW)


def test_slow_jobs_default_is_backend_test_not_frontend(tmp_path: Path):
    """钉住实测结论：慢的是 backend-test。若有人把慢名单改回 frontend-check，
    这条会红——它是 #3573 演进记录里那次写反的防复发锚点。"""
    script = _SCRIPT.read_text(encoding="utf-8")
    assert 'SLOW_JOBS:-backend-test' in script.replace('"', "").replace("'", "")


def test_slow_jobs_list_is_configurable(tmp_path: Path):
    """SLOW_JOBS 可配置：换白名单后行为随之改变（不把 job 名硬编码死）。"""
    _, out = _run(tmp_path, failed=("frontend-check",), SLOW_JOBS="frontend-check")
    assert _field(out, "wait_budget") == str(_SLOW)
    _, out2 = _run(tmp_path, failed=("frontend-check",), SLOW_JOBS="docker-build")
    assert _field(out2, "wait_budget") == str(_BASE)


def test_budget_env_overrides_are_respected(tmp_path: Path):
    """WAIT_MAX_SLOW / WAIT_INTERVAL 可覆盖，便于按仓调参而不改脚本。"""
    _, out = _run(tmp_path, failed=("backend-test",),
                  WAIT_INTERVAL="10", WAIT_MAX_SLOW="60")
    assert _field(out, "wait_budget") == "600"


def test_budget_is_summarized_in_stdout_for_log_trail(tmp_path: Path):
    """stdout 摘要带上本次预算，留痕可查（不只存在于 output）。"""
    stdout, _ = _run(tmp_path, failed=("backend-test",))
    assert f"wait_budget={_SLOW}s" in stdout


# ── 等待口径：目标 job 自身结论，而非 run 级 ────────────────────────────────

def test_mixed_target_conclusion_is_deterministic_with_note(tmp_path: Path):
    """目标 job 有转绿有仍红 → 仍红者已构成确定性缺陷，按 deterministic 处置并说明。

    这正是「只看目标 job」带来的新语义：run 级口径下这种情况会被 run 的整体结论
    掩盖成单一 success/failure。
    """
    _, out = _run(tmp_path, failed=("backend-test", "frontend-check"),
                  conclusion="mixed")
    assert _field(out, "classification") == "deterministic"
    assert _field(out, "rerun_conclusion") == "mixed"
    assert "结论不一致" in out


def test_all_targets_green_is_flake(tmp_path: Path):
    """目标 job 全部转绿 → flake（不进 #1525 前移评估）。"""
    _, out = _run(tmp_path, failed=("frontend-check",), conclusion="success")
    assert _field(out, "classification") == "flake"


def test_unresolved_conclusion_fails_fast_without_sleeping(tmp_path: Path):
    """夹具给不出结论（预算内未落定）→ 立刻非零退出走「未能分类」，**不得真实 sleep**。

    dry-run 若照真实路径 sleep 满预算，测试进程会被挂住——本条即该回归的守卫。
    """
    _, out = _run(tmp_path, failed=("frontend-check",), conclusion="")
    assert _field(out, "classification") == "unknown"
    assert "未在预算内完成" in out
