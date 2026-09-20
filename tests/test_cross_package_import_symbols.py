# -*- coding: utf-8 -*-
"""#2946：跨包导入的**符号存在性**静态守卫（含函数体内导入）。

背景（2026-09-20 实测）：PR 阶段不跑 `backend/tests/` 全量，**跨包改名**类回归要等夜间
`main-ci-backstop`（UTC 18:00）才红——`261959f7`（#736 抽取 `job_runtime`）把
`OutboxDrainThread` 迁到 `backend/agent/outbox_drainer.py` 后，`backend/tests/
test_phase0_closure.py` 6 处旧导入漏改 ⇒ main 上 7 例 ImportError（#2941 修）。

`--collect-only` **抓不到这一类**：导入写在函数体内（`def _build_drain(): from
backend.agent.main import OutboxDrainThread`），收集期不解析符号——含缺陷的 main 上
`pytest backend/tests/ --collect-only -q` 实测 3418 用例全收集通过。

本守卫纯 AST、秒级、无需 DB，落在 `tests/` 的离线子集里 ⇒ 自动进 PR 路径（CI 的
`Run agent tests` 步骤并行跑 `pytest tests/`）。

判据：`from backend.<mod> import <Name>` 必须能在目标模块**静态解析**到——
顶层 def / class / 赋值（含带注解）或该模块自身的导入，或（目标是包时）同名子模块。

**不可判定的两类显式登记，不静默跳过**（本仓口径：不可判定 ≠ 通过）：
- 模块级 `__getattr__`（PEP 562 惰性导出，如 `backend/core/security.py` 的 cookie 名）；
- `from X import *`（当前 0 处）。
`test_opaque_export_modules_are_registered` 拿登记表与仓库真值对拍：新增一处即红。
"""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: PEP 562 惰性导出面（模块级 `__getattr__`）：**显式登记，不静默跳过**。
#: 新增一处即由 `test_opaque_export_modules_are_registered` 判红，需人工确认后登记。
_PEP562_EXPORT_MODULES = {
    # ADR-0042 P2：cookie 名收敛到 AuthSessionSettings，用模块级 __getattr__ 惰性解析
    # （不在 import 时读 env），`ACCESS_COOKIE_NAME` 等即由此导出。
    "backend.core.security",
}

#: 解析得到路径、但**符号静态不可枚举**的目标：命名空间包（目录即包，无 `__init__.py`）。
#: 登记而非静默跳过；新增一处同样要人工确认（主用例断言 unresolved ⊆ 本表）。
_NON_ENUMERABLE_TARGETS = {
    "backend.alembic.versions",  # 迁移文件按 dotted path 互相引用；目录无 __init__.py
}

#: 已发布脚本版本目录：自足、只依赖同目录 `_adb`，不参与本守卫。
_SKIP_PARTS = ("scripts",)


def _module_path(repo_root: Path, module: str):
    """``backend.a.b`` → 文件路径；解析不到返回 None。"""
    parts = module.split(".")
    if parts[0] != "backend":
        return None
    base = repo_root.joinpath(*parts)
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _module_surface(path: Path):
    """返回 ``(顶层可用名, 是否不可判定)``。

    不可判定 = 定义了模块级 ``__getattr__``（PEP 562）或存在 ``from X import *``：
    两者都让「名字是否存在」无法静态回答。
    """
    names = set()
    opaque = False

    def walk(body):
        nonlocal opaque
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
                if node.name == "__getattr__":  # PEP 562：惰性导出，符号集静态不可知
                    opaque = True
            elif isinstance(node, ast.ClassDef):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name == "*":
                        opaque = True
                    else:
                        names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, (ast.If, ast.Try)):
                walk(node.body)
                walk(getattr(node, "orelse", []) or [])
                for handler in getattr(node, "handlers", []) or []:
                    walk(handler.body)
                walk(getattr(node, "finalbody", []) or [])

    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    walk(tree.body)
    return names, opaque


def _iter_import_froms(tree: ast.AST):
    yield from (n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom))


def scan(repo_root: Path):
    """全量扫描 → ``(violations, opaque_modules, unresolved)``。

    ``violations``：``[(相对路径, 行号, module, name)]``——静态可判定却不存在；
    ``opaque_modules``：本次扫描**实际用到**的不可判定模块（与登记表对拍）；
    ``unresolved``：模块本身解析不到（只报告，不判红——可能指向仓库外）。
    """
    violations = []
    opaque_used = set()
    unresolved = []
    sources = list(repo_root.glob("backend/**/*.py")) + list(repo_root.glob("tests/**/*.py"))
    surface_cache: dict = {}
    for src in sources:
        rel = src.relative_to(repo_root)
        if "__pycache__" in rel.parts or any(part in _SKIP_PARTS for part in rel.parts[1:2]):
            continue
        try:
            tree = ast.parse(src.read_text(encoding="utf-8"))
        except SyntaxError:  # 语法面归 pr-compileall，不在本守卫职责内
            continue
        for node in _iter_import_froms(tree):
            module = node.module
            if not module or not module.startswith("backend"):
                continue
            target = _module_path(repo_root, module)
            if target is None:
                unresolved.append((str(rel), node.lineno, module))
                continue
            if module in _PEP562_EXPORT_MODULES:
                opaque_used.add(module)
                continue
            if target not in surface_cache:
                surface_cache[target] = _module_surface(target)
            provided, opaque = surface_cache[target]
            if opaque:
                opaque_used.add(module)
                continue
            is_package = target.name == "__init__.py"
            for alias in node.names:
                if alias.name == "*":
                    continue
                if alias.name in provided:
                    continue
                if is_package and _module_path(repo_root, f"{module}.{alias.name}"):
                    continue  # 包内子模块（`from backend.services import job_terminalization`）
                violations.append((str(rel), node.lineno, module, alias.name))
    return violations, opaque_used, unresolved


