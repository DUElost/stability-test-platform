"""#2333 兜底单归因的功能验证：失败 job 重跑一次 + 缺陷/flake 分类 + 机器附失败用例名。

`main-ci-backstop.yml` 不能在本机执行，故把机械部分抽成
`scripts/ci/backstop-attribution.sh`，用 `DRY_RUN=1` 换掉取数层（**只换取数，分类与
抽取逻辑与真实路径同一份代码**）跑真脚本断言行为，而不是只断言 YAML 里有没有某段文本。

同时钉两条结构不变量（它们各自防的是一类已发生过的形态）：
1. 归因步骤失败**不得**把兜底单一起打掉（#1548：一个步骤报错让红灯彻底静默）；
2. 红灯 job 清单**不得**在重跑之后重查（重跑会重置 job 结论，转绿时清单直接变空）。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "ci" / "backstop-attribution.sh"
_WORKFLOW = _ROOT / ".github" / "workflows" / "main-ci-backstop.yml"

# 逼真的 GHA job 日志：每行带时间戳前缀，`FAILED` 被自身 ANSI 色码包住（pytest 会着色）。
_LOG = (
    "2026-09-16T02:00:01.1234567Z ##[group]Run pytest backend/tests -q\n"
    "2026-09-16T02:40:00.0000000Z = 1 failed, 975 passed in 2200s =\n"
    "2026-09-16T02:40:00.1234567Z \033[31mFAILED\033[0m "
    "backend/tests/services/test_host_retirement_read_filters_1804.py"
    "::TestStatsFaces::test_file_server_active_hosts_exclude_retired - AssertionError\n"
    "2026-09-16T02:40:00.2000000Z FAIL src/components/foo.test.tsx > suite > case\n"
    "2026-09-16T02:40:00.3000000Z ERROR backend/tests/api/test_recovery_sync_lock_order_2015.py"
    "::test_recovery_sync_locks_job_before_lease\n"
    # 反向样例：正文里的同名词不得被当成用例名（锚点要求「Z + 空格 + 摘要词」）
    "2026-09-16T02:40:00.4000000Z FAILEDX not-a-test\n"
    "2026-09-16T02:40:00.5000000Z 2026-09-16 02:40:00 ERROR backend.core.db table missing\n"
)

_JOBS = {
    "jobs": [
        {"id": 111, "name": "backend-test", "conclusion": "failure",
         "steps": [{"name": "Run backend tests", "conclusion": "failure"},
                   {"name": "Setup python", "conclusion": "success"}]},
        {"id": 222, "name": "frontend-check", "conclusion": "success",
         "steps": [{"name": "build", "conclusion": "success"}]},
        {"id": 333, "name": "lint", "conclusion": "failure",
         "steps": [{"name": "ruff", "conclusion": "failure"},
                   {"name": "eslint", "conclusion": "failure"}]},
    ]
}


def _parse_outputs(path: Path) -> dict[str, str]:
    """解析 $GITHUB_OUTPUT（含 `name<<DELIM ... DELIM` 多行形态）。"""
    out: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<__STP_" in line:
            name, delim = line.split("<<", 1)
            i += 1
            buf = []
            while i < len(lines) and lines[i] != delim:
                buf.append(lines[i])
                i += 1
            out[name] = "\n".join(buf)
        elif "=" in line:
            name, value = line.split("=", 1)
            out[name] = value
        i += 1
    return out


def _run(tmp_path: Path, **overrides: str) -> tuple[subprocess.CompletedProcess, dict[str, str]]:
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps(_JOBS), encoding="utf-8")
    log = tmp_path / "job.log"
    log.write_text(_LOG, encoding="utf-8")
    out_file = tmp_path / "gh_output"
    out_file.touch()

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
        ["bash", str(_SCRIPT)], env=env, capture_output=True, text=True, check=False,
    )
    return proc, _parse_outputs(out_file)


def test_rerun_green_classified_as_flake(tmp_path: Path):
    """重跑转绿 → flake，且明确指向「去 flake」而**不**进 #1525 的前移评估。"""
    proc, out = _run(tmp_path, DRY_RUN_ATTEMPT="1", DRY_RUN_RERUN_CONCLUSION="success")
    assert proc.returncode == 0, proc.stderr
    assert out["classification"] == "flake"
    assert out["rerun_conclusion"] == "success"
    assert "flake" in out["attribution_md"]
    assert "#1525" in out["attribution_md"]


