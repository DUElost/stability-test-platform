#!/usr/bin/env python3
"""静默异常吞咽审计（#739 §2，只读）。

背景：#739 把「431 处静默异常吞咽」列为治理对象，但那个数字是 2026-09-02 的
一次粗测，口径与扫描面都没固化——本工具先把**口径**写死，产出一份可复现的基线，
供后续裁决「要不要上门禁、怎么分批治理」。**本工具只读、不阻断**（恒 exit 0，
扫描面塌陷除外），也不改动任何被扫代码。

口径（三类，按「异常被吞掉后的可观测性」排）：

- ``pass``：handler 体只有 ``pass``；
- ``continue``：handler 体只有 ``continue``（循环里跳过）；
- ``return_none``：handler 体只有 ``return`` / ``return None``（把错误变成缺省值）。

不计入：handler 体里有任何其它语句（哪怕只是一行 ``logger.debug``）——那些**不是
静默**；本工具不判断日志级别是否恰当（那是评审的事）。

扫描面：``backend/`` ``tools/`` ``scripts/`` 下的生产代码，排除

- 测试（``tests/`` 目录、``test_*.py``）；
- **已发布脚本版本**（``backend/agent/scripts/<name>/v<version>/``，ADR-0020 不可修改）；
- **alembic 历史 revision**（``backend/alembic/versions/``，#2258 不可改写）；
- **第三方随包工具**（``backend/agent/resources/``：AIMonkey / flashtool，vendored）。

这些面若计入，会让「治理面」的规模被冻结 artifact 与第三方代码淹没（本口径实测：
全量 923 处、排除后 **254** 处）。注意 issue #739 里的「431 处」是 2026-09-02 的
另一次粗测（口径与扫描面未固化），与本工具的数字**不可直接比较**；本工具的
`--json` 输出可复现本节全部数字。

输出：默认按文件汇总的文本；``--json`` 输出机器可读明细；``--top N`` 只看前 N 个
文件。``--self-test`` 离线红绿自证（三类判定 + 排除面）。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = (ROOT / "backend", ROOT / "tools", ROOT / "scripts")

#: 已发布脚本版本目录（ADR-0020 不可修改）——排除。
# ADR-0051 Phase 3：版本目录已退役，脚本族树（backend/agent/scripts/<name>/）按包发布、
# 属独立审计面，整棵排除（此前只排除 v<version>/ 冻结目录）。
_FROZEN_SCRIPT_RE = re.compile(r"^backend/agent/scripts/[^/]+/")
#: alembic 历史 revision（#2258 不可改写）——排除。
_FROZEN_ALEMBIC_PREFIX = "backend/alembic/versions/"
#: 第三方随包工具（AIMonkey / flashtool）——**不是我们的代码**，排除。
#: 判据同 ruff.toml 的 extend-exclude（`backend/agent/resources`）：实测不排除时
#: 16 处 `except: pass` 里 15 处落在 AIMonkey 的 vendored 文件里，会把「治理面」
#: 的规模与形态都带偏。
_VENDORED_PREFIXES = ("backend/agent/resources/",)

REASONS = ("pass", "continue", "return_none")


@dataclass(frozen=True)
class Finding:
    path: str
    lineno: int
    reason: str
    exception_type: str

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "lineno": self.lineno,
            "reason": self.reason,
            "exception_type": self.exception_type,
        }


def _body_without_docstring(handler: ast.ExceptHandler) -> list[ast.stmt]:
    body = list(handler.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body


def classify_handler(handler: ast.ExceptHandler) -> str | None:
    """静默吞咽的类别；有其它语句（含日志）→ ``None``。"""
    body = _body_without_docstring(handler)
    if not body:
        return "pass"  # `except X: ...` 与 pass 同义（Ellipsis 段落亦归此类）
    if len(body) != 1:
        return None
    stmt = body[0]
    if isinstance(stmt, ast.Pass):
        return "pass"
    if isinstance(stmt, ast.Continue):
        return "continue"
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return "pass"  # `...`（与 pass 同义）
    if isinstance(stmt, ast.Return):
        value = stmt.value
        if value is None or (isinstance(value, ast.Constant) and value.value is None):
            return "return_none"
    return None


def _exception_label(handler: ast.ExceptHandler) -> str:
    node = handler.type
    if node is None:
        return "bare"
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 - 仅用于展示，不阻断审计
        return "?"


def _is_frozen(rel: str) -> bool:
    return (
        bool(_FROZEN_SCRIPT_RE.match(rel))
        or rel.startswith(_FROZEN_ALEMBIC_PREFIX)
        or rel.startswith(_VENDORED_PREFIXES)
    )


def _iter_scan_files() -> list[Path]:
    files: list[Path] = []
    for base in SCAN_DIRS:
        for path in base.rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if _is_frozen(rel) or "/tests/" in f"/{rel}" or path.name.startswith("test_"):
                continue
            files.append(path)
    return sorted(files)


def audit() -> list[Finding]:
    """扫描生产面，返回全部静默吞咽点（升序：路径 → 行号）。"""
    findings: list[Finding] = []
    for path in _iter_scan_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                reason = classify_handler(node)
                if reason is not None:
                    findings.append(
                        Finding(rel, node.lineno, reason, _exception_label(node))
                    )
    return sorted(findings, key=lambda f: (f.path, f.lineno))


def _summary(findings: list[Finding]) -> dict:
    per_reason = {reason: 0 for reason in REASONS}
    per_file: dict[str, int] = {}
    for finding in findings:
        per_reason[finding.reason] += 1
        per_file[finding.path] = per_file.get(finding.path, 0) + 1
    return {"total": len(findings), "per_reason": per_reason, "per_file": per_file}


def _self_test() -> int:
    """离线红绿自证：三类都要判得出，有日志/有其它语句的不算静默。"""
    cases = {
        "pass": "try:\n    f()\nexcept OSError:\n    pass\n",
        "continue": "for i in x:\n    try:\n        f(i)\n    except ValueError:\n        continue\n",
        "return_none": "def g():\n    try:\n        return f()\n    except KeyError:\n        return None\n",
        "bare_return": "def h():\n    try:\n        return f()\n    except KeyError:\n        return\n",
        "ellipsis": "try:\n    f()\nexcept OSError:\n    ...\n",
        # `return` 与 `return None` 同类（都是把错误变成缺省值）
    }
    expected_reason = {"bare_return": "return_none", "ellipsis": "pass"}
    for expected, src in cases.items():
        handlers = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ExceptHandler)]
        got = classify_handler(handlers[0])
        want = expected_reason.get(expected, expected)
        if got != want:
            print(f"[self-test] {expected}: 期望 {want} 实得 {got}", file=sys.stderr)
            return 1

    noisy = "try:\n    f()\nexcept OSError:\n    logger.debug('x')\n"
    handlers = [n for n in ast.walk(ast.parse(noisy)) if isinstance(n, ast.ExceptHandler)]
    if classify_handler(handlers[0]) is not None:
        print("[self-test] 有日志的 handler 被误判为静默", file=sys.stderr)
        return 1

    # 排除面：已发布脚本版本与 alembic 历史 revision 不得进入扫描结果
    frozen = "backend/agent/scripts/flash_firmware/v1.3.17/flash_firmware.py"
    if not _is_frozen(frozen) or not _is_frozen(f"{_FROZEN_ALEMBIC_PREFIX}x.py"):
        print("[self-test] 冻结面排除失效", file=sys.stderr)
        return 1
    if _is_frozen("backend/agent/pipeline_engine.py"):
        print("[self-test] 生产文件被误判为冻结面", file=sys.stderr)
        return 1

    print("[OK] audit_silent_exceptions self-test 通过（三类判定 + 冻结面排除）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="静默异常吞咽审计（#739 §2，只读）")
    parser.add_argument("--json", action="store_true", help="机器可读明细")
    parser.add_argument("--top", type=int, default=15, help="文本模式只列前 N 个文件")
    parser.add_argument("--self-test", action="store_true", help="离线红绿自证")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    files = _iter_scan_files()
    if not files:
        print("[FAIL] 扫描面为空——什么都没检查", file=sys.stderr)
        return 2

    findings = audit()
    summary = _summary(findings)

    if args.json:
        print(
            json.dumps(
                {
                    "scanned_files": len(files),
                    "summary": summary,
                    "findings": [f.as_dict() for f in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"扫描 {len(files)} 个生产文件；静默吞咽 {summary['total']} 处")
    print(
        "  按类别："
        + " / ".join(f"{k} {v}" for k, v in summary["per_reason"].items())
    )
    print(f"  文件数：{len(summary['per_file'])}（前 {args.top} 名）")
    for path, count in sorted(
        summary["per_file"].items(), key=lambda kv: (-kv[1], kv[0])
    )[: args.top]:
        print(f"    {count:4d}  {path}")
    print(
        "\n本工具只读、不阻断（#739 §2 的基线测量）；"
        "是否上门禁与分批治理见 docs/notes/process/ 的审计 Note。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
