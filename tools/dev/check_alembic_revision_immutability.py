#!/usr/bin/env python3
"""alembic revision 不可变门禁（#2258，承接 #2046 的结构性建议）。

背景（2026-09-13 06:20Z–07:25Z 的 rechain 窗口）：**已合入 main 的 revision** 被反复改写
`down_revision`（6 次改写跨 3 个 revision，其中 5 个是 seed 迁移，只有 `dd44ee55ff66`
由 #1717 补了重放迁移）。对在该窗口执行过 `alembic upgrade` 的库，新插入到其当前版本
**之前**的 revision **永远不会执行**——而这类库的 `alembic_version` 仍是 head，
`tools/dev/check_alembic_at_head.py` 的**等值**判定判绿：库缺陷与「已对齐」在护栏看来一样。

**第二例（2026-09-15，`02941d3c` / #2055）——本门禁落地之后被发现**：两个已合入的
seed revision 被**原地改写函数体**（`z1a2b3c4d5e6` 补 `is_active = true` 分支、
`y0z1a2b3c4d5` 补停用前的 `plan_step` 引用核对），同样没有附重放迁移；对已 upgrade 的
库，改后的代码永远不会执行，影响面与第一例同类（#2322）。已由 `d4e5f6a7b8c9` 重放迁移
收口。**本门禁只对比当次 diff 与 base，对历史改写没有回溯能力**——所以「存量清单」必须
逐例登记，不能只记第一次事件，否则后来者从本文件读到的是「这类事只发生过一次」。
已知两例：2026-09-13 rechain 窗口（5 个 seed，`dd44ee55ff66` 由 #1717 补重放）、
2026-09-15 函数体改写（2 个 seed，`d4e5f6a7b8c9` 由 #2322 补重放）。

本门禁把「已合入 main 的 revision 文件不可变」机械化，唯一豁免是**同一 diff 里为被改写的
revision 附了重放迁移**（新增 revision 的 `down_revision` 指向它）——即 #1717 的先例形态。

契约：
- **不可变面** = `backend/alembic/versions/*.py` 中**基线已存在**的文件；
  修改 / 删除 / 改名 / 类型变更一律违约；
- **豁免**：本 diff 新增（A）的 revision 文件里，有 `down_revision`（字符串、元组均可）
  指向被改写文件的 `revision` id → 该文件放行并打印留痕；没有豁免即红；
- **新增 revision 文件本身永远安全**（新链上的库会正常执行它）。

违约后的正确做法（不是找豁免）：
1. 只改**未发布**的 revision —— 合并前 rechain；
2. 已经合入 main 且**必须**改写时：同一 PR 附一个重放迁移
   （`down_revision` 指向被改写的 revision，`WHERE ... 命中才写` 的幂等自愈），
   并在 PR / Agent Note 里写明「哪些库会漏跑、如何自愈」。

用法：
    python tools/dev/check_alembic_revision_immutability.py            # 对比 origin/main
    python tools/dev/check_alembic_revision_immutability.py --base <ref>
    python tools/dev/check_alembic_revision_immutability.py --self-test
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

REVISION_ROOT = "backend/alembic/versions"
DEFAULT_BASE = "origin/main"

_STRING_LITERAL = re.compile(r"""["']([^"']+)["']""")
# `revision = "abc"` / `revision: str = "abc"`（alembic 模板两种写法都出现过）
_REVISION_DECL = re.compile(
    r"""^revision(?:\s*:\s*\w+)?\s*=\s*(?P<rhs>.+)$""", re.MULTILINE
)
_DOWN_DECL = re.compile(
    r"""^down_revision(?:\s*:\s*[^=]+)?\s*=\s*(?P<rhs>.+)$""", re.MULTILINE
)

# 修改/删除/改名/类型变更都会让既有 revision 的字节变化；「新增」永远安全。
_MUTATING_STATUS = {"M": "修改", "D": "删除", "R": "改名", "T": "类型变更"}
ADDED_STATUS = "A"

Violation = tuple[str, str, str]  # path, action, revision_id


def parse_revision_id(text: str) -> str | None:
    """从 revision 文件源码里取 `revision` 声明值（纯函数）。"""
    match = _REVISION_DECL.search(text)
    if not match:
        return None
    literals = _STRING_LITERAL.findall(match.group("rhs"))
    return literals[0] if literals else None


def parse_down_revisions(text: str) -> set[str]:
    """取 `down_revision` 的全部父 id——None / 字符串 / 元组三种形态（纯函数）。"""
    match = _DOWN_DECL.search(text)
    if not match:
        return set()
    return set(_STRING_LITERAL.findall(match.group("rhs")))


def is_revision_path(path: str) -> bool:
    return path.startswith(f"{REVISION_ROOT}/") and path.endswith(".py")


def classify_change(
    status: str,
    path: str,
    revision_id: str | None,
    exempt_ids: set[str],
) -> Violation | None:
    """单条 name-status 变更 → 违约或 None（纯函数，供 --self-test）。"""
    if not is_revision_path(path):
        return None
    if status == ADDED_STATUS:
        return None
    if status not in _MUTATING_STATUS:
        return None
    # 豁免只对「修改」成立：重放迁移能补上被改写 revision 漏跑的效果，但**补不了**
    # 被删除/改名者的存在性——停在该版本的库会直接找不到它。
    if status == "M" and revision_id is not None and revision_id in exempt_ids:
        return None
    return (path, _MUTATING_STATUS[status], revision_id or "?")


def collect_violations(
    changed: list[tuple[str, str, str | None]],
    exempt_ids: set[str],
) -> list[Violation]:
    """批量分类（纯函数）：changed = [(status, path, revision_id)]。"""
    out: list[Violation] = []
    for status, path, revision_id in changed:
        hit = classify_change(status, path, revision_id, exempt_ids)
        if hit is not None:
            out.append(hit)
    return out


def _git(*args: str) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败:\n{proc.stderr.strip()}")
    return proc.stdout


def _changed_paths(base: str) -> list[tuple[str, str]]:
    """三点 diff = 与 merge-base 比，只看本分支引入的改动。"""
    raw = _git("diff", "--name-status", "-M", f"{base}...HEAD", "--", REVISION_ROOT)
    out: list[tuple[str, str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0][:1]
        # 改名是 "R100\told\tnew" —— 旧路径消失了，按旧路径记违约。
        out.append((status, parts[1]))
    return out


def _read_text(ref: str, path: str) -> str:
    return _git("show", f"{ref}:{path}")


def _exempt_ids_from_additions(base: str, added: list[str]) -> dict[str, str]:
    """新增 revision 的 `down_revision` 集合 → {父 id: 新增文件路径}。"""
    exempt: dict[str, str] = {}
    for path in added:
        try:
            text = _read_text("HEAD", path)
        except SystemExit:
            continue
        for parent in parse_down_revisions(text):
            exempt.setdefault(parent, path)
    return exempt


def run_check(base: str) -> int:
    changed = _changed_paths(base)
    if not changed:
        print(f"[OK] {REVISION_ROOT} 无变更（base={base}）")
        return 0

    rows: list[tuple[str, str, str | None]] = []
    for status, path in changed:
        revision_id = None
        if is_revision_path(path) and status in _MUTATING_STATUS:
            try:
                revision_id = parse_revision_id(_read_text(base, path))
            except SystemExit:
                revision_id = None  # 基线侧缺失（改名/删除）→ 由 status 判定
        rows.append((status, path, revision_id))

    exempt = _exempt_ids_from_additions(base, [p for s, p in changed if s == ADDED_STATUS])
    violations = collect_violations(rows, set(exempt))

    for parent_id, replay_path in sorted(exempt.items()):
        print(f"[NOTICE] 豁免：{parent_id} 有同 PR 重放迁移 {replay_path}")

    if violations:
        print("")
        for path, action, revision_id in violations:
            print(f"[BLOCK] {action}已合入的 revision: {path}（revision={revision_id}）")
        print(
            "\n已合入 main 的 revision 不可改写/删除：停在该版本之后的库不会再执行被插入的\n"
            "祖先，且 check_alembic_at_head 判不出来。正确做法：\n"
            "  1) 只改未发布的 revision（合并前 rechain）；\n"
            "  2) 必须改写已合入的 revision 时，同一 PR 附**重放迁移**——新增一个 revision，\n"
            "     down_revision 指向被改写者，body 用 `WHERE ... 命中才写` 的幂等自愈；\n"
            "     并在 PR / Agent Note 写明哪些库会漏跑。\n"
            "详见 issues #2258 / #2046、先例 #1717（dd44 重放）。",
            file=sys.stderr,
        )
        return 1

    print(f"[OK] alembic revision 不可变检查通过（base={base}，变更 {len(changed)} 项）")
    return 0


def run_self_test() -> int:
    """分类规则红绿双向自证（纯函数，不触 git）。"""
    existing = f"{REVISION_ROOT}/aaa111_old.py"
    added = f"{REVISION_ROOT}/bbb222_replay.py"

    cases: list[tuple[str, tuple, set, bool]] = [
        # (说明, (status, path, revision_id), exempt_ids, 期望违约)
        ("新增 revision 永远安全", ("A", added, None), set(), False),
        ("改写已合入 revision 且无豁免 → 红", ("M", existing, "aaa111"), set(), True),
        ("改写已合入 revision 且有重放迁移 → 绿", ("M", existing, "aaa111"), {"aaa111"}, False),
        ("删除已合入 revision → 红", ("D", existing, "aaa111"), {"aaa111"}, True),
        ("改名已合入 revision → 红", ("R", existing, "aaa111"), {"aaa111"}, True),
        ("非 revision 路径的改动不参与", ("M", "backend/scheduler/cron_scheduler.py", None), set(), False),
    ]
    failures = 0
    for label, (status, path, revision_id), exempt, expect_violation in cases:
        hit = classify_change(status, path, revision_id, exempt)
        ok = (hit is not None) == expect_violation
        print(f"  [{'OK' if ok else 'FAIL'}] {label}")
        failures += 0 if ok else 1

    parse_cases = [
        ('revision = "abc123"', "abc123"),
        ('revision: str = "abc123"', "abc123"),
        ("revision = None", None),
    ]
    for text, expected in parse_cases:
        got = parse_revision_id(text)
        ok = got == expected
        print(f"  [{'OK' if ok else 'FAIL'}] parse_revision_id({text!r}) -> {got!r}")
        failures += 0 if ok else 1

    down_cases = [
        ('down_revision = "p1"', {"p1"}),
        ('down_revision = ("p1", "p2")', {"p1", "p2"}),
        ("down_revision = None", set()),
        ("down_revision: str | None = 'p3'", {"p3"}),
    ]
    for text, expected in down_cases:
        got = parse_down_revisions(text)
        ok = got == expected
        print(f"  [{'OK' if ok else 'FAIL'}] parse_down_revisions({text!r}) -> {sorted(got)}")
        failures += 0 if ok else 1

    if failures:
        print(f"\n[FAIL] self-test 失败 {failures} 项", file=sys.stderr)
        return 1
    print("\n[OK] self-test 通过（分类规则与解析器红绿双向）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="alembic revision 不可变门禁（#2258）")
    parser.add_argument("--base", default=DEFAULT_BASE, help="对比基线（默认 origin/main）")
    parser.add_argument("--self-test", action="store_true", help="离线红绿自证")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()
    return run_check(args.base)


if __name__ == "__main__":
    sys.exit(main())
