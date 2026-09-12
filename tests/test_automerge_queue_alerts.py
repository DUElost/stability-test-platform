"""pr-automerge-queue.sh 队首停摆告警回归（#1246 / R15-R02）。

用假的 `gh`（严格路由：未知调用 exit 2）跑真实队列脚本，覆盖场景矩阵：

1. 红队首 → 开 `ci/queue-blocked` 去重告警；不更新红队首分支；
2. 同指纹复检 → 零写入（不刷屏）；
3. 指纹变化 → 编辑存量告警；
4. 队首全绿 + behind → 关闭存量告警 + 正常 update-branch；
5. 队列空 → 关闭存量告警；
6. 告警创建失败 → reconcile 仍退出 0（可见性通道不阻断队列）。
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "pr-automerge-queue.sh"
_REPO = "acme/widgets"

_HEAD_ROW = {
    "number": 101,
    "url": f"https://github.com/{_REPO}/pull/101",
    "headRefName": "fix/101-red-head",
    "createdAt": "2026-09-11T00:00:00Z",
    "isDraft": False,
    "isCrossRepository": False,
    "autoMergeRequest": None,
}


def _head_detail(checks: dict[str, str]) -> dict:
    return {
        "autoMergeRequest": {"mergeMethod": "MERGE"},
        "headRefName": _HEAD_ROW["headRefName"],
        "statusCheckRollup": [{"name": k, "conclusion": v} for k, v in checks.items()],
    }


_ALL_GREEN = {
    "lint": "SUCCESS",
    "CodeQL": "SUCCESS",
    "pr-typecheck": "SUCCESS",
    "pr-compileall": "SUCCESS",
    "pr-agent-tests": "SUCCESS",
    "pr-migrate-empty-db": "SUCCESS",
}
_RED = {**_ALL_GREEN, "pr-agent-tests": "FAILURE"}


def _write_fake_gh(bindir: Path, scenario: dict, call_log: Path) -> None:
    scenario_file = bindir / "scenario.json"
    scenario_file.write_text(json.dumps(scenario), encoding="utf-8")
    argv_log = call_log.with_name("argv.jsonl")
    gh = bindir / "gh"
    gh.write_text(
        f"""#!/usr/bin/env python3
import json, os, sys

scenario = json.load(open({str(scenario_file)!r}, encoding="utf-8"))
log_path = {str(call_log)!r}
argv_log_path = {str(argv_log)!r}
args = sys.argv[1:]
with open(log_path, "a", encoding="utf-8") as fh:
    fh.write(" ".join(args) + "\\n")
