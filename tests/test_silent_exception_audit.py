"""#739 §2：静默异常吞咽审计工具的口径与排除面守卫。

工具本体 `tools/dev/audit_silent_exceptions.py` 只读、不阻断，先把**口径**固化：
`pass` / `continue` / `return None` 三类算静默；handler 体里有任何其它语句（哪怕一行
`logger.debug`）不算。本文件钉住三件事：三类判定与「有日志不算」、冻结面（已发布
脚本版本 + alembic 历史 revision）必须排除、扫描面不得塌成空。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "audit_silent_exceptions",
    REPO_ROOT / "tools" / "dev" / "audit_silent_exceptions.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["audit_silent_exceptions"] = _mod
_spec.loader.exec_module(_mod)


def _handler(src: str) -> ast.ExceptHandler:
    return next(
        node for node in ast.walk(ast.parse(src)) if isinstance(node, ast.ExceptHandler)
    )


def test_classifies_the_three_silent_shapes():
    """三类静默形态都判得出（`return` 与 `return None` 同类）。"""
    assert _mod.classify_handler(_handler("try:\n f()\nexcept OSError:\n pass\n")) == "pass"
    assert _mod.classify_handler(
        _handler("for i in x:\n try:\n  f(i)\n except ValueError:\n  continue\n")
    ) == "continue"
    assert _mod.classify_handler(
        _handler("def g():\n try:\n  return f()\n except KeyError:\n  return None\n")
    ) == "return_none"
    assert _mod.classify_handler(
        _handler("def g():\n try:\n  return f()\n except KeyError:\n  return\n")
    ) == "return_none"


def test_handler_with_any_statement_is_not_silent():
    """有日志（或任何其它语句）的 handler 不算静默——本工具不评判日志级别。"""
    assert _mod.classify_handler(
        _handler("try:\n f()\nexcept OSError:\n logger.debug('x')\n")
    ) is None
    assert _mod.classify_handler(
        _handler("try:\n f()\nexcept OSError:\n pass\n return 1\n")
    ) is None


def test_ellipsis_is_treated_as_silent():
    """`...` 与 `pass` 同义，同样算静默。"""
    assert _mod.classify_handler(
        _handler("try:\n f()\nexcept OSError:\n ...\n")
    ) == "pass"


def test_frozen_surfaces_are_excluded():
    """已发布脚本版本与 alembic 历史 revision 不在治理面内（不可修改）。"""
    assert _mod._is_frozen("backend/agent/scripts/flash_firmware/v1.3.17/flash_firmware.py")
    assert _mod._is_frozen("backend/alembic/versions/e5f6a7b8c9d0_x.py")
    assert not _mod._is_frozen("backend/agent/pipeline_engine.py")
    assert not _mod._is_frozen("backend/agent/scripts/loader.py")


def test_scan_surface_is_not_empty_and_respects_frozen_exclusions():
    """扫描面非空，且结果里不得出现冻结面路径（防排除规则悄悄失效）。"""
    findings = _mod.audit()
    assert findings, "扫描面塌陷：一个生产文件都没扫到"

    leaked = sorted({f.path for f in findings if _mod._is_frozen(f.path)})
    assert leaked == [], f"冻结面文件进入了审计结果：{leaked}"


def test_tool_self_test_passes():
    """工具自带的离线红绿自证必须通过。"""
    assert _mod.main(["--self-test"]) == 0
