"""pr-automerge-queue.sh 队首停摆告警回归（#1246 / R15-R02）。

用假的 `gh`（严格路由：未知调用 exit 2）跑真实队列脚本，覆盖场景矩阵：

1. 红队首 → 开 `ci/queue-blocked` 去重告警；不更新红队首分支；
2. 同指纹复检 → 零写入（不刷屏）；
3. 指纹变化 → 编辑存量告警；
4. 队首全绿 + behind → 关闭存量告警 + 正常 update-branch；
5. 队列空 → 关闭存量告警；
6. 告警创建失败 → reconcile 仍退出 0（可见性通道不阻断队列）；
7. #2556：checks「从未创建」（rollup 缺条目 + 该 sha 上 `ci.yml` run 数 0）→
   一次带冷却的自续重基；同一 `(PR, sha)` 第二次不再动手，只升级告警措辞。
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



def _head_detail_no_auto(checks: dict[str, str]) -> dict:
    """队首**未挂** auto-merge 的 detail——enable 分支的真实前提。

    `_head_detail()` 预置 `autoMergeRequest.mergeMethod=MERGE`，那是「已挂」形态；
    脚本只有在 `method != MERGE` 时才走 enable，故 #2646 的用例必须用本 helper，
    否则 fixture 自相矛盾（既说已挂、又走 enable），且会让「兜底是否误吞真故障」
    的断言失去意义。
    """
    detail = _head_detail(checks)
    detail["autoMergeRequest"] = None
    return detail

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
    token_log = call_log.with_name("tokens.jsonl")
    gh = bindir / "gh"
    gh.write_text(
        f"""#!/usr/bin/env python3
import json, os, sys

scenario = json.load(open({str(scenario_file)!r}, encoding="utf-8"))
log_path = {str(call_log)!r}
argv_log_path = {str(argv_log)!r}
token_log_path = {str(token_log)!r}
args = sys.argv[1:]
with open(log_path, "a", encoding="utf-8") as fh:
    fh.write(" ".join(args) + "\\n")
# #1549：另存一份 argv JSON —— 正文含换行，空格拼接的日志无法还原单个参数，
# 而「脚本实际写出的 issue body」正是此前测试从未检查过的东西。
with open(argv_log_path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\\n")
# #2556：记录该次调用用的 GH_TOKEN —— 判据读取必须走有 Actions 读权限的那个令牌
# （见 probe_gh）。只用于断言「用了哪个」，不涉及真实凭据。
with open(token_log_path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"args": args, "gh_token": os.environ.get("GH_TOKEN", "")}}) + "\\n")

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
    # #2646：可注入 enable 失败（如 PAT 缺 workflow scope 的 GraphQL 拒绝）以验容错分支。
    # 只对 `--auto`（启用路径）注入——`--disable-auto` 本身走 `|| true`，无需覆盖。
    merge_err = scenario.get("merge_error")
    if merge_err and "--auto" in args:
        print(merge_err, file=sys.stderr)
        sys.exit(1)
    out()
if args[:2] == ["pr", "update-branch"]:
    # #1783：可注入失败（如 PAT 缺 workflow scope 的 GraphQL 拒绝）以验容错分支
    err = scenario.get("update_branch_error")
    if err:
        print(err, file=sys.stderr)
        sys.exit(1)
    out()
if args[0] == "api":
    path = args[1] if len(args) > 1 else ""
    if path == "graphql":
        out(scenario.get("merge_method_out", ""))
    if "actions/runs?" in path:
        out('{{"workflow_runs": []}}')
    # #2556：队首 head sha 上 ci.yml 的 run 数（脚本用 `--jq .total_count` 取值，
    # 故这里直接回 jq 结果）。缺省 1＝「触发正常、跑了没上报」，保持既有用例不变；
    # `ci_run_probe_rc` 注入探测失败。
    if "actions/workflows/ci.yml/runs" in path:
        if scenario.get("ci_run_probe_rc"):
            fail(scenario["ci_run_probe_rc"])
        out(str(scenario.get("ci_run_total", 1)))
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


