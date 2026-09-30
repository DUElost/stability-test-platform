"""#3516 G1：`.githooks/reference-transaction` 的 stdin 参数顺序回归。

githooks(5) 的 reference-transaction 每行输入为
`<old-value> SP <new-value> SP <ref-name> LF`。钩子曾按 `ref old new` 读取，
`refs/stash` 更新永远不命中、stash 留痕告警失效。

本测试直接用 `sh .githooks/reference-transaction <state>` + 标准 stdin 样例驱动，
不依赖 `core.hooksPath` 已配置，也不需要真实 git 事务；纯离线，可进 PR 路径
的根 tests/ 离线子集。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".githooks" / "reference-transaction"

ZERO = "0" * 40
SHA_A = "a" * 40
SHA_B = "b" * 40

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="需要 POSIX sh")


def _run(state: str, stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(HOOK), state],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("state", ["prepared", "committed", "aborted"])
def test_stash_update_warns_with_state_and_shas(state: str) -> None:
    result = _run(state, f"{ZERO} {SHA_A} refs/stash\n")
    assert result.returncode == 0
    assert "refs/stash" in result.stderr
    assert f"refs/stash {state}:" in result.stderr
    assert f"{ZERO} -> {SHA_A}" in result.stderr
    assert result.stdout == ""


def test_stash_drop_line_warns() -> None:
    result = _run("committed", f"{SHA_A} {ZERO} refs/stash\n")
    assert result.returncode == 0
    assert "refs/stash" in result.stderr


def test_non_stash_ref_is_silent() -> None:
    stdin = (
        f"{ZERO} {SHA_A} refs/heads/main\n"
        f"{SHA_A} {SHA_B} refs/remotes/origin/main\n"
        f"{ZERO} {SHA_B} refs/tags/v1\n"
    )
    result = _run("committed", stdin)
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == ""


def test_stash_among_other_refs_warns_only_for_stash() -> None:
    stdin = (
        f"{ZERO} {SHA_A} refs/heads/feature\n"
        f"{ZERO} {SHA_B} refs/stash\n"
        f"{SHA_A} {SHA_B} refs/heads/feature\n"
    )
    result = _run("prepared", stdin)
    assert result.returncode == 0
    assert result.stderr.count("refs/stash") == 1
    assert "refs/heads/feature" not in result.stderr


def test_stash_prefix_lookalike_is_not_matched() -> None:
    result = _run("committed", f"{ZERO} {SHA_A} refs/stash-backup\n")
    assert result.returncode == 0
    assert result.stderr == ""


def test_empty_stdin_exits_zero() -> None:
    result = _run("prepared", "")
    assert result.returncode == 0
    assert result.stderr == ""
