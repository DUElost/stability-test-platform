"""仓库布局守卫。

Why: `pytest.ini` 的 `testpaths` 只收 `backend/tests` / `backend/agent/tests` / `tests`,
     CI 也按同样路径调用。历史上有 4 个真实测试文件(24 个用例)散落在
     `backend/api/`、`backend/core/`、`backend/api/routes/` 下,写好了、能跑通,
     但从未被 CI 收集过 —— 覆盖率上表现为"零测试",实则是收集路径漏了。
     这条守卫让同类漂移在 CI 里立刻可见。
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# 与 pytest.ini `testpaths` 保持一致
_COLLECTED_ROOTS = (
    _REPO_ROOT / "backend" / "tests",
    _REPO_ROOT / "backend" / "agent" / "tests",
    _REPO_ROOT / "tests",
)

# Agent 侧脚本目录含被测脚本自身,不属于平台测试树
_IGNORED_PREFIXES = (
    _REPO_ROOT / "backend" / "agent" / "scripts",
    _REPO_ROOT / "backend" / "agent" / "resources",
)


def _is_under(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def test_no_test_files_outside_collected_testpaths():
    stray = [
        p.relative_to(_REPO_ROOT)
        for p in (_REPO_ROOT / "backend").rglob("test_*.py")
        if not _is_under(p, _COLLECTED_ROOTS)
        and not _is_under(p, _IGNORED_PREFIXES)
        and "__pycache__" not in p.parts
    ]
    assert not stray, (
        "以下 test_*.py 不在 pytest.ini testpaths 下,CI 永远不会收集它们:\n  "
        + "\n  ".join(str(s) for s in stray)
        + "\n把它们移进 backend/tests/(真测试)或改名去掉 test_ 前缀(手工冒烟脚本)。"
    )


@pytest.mark.parametrize("root", _COLLECTED_ROOTS)
def test_collected_roots_exist(root: Path):
    """testpaths 指向的目录必须真实存在,否则静默少收一整棵树。"""
    assert root.is_dir(), f"pytest.ini testpaths 指向不存在的目录: {root}"
