#!/usr/bin/env python3
"""结构日报：每日审计的「结构」一页（只读，纯 git，不联网、不碰数据库）。

每日审计核对的是「修复有没有落地」（行为面）；本脚本补「这一段时间结构往哪走」：

  1. 合入构成        —— 窗口内合入 PR 按分支前缀计数（fix/feat/docs/...）
  2. 边界合约基线    —— .importlinter 各合约 ignore_imports 行数，窗口起点 → 现在
  3. 三次法则热点    —— 滚动窗口内被 ≥N 个 fix PR 改过的生产文件（止血 vs 根治的信号）
  4. 治理面增量      —— 窗口内新增的门禁脚本（tools/dev）与根目录契约测试（tests/）
  5. 过渡登记        —— docs/governance/transitions.json 在役条目，起点 → 现在
  6. 兜底/兼容标记   —— 生产代码中「兜底 / fallback / legacy / 兼容」行数，起点 → 现在

用法：
    python tools/dev/structure_digest.py                    # 近 1 天合入，热点窗口 7 天
    python tools/dev/structure_digest.py --since "3 days ago" --hotspot-days 14
    python tools/dev/structure_digest.py --self-test

读法：基线数只该下降；热点清单是冲刺后结算的候选（同一文件反复被 fix 说明在止血）；
治理面增量应能说明各自合并或替代了哪条既有门禁。本脚本只出报告，不设阈值、不判红。
"""

from __future__ import annotations

import argparse
import configparser
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_MERGE_RE = re.compile(r"^Merge pull request #(\d+) from [^/]+/([^/\s]+)")
_PROD_RE = re.compile(r"^(backend|frontend/src)/")
_NON_PROD_RE = re.compile(
    r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]*$|\.test\.[jt]sx?$"
    r"|^backend/alembic/versions/|^backend/agent/resources/"
)
_MARKER_RE = r"兜底|fallback|legacy|兼容"
_PROD_PATHSPEC = [
    "backend/*.py", "frontend/src/*.ts", "frontend/src/*.tsx",
    ":(exclude)backend/tests", ":(exclude)backend/agent/tests",
    ":(exclude)backend/alembic/versions", ":(exclude)backend/agent/resources",
    ":(exclude)*test_*", ":(exclude)*.test.*", ":(exclude)*__tests__*",
]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout


def _rev_before(when: str) -> str | None:
    rev = _git("rev-list", "-1", "--first-parent", f"--before={when}", "HEAD").strip()
    return rev or None


def _merges(since: str) -> list[tuple[str, str, str]]:
    """[(sha, pr_number, branch_prefix)]，只看 first-parent 上的 PR 合入。"""
    out = []
    for line in _git(
        "log", "--first-parent", "--merges", f"--since={since}", "--format=%H\t%s", "HEAD",
    ).splitlines():
        sha, _, subject = line.partition("\t")
        m = _MERGE_RE.match(subject)
        if m:
            out.append((sha, m.group(1), m.group(2)))
    return out


def _is_prod(path: str) -> bool:
    return bool(_PROD_RE.match(path)) and not _NON_PROD_RE.search(path)


def contract_baseline(text: str | None) -> dict[str, int]:
    """每条合约的 ignore_imports 行数；text 为 None（文件不存在）返回空。"""
    if not text:
        return {}
    cp = configparser.ConfigParser()
    cp.read_string(text)
    result: dict[str, int] = {}
    for section in cp.sections():
        if not section.startswith("importlinter:contract:"):
            continue
        raw = cp.get(section, "ignore_imports", fallback="")
        lines = [ln.strip() for ln in raw.splitlines()]
        result[section.split(":", 2)[2]] = sum(1 for ln in lines if ln and not ln.startswith("#"))
    return result


def active_transitions(text: str | None) -> set[str]:
    if not text:
        return set()
    data = json.loads(text)
    return {t["id"] for t in data.get("transitions", []) if t.get("status") == "active"}