# #1549：另存一份 argv JSON —— 正文含换行，空格拼接的日志无法还原单个参数，
# 而「脚本实际写出的 issue body」正是此前测试从未检查过的东西。
with open(argv_log_path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\\n")

def out(text=""):
    if text:
        print(text)
    sys.exit(0)

def fail(code=1):
    sys.exit(code)

if args[:2] == ["pr", "list"]:
    for row in scenario.get("pr_rows", []):
        print(json.dumps(row))
    sys.exit(0)
if args[:2] == ["pr", "view"]:
    if "--json" in args and "author" in " ".join(args):
        out(scenario.get("pr_author", "tester"))
    out(json.dumps(scenario.get("head_detail", {{}})))
if args[:2] == ["pr", "merge"]:
    out()
if args[:2] == ["pr", "update-branch"]:
    out()
if args[0] == "api":
    path = args[1] if len(args) > 1 else ""
    if path == "graphql":
        out(scenario.get("merge_method_out", ""))
    if "actions/runs?" in path:
        out('{{"workflow_runs": []}}')
    if "issues?labels=" in path:
        out(scenario.get("open_issue", ""))
    if "/compare/" in path:
        out(str(scenario.get("behind_by", 0)))
    out()
if args[0] == "label":
    fail(scenario.get("label_create_rc", 0))
if args[:2] == ["issue", "create"]:
    fail(scenario.get("issue_create_rc", 0))
if args[:2] == ["issue", "view"]:
    out(scenario.get("issue_body", ""))
if args[:2] in (["issue", "edit"], ["issue", "comment"], ["issue", "close"]):
    out()
out()
""",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)


def _run_queue(tmp_path: Path, scenario: dict) -> tuple[subprocess.CompletedProcess, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    call_log = tmp_path / "calls.log"
    call_log.write_text("", encoding="utf-8")
    _write_fake_gh(bindir, scenario, call_log)

    env = dict(os.environ)
    env["GITHUB_REPOSITORY"] = _REPO
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env.pop("ALERT_TOKEN", None)
    result = subprocess.run(
        ["bash", str(_SCRIPT)], capture_output=True, text=True, env=env, check=False
    )
    return result, call_log.read_text(encoding="utf-8")


def _assert_called(calls: str, needle: str) -> None:
    assert needle in calls, f"expected call containing {needle!r}; calls:\n{calls}"


def _read_argv(tmp_path: Path) -> list[list[str]]:
    """读出假 gh 记录的 argv（每行一个 JSON 数组，保留含换行的参数原样）。"""
    log = tmp_path / "argv.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def _body_of(argv_calls: list[list[str]], verb: str) -> str:
    """取 `gh issue <verb> ... --body <value>` 的 body 实参。"""
    for args in argv_calls:
        if len(args) >= 2 and args[0] == "issue" and args[1] == verb and "--body" in args:
            return args[args.index("--body") + 1]
    raise AssertionError(f"no `issue {verb} --body` call; argv={argv_calls}")


def _assert_not_called(calls: str, needle: str) -> None:
    assert needle not in calls, f"unexpected call containing {needle!r}; calls:\n{calls}"


def test_red_head_opens_alert_and_skips_branch_update(tmp_path):
    result, calls = _run_queue(
        tmp_path,
        {"pr_rows": [_HEAD_ROW], "head_detail": _head_detail(_RED), "open_issue": ""},
    )

    assert result.returncode == 0, result.stderr
    _assert_called(calls, "issue create")
    _assert_called(calls, "ci/queue-blocked")
    _assert_not_called(calls, "pr update-branch")
    assert "queue-blocked" in result.stdout or "alert" in result.stdout.lower()


def test_same_fingerprint_writes_nothing(tmp_path):
    fingerprint = "head=#101 failed=pr-agent-tests:FAILURE"
    result, calls = _run_queue(
        tmp_path,
        {
            "pr_rows": [_HEAD_ROW],
            "head_detail": _head_detail(_RED),
            "open_issue": "999",
            "issue_body": f"<!-- queue-blocked-fingerprint: {fingerprint} -->",
        },
    )

    assert result.returncode == 0, result.stderr
    _assert_not_called(calls, "issue create")
    _assert_not_called(calls, "issue edit")
    _assert_not_called(calls, "issue comment")
    assert "unchanged" in result.stdout


def test_alert_body_renders_placeholders_and_self_dedups(tmp_path):
    """#1549：断言脚本**实际写出**的告警正文，并做一次指纹回环。

    原实现把整行（含 `%s`）当 `printf` 的参数、只把 `'%s\\n'` 当格式串，于是
    正文全是字面 `%s`、指纹行也不含真实指纹 → 第 113 行的
    `grep -qF "queue-blocked-fingerprint: ${fingerprint}"` 永不命中，
    「同指纹零写入」失效，告警 issue 被每次 reconcile 反复重写。

    原测试只用**手搓**的 `issue_body` 驱动，从不检查写出的正文，所以这个缺陷
    长期存活。这里：① 断言正文占位符已替换、指纹注释与实际指纹一致；
    ② 回环——把脚本自己写出的正文当作存量 issue 正文喂回去，必须零写入。
    """
    red_scenario = {
        "pr_rows": [_HEAD_ROW],
        "head_detail": _head_detail(_RED),
        "open_issue": "",
    }

    first = tmp_path / "first"
    first.mkdir()
    result, _ = _run_queue(first, red_scenario)
    assert result.returncode == 0, result.stderr
    body = _body_of(_read_argv(first), "create")

    assert "%s" not in body, f"占位符未被替换，正文含字面 %s:\n{body}"
    assert (
        "<!-- queue-blocked-fingerprint: head=#101 failed=pr-agent-tests:FAILURE -->" in body
    ), f"指纹注释与实际指纹不一致:\n{body}"
    assert "[#101](https://github.com/acme/widgets/pull/101)" in body
    assert "`fix/101-red-head`" in body
    assert "@tester" in body

    second = tmp_path / "second"
    second.mkdir()
    result2, calls2 = _run_queue(
        second,
        {
            "pr_rows": [_HEAD_ROW],
            "head_detail": _head_detail(_RED),
            "open_issue": "999",
            "issue_body": body,
        },
    )
    assert result2.returncode == 0, result2.stderr
    _assert_not_called(calls2, "issue edit")
    _assert_not_called(calls2, "issue create")
    assert "unchanged" in result2.stdout, result2.stdout


def test_changed_fingerprint_edits_alert(tmp_path):
    result, calls = _run_queue(
        tmp_path,
        {
            "pr_rows": [_HEAD_ROW],
            "head_detail": _head_detail(_RED),
            "open_issue": "999",
            "issue_body": "<!-- queue-blocked-fingerprint: head=#100 failed=lint:FAILURE -->",
        },
    )

    assert result.returncode == 0, result.stderr
    _assert_called(calls, "issue edit")
    _assert_not_called(calls, "issue create")


def test_green_head_closes_alert_and_updates_branch(tmp_path):
    result, calls = _run_queue(
        tmp_path,
        {
            "pr_rows": [_HEAD_ROW],
            "head_detail": _head_detail(_ALL_GREEN),
            "open_issue": "999",
            "behind_by": 2,
        },
    )

    assert result.returncode == 0, result.stderr
    _assert_called(calls, "issue close")
    _assert_called(calls, "pr update-branch")


def test_empty_queue_closes_alert(tmp_path):
    result, calls = _run_queue(tmp_path, {"pr_rows": [], "open_issue": "999"})

    assert result.returncode == 0, result.stderr
    _assert_called(calls, "issue close")
    assert "No eligible PRs" in result.stdout


def test_alert_creation_failure_does_not_fail_reconcile(tmp_path):
    result, calls = _run_queue(
        tmp_path,
        {
            "pr_rows": [_HEAD_ROW],
            "head_detail": _head_detail(_RED),
            "open_issue": "",
            "issue_create_rc": 1,
        },
    )

    assert result.returncode == 0, result.stderr
    _assert_called(calls, "issue create")
    _assert_not_called(calls, "pr update-branch")