def test_rerun_still_red_classified_as_deterministic(tmp_path: Path):
    proc, out = _run(tmp_path, DRY_RUN_ATTEMPT="1", DRY_RUN_RERUN_CONCLUSION="failure")
    assert proc.returncode == 0, proc.stderr
    assert out["classification"] == "deterministic"
    assert "确定性缺陷" in out["attribution_md"]


def test_already_rerun_is_not_rerun_again(tmp_path: Path):
    """「自动 rerun 一次」的「一次」：已是第 N 次尝试时不得再重跑。

    DRY_RUN_RERUN_CONCLUSION 故意不给——若实现错误地走了重跑分支，
    结论会取空、classification 变 unknown，本断言即失败。
    """
    proc, out = _run(tmp_path, DRY_RUN_ATTEMPT="2")
    assert proc.returncode == 0, proc.stderr
    assert out["classification"] == "deterministic"
    assert out["rerun_conclusion"] == "failure"  # = FIRST_CONCLUSION，未发生新重跑
    assert "不再自动重跑" in out["attribution_md"]


def test_missing_log_is_explicit_never_silent(tmp_path: Path):
    """取不到日志必须**显式**写出（逐个 job 带原因），不能只是省略。"""
    proc, out = _run(
        tmp_path,
        DRY_RUN_ATTEMPT="1",
        DRY_RUN_RERUN_CONCLUSION="failure",
        DRY_RUN_LOG_FILE=str(tmp_path / "does-not-exist.log"),
    )
    assert proc.returncode == 0, proc.stderr
    md = out["attribution_md"]
    assert "未取到日志" in md
    assert "backend-test" in md and "111" in md
    assert "未取到失败用例名" in md


def test_failed_cases_extracted_but_noise_anchored_out(tmp_path: Path):
    """机械抽取要点：ANSI 着色不遮挡、vitest 的 `FAIL` 也收、正文同名词不收。"""
    _, out = _run(tmp_path, DRY_RUN_ATTEMPT="1", DRY_RUN_RERUN_CONCLUSION="failure")
    md = out["attribution_md"]
    assert "test_file_server_active_hosts_exclude_retired" in md
    assert "FAIL src/components/foo.test.tsx" in md
    assert "test_recovery_sync_locks_job_before_lease" in md
    assert "FAILEDX" not in md
    assert "backend.core.db" not in md


def test_failed_jobs_snapshot_lists_red_jobs_with_steps(tmp_path: Path):
    """三要素①由归因步骤在重跑前快照回传；无失败步骤名时显式回落。"""
    _, out = _run(tmp_path, DRY_RUN_ATTEMPT="1", DRY_RUN_RERUN_CONCLUSION="success")
    snapshot = out["failed_jobs_md"]
    assert "- **backend-test** → 失败步骤: Run backend tests" in snapshot
    assert "- **lint** → 失败步骤: ruff, eslint" in snapshot
    assert "frontend-check" not in snapshot  # 绿 job 不进清单
    assert snapshot.count("- **") == 2


# ── 结构不变量（各自防一类已发生过的形态）──────────────────────────────────

def _steps() -> list[dict]:
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]["notify-failure"]["steps"]


def _step(name_fragment: str) -> dict:
    for step in _steps():
        if name_fragment in (step.get("name") or ""):
            return step
    raise AssertionError(f"step not found: {name_fragment}")


def test_attribution_step_cannot_silence_the_notification():
    """#1548 形态：归因步骤报错不得让兜底单一起消失。"""
    assert _step("Rerun failed jobs").get("continue-on-error") is True
    assert _step("Create or comment failure issue") is not None


def test_failed_jobs_are_snapshotted_before_the_rerun():
    """红灯 job 清单不得在重跑后重查——重跑会重置 job 结论。

    判据：归因步骤必须排在开单步骤**之前**，且开单步骤不再自己查 jobs 端点。
    """
    names = [s.get("name") or s.get("uses", "") for s in _steps()]
    attribution_idx = next(i for i, n in enumerate(names) if "Rerun failed jobs" in n)
    issue_idx = next(i for i, n in enumerate(names) if "Create or comment failure issue" in n)
    assert attribution_idx < issue_idx
    issue_run = _step("Create or comment failure issue").get("run") or ""
    assert "runs/$CI_RUN_ID/jobs" not in issue_run, "开单步骤不得在重跑后重查 job 清单"
    assert "${FAILED_JOBS}" in issue_run


def test_job_has_actions_write_for_rerun_and_log_read():
    """重跑要 actions: write；读 job 日志同样绕不过该权限（裸 GET 该端点 403）。"""
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    perms = doc["jobs"]["notify-failure"]["permissions"]
    assert perms.get("actions") == "write"