def test_cross_package_import_symbols_exist():
    """#2946：跨包导入的符号必须静态存在（含函数体内导入）。"""
    violations, opaque_used, unresolved = scan(_REPO_ROOT)
    assert not violations, (
        "跨包导入的符号在目标模块中不存在（#2946）——改名/迁移后导入点必须同步：\n"
        + "\n".join(f"  {p}:{ln} from {m} import {n}" for p, ln, m, n in violations)
    )
    stray = sorted({m for _, _, m in unresolved} - _NON_ENUMERABLE_TARGETS)
    assert not stray, (
        "以下导入的模块在仓库内解析不到且未登记（请人工确认是否该登记）：\n"
        + "\n".join(f"  {p}:{ln} from {m} import ..." for p, ln, m in unresolved if m in stray)
    )
    assert opaque_used <= _PEP562_EXPORT_MODULES, (
        f"扫描到未登记的不可判定导出面：{sorted(opaque_used - _PEP562_EXPORT_MODULES)}"
    )


def test_pep562_export_modules_are_registered():
    """登记表与仓库真值对拍：新增模块级 `__getattr__` / `import *` 即红（不可判定要留痕）。"""
    found = set()
    for path in _REPO_ROOT.glob("backend/**/*.py"):
        rel = path.relative_to(_REPO_ROOT)
        if "__pycache__" in rel.parts or any(part in _SKIP_PARTS for part in rel.parts[1:2]):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        module = ".".join(rel.with_suffix("").parts)
        if rel.name == "__init__.py":
            module = ".".join(rel.parent.parts)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattr__":
                found.add(module)
    assert found == _PEP562_EXPORT_MODULES, (
        f"不可判定导出面与登记表不一致：仓库={sorted(found)} 登记={sorted(_PEP562_EXPORT_MODULES)}"
        "——新增惰性导出/星号导入请登记并说明理由；移除后请下调登记表"
    )


# ── 自检：守卫的牙齿（假包布局 + 内存源码） ────────────────────────────────
def _fake_repo(tmp_path: Path, files: dict) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_guard_self_test_catches_function_local_import(tmp_path):
    """#2941 的原形：**函数体内**导入一个已被迁走的符号 ⇒ 必红。"""
    root = _fake_repo(tmp_path, {
        "backend/agent/__init__.py": "",
        "backend/agent/main.py": "class SomethingElse:\n    pass\n",  # 存在，但没有该符号
        "backend/agent/new_home.py": "class OutboxDrainThread:\n    pass\n",
        "backend/tests/test_x.py": (
            "def _build():\n"
            "    from backend.agent.main import OutboxDrainThread\n"
            "    return OutboxDrainThread\n"
        ),
    })
    violations, _, _ = scan(root)
    assert [(v[2], v[3]) for v in violations] == [("backend.agent.main", "OutboxDrainThread")]


def test_guard_self_test_accepts_valid_forms(tmp_path):
    """正例：顶层符号、包内子模块、`__getattr__` 惰性导出都要放行。"""
    root = _fake_repo(tmp_path, {
        "backend/agent/__init__.py": "",
        "backend/agent/thing.py": (
            "class Thing:\n    pass\n\n\ndef __getattr__(name):\n    raise AttributeError(name)\n"
        ),
        "backend/services/__init__.py": "",
        "backend/services/job_terminalization.py": "def run():\n    pass\n",
        "backend/services/user.py": "from backend.agent.thing import Thing\nfrom backend.services import job_terminalization\n",
        "tests/test_y.py": "from backend.agent.thing import Thing\nfrom backend.services import job_terminalization\n",
    })
    violations, opaque_used, unresolved = scan(root)
    assert violations == []
    assert unresolved == []
    assert opaque_used == {"backend.agent.thing"}   # __getattr__ 模块被识别为不可判定


def test_guard_self_test_catches_star_import_as_opaque(tmp_path):
    """星号导入使模块不可判定：登记表未含即由主用例的断言拦下（此处只验识别）。"""
    root = _fake_repo(tmp_path, {
        "backend/agent/__init__.py": "",
        "backend/agent/wide.py": "from backend.agent.thing import *\n",
        "backend/agent/thing.py": "class Thing:\n    pass\n",
        "tests/test_z.py": "from backend.agent.wide import Anything\n",
    })
    violations, opaque_used, _ = scan(root)
    assert violations == []
    assert "backend.agent.wide" in opaque_used
