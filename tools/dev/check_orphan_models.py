#!/usr/bin/env python3
"""孤立 ORM 模型挂载门禁（#1890-B，源自 #734 评论要求）。

规则：`backend/models/` 下继承 `Base` 的模型类，若**类名**在
「backend/（除 models/、tests/、alembic/）+ tools/ + scripts/」中零引用 → 变红。
即「新增了 ORM 模型，却没有任何消费方（查询/写入/服务引用）」——#734 的
`ActionTemplate` 幽灵（模型删了、表还在）的镜像形态：模型在、无人用。

引用计数只认**使用点**（#2062：AST 的 `Name`/`Attribute`/import 别名，
外加「整串即标识符」的字符串常量——覆盖 `relationship("Foo")` 这类惯用法）；
注释、文档字符串等散文不再算引用——原实现的整文件正则让「被提及」等于「被使用」，
正是 #734 那类幽灵模型可以存活的口子。仅用于 metadata 注册的模块路径导入仍不算消费方。

豁免：`_LEGACY_ALLOWLIST` 列出存量、经确认属「有意保留的历史表模型」；
新增条目必须在 PR 里写明理由（门禁只保证可见性，不代替裁决）。

退出码：有违规 → 1 并列出模型与定义位置；**解析不到任何模型 → 2**（#2062：
「什么都没检查」不能长得像「全绿」）；否则 0。`--self-test` 离线红绿自证。
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / "backend" / "models"
SCAN_DIRS = (ROOT / "backend", ROOT / "tools", ROOT / "scripts")
_SKIP_PARTS = {"models", "tests", "alembic"}

#: 存量豁免：ADR-0020 一次性迁移的审计表（写方是已执行完的迁移动作；
#: 表按 ADR 保留 ≥6 个月，模型仅作 metadata 注册）。新增豁免必须写明理由。
_LEGACY_ALLOWLIST = {
    "PlanMigrationAudit",
}


def _display(path: Path) -> Path:
    """仓库内路径显示相对路径；self-test 的临时目录显示绝对路径。"""
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def model_classes(models_dir: Path = MODELS_DIR) -> dict[str, Path]:
    """模型类名 → 定义文件（基类名含 ``Base`` 视为 ORM 模型）。"""
    found: dict[str, Path] = {}
    for path in sorted(models_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                "Base" in ast.unparse(base) for base in node.bases
            ):
                found[node.name] = path
    return found


def referenced_names(path: Path) -> set[str]:
    """文件里的**使用点**标识符（AST）——注释/散文不算引用（#2062）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError):
        return set()
    names: set[str] = set()
    # 文档字符串节点：即使整串恰好是标识符也不算引用（#2062）
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[-1])
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            # `relationship("Foo")` / `backref("Foo")` 这类字符串引用：仅当整串
            # 就是标识符才算（散文/路径/句子都排除）。
            value = node.value.strip()
            if value.isidentifier():
                names.add(value)
    return names


def scan(
    scan_dirs: tuple[Path, ...] = SCAN_DIRS,
    models_dir: Path = MODELS_DIR,
) -> list[str]:
    """返回零引用模型清单（`类名: 定义文件`），豁免项不计。"""
    classes = model_classes(models_dir)
    if not classes:
        return []
    referenced: set[str] = set()
    for root in scan_dirs:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if _SKIP_PARTS & set(path.parts):
                continue
            for name in referenced_names(path):
                if name in classes:
                    referenced.add(name)
    return [
        f"{name}: {_display(classes[name])}"
        for name in sorted(classes)
        if name not in referenced and name not in _LEGACY_ALLOWLIST
    ]


def _self_test() -> int:
    """红绿双向：合成模型目录 + 扫描目录，验证「有引用/无引用/豁免」三态。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        models = base / "backend" / "models"
        consumer = base / "backend" / "services"
        models.mkdir(parents=True)
        consumer.mkdir(parents=True)
        (models / "ghost.py").write_text(
            "class GhostModel(Base):\n    pass\n", encoding="utf-8",
        )
        (models / "wired.py").write_text(
            "class WiredModel(Base):\n    pass\n", encoding="utf-8",
        )
        (consumer / "use.py").write_text(
            "from backend.models.wired import WiredModel\n\n"
            "def q(db):\n    return db.query(WiredModel)\n",
            encoding="utf-8",
        )

        findings = scan((base / "backend",), base / "backend" / "models")
        ok = any("GhostModel" in f for f in findings) and not any(
            "WiredModel" in f for f in findings
        )
        # 豁免生效：临时把 GhostModel 加入豁免后不再报告
        global _LEGACY_ALLOWLIST
        original = _LEGACY_ALLOWLIST
        _LEGACY_ALLOWLIST = original | {"GhostModel"}
        try:
            exempted = scan((base / "backend",), base / "backend" / "models")
        finally:
            _LEGACY_ALLOWLIST = original
        ok = ok and not any("GhostModel" in f for f in exempted)

        # models 目录自身与 tests/ 不构成引用
        (models / "selfref.py").write_text(
            "class SelfRefModel(Base):\n    pass\n\n"
            "x = SelfRefModel\n",
            encoding="utf-8",
        )
        ok = ok and any(
            "SelfRefModel" in f
            for f in scan((base / "backend",), base / "backend" / "models")
        )

        # #2062：散文提及（注释 + 恰好是标识符的文档字符串）不算引用
        (consumer / "prose.py").write_text(
            "# GhostModel 只是被提到，不是消费方\n"
            '"""GhostModel"""\n',
            encoding="utf-8",
        )
        prose_findings = scan((base / "backend",), base / "backend" / "models")
        ok = ok and any("GhostModel" in f for f in prose_findings)

        # #2062：字符串常量里作为标识符出现仍算引用（relationship("WiredModel") 惯用法）
        (consumer / "stringref.py").write_text(
            "class R:\n"
            "    target = 'WiredModel'\n",
            encoding="utf-8",
        )
        ok = ok and not any(
            "WiredModel" in f
            for f in scan((base / "backend",), base / "backend" / "models")
        )

        if not ok:
            print(
                "[FAIL] self-test：预期 GhostModel 红（含仅散文提及）、"
                "WiredModel 绿（含字符串引用）、豁免绿",
                file=sys.stderr,
            )
            return 1
    print("[OK] check_orphan_models self-test 红绿双向")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()

    classes = model_classes()
    if not classes:
        # #2062：空跑不能长得像全绿——models 目录被改名/清空或全部语法错误时，
        # 原先打印「无零消费方模型」，与「检查通过」不可区分。
        print(
            "[FAIL] backend/models 下没有解析到任何 ORM 模型——门禁无法给出结论"
            "（不是「全绿」）：检查目录是否被改名/清空，或模型文件是否有语法错误。",
            file=sys.stderr,
        )
        return 2

    findings = scan()
    if findings:
        print("[FAIL] 孤立 ORM 模型（零消费方，#1890-B / #734）：", file=sys.stderr)
        for item in findings:
            print(f"        {item}", file=sys.stderr)
        print(
            "        修法：接线到服务/路由，或删除模型；确属有意保留的历史表模型"
            "请加入 _LEGACY_ALLOWLIST 并在 PR 写明理由。",
            file=sys.stderr,
        )
        return 1
    print("[OK] 孤立模型门禁：无零消费方模型")
    return 0


if __name__ == "__main__":
    sys.exit(main())
