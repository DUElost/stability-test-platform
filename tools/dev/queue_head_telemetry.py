#!/usr/bin/env python3
"""队首阻塞遥测（Queue Head Blocking Telemetry）——只读观测。

只回答一个问题：**当前 FIFO 队首为什么没有前进，已经卡了多久。**

设计约束（详见 docs/notes/process/2026-09-09-queue-head-blocking-telemetry.md）：

- **单一权威**：integration 事实复用 tools/dev/ai_work.py::derive_integration
  （契约 §3.1 的 `{NO_PR, PR_OPEN, READY, MERGED, CLOSED}` 五态），本工具
  不引入任何新的 integration 顶层状态、不复制队列资格谓词（队首身份直接读
  队列自己的产物：FIFO 不变式「仅队首启用 auto-merge」）。
- **零副作用**：不写持久化、不改 FIFO 行为、不新增 workflow / check run /
  label。GitHub 是事实来源，本工具只是解释器。
- **reason_code 是 advisory telemetry**：本工具与任何 workflow 都不得据其
  分支。出现 `if reason_code == ...; then` 即意味着它已悄悄变成控制面契约。
- **blocked_since 是观测代理**：无持久化时无法知道「首次进入阻塞」的真值，
  只取当前可观测信号的时间戳（见 `--json` 的 `blocked_since_source`）。
  终态出口：若将来证明需要精确起点，再引入记录（届时须另行裁决）。

用法：
    python -m tools.dev.queue_head_telemetry            # 人类可读
    python -m tools.dev.queue_head_telemetry --json      # 机器可读
    python -m tools.dev.queue_head_telemetry --pr 1173   # 指定 PR
    python -m tools.dev.queue_head_telemetry --no-log    # 跳过失败日志抓取
    python -m tools.dev.queue_head_telemetry --self-test # 离线红绿自证
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

# required checks 的权威来源是分支保护（GitHub）。此列表仅作 API 不可用时的
# 回退，并会在输出里标注 required_source=fallback——不回退成静默猜测。
FALLBACK_REQUIRED = [
    "lint",
    "CodeQL",
    "pr-typecheck",
    "pr-compileall",
    "pr-agent-tests",
    "pr-migrate-empty-db",
]

# advisory telemetry：当前能稳定观察到的阻塞原因。不追求完备，也不允许分支。
REASON_OWNER = {
    "NO_QUEUE_HEAD": "machine",  # reconcile 尚未给下一 PR 挂 auto-merge
    "NO_BLOCKER": "machine",  # 已满足合入条件，等 GitHub 执行
    "REQUIRED_CHECK_PENDING": "machine",  # CI 正在跑
    "BEHIND_MAIN": "machine",  # 队首会被队列自动 update-branch
    "REQUIRED_CHECK_FAILED": "developer",  # 必须有人改代码
    "CONFLICTING": "developer",  # P0 阶段不做自动解冲突
    "UNKNOWN": "developer",
}

_RUN_ID_RE = re.compile(r"/actions/runs/(\d+)")
_LOG_PREFIX_RE = re.compile(r"^\S+\t\S+\t\S+Z ?")
_FAIL_LINE_RE = re.compile(
    r"(?:^[A-Z]\d{3}\s)|(?:\b(?:error|Error|ERROR|fatal|FAILED|Traceback)\b)"
)
_LOCATION_RE = re.compile(r"-->\s+(\S+:\d+(?::\d+)?)")


# ── 纯函数（--self-test 覆盖） ────────────────────────────────────────────


def parse_run_id(details_url: str | None) -> int | None:
    """从 check run 的 detailsUrl 提取 Actions run id；非 Actions 返回 None。"""
    if not details_url:
        return None
    m = _RUN_ID_RE.search(details_url)
    return int(m.group(1)) if m else None


def fmt_duration(seconds: float | None) -> str | None:
    if seconds is None or seconds < 0:
        return None
    total = int(seconds)
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m}m"
    if m:
        return f"{m}m{s}s"
    return f"{s}s"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def blocking_checks(checks: list[dict], required: list[str]) -> list[dict]:
    """required 中未 SUCCESS 的 check（与两个 CI 脚本的判据一致：conclusion != SUCCESS）。"""
    req = set(required)
    out = []
    for c in checks:
        name = c.get("name")
        if name not in req:
            continue
        if c.get("conclusion") == "SUCCESS":
            continue
        out.append(
            {
                "name": name,
                "status": c.get("status"),
                "conclusion": c.get("conclusion") or "",
                "run_id": parse_run_id(c.get("detailsUrl")),
                "started_at": c.get("startedAt"),
                "completed_at": c.get("completedAt"),
            }
        )
    return out


def classify(
    *,
    integration: str,
    checks: list[dict],
    required: list[str],
    mergeable: str | None,
    merge_state: str | None,
    behind_by: int,
    now: datetime,
    conflict_onset: str | None = None,
) -> dict:
    """由已采集事实推导 (reason_code, owner, blocked_since...)。纯函数，可离线自证。"""
    blocking = blocking_checks(checks, required)
    failed = [b for b in blocking if b["status"] == "COMPLETED"]
    pending = [b for b in blocking if b["status"] != "COMPLETED"]

    if integration in ("MERGED", "CLOSED"):
        reason = "NO_QUEUE_HEAD"
    elif mergeable == "CONFLICTING" or merge_state == "DIRTY":
        reason = "CONFLICTING"
    elif failed:
        reason = "REQUIRED_CHECK_FAILED"
    elif pending:
        reason = "REQUIRED_CHECK_PENDING"
    elif behind_by > 0:
        reason = "BEHIND_MAIN"
    else:
        reason = "NO_BLOCKER"

    since = None
    since_source = "unavailable"
    if reason == "REQUIRED_CHECK_FAILED":
        stamps = [t for t in (_parse_ts(b["completed_at"]) for b in failed) if t]
        if stamps:
            since = min(stamps)
            since_source = "failed_required_check.completedAt(min)"
    elif reason == "REQUIRED_CHECK_PENDING":
        stamps = [t for t in (_parse_ts(b["started_at"]) for b in pending) if t]
        if stamps:
            since = min(stamps)
            since_source = "pending_required_check.startedAt(min)"
    elif reason in ("CONFLICTING", "BEHIND_MAIN"):
        ts = _parse_ts(conflict_onset)
        if ts:
            since = ts
            # 注意：这是**分歧起点**（main 首个领先提交），不是冲突起点——
            # 冲突可能在该时刻之后才产生，故它是 blocked_since 的**上界**。
            # 精确冲突起点需要逐提交二分（且 GitHub 不提供冲突路径清单，见 P1），
            # 超出 P0 的观测范围；此处只如实标注口径，不假装精确。
            since_source = (
                "divergence_onset_approx(上界：main 首个领先提交；真实冲突起点不晚于此)"
                if reason == "CONFLICTING"
                else "main_advanced_approx(main 首个领先提交)"
            )

    duration = None
    if since is not None:
        duration = fmt_duration((now - since).total_seconds())

    return {
        "reason_code": reason,
        "owner": REASON_OWNER.get(reason, "developer"),
        "actionable": REASON_OWNER.get(reason, "developer") == "developer",
        "blocking_checks": blocking,
        "blocked_since": since.isoformat() if since else None,
        "blocked_since_source": since_source,
        "blocked_duration": duration,
    }


def extract_failure_excerpt(log_text: str, max_lines: int = 4) -> dict:
    """从 `gh run view --log-failed` 输出提取首条失败摘要与首个位置。"""
    lines = []
    location = None
    for raw in log_text.splitlines():
        text = _LOG_PREFIX_RE.sub("", raw).strip()
        if not text:
            continue
        if location is None:
            m = _LOCATION_RE.search(text)
            if m:
                location = m.group(1)
        if _FAIL_LINE_RE.search(text) and text not in lines and len(lines) < max_lines:
            lines.append(text)
    return {"failure": lines or None, "location": location}


# ── GitHub 采集（只读） ──────────────────────────────────────────────────


def _gh(args: list[str], cwd: str, timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
    except (subprocess.TimeoutExpired, OSError) as exc:  # 网络/未装 gh：降级不猜测
        return 1, f"{type(exc).__name__}: {exc}"
    return p.returncode, p.stdout if p.returncode == 0 else p.stderr


def repo_slug(cwd: str) -> str | None:
    rc, out = _gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], cwd)
    return out.strip() if rc == 0 and out.strip() else None


def fetch_required(repo: str, cwd: str) -> tuple[list[str], str]:
    rc, out = _gh(
        ["api", f"repos/{repo}/branches/main/protection/required_status_checks",
         "--jq", ".contexts[]"],
        cwd,
    )
    if rc == 0 and out.strip():
        return out.split(), "branch_protection"
    return list(FALLBACK_REQUIRED), "fallback"


def fetch_head(repo: str, cwd: str) -> tuple[int | None, str, list[int]]:
    """队首身份 = 队列自己的产物（FIFO 不变式：仅队首启用 auto-merge）。

    不复制 pr-automerge-queue.sh 的资格谓词：谓词漂移时本工具仍按队列实际
    行为读数，而不是按一份可能过期的规则猜测。返回 (head, source, 多挂 auto 的 PR)。
    """
    rc, out = _gh(
        ["pr", "list", "--repo", repo, "--state", "open", "--limit", "100",
         "--json", "number,createdAt,autoMergeRequest,isDraft,isCrossRepository",
         "--jq", "sort_by(.createdAt) | .[] | @json"],
        cwd,
    )
    if rc != 0:
        return None, "unavailable", []
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    with_auto = [r for r in rows if r.get("autoMergeRequest")]
    extra = [r["number"] for r in with_auto[1:]]
    if with_auto:
        return with_auto[0]["number"], "auto_merge_invariant", extra
    if rows:
        return None, "queue_not_settled", []
    return None, "no_open_pr", []


def fetch_pr(repo: str, num: int, cwd: str) -> dict | None:
    rc, out = _gh(
        ["pr", "view", str(num), "--repo", repo, "--json",
         "number,headRefName,headRefOid,mergeable,mergeStateStatus,autoMergeRequest,"
         "statusCheckRollup,createdAt,updatedAt,url"],
        cwd,
    )
    return json.loads(out) if rc == 0 and out.strip() else None


def fetch_behind(repo: str, head_ref: str, cwd: str) -> int:
    rc, out = _gh(
        ["api", f"repos/{repo}/compare/main...{head_ref}", "--jq", ".behind_by // 0"], cwd
    )
    if rc != 0:
        return 0
    try:
        return int(out.strip() or 0)
    except ValueError:
        return 0


def fetch_conflict_onset(repo: str, head_ref: str, cwd: str) -> str | None:
    """main 上首个不在 head 的提交时间 ≈ 分支开始落后/冲突的近似起点。"""
    rc, out = _gh(
        ["api", f"repos/{repo}/compare/{head_ref}...main",
         "--jq", "[.commits[].commit.committer.date] | min // empty"],
        cwd,
    )
    return out.strip() or None if rc == 0 else None


def fetch_failure(repo: str, run_id: int, cwd: str) -> dict:
    rc, out = _gh(["run", "view", str(run_id), "--repo", repo, "--log-failed"],
                  cwd, timeout=45)
    if rc != 0:
        return {"failure": None, "location": None, "failure_source": "unavailable"}
    res = extract_failure_excerpt(out)
    res["failure_source"] = "run_log"
    return res


# ── 装配与渲染 ──────────────────────────────────────────────────────────


def collect(repo: str, cwd: str, pr: int | None, with_log: bool) -> dict:
    now = datetime.now(timezone.utc)
    required, required_source = fetch_required(repo, cwd)
    warnings: list[str] = []

    if pr is not None:
        head, head_source, extra = pr, "explicit", []
    else:
        head, head_source, extra = fetch_head(repo, cwd)
    if extra:
        warnings.append(f"FIFO 不变式告警：多个 PR 挂了 auto-merge {extra}")

    record: dict = {
        "observed_at": now.isoformat(),
        "repo": repo,
        "queue_head": head,
        "queue_head_source": head_source,
        "required_checks": required,
        "required_source": required_source,
        "warnings": warnings,
    }
    if head is None:
        record.update(
            integration=None,
            head_ref=None,
            mergeable=None,
            merge_state=None,
            behind_by=None,
            reason_code="NO_QUEUE_HEAD",
            owner=REASON_OWNER["NO_QUEUE_HEAD"],
            actionable=False,
            blocking_checks=[],
            blocked_since=None,
            blocked_since_source="unavailable",
            blocked_duration=None,
            failure=None,
            location=None,
        )
        return record

    data = fetch_pr(repo, head, cwd)
    if data is None:
        record.update(
            integration=None, reason_code="UNKNOWN", owner="developer", actionable=True,
            blocking_checks=[], blocked_since=None,
            blocked_since_source="unavailable", blocked_duration=None,
            failure=None, location=None,
            warnings=[*warnings, "PR 读取失败：GitHub 不可达，不猜测状态"],
        )
        return record

    # integration 单一权威：与 ai_work.py 完全同源，不在此重算。
    from tools.dev.ai_work import derive_integration

    integration, refreshed = derive_integration(str(head), None, cwd)
    if not refreshed:
        warnings.append("GitHub 暂不可达：integration 沿用旧值，不推进终态")

    head_ref = data.get("headRefName") or ""
    behind = fetch_behind(repo, head_ref, cwd) if head_ref else 0
    onset = fetch_conflict_onset(repo, head_ref, cwd) if head_ref else None

    verdict = classify(
        integration=integration,
        checks=data.get("statusCheckRollup") or [],
        required=required,
        mergeable=data.get("mergeable"),
        merge_state=data.get("mergeStateStatus"),
        behind_by=behind,
        now=now,
        conflict_onset=onset,
    )

    failure = None
    location = None
    failure_source = "skipped"
    if with_log and verdict["reason_code"] == "REQUIRED_CHECK_FAILED":
        failed = [b for b in verdict["blocking_checks"] if b.get("run_id")]
        if failed:
            got = fetch_failure(repo, failed[0]["run_id"], cwd)
            failure, location = got["failure"], got["location"]
            failure_source = got["failure_source"]

    record.update(
        integration=integration,
        head_ref=head_ref,
        head_oid=(data.get("headRefOid") or "")[:8],
        mergeable=data.get("mergeable"),
        merge_state=data.get("mergeStateStatus"),
        auto_merge=bool(data.get("autoMergeRequest")),
        behind_by=behind,
        pr_updated_at=data.get("updatedAt"),
        url=data.get("url"),
        failure=failure,
        location=location,
        failure_source=failure_source,
        **verdict,
    )
    return record


def render_human(r: dict) -> str:
    lines = []
    if r.get("queue_head") is None:
        lines.append("queue_head: none")
        lines.append(f"queue_head_source: {r.get('queue_head_source')}")
        lines.append("reason_code: NO_QUEUE_HEAD")
        lines.append("actionable: false")
        lines.append("owner: machine")
        lines.append("note: 队列尚未换档（队首已合入，等 reconcile 给下一 PR 挂 auto-merge）")
    else:
        lines.append(f"queue_head: #{r['queue_head']}  ({r.get('head_ref')})")
        lines.append(f"integration: {r.get('integration')}      # 权威五态，来自 ai_work.derive_integration")
        lines.append(f"reason_code: {r.get('reason_code')}")
        lines.append(f"owner: {r.get('owner')}   actionable: {str(r.get('actionable')).lower()}")
        lines.append(f"mergeable: {r.get('mergeable')}  merge_state: {r.get('merge_state')}"
                     f"  behind_by: {r.get('behind_by')}")
        bc = r.get("blocking_checks") or []
        if bc:
            lines.append("blocking_checks:")
            for b in bc:
                lines.append(f"  - {b['name']}: {b.get('status')}/{b.get('conclusion') or '-'}"
                             f"  run={b.get('run_id')}")
        else:
            lines.append("blocking_checks: []")
        if r.get("failure"):
            for f in r["failure"]:
                lines.append(f"failure: {f}")
        if r.get("location"):
            lines.append(f"location: {r['location']}")
        lines.append(f"blocked_since: {r.get('blocked_since')}  "
                     f"({r.get('blocked_since_source')})")
        lines.append(f"blocked_duration: {r.get('blocked_duration')}")
    lines.append(f"observed_at: {r.get('observed_at')}")
    lines.append(f"required_source: {r.get('required_source')}")
    for w in r.get("warnings") or []:
        lines.append(f"warning: {w}")
    return "\n".join(lines)


# ── 自证 ────────────────────────────────────────────────────────────────

_GREEN = {"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS",
          "startedAt": "2026-09-09T06:42:00Z", "completedAt": "2026-09-09T06:42:45Z"}


def run_self_test() -> int:
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    fails = []

    def check(label, got, want):
        if got != want:
            fails.append(f"{label}: got={got!r} want={want!r}")

    # 1. 全绿且不落后 → NO_BLOCKER / machine / 非人工
    r = classify(integration="READY", checks=[_GREEN], required=["lint"],
                 mergeable="MERGEABLE", merge_state="CLEAN", behind_by=0, now=now)
    check("green.reason", r["reason_code"], "NO_BLOCKER")
    check("green.actionable", r["actionable"], False)

    # 2. required 失败 → 人工、起点取 completedAt
    bad = {**_GREEN, "conclusion": "FAILURE", "completedAt": "2026-09-09T11:00:00Z"}
    r = classify(integration="PR_OPEN", checks=[bad], required=["lint"],
                 mergeable="MERGEABLE", merge_state="BLOCKED", behind_by=0, now=now)
    check("failed.reason", r["reason_code"], "REQUIRED_CHECK_FAILED")
    check("failed.actionable", r["actionable"], True)
    check("failed.since", r["blocked_since"], "2026-09-09T11:00:00+00:00")
    check("failed.duration", r["blocked_duration"], "1h0m")

    # 3. pending → 机器
    pend = {**_GREEN, "status": "IN_PROGRESS", "conclusion": ""}
    r = classify(integration="PR_OPEN", checks=[pend], required=["lint"],
                 mergeable="MERGEABLE", merge_state="BLOCKED", behind_by=0, now=now)
    check("pending.reason", r["reason_code"], "REQUIRED_CHECK_PENDING")
    check("pending.owner", r["owner"], "machine")

    # 4. 实时样本：全绿 + CONFLICTING（#1173）→ 冲突优先，人工
    r = classify(integration="READY", checks=[_GREEN], required=["lint"],
                 mergeable="CONFLICTING", merge_state="DIRTY", behind_by=3, now=now,
                 conflict_onset="2026-09-09T09:30:00Z")
    check("conflict.reason", r["reason_code"], "CONFLICTING")
    check("conflict.actionable", r["actionable"], True)
    check("conflict.beats_behind", r["blocked_since"], "2026-09-09T09:30:00+00:00")
    check("conflict.source_is_upper_bound", "上界" in r["blocked_since_source"], True)

    # 5. 落后且全绿 → 机器（队列会自动 update-branch）
    r = classify(integration="READY", checks=[_GREEN], required=["lint"],
                 mergeable="MERGEABLE", merge_state="BEHIND", behind_by=4, now=now,
                 conflict_onset="2026-09-09T10:00:00Z")
    check("behind.reason", r["reason_code"], "BEHIND_MAIN")
    check("behind.actionable", r["actionable"], False)
    check("behind.duration", r["blocked_duration"], "2h0m")

    # 6. 非 required 的失败不得计入阻塞
    other = {**_GREEN, "name": "pr-agent-review", "conclusion": "FAILURE"}
    r = classify(integration="READY", checks=[_GREEN, other], required=["lint"],
                 mergeable="MERGEABLE", merge_state="CLEAN", behind_by=0, now=now)
    check("nonrequired.reason", r["reason_code"], "NO_BLOCKER")

    # 7. 工具函数
    check("runid", parse_run_id("https://github.com/o/r/actions/runs/34337998363/job/1"),
          34337998363)
    check("runid.none", parse_run_id("https://example.com/x"), None)
    check("dur.s", fmt_duration(9), "9s")
    check("dur.d", fmt_duration(90000), "1d1h")
    check("dur.neg", fmt_duration(-5), None)

    # 8. 失败日志摘录（真实 ruff 日志形态）
    log = (
        "lint\tRuff\t2026-09-09T10:02:36.5349611Z F821 Undefined name `_monotonic`\n"
        "lint\tRuff\t2026-09-09T10:02:36.5350392Z   --> backend/services/run_console.py:71:16\n"
    )
    got = extract_failure_excerpt(log)
    check("excerpt.failure", got["failure"], ["F821 Undefined name `_monotonic`"])
    check("excerpt.location", got["location"], "backend/services/run_console.py:71:16")

    if fails:
        print("[FAIL] self-test")
        for f in fails:
            print("  -", f)
        return 1
    print("[OK] self-test：8 组红绿样例双向通过")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="队首阻塞遥测（只读）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--pr", type=int, default=None, help="指定 PR 而非自动判定队首")
    ap.add_argument("--no-log", action="store_true", help="跳过失败日志抓取（省 API 调用）")
    ap.add_argument("--self-test", action="store_true", help="离线红绿自证")
    ap.add_argument("--cwd", default=".", help="gh 工作目录（默认当前目录）")
    args = ap.parse_args()

    if args.self_test:
        return run_self_test()

    repo = repo_slug(args.cwd)
    if not repo:
        print("[ERROR] 无法解析仓库（gh repo view 失败）", file=sys.stderr)
        return 2

    record = collect(repo, args.cwd, args.pr, with_log=not args.no_log)
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        print(render_human(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
