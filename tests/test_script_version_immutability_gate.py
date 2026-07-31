"""ADR-0020 脚本版本不可变门禁的行为测试。

在临时 git 仓库里真实构造「原地改已发布版本」与「新建版本」两种历史,
而不是断言工具源码里的字符串 —— 门禁值钱的是它会不会真的拦下 2026-07-31
那次事故(ruff --fix 原地改写 14 个版本目录),不是它长什么样。
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "tools/dev/check-script-version-immutability.py"
SCRIPT_ROOT = "backend/agent/scripts"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout


def _run_gate(repo: Path, base: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--base", base],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """一个有 main 分支和一个已发布脚本版本的最小仓库。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")

    published = repo / SCRIPT_ROOT / "check_device" / "v1.0.0"
    published.mkdir(parents=True)
    (published / "check_device.py").write_text("import os\nprint('v1')\n", encoding="utf-8")
    (published / "_adb.py").write_text("def shell(): pass\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "publish check_device v1.0.0")
    return repo


def test_gate_blocks_in_place_edit_of_published_entry(repo: Path):
    """复现事故形态:像 ruff --fix 那样删掉一个未使用导入。"""
    entry = repo / SCRIPT_ROOT / "check_device" / "v1.0.0" / "check_device.py"
    entry.write_text("print('v1')\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "ruff --fix: drop unused import")

    result = _run_gate(repo, "main~1")

    assert result.returncode == 1
    assert "check_device/v1.0.0/check_device.py" in result.stderr
    assert "ADR-0020" in result.stderr


def test_gate_blocks_in_place_edit_of_underscore_helper(repo: Path):
    """`_adb.py` 不计入 entry sha,改它连 conflicts 都不会报 —— 更该拦。"""
    helper = repo / SCRIPT_ROOT / "check_device" / "v1.0.0" / "_adb.py"
    helper.write_text("def shell(cmd): return cmd\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "tweak helper")

    result = _run_gate(repo, "main~1")

    assert result.returncode == 1
    assert "check_device/v1.0.0/_adb.py" in result.stderr


def test_gate_blocks_deleting_a_published_version(repo: Path):
    entry = repo / SCRIPT_ROOT / "check_device" / "v1.0.0" / "check_device.py"
    entry.unlink()
    _git(repo, "commit", "-qam", "drop entry")

    result = _run_gate(repo, "main~1")

    assert result.returncode == 1
    assert "删除" in result.stderr


def test_gate_allows_publishing_a_new_version(repo: Path):
    """ADR-0020 指定的正确做法:新建版本目录,旧版本字节不动。"""
    new_version = repo / SCRIPT_ROOT / "check_device" / "v1.1.0"
    new_version.mkdir(parents=True)
    (new_version / "check_device.py").write_text("print('v2')\n", encoding="utf-8")
    (new_version / "_adb.py").write_text("def shell(): pass\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "publish check_device v1.1.0")

    result = _run_gate(repo, "main~1")

    assert result.returncode == 0, result.stderr


def test_gate_ignores_changes_outside_version_directories(repo: Path):
    """脚本根下的非版本文件(README 等)不受不可变约束。"""
    (repo / SCRIPT_ROOT / "README.md").write_text("catalog\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "docs")

    result = _run_gate(repo, "main~1")

    assert result.returncode == 0, result.stderr