def _run_queue(
    tmp_path: Path, scenario: dict, *, alert_token: str | None = None
) -> tuple[subprocess.CompletedProcess, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    call_log = tmp_path / "calls.log"
    call_log.write_text("", encoding="utf-8")
    _write_fake_gh(bindir, scenario, call_log)

    env = dict(os.environ)
    env["GITHUB_REPOSITORY"] = _REPO
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env.pop("ALERT_TOKEN", None)
    if alert_token is not None:
        env["ALERT_TOKEN"] = alert_token
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


def _tokens_of(tmp_path: Path) -> list[dict]:
    """读出假 gh 记录的「每次调用用了哪个 GH_TOKEN」（#2556 判据令牌断言用）。"""
    log = tmp_path / "tokens.jsonl"
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


def test_shrunk_failure_set_still_updates_alert(tmp_path):
    """#2075：失败集**收敛**到旧值前缀时仍须刷新正文。

    指纹是 `head=#N failed=<按 REQUIRED 顺序逗号连接>`，而守卫用 `grep -qF`
    （子串匹配）。所以「前几项仍红、后面某项转绿」时计算值正好是存量值的**子串**
    ——比较不带行尾终止符就会命中 → 误判 unchanged → 正文继续列着已经通过的
    check。这里末项由红转绿，断言脚本必须改写正文。
    """
    stale_body = (
        "<!-- queue-blocked-fingerprint: "
        "head=#101 failed=lint:FAILURE, pr-agent-tests:FAILURE -->"
    )
    # 当前只剩 lint 红（pr-agent-tests 已转绿）→ 计算值 = `...failed=lint:FAILURE`
    result, calls = _run_queue(
        tmp_path,
        {
            "pr_rows": [_HEAD_ROW],
            "head_detail": _head_detail({**_ALL_GREEN, "lint": "FAILURE"}),
            "open_issue": "999",
            "issue_body": stale_body,
        },
    )

    assert result.returncode == 0, result.stderr
    assert "unchanged" not in result.stdout, (
        "失败集已从「lint + pr-agent-tests」收敛为「lint」，不得判为 unchanged：\n"
        f"{result.stdout}"
    )
    _assert_called(calls, "issue edit")


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


def test_workflow_scope_rejection_keeps_reconcile_green(tmp_path):
    """#1783：队首 PR 改过 .github/workflows/* 而 PAT 无 workflow scope 时，
    update-branch 被 GitHub 拒绝——必须绿退并给人工指引，不得整 job 红（否则
    队首 rebase 停摆、后续 PR 全部积压）。"""
    result, calls = _run_queue(
        tmp_path,
        {
            "pr_rows": [_HEAD_ROW],
            "head_detail": _head_detail(_ALL_GREEN),
            "open_issue": "999",
            "behind_by": 2,
            "update_branch_error": (
                "GraphQL: refusing to allow a Personal Access Token to create or "
                "update workflow `.github/workflows/ci.yml` without `workflow` "
                "scope (updatePullRequestBranch)"
            ),
        },
    )

    assert result.returncode == 0, result.stderr
    _assert_called(calls, "pr update-branch")
    assert "workflow" in result.stdout and "scope" in result.stdout, result.stdout
    # 指引要能落地：提示手工 rebase
    assert "rebase" in result.stdout.lower(), result.stdout


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


# ── #1761：pending（进行中）不得被当作失败告警 ──────────────────────────────


def _now_iso() -> str:
    """当前 UTC 的 ISO8601（供「仍在启动窗口」用例）。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _head_detail_with_status(
    checks: dict[str, tuple[str, str]], *, started_at: str | None = None,
) -> dict:
    """带 status 的 statusCheckRollup（#1761 之前 harness 只造 name+conclusion，
    正是这个缺口让「进行中 conclusion 为空」被误判为 missing 而长期未被发现）。

    ``started_at``（#1792 第三类）：模拟已注册 check 的启动时刻——脚本据「最早
    startedAt 距今是否超过宽限」区分「启动窗口内尚未注册」与「真 missing」。
    """
    entries = []
    for k, (s, c) in checks.items():
        entry = {"name": k, "status": s, "conclusion": c}
        if started_at is not None:
            entry["startedAt"] = started_at
        entries.append(entry)
    return {
        "autoMergeRequest": {"mergeMethod": "MERGE"},
        "headRefName": _HEAD_ROW["headRefName"],
        "statusCheckRollup": entries,
    }


def _pending_detail(*pending: str) -> dict:
    """指定 check 为 IN_PROGRESS，其余全绿。"""
    checks = {k: ("COMPLETED", "SUCCESS") for k in _ALL_GREEN}
    for name in pending:
        checks[name] = ("IN_PROGRESS", "")
    return _head_detail_with_status(checks)


def test_pending_check_does_not_open_alert(tmp_path):
    """#1761：CI 进行中不得开告警——此前被渲染成 missing 并开 issue。"""
    result, calls = _run_queue(
        tmp_path,
        {"pr_rows": [_HEAD_ROW], "head_detail": _pending_detail("pr-agent-tests"), "open_issue": ""},
    )

    assert result.returncode == 0, result.stderr
    _assert_not_called(calls, "issue create")
    assert "IN_PROGRESS" in result.stdout
    _assert_not_called(calls, "pr update-branch")


def test_queued_check_does_not_open_alert(tmp_path):
    """QUEUED（尚未开始）同属 pending，同样不得告警。"""
    checks = {k: ("COMPLETED", "SUCCESS") for k in _ALL_GREEN}
    checks["lint"] = ("QUEUED", "")
    result, calls = _run_queue(
        tmp_path,
        {"pr_rows": [_HEAD_ROW], "head_detail": _head_detail_with_status(checks), "open_issue": ""},
    )

    assert result.returncode == 0, result.stderr
    _assert_not_called(calls, "issue create")
    assert "QUEUED" in result.stdout


def test_pending_does_not_close_existing_alert(tmp_path):
    """仅有 pending 时不得 resolve 存量告警——CI 没跑完，真实失败可能紧随其后。"""
    result, calls = _run_queue(
        tmp_path,
        {"pr_rows": [_HEAD_ROW], "head_detail": _pending_detail("lint"), "open_issue": "999"},
    )

    assert result.returncode == 0, result.stderr
    _assert_not_called(calls, "issue close")
    _assert_not_called(calls, "issue create")


def test_completed_failure_still_opens_alert(tmp_path):
    """#1246 的真实停摆必须继续告警——修复不得把 failed 一起静音。"""
    result, calls = _run_queue(
        tmp_path,
        {"pr_rows": [_HEAD_ROW], "head_detail": _head_detail(_RED), "open_issue": ""},
    )

    assert result.returncode == 0, result.stderr
    _assert_called(calls, "issue create")
    assert "FAILURE" in result.stdout


def test_missing_check_entry_still_opens_alert(tmp_path):
    """**超出启动窗口**仍缺条目：该 sha 上 CI 跑过（`ci_run_total`>0）却始终没上报
    此 check —— 属人工排查（#1792 第三类），不得自愈重基（#2556 判据的另一半）。"""
    checks = {k: ("COMPLETED", "SUCCESS") for k in _ALL_GREEN if k != "CodeQL"}
    detail = _head_detail_with_status(checks, started_at="2020-01-01T00:00:00Z")
    detail["headRefOid"] = _SELF_HEAL_SHA
    result, calls = _run_queue(
        tmp_path,
        {
            "pr_rows": [_HEAD_ROW],
            # 关键：给已注册 check 一个**久远**的 startedAt，表示早已超出启动窗口
            "head_detail": detail,
            "open_issue": "",
            "ci_run_total": 3,
        },
    )

    assert result.returncode == 0, result.stderr
    _assert_called(calls, "issue create")
    _assert_not_called(calls, "pr update-branch")
    # 真正的 missing 走告警分支（与「进行中」区分）——日志措辞为 "not reported"
    assert "CodeQL not reported (missing)" in result.stdout
    body = _body_of(_read_argv(tmp_path), "create")
    assert "CodeQL:missing" in body
    assert "从未上报" in body


# ── #1792 第三类：启动窗口内的「尚未注册」不得告警 ──────────────────────────


def test_check_not_yet_registered_within_startup_window_does_not_alert(tmp_path):
    """#1855 形态：CodeQL 作为独立 workflow 晚注册，告警时条目尚不存在。

    实测时序：lint 启动 11:14:42、CodeQL 启动 11:15:15（+33s），而告警发生在
    11:14:49（早于 CodeQL 注册 26s）。该窗口内条目确实不存在，但属正常启动时序。
    """
    checks = {k: ("COMPLETED", "SUCCESS") for k in _ALL_GREEN if k != "CodeQL"}
    result, calls = _run_queue(
        tmp_path,
        {
            "pr_rows": [_HEAD_ROW],
            # 刚启动（相对测试运行时刻）→ 仍在启动窗口
            "head_detail": _head_detail_with_status(checks, started_at=_now_iso()),
            "open_issue": "",
        },
    )

    assert result.returncode == 0, result.stderr
    _assert_not_called(calls, "issue create")
    assert "not yet registered (startup window)" in result.stdout


def test_no_checks_registered_at_all_is_startup_window(tmp_path):
    """极早期（rollup 无任何 startedAt）视为启动窗口，不告警。"""
    checks = {k: ("COMPLETED", "SUCCESS") for k in _ALL_GREEN if k != "CodeQL"}
    result, calls = _run_queue(
        tmp_path,
        {"pr_rows": [_HEAD_ROW], "head_detail": _head_detail_with_status(checks), "open_issue": ""},
    )

    assert result.returncode == 0, result.stderr
    _assert_not_called(calls, "issue create")
    assert "startup window" in result.stdout


def test_startup_window_does_not_close_existing_alert(tmp_path):
    """启动窗口中不得 resolve 存量告警（CI 未跑完，真实失败可能紧随其后）。"""
    checks = {k: ("COMPLETED", "SUCCESS") for k in _ALL_GREEN if k != "CodeQL"}
    result, calls = _run_queue(
        tmp_path,
        {
            "pr_rows": [_HEAD_ROW],
            "head_detail": _head_detail_with_status(checks, started_at=_now_iso()),
            "open_issue": "999",
        },
    )

    assert result.returncode == 0, result.stderr
    _assert_not_called(calls, "issue close")
    _assert_not_called(calls, "issue create")


# ── #1792：CodeQL 聚合 check 的 COMPLETED/NEUTRAL 不得判为失败 ──────────────


def _neutral_detail() -> dict:
    """#1792 真实形态：CodeQL=COMPLETED/NEUTRAL（子分析未全完成），其余全绿。

    实证来源：#1775 告警时 1 个 Analyze SUCCESS + 2 个 IN_PROGRESS → 父 check NEUTRAL；
    以及 #1772 终态 NEUTRAL 在 strict=true 分支保护下**被 GitHub 允许合入**。
    故 NEUTRAL 是「满足」而非「失败」——我们的 FIFO 不得比分支保护更严。
    """
    checks = {k: ("COMPLETED", "SUCCESS") for k in _ALL_GREEN}
    checks["CodeQL"] = ("COMPLETED", "NEUTRAL")
    return _head_detail_with_status(checks)


def test_codeql_neutral_does_not_open_alert(tmp_path):
    """#1792：NEUTRAL 不得告警（#1764 合入后仍在误报的残余源，20 分钟 6 条）。"""
    result, calls = _run_queue(
        tmp_path,
        {"pr_rows": [_HEAD_ROW], "head_detail": _neutral_detail(), "open_issue": ""},
    )

    assert result.returncode == 0, result.stderr
    _assert_not_called(calls, "issue create")


def test_codeql_neutral_does_not_block_branch_update(tmp_path):
    """NEUTRAL 应放行 update-branch——若判失败会造成「GitHub 可合入、FIFO 却拒更」的伪停摆。"""
    result, calls = _run_queue(
        tmp_path,
        {
            "pr_rows": [_HEAD_ROW],
            "head_detail": _neutral_detail(),
            "open_issue": "",
            "behind_by": 3,
        },
    )

    assert result.returncode == 0, result.stderr
    _assert_called(calls, "pr update-branch")


def test_codeql_failure_still_opens_alert(tmp_path):
    """FAILURE 必须继续告警——NEUTRAL 的放行不得把真实失败一起放过。"""
    checks = {k: ("COMPLETED", "SUCCESS") for k in _ALL_GREEN}
    checks["CodeQL"] = ("COMPLETED", "FAILURE")
    result, calls = _run_queue(
        tmp_path,
        {"pr_rows": [_HEAD_ROW], "head_detail": _head_detail_with_status(checks), "open_issue": ""},
    )

    assert result.returncode == 0, result.stderr
    _assert_called(calls, "issue create")
    assert "CodeQL" in result.stdout and "FAILURE" in result.stdout


# ── #2556：checks「从未创建」（GitHub 触发丢失）→ 一次带冷却的自续重基 ──────


_SELF_HEAL_SHA = "a" * 40


def _never_created_detail(*, sha: str = _SELF_HEAL_SHA) -> dict:
    """#2529 实证形态：部分 required check 在 rollup 里**全缺**，且该 head sha 上
    `ci.yml` 一个 run 都没有。startedAt 取久远值以越过启动窗口（真实事件里
    Vercel/Cursor 的 check-suite 提供了这个时间戳）。

    对照的另一半是 run 数 > 0：那时缺条目意味着「跑了没上报」，走人工（见
    `test_missing_check_entry_still_opens_alert`）。
    """
    checks = {
        k: ("COMPLETED", "SUCCESS") for k in _ALL_GREEN if k not in ("CodeQL", "pr-typecheck")
    }
    detail = _head_detail_with_status(checks, started_at="2020-01-01T00:00:00Z")
    detail["headRefOid"] = sha
    return detail


def _self_heal_scenario(**overrides) -> dict:
    """自愈形态的默认场景：从未创建 + 落后 main（base-change 可做）。"""
    scenario = {
        "pr_rows": [_HEAD_ROW],
        "head_detail": _never_created_detail(),
        "open_issue": "",
        "ci_run_total": 0,
        "behind_by": 1,
    }
    scenario.update(overrides)
    return scenario


def test_never_created_checks_self_heal_with_one_branch_update(tmp_path):
    """#2556 正向：run 数为 0 → 执行一次 update-branch（唯一的自愈动作），
    告警文案也如实改成「已自续重基」，不再说「不对红队首自动重基」。"""
    result, calls = _run_queue(tmp_path, _self_heal_scenario())

    assert result.returncode == 0, result.stderr
    _assert_called(calls, "pr update-branch")
    _assert_called(calls, "issue create")
    body = _body_of(_read_argv(tmp_path), "create")
    assert "已自续重基一次" in body, body
    assert "不对红队首自动重基" not in body, "那是「判为红」的措辞，自愈形态必须换掉"
    assert f"<!-- queue-selfheal: head=#101 sha={_SELF_HEAL_SHA} -->" in body, body


def test_second_reconcile_on_same_sha_does_not_self_heal_again(tmp_path):
    """#2556 冷却（防「missing ↔ 重基」死循环的正向钉子）：把上一轮**脚本自己
    写出**的正文喂回，同一 `(PR, sha)` 第二次不得再 update-branch，只升级措辞。

    去掉冷却（或把冷却键从 `(pr, sha)` 放宽成 `pr`）都会让本用例红。
    """
    first = tmp_path / "first"
    first.mkdir()
    result, _ = _run_queue(first, _self_heal_scenario())
    assert result.returncode == 0, result.stderr
    first_body = _body_of(_read_argv(first), "create")

    second = tmp_path / "second"
    second.mkdir()
    result2, calls2 = _run_queue(
        second, _self_heal_scenario(open_issue="999", issue_body=first_body)
    )

    assert result2.returncode == 0, result2.stderr
    _assert_not_called(calls2, "pr update-branch")
    _assert_called(calls2, "issue edit")
    escalated = _body_of(_read_argv(second), "edit")
    assert "已试过自续重基一次仍无 run" in escalated, escalated


def test_exhausted_self_heal_alert_is_deduped(tmp_path):
    """冷却用尽后的重复 reconcile 不刷屏：指纹（含 sha 与自愈状态）未变 → 零写入。"""
    first = tmp_path / "first"
    first.mkdir()
    _run_queue(first, _self_heal_scenario())
    first_body = _body_of(_read_argv(first), "create")

    second = tmp_path / "second"
    second.mkdir()
    _run_queue(second, _self_heal_scenario(open_issue="999", issue_body=first_body))
    escalated = _body_of(_read_argv(second), "edit")

    third = tmp_path / "third"
    third.mkdir()
    result3, calls3 = _run_queue(
        third, _self_heal_scenario(open_issue="999", issue_body=escalated)
    )

    assert result3.returncode == 0, result3.stderr
    _assert_not_called(calls3, "pr update-branch")
    _assert_not_called(calls3, "issue edit")
    _assert_not_called(calls3, "issue create")
    assert "unchanged" in result3.stdout, result3.stdout


def test_self_heal_cooldown_is_keyed_by_head_sha(tmp_path):
    """冷却键是 `(PR, head_sha)`：换了 head（如自愈后的新提交）就是全新判定，
    旧 sha 的标记不得把它挡住。"""
    stale_marker = f"<!-- queue-selfheal: head=#101 sha={'b' * 40} -->"
    result, calls = _run_queue(
        tmp_path, _self_heal_scenario(open_issue="999", issue_body=stale_marker)
    )

    assert result.returncode == 0, result.stderr
    _assert_called(calls, "pr update-branch")


def test_never_created_without_base_change_escalates_without_push(tmp_path):
    """队首没落后 main 时没有 base-change 可做：不得调用 update-branch（那会硬失败
    并染红 reconcile），直接在告警里交回人工。"""
    result, calls = _run_queue(tmp_path, _self_heal_scenario(behind_by=0))

    assert result.returncode == 0, result.stderr
    _assert_not_called(calls, "pr update-branch")
    _assert_called(calls, "issue create")
    body = _body_of(_read_argv(tmp_path), "create")
    assert "未落后 main" in body, body


def test_ci_run_probe_failure_does_not_self_heal(tmp_path):
    """探测失败（API 错误/无 head sha）时判据未知——不得当成「从未创建」去重基，
    仍按人工告警，且不因告警失败染红 reconcile。"""
    result, calls = _run_queue(tmp_path, _self_heal_scenario(ci_run_probe_rc=1))

    assert result.returncode == 0, result.stderr
    _assert_not_called(calls, "pr update-branch")
    _assert_called(calls, "issue create")
    body = _body_of(_read_argv(tmp_path), "create")
    assert "探测失败" in body, body


def test_ci_run_probe_uses_the_actions_capable_token(tmp_path):
    """判据读取必须走 ALERT_TOKEN（workflow 注入的 GITHUB_TOKEN，job 授了
    `actions: write`）：AUTO_MERGE_PAT 的 scope 集不受本仓控制（#1783 已证其缺
    `workflow` scope）。若改回默认 GH_TOKEN，PAT 一旦没有 Actions 读权限，自愈就会
    静默退化成「每轮交给人工」——本单的核心能力等于没上。"""
    result, calls = _run_queue(
        tmp_path, _self_heal_scenario(), alert_token="ghs_alerts_token"
    )

    assert result.returncode == 0, result.stderr
    _assert_called(calls, "pr update-branch")
    probe_calls = [
        c
        for c in _tokens_of(tmp_path)
        if len(c["args"]) > 1 and "actions/workflows/ci.yml/runs" in c["args"][1]
    ]
    assert probe_calls, "判据读取（ci.yml runs）没有发生"
    assert all(c["gh_token"] == "ghs_alerts_token" for c in probe_calls), probe_calls


# ── #2646：enable auto-merge 的 workflow-scope 拒绝（#1783 同根因的未覆盖调用点）──

_WORKFLOW_SCOPE_ERR = (
    "GraphQL: Pull request refusing to allow a Personal Access Token to create or "
    "update workflow `.github/workflows/ci.yml` without `workflow` scope "
    "(enablePullRequestAutoMerge)"
)


def test_enable_automerge_workflow_scope_rejection_keeps_reconcile_green(tmp_path):
    """#2646 主判据①：队首改过 .github/workflows/* 时，enable 被 workflow-scope 拒绝
    **不得让整 job 红**（否则队列自持停摆，实测 6h20m）。

    #1783 修的是同一根因在 `update-branch` 路径上的表现；enable 这条路径当时没有
    同样的容错，故本用例钉住它。
    """
    result, calls = _run_queue(
        tmp_path,
        {
            "pr_rows": [_HEAD_ROW],
            "head_detail": _head_detail_no_auto(_ALL_GREEN),
            "open_issue": "999",
            "merge_error": _WORKFLOW_SCOPE_ERR,
        },
    )

    assert result.returncode == 0, result.stderr
    _assert_called(calls, "pr merge")
    assert "workflow" in result.stdout and "scope" in result.stdout, result.stdout
    # 指引必须可执行：提示用有 scope 的凭据启用 auto-merge
    assert "auto-merge" in result.stdout, result.stdout


def test_enable_automerge_scope_rejection_opens_distinguishable_alert(tmp_path):
    """#2646 主判据②：该状态须发**可区分**告警。

    既有指纹是 `head=#N failed=<失败项>`，而本状态的队首 required checks **全绿**、
    failed 为空 ⇒ 若不单列，运维只看到「一切正常却没合入」。故断言：
    ① 开了告警；② 指纹含 `reason=credential-scope`（与普通空-failed 停摆可区分）；
    ③ 正文含人工动作。
    """
    result, calls = _run_queue(
        tmp_path,
        {
            "pr_rows": [_HEAD_ROW],
            "head_detail": _head_detail_no_auto(_ALL_GREEN),
            "open_issue": "",
            "merge_error": _WORKFLOW_SCOPE_ERR,
        },
    )

    assert result.returncode == 0, result.stderr
    body = _body_of(_read_argv(tmp_path), "create")
    assert "reason=credential-scope" in body, body
    assert "credential-scope" in body, body
    # 必须写明「不自愈」——否则运维会以为等下一轮就好
    assert "不自愈" in body or "not self-heal" in body, body


def test_enable_automerge_unrelated_failure_still_fails_loudly(tmp_path):
    """#2646 负向对照：**非** workflow-scope 的 enable 失败**不得**被吞——

    容错只能收「凭据配置」这一可处置状态；真故障（网络/权限/未知）必须继续上抛，
    否则会把真实故障伪装成绿。
    """
    result, _calls = _run_queue(
        tmp_path,
        {
            "pr_rows": [_HEAD_ROW],
            "head_detail": _head_detail_no_auto(_ALL_GREEN),
            "open_issue": "",
            "merge_error": "HTTP 500: internal server error",
        },
    )

    assert result.returncode != 0, (
        f"非 workflow-scope 的 enable 失败必须上抛，实际 rc={result.returncode}"
    )
