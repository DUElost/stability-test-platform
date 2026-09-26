"""main-ci-backstop.yml 的清理/重派安全守卫回归（R15-F02 #1294 / R15-F06 #1298）。

工作流不能在本机执行，这里对 YAML 里的 shell 文本做结构性断言：把「删除
分支前必须证明 tip 已在 main 中」与「重派后不得认领旧 run」两条不变量钉住，
防止未来编辑静默移除守卫。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "main-ci-backstop.yml"
_WORKFLOWS_DIR = _WORKFLOW.parent

# `jq -r --argjson floor "$pre_existing_max_id" --arg sha "$main_sha" '<filter>'`
# —— 取 filter 字面量做功能验证
# （两者之间是「空格 + 续行符 \ + 换行 + 缩进」，故用 [\s\\]* 一次性跨过）
_JQ_POLL_RE = re.compile(
    r'jq\s+-r\s+--argjson\s+floor\s+"\$pre_existing_max_id"\s+--arg\s+sha\s+"\$main_sha"[\s\\]*\'([^\']*)\'',
    re.DOTALL,
)


def _steps(job: str = "verify-and-cleanup") -> list[dict]:
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"][job]["steps"]


def _step_run(name_fragment: str, job: str = "verify-and-cleanup") -> str:
    for step in _steps(job):
        if name_fragment in (step.get("name") or ""):
            return step.get("run") or ""
    raise AssertionError(f"step not found: {name_fragment}")


def test_redispatch_polling_excludes_pre_existing_runs():
    """#1298/#3363: 重派后只接纳同 SHA 且 id 高于派发前快照的 run。"""
    run = _step_run("Ensure full CI run")
    assert "pre_existing_max_id=" in run, "缺少 dispatch 前 run id 上界"
    assert "head_sha=$main_sha&event=workflow_dispatch" in run, "轮询缺少 SHA 查询过滤"
    assert ".head_sha == $sha and .id > $floor" in run, "轮询缺少响应 SHA 与 id 双重判据"
    assert 'run_sha="$(gh api "repos/$REPO/actions/runs/$run_id" --jq .head_sha)"' in run
    assert 'if [ "$run_sha" != "$main_sha" ]; then' in run
    # 旧写法（直接取第一条）不得回归为主判据
    assert '"repos/$REPO/actions/workflows/ci.yml/runs?head_sha=$main_sha&event=workflow_dispatch&per_page=5"' not in run


def test_gh_api_jq_never_receives_jq_options():
    """#1548：`gh api --jq` 只接受**一个**字符串，其后不得再跟 jq 选项。

    写成 `--jq --arg pre "$ids" '...'` 时 gh 报
    ``accepts 1 arg(s), received 4`` 并以非零退出；该步骤在 ``set -e`` 下会
    中止整个 job，连带跳过等待/分支清理/补关，且不生成兜底单——红灯彻底静默。
    带参数的 jq 一律管道给真正的 ``jq``。对所有 workflow 统一断言。
    """
    offenders = []
    for path in sorted(_WORKFLOWS_DIR.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        # 注释里会引用错误写法做说明，先剔除整行注释再扫描
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        for match in re.finditer(r"--jq\s+(?:--?\w)", code):
            offenders.append(f"{path.name}: {match.group(0)!r}")
    assert not offenders, f"`gh api --jq` 后跟了 jq 选项（gh 不接受）: {offenders}"


def test_redispatch_poll_filter_functionally_excludes_preexisting_runs():
    """#3363：旧失败 run 在场且新 run 延迟出现时，不得选中旧 run。

    原判据只检查片段文本，语法错误的 `gh api` 调用因此能长期存活。这里用
    fixture 验证工作流原样过滤器：同 SHA、id 增长两条同时成立才可选；
    列表首条旧 run 或不同 SHA 时必须回落到 0。
    """
    run = _step_run("Ensure full CI run")
    match = _JQ_POLL_RE.search(run)
    assert match, "未找到带 SHA 和 id 上界参数的重派过滤器"
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq 不可用，跳过过滤器的功能验证")
    filter_expr = match.group(1)
    sha = "a" * 40
    other_sha = "b" * 40

    def _select(runs: list[dict], floor: int = 111) -> str:
        proc = subprocess.run(
            [jq, "-r", "--argjson", "floor", str(floor), "--arg", "sha", sha, filter_expr],
            input=json.dumps({"workflow_runs": runs}),
            capture_output=True, text=True, check=True,
        )
        return proc.stdout.strip()

    old = {"id": 111, "head_sha": sha}
    new = {"id": 222, "head_sha": sha}
    foreign = {"id": 333, "head_sha": other_sha}
    assert _select([old]) == "0", "新 run 未入列表时不得认领旧 run"
    assert _select([old, foreign]) == "0", "不得认领另一 SHA 的 run"
    assert _select([old, new, foreign]) == "222", "应选中同 SHA 的新 run"
    assert _select([new, old], floor=222) == "0", "派发前已有的同 SHA run 不得认领"


def test_failure_comment_checks_selected_run_sha():
    steps = _steps("notify-failure")
    names = [step.get("name") for step in steps]
    assert names.index("Verify failed CI run belongs to main tip") < names.index(
        "Rerun failed jobs once and classify (flake vs deterministic)"
    )
    verify = _step_run("Verify failed CI run belongs to main tip", job="notify-failure")
    assert 'if [ "$run_sha" != "$MAIN_SHA" ]; then' in verify
    run = _step_run("Create or comment failure issue", job="notify-failure")
    assert 'run_sha="$(gh api "repos/$REPO/actions/runs/$CI_RUN_ID" --jq .head_sha)"' in run
    assert 'if [ "$run_sha" != "$MAIN_SHA" ]; then' in run
    assert run.index('if [ "$run_sha" != "$MAIN_SHA" ]; then') < run.index("gh issue comment")


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