def _show(rev: str | None, path: str) -> str | None:
    if rev is None:
        return None
    proc = subprocess.run(
        ["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def _marker_lines(rev: str) -> int:
    out = _git("grep", "-c", "-E", _MARKER_RE, rev, "--", *_PROD_PATHSPEC)
    return sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines() if ":" in line)


def _delta(a: int | None, b: int) -> str:
    if a is None:
        return f"{b}"
    d = b - a
    return f"{a} → {b}（{'+' if d > 0 else ''}{d}）" if d else f"{b}（持平）"


def build_report(since: str, hotspot_days: int, threshold: int) -> str:
    base = _rev_before(since)
    lines = [f"# 结构日报（{since} → HEAD {_git('rev-parse', '--short', 'HEAD').strip()}）", ""]

    merges = _merges(since)
    kinds = Counter(prefix for _, _, prefix in merges)
    lines += ["## 1. 合入构成", ""]
    lines.append(
        f"合入 {len(merges)} 个 PR：" + "、".join(f"{k} {v}" for k, v in kinds.most_common())
        if merges else "窗口内无合入。"
    )
    lines.append("")

    lines += ["## 2. 边界合约基线（只该下降）", ""]
    now = contract_baseline(_show("HEAD", ".importlinter"))
    before = contract_baseline(_show(base, ".importlinter"))
    if not now:
        lines.append("HEAD 上没有 .importlinter。")
    else:
        lines += ["| 合约 | 忽略项 |", "| --- | --- |"]
        for name, count in now.items():
            lines.append(f"| {name} | {_delta(before.get(name) if before else None, count)} |")
        total_before = sum(before.values()) if before else None
        lines.append(f"| **合计** | **{_delta(total_before, sum(now.values()))}** |")
    lines.append("")

    lines += [f"## 3. 三次法则热点（{hotspot_days} 天内被 ≥{threshold} 个 fix PR 改过）", ""]
    touched: Counter[str] = Counter()
    for sha, _, prefix in _merges(f"{hotspot_days} days ago"):
        if prefix != "fix":
            continue
        for path in set(_git("diff", "--name-only", f"{sha}^1", sha).split()):
            if _is_prod(path):
                touched[path] += 1
    hot = [(p, n) for p, n in touched.most_common() if n >= threshold]
    if hot:
        lines += ["| fix PR 数 | 文件 |", "| --- | --- |"]
        lines += [f"| {n} | `{p}` |" for p, n in hot]
    else:
        lines.append("无。")
    lines.append("")

    lines += ["## 4. 治理面增量（窗口内新增）", ""]
    added = []
    if base:
        added = [
            p for p in _git("diff", "--name-only", "--diff-filter=A", base, "HEAD", "--",
                            "tools/dev", "tests").split()
            if p.endswith(".py")
        ]
    lines += [f"- `{p}`" for p in added] or ["无。"]
    lines.append("")

    lines += ["## 5. 过渡登记（在役）", ""]
    t_now = active_transitions(_show("HEAD", "docs/governance/transitions.json"))
    before_text = _show(base, "docs/governance/transitions.json")
    if before_text is None:
        # 窗口起点还没有登记簿：只报现状，不把存量全算成「新增」
        lines.append(f"在役 {len(t_now)}（窗口起点无登记簿，不计增量）")
    else:
        t_before = active_transitions(before_text)
        lines.append(f"在役 {_delta(len(t_before), len(t_now))}")
        for tid in sorted(t_now - t_before):
            lines.append(f"- 新增 `{tid}`")
        for tid in sorted(t_before - t_now):
            lines.append(f"- 收口 `{tid}`")
    lines.append("")

    lines += ["## 6. 兜底 / fallback / legacy / 兼容 标记（生产代码行数）", ""]
    lines.append(_delta(_marker_lines(base) if base else None, _marker_lines("HEAD")))
    lines.append("")
    return "\n".join(lines)


def _self_test() -> int:
    ini = (
        "[importlinter]\nroot_packages =\n    backend\n\n"
        "[importlinter:contract:c1]\nname = C1\ntype = layers\nlayers =\n    a\n    b\n"
        "ignore_imports =\n    # 注释行不计\n    a.x -> b.y\n    a.z -> b.w\n\n"
        "[importlinter:contract:c2]\nname = C2\ntype = forbidden\n"
        "source_modules =\n    a\nforbidden_modules =\n    b\n"
    )
    trans = json.dumps({"transitions": [
        {"id": "x", "status": "active"}, {"id": "y", "status": "done"},
    ]})
    checks = [
        (contract_baseline(ini) == {"c1": 2, "c2": 0}, "contract_baseline"),
        (contract_baseline(None) == {}, "contract_baseline(None)"),
        (active_transitions(trans) == {"x"}, "active_transitions"),
        (_is_prod("backend/services/a.py"), "prod: services"),
        (not _is_prod("backend/tests/services/test_a.py"), "non-prod: tests"),
        (not _is_prod("frontend/src/a.test.tsx"), "non-prod: vitest"),
        (not _is_prod("docs/a.md"), "non-prod: docs"),
        (bool(_MERGE_RE.match("Merge pull request #12 from o/fix/12-x")), "merge subject"),
        (_delta(3, 1) == "3 → 1（-2）" and _delta(None, 4) == "4", "delta"),
    ]
    bad = [name for ok, name in checks if not ok]
    if bad:
        print("[FAIL] structure_digest self-test:", bad, file=sys.stderr)
        return 1
    print("[OK] structure_digest self-test")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="1 day ago", help="合入与增量的窗口起点（git 日期表达式）")
    parser.add_argument("--hotspot-days", type=int, default=7, help="热点滚动窗口天数")
    parser.add_argument("--threshold", type=int, default=3, help="热点判定的 fix PR 数下限")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return _self_test()
    print(build_report(args.since, args.hotspot_days, args.threshold))
    return 0


if __name__ == "__main__":
    sys.exit(main())
