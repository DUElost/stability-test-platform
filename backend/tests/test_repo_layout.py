"""仓库布局守卫 —— 保证「写了的测试」真的会被 CI 跑到。

这条链路有两个环节,断哪个都会让测试静默失效:

  1. 测试文件必须落在 `pytest.ini` 的 `testpaths` 下
  2. CI workflow 必须真的调用了每一个 `testpaths`

历史上两个环节各断过一次:
- 环节 1:4 个测试文件(24 个用例)散落在 `backend/api/`、`backend/core/` 下,
  写好了、能跑通,但从没被收集过 —— 覆盖率上表现为"零测试"。
- 环节 2:根目录 `tests/`(31 个用例,含迁移升级与部署脚本契约)在 testpaths
  里,但 `ci.yml` 只写了 `backend/tests/` 和 `backend/agent/tests/`,
  于是这 31 个用例在 CI 里从未执行过。

所以两个环节都要守。
"""
from __future__ import annotations

import configparser
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYTEST_INI = _REPO_ROOT / "pytest.ini"
_CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

# 不属于本仓源码的目录:虚拟环境、依赖、构建产物
_SKIP_DIR_NAMES = frozenset({
    "__pycache__", ".git", "node_modules", "venv", "venv-wsl", ".venv",
    "dist", "dist-prod", ".pytest_cache", "backups", ".tmp",
})

# Agent 侧脚本/随包资源含被测脚本自身,不属于平台测试树
_IGNORED_PREFIXES = (
    _REPO_ROOT / "backend" / "agent" / "scripts",
    _REPO_ROOT / "backend" / "agent" / "resources",
)


def _testpaths() -> list[Path]:
    """从 pytest.ini 读 testpaths —— 不硬编码,避免与配置漂移。"""
    parser = configparser.ConfigParser()
    parser.read(_PYTEST_INI, encoding="utf-8")
    raw = parser.get("pytest", "testpaths", fallback="")
    return [_REPO_ROOT / line.strip() for line in raw.split() if line.strip()]


def _is_under(path: Path, roots) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def _iter_test_files():
    stack = [_REPO_ROOT]
    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _SKIP_DIR_NAMES:
                    stack.append(entry)
            elif entry.name.startswith("test_") and entry.suffix == ".py":
                yield entry


def test_no_test_files_outside_collected_testpaths():
    """环节 1:所有 test_*.py 都必须在 testpaths 之下。"""
    roots = _testpaths()
    stray = sorted(
        str(p.relative_to(_REPO_ROOT))
        for p in _iter_test_files()
        if not _is_under(p, roots) and not _is_under(p, _IGNORED_PREFIXES)
    )
    assert not stray, (
        "以下 test_*.py 不在 pytest.ini testpaths 下,CI 永远不会收集它们:\n  "
        + "\n  ".join(stray)
        + "\n把它们移进 backend/tests/(真测试)或改名去掉 test_ 前缀(手工冒烟脚本)。"
    )


@pytest.mark.parametrize("root", _testpaths(), ids=lambda p: p.name)
def test_collected_roots_exist(root: Path):
    """testpaths 指向的目录必须真实存在,否则静默少收一整棵树。"""
    assert root.is_dir(), f"pytest.ini testpaths 指向不存在的目录: {root}"


def _run_blocks(ci_text: str) -> list[str]:
    """抽出 workflow 里所有 `run:` 步骤的完整命令文本。

    按缩进切块,folded(`run: >-`)与单行两种写法都能覆盖 —— 不引入 YAML
    依赖,也不会像正则那样贪婪吃掉整个文件。
    """
    lines = ci_text.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        m = re.match(r"(\s*)-?\s*run:\s*(.*)$", lines[i])
        if not m:
            i += 1
            continue
        indent, first = len(m.group(1)), m.group(2)
        chunk = [first]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                break
            chunk.append(nxt)
            i += 1
        blocks.append(" ".join(chunk))
    return blocks


def test_ci_workflow_runs_every_testpath():
    """环节 2:CI 必须真的跑到每一个 testpath。

    只在 pytest.ini 里列出来是不够的 —— workflow 得真的把它传给 pytest。
    """
    assert _CI_WORKFLOW.is_file(), f"未找到 CI workflow: {_CI_WORKFLOW}"
    ci_text = _CI_WORKFLOW.read_text(encoding="utf-8")

    invoked: set[str] = set()
    for block in _run_blocks(ci_text):
        if "pytest" not in block:
            continue
        # 去掉行内注释再切词,避免把注释里的路径当成真的调用
        cleaned = re.sub(r"#[^\n]*", " ", block)
        for tok in cleaned.split():
            if tok.startswith("-"):
                continue
            invoked.add(tok.rstrip("/"))

    missing = []
    for root in _testpaths():
        rel = str(root.relative_to(_REPO_ROOT)).rstrip("/")
        # 该 testpath 本身被调用,或其内某个文件被显式点名,都算覆盖
        if not any(p == rel or p.startswith(rel + "/") for p in invoked):
            missing.append(rel)

    assert not missing, (
        "pytest.ini 的 testpaths 里有目录从未出现在 CI 的 pytest 调用中,\n"
        "这些测试在本地能跑、在 CI 却静默跳过:\n  "
        + "\n  ".join(missing)
        + f"\n请在 {_CI_WORKFLOW.relative_to(_REPO_ROOT)} 增加对应的 pytest 步骤。"
    )
