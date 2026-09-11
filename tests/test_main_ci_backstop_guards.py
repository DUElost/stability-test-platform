"""main-ci-backstop.yml 的清理/重派安全守卫回归（R15-F02 #1294 / R15-F06 #1298）。

工作流不能在本机执行，这里对 YAML 里的 shell 文本做结构性断言：把「删除
分支前必须证明 tip 已在 main 中」与「重派后不得认领旧 run」两条不变量钉住，
防止未来编辑静默移除守卫。
"""
from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "main-ci-backstop.yml"


def _steps() -> list[dict]:
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]["verify-and-cleanup"]["steps"]


def _step_run(name_fragment: str) -> str:
    for step in _steps():
        if name_fragment in (step.get("name") or ""):
            return step.get("run") or ""
    raise AssertionError(f"step not found: {name_fragment}")


def test_redispatch_polling_excludes_pre_existing_runs():
    """#1298: 重派后的轮询必须排除 dispatch 前已存在的 run id。"""
    run = _step_run("Ensure full CI run")
    assert "pre_existing_ids=" in run, "缺少 dispatch 前 run id 快照"
    assert 'index($id|tostring)) | not' in run, "轮询未排除已存在的 run"
    # 旧写法（直接取第一条）不得回归为主判据
    assert '"repos/$REPO/actions/workflows/ci.yml/runs?head_sha=$main_sha&event=workflow_dispatch&per_page=5"' not in run


def test_branch_delete_requires_tip_contained_in_main():
    """#1294: 删除复用分支前必须证明当前 tip 已包含在 main 中。"""
    run = _step_run("Delete merged head branches")
    assert "compare/main...$branch" in run, "缺少相对 main 的祖先/包含判定"
    assert "behind|identical" in run, "缺少 behind/identical 白名单"
    # 检查与删除间的并发推送防护：重读 tip 并要求不变
    assert "skip branch moved during cleanup" in run


def test_branch_delete_keeps_sha_guard_before_delete():
    run = _step_run("Delete merged head branches")
    guard_idx = run.index("skip branch moved during cleanup")
    delete_idx = run.index("git/refs/heads/$branch")
    assert guard_idx < delete_idx, "tip 重读守卫必须在 DELETE 之前"
