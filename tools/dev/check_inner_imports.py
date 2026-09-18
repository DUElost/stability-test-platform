#!/usr/bin/env python3
"""函数体内局部 import 棘轮门禁（#738）。

背景：函数体内的 import（"lazy import"）常被用来**绕开循环依赖**——代码能跑，
但依赖关系被藏进运行时：静态门禁（分层检查、依赖图、孤儿模型扫描）都看不见它，
下一次重构也无从判断「这个模块到底依赖谁」。#738 要求把存量**锁在基线上、只降
不升**；解耦掉若干处之后，在同一个 PR 里把 `_BASELINE` 调小（棘轮方向）。

判据：``backend/`` ``tools/`` ``scripts/`` 生产面里，处于**函数/方法体内**的
``import`` / ``from … import`` 语句总数 ≤ `_BASELINE`。

不计入（与 `tools/dev/audit_silent_exceptions.py` 同一口径）：
- 测试（``tests/``、``test_*.py``）；
- 已发布脚本版本（``backend/agent/scripts/<name>/v*/``，ADR-0020 不可修改）；
- alembic 历史 revision（``backend/alembic/versions/``，#2258 不可改写）；
- vendored 第三方（``backend/agent/resources/``）。

也不计入**模块顶层**的 import——包括顶层 ``try:`` 里的条件导入（那是平台差异
适配，不是依赖遮掩）。

退出码：超基线 → 1（列出超出量与增长最多的文件）；扫描面塌陷 → 2；否则 0。
``--self-test`` 离线红绿自证；``--list`` 打印全部命中点（解耦时按图索骥）。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = (ROOT / "backend", ROOT / "tools", ROOT / "scripts")

#: 棘轮基线：2026-09-18 #736 startup_identity 搬家后实测 **607**（原 610；
#: `main()` 内身份相关局部 import 随模块迁出）。**只许下调**。
#: 下调时机：解耦掉若干处局部 import 的同一个 PR 里。
#: （issue #738 记录的是 2026-09-03 的 634 处；口径与扫描面当时未固化。）
_BASELINE = 607

_FROZEN_SCRIPT_RE = re.compile(r"^backend/agent/scripts/[^/]+/v[0-9][^/]*/")
_FROZEN_ALEMBIC_PREFIX = "backend/alembic/versions/"
_VENDORED_PREFIXES = ("backend/agent/resources/",)


def _is_excluded(rel: str, name: str) -> bool:
    if "/tests/" in f"/{rel}" or name.startswith("test_"):
        return True
    return (
        bool(_FROZEN_SCRIPT_RE.match(rel))
        or rel.startswith(_FROZEN_ALEMBIC_PREFIX)
        or rel.startswith(_VENDORED_PREFIXES)
    )


def inner_import_lines(source: str) -> list[int]:
    """函数/方法体内的 import 行号（模块顶层的条件导入不算）。"""
    tree = ast.parse(source)
    lines: list[int] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.depth = 0

        def visit_FunctionDef(self, node: ast.AST) -> None:  # noqa: N802 - ast 约定
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node: ast.AST) -> None:  # noqa: N802
            # 类体里的 import 虽在缩进内，但仍是「模块加载时执行」——
            # 只有函数体才是运行期才解析的依赖遮掩。
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
            if self.depth:
                lines.append(node.lineno)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
            if self.depth:
                lines.append(node.lineno)

    _Visitor().visit(tree)
    return lines


def scan(root: Path = ROOT) -> dict[str, list[int]]:
    """{仓库相对路径: [行号, …]}（只收命中项）。"""
    found: dict[str, list[int]] = {}
    for base in SCAN_DIRS if root is ROOT else (root,):
        for path in sorted(base.rglob("*.py")):
            try:
                rel = path.relative_to(ROOT).as_posix()
            except ValueError:
                rel = path.as_posix()
            if _is_excluded(rel, path.name):
                continue
            try:
                lines = inner_import_lines(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            if lines:
                found[rel] = lines
    return found


def _self_test() -> int:
    """离线红绿自证：函数体内的算、模块顶层与顶层 try 里的不算。"""
    nested = "def f():\n    import os\n    from x import y\n"
    if inner_import_lines(nested) != [2, 3]:
        print("[self-test] 函数体内的 import 未被识别", file=sys.stderr)
        return 1

    top = "import os\nfrom x import y\n"
    if inner_import_lines(top):
        print("[self-test] 模块顶层 import 被误判", file=sys.stderr)
        return 1

    conditional = "try:\n    import ujson\nexcept ImportError:\n    ujson = None\n"
    if inner_import_lines(conditional):
        print("[self-test] 顶层条件导入被误判（应是平台适配，不是依赖遮掩）", file=sys.stderr)
        return 1

    cls = "class C:\n    import os\n"
    if inner_import_lines(cls):
        print("[self-test] 类体 import 被误判（类体在模块加载时执行）", file=sys.stderr)
        return 1

    if not _is_excluded("backend/agent/scripts/x/v1.0.0/x.py", "x.py"):
        print("[self-test] 已发布脚本版本未被排除", file=sys.stderr)
        return 1
    if _is_excluded("backend/services/foo.py", "foo.py"):
        print("[self-test] 生产文件被误排除", file=sys.stderr)
        return 1

    print("[OK] check_inner_imports self-test 通过（函数体内/顶层/条件/类体四态可判）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="函数体局部 import 棘轮门禁（#738）")
    parser.add_argument("--self-test", action="store_true", help="离线红绿自证")
    parser.add_argument("--list", action="store_true", help="打印全部命中点")
    parser.add_argument("--top", type=int, default=10, help="非 --list 时只列前 N 个文件")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    found = scan()
    if not found:
        print("[FAIL] 扫描面为空——什么都没检查", file=sys.stderr)
        return 2

    total = sum(len(v) for v in found.values())

    if args.list:
        for rel, lines in sorted(found.items()):
            for lineno in lines:
                print(f"{rel}:{lineno}")
        print(f"# 合计 {total} 处 / {len(found)} 文件；基线 {_BASELINE}")
        return 0

    if total > _BASELINE:
        top = sorted(found.items(), key=lambda kv: (-len(kv[1]), kv[0]))[: args.top]
        print(
            f"[FAIL] 函数体内 import {total} 处 > 基线 {_BASELINE}（超出 {total - _BASELINE}）\n"
            + "\n".join(f"  {len(v):4d}  {rel}" for rel, v in top)
            + "\n新代码请优先**重构消除循环依赖**，而不是在函数体内 import 绕开它；"
            "确有必要时在 PR 描述里写明理由并同步上调基线（棘轮允许，但要留痕）。",
            file=sys.stderr,
        )
        return 1

    print(f"[OK] 函数体内 import {total} 处 ≤ 基线 {_BASELINE}（{len(found)} 个文件）")
    if total < _BASELINE:
        print(
            f"提示：已低于基线 {_BASELINE - total} 处——请在同一个 PR 里把 "
            f"_BASELINE 调到 {total}（棘轮只降不升）。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
