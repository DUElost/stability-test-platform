"""#739 面③：`xlrd` / `xlwt` 的**生产**使用面冻结（依赖收敛台账，不是许可）。

为什么要冻结：`openpyxl` 不能读写 `.xls`（OLE/BIFF），替换 `xlwt` 实质是**产物格式迁移**，
需要跨方（Toolkit / JIRA 上传链 / 归档下载面）协调——完整评估与三阶段路径见
`docs/notes/process/2026-09-20-dependency-convergence-739.md`。阶段 2 启动前，本守卫保证：

1. 生产代码里 `xlrd`/`xlwt` 的使用**不扩散**（新增文件或新增库即红——先更新台账与规划）；
2. 台账**不腐烂**（已移除的使用点仍留在台账里即红，提示同步）。

测试夹具不受限（`backend/tests` / `backend/agent/tests` / 根 `tests` 里的用例用它们造
`.xls` 样本是合理的、不构成产线耦合）；判据只看生产面。

实现：AST 扫描 `backend/` `tools/` `scripts/` 下的非测试 `.py`（静态 import 与
`import_module` / `__import__` 字面量），与台账逐项比对。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("backend", "tools", "scripts")
#: 测试面（不属生产耦合）、迁移脚本目录与缓存目录不进扫描面。
SKIP_PARTS = {"tests", "alembic", "__pycache__", "resources", "node_modules"}
_LIBS = ("xlrd", "xlwt")

#: 台账：相对路径 → 允许的库集合。**新增/删除都必须同步这里与规划文档**
#: （`docs/notes/process/2026-09-20-dependency-convergence-739.md`）。
_INVENTORY: dict[str, set[str]] = {
    "backend/services/dedup_extract.py": {"xlrd"},
    "backend/services/dedup_scan.py": {"xlrd", "xlwt"},
}

_PLAN_DOC = REPO_ROOT / "docs" / "notes" / "process" / "2026-09-20-dependency-convergence-739.md"


def _imported_libs(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _LIBS:
                    found.add(top)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in _LIBS:
                found.add(top)
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if (
                name in {"import_module", "__import__"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                top = node.args[0].value.split(".")[0]
                if top in _LIBS:
                    found.add(top)
    return found


def _scan() -> dict[str, set[str]]:
    """生产面的 {相对路径: 使用的库集合}（只收非空项）。"""
    hits: dict[str, set[str]] = {}
    for dirname in SCAN_DIRS:
        for path in (REPO_ROOT / dirname).rglob("*.py"):
            if SKIP_PARTS & set(path.parts):
                continue
            libs = _imported_libs(path.read_text(encoding="utf-8"))
            if libs:
                hits[path.relative_to(REPO_ROOT).as_posix()] = libs
    return hits


def test_production_excel_usage_matches_inventory():
    """生产面使用集合必须与台账逐项一致（多出=扩散，少=台账腐烂）。"""
    scanned = _scan()
    unexpected = {
        path: sorted(libs) for path, libs in scanned.items() if path not in _INVENTORY
    }
    assert not unexpected, (
        "以下生产文件新增了对 xlrd/xlwt 的依赖（#739 面③ 台账外）：\n  "
        + "\n  ".join(f"{p}: {libs}" for p, libs in sorted(unexpected.items()))
        + "\nxlwt 无维护、openpyxl 不能读写 .xls——新增耦合前先更新"
        " docs/notes/process/2026-09-20-dependency-convergence-739.md 与 _INVENTORY"
        "（并说明为何不能避免 .xls）。"
    )

    stale = sorted(path for path in _INVENTORY if path not in scanned)
    assert not stale, (
        f"台账条目已失效（这些文件不再使用 xlrd/xlwt，请同步删除并更新规划）：{stale}"
    )

    for path, expected in sorted(_INVENTORY.items()):
        assert scanned[path] == expected, (
            f"{path} 的 xlrd/xlwt 使用集合变化：{sorted(scanned[path])} != {sorted(expected)}"
            "——台账与规划文档需同步"
        )


def test_inventory_is_linked_to_the_convergence_plan():
    """台账必须指向存在的收敛规划（防「台账在、规划丢」）。"""
    assert _PLAN_DOC.is_file(), f"收敛规划不存在：{_PLAN_DOC}"
    text = _PLAN_DOC.read_text(encoding="utf-8")
    for marker in ("openpyxl", "apscheduler", "触发条件"):
        assert marker in text, f"收敛规划缺少关键内容：{marker!r}"
