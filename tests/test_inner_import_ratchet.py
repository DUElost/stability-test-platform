"""#738：函数体局部 import 棘轮的语料与判据守卫。

门禁本体 `tools/dev/check_inner_imports.py`：生产面里处于函数/方法体内的
`import` / `from … import` 总数不得超过 `_BASELINE`（只降不升）。本文件钉住：

1. 本仓当前在基线内；
2. **基线不得陈旧**——实测已低于 `_BASELINE` 时必须同步下调（棘轮纪律：
   「解耦掉若干处」与「调小基线」发生在同一个 PR 里）；
3. 判据四态可判：函数体内算、模块顶层不算、顶层条件导入不算、类体不算；
4. 扫描面非空。

另附一条**生产面判据**：排除面（测试/已发布脚本版本/alembic 历史/vendored）
不得把生产文件误排除。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "check_inner_imports",
    REPO_ROOT / "tools" / "dev" / "check_inner_imports.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["check_inner_imports"] = _mod
_spec.loader.exec_module(_mod)


def test_repo_is_within_baseline():
    """当前树必须在基线内——否则门禁一上线就红。"""
    total = sum(len(v) for v in _mod.scan().values())
    assert total <= _mod._BASELINE, (
        f"函数体内 import {total} 处 > 基线 {_mod._BASELINE}——"
        "新代码优先重构消除循环依赖，而不是在函数体内 import 绕开它"
    )


def test_baseline_is_never_stale():
    """棘轮纪律：实测低于基线时必须同步下调（同一个 PR 里完成）。"""
    total = sum(len(v) for v in _mod.scan().values())
    assert total == _mod._BASELINE, (
        f"实测 {total} 处 < 基线 {_mod._BASELINE}——请把 _BASELINE 调到 {total}"
        "（棘轮只降不升：解耦与调小基线应在同一个 PR 里）"
    )


def test_detector_counts_function_body_only():
    """四态：函数体内算；模块顶层 / 顶层条件导入 / 类体不算。"""
    assert _mod.inner_import_lines("def f():\n    import os\n") == [2]
    assert _mod.inner_import_lines("import os\n") == []
    assert _mod.inner_import_lines("try:\n    import ujson\nexcept ImportError:\n    ujson = None\n") == []
    assert _mod.inner_import_lines("class C:\n    import os\n") == []


def test_detector_sees_methods_inside_classes():
    """类**方法**体内的 import 仍要算（它才是运行期依赖）。"""
    src = "class C:\n    def m(self):\n        from x import y\n"
    assert _mod.inner_import_lines(src) == [3]


def test_exclusion_rules_do_not_hit_production_files():
    """排除面只排除四类非治理面，生产文件不得被误排除。"""
    assert _mod._is_excluded("backend/agent/scripts/x/v1.0.0/x.py", "x.py")
    assert _mod._is_excluded("backend/alembic/versions/abc.py", "abc.py")
    assert _mod._is_excluded("backend/agent/resources/aimonkey/MonkeyTest.py", "MonkeyTest.py")
    assert _mod._is_excluded("backend/tests/api/test_x.py", "test_x.py")
    assert not _mod._is_excluded("backend/services/dedup_scan.py", "dedup_scan.py")
    assert not _mod._is_excluded("tools/dev/ai_work.py", "ai_work.py")


def test_scan_surface_is_not_empty():
    """扫描面塌陷（目录改名/搬走）不能长得像「全绿」。"""
    found = _mod.scan()
    assert len(found) >= 30, f"扫描面异常，只看到 {len(found)} 个文件有局部 import"
    assert sum(len(v) for v in found.values()) > 100
