"""#3516 G1：`tools/dev/check_destructive_git.py`（Claude PreToolUse hook）回归。

脚本自带 `--self-test`（红绿双向用例表）；它不在任何 CI 步骤里，本文件把它与
hook 端到端契约（stdin JSON → 退出码）接入根 `tests/` 离线子集（`pr-agent-tests`）：

- self-test 全绿；
- 阻断 → exit 2 且 stderr 含 `[BLOCKED]`；放行 → exit 0；
- 词法解析失败：疑似命中仍阻断并注明「无法解析」，无危险信号则放行；
- 非 hook JSON / 非 Bash 工具 / 缺 command → exit 0（放行）。

纯离线（只起当前解释器子进程），不依赖 `core.hooksPath` 或任何 git 状态。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD = REPO_ROOT / "tools" / "dev" / ("check_destructive" + "_git.py")


def _run(stdin: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUARD), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def _hook(command: str, tool: str = "Bash") -> subprocess.CompletedProcess[str]:
    payload = {"tool_name": tool, "tool_input": {"command": command}}
    return _run(json.dumps(payload))


def test_self_test_passes() -> None:
    result = _run("", "--self-test")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "git reset --hard",
        'git reset "--hard"',
        'git -C "/tmp/repo path" reset --hard',
        'bash -c "git reset --hard"',
        "sh -c 'git stash'",
        "env git reset --hard",
        "sudo git reset --hard",
        "/usr/bin/git reset --hard",
        "echo $(git reset --hard)",
        "(git reset --hard)",
        "echo x | xargs git stash drop",
        # #3545 复核返修
        "git --config-env core.abbrev=STP_ENV reset --hard",
        "git --config-env=core.abbrev=STP_ENV reset --hard",
        "git --attr-source HEAD reset --hard",
        "git --future-opt k=v reset --hard",
        "cat <<EOF\n" + "$(" * 26 + "git reset --hard" + ")" * 26 + "\nEOF\n",
        "cat <<EOF\n" + "$(" * 26 + "git $'reset' $'--hard'" + ")" * 26 + "\nEOF\n",
        "cat <<EOF\n" + "$(" * 26 + "git stash" + ")" * 26 + "\nEOF\n",
    ],
)
def test_hook_blocks_with_exit_2(command: str) -> None:
    result = _hook(command)
    assert result.returncode == 2, (command, result.stderr)
    assert "[BLOCKED]" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "git status --short",
        "git stash list",
        'git commit -m "docs; git stash is forbidden"',
        "grep -E 'a|git stash|b' file",
        "cat <<EOF\ngit stash\nEOF\n",
        'git log --grep "x; git stash push"',
        # #3545 复核返修：合法读侧 / 数据不得因新增选项与回退而误拦
        "git --config-env core.abbrev=STP_ENV status",
        "git --config-env core.abbrev=STP_ENV stash list",
        "cat <<'EOF'\n" + "$(" * 26 + "git reset --hard" + ")" * 26 + "\nEOF\n",
        "cat <<EOF\n" + "$(" * 26 + "echo hi" + ")" * 26 + "\nEOF\n",
    ],
)
def test_hook_allows_with_exit_0(command: str) -> None:
    result = _hook(command)
    assert result.returncode == 0, (command, result.stderr)
    assert result.stderr == ""


def test_unparsable_with_danger_signal_blocks_and_says_so() -> None:
    result = _hook('git reset --hard "unterminated')
    assert result.returncode == 2
    assert "无法解析" in result.stderr


def test_heredoc_substitution_over_depth_falls_back_and_blocks() -> None:
    """#3545 复核反例 2：深度超限是合法 Bash（内层 git 会执行），不得被静默当成「没有命令替换」。"""
    command = "cat <<EOF\n" + "$(" * 26 + "git reset --hard" + ")" * 26 + "\nEOF\n"
    result = _hook(command)
    assert result.returncode == 2
    assert "无法解析" in result.stderr


def test_unparsable_without_danger_signal_passes() -> None:
    assert _hook("echo 'unterminated").returncode == 0


@pytest.mark.parametrize(
    "stdin",
    [
        "",
        "not json",
        "[1, 2]",
        '"git reset --hard"',
        json.dumps({"tool_name": "Bash"}),
        json.dumps({"tool_name": "Bash", "tool_input": "git reset --hard"}),
        json.dumps({"tool_name": "Bash", "tool_input": {"command": 7}}),
    ],
)
def test_non_hook_input_passes(stdin: str) -> None:
    assert _run(stdin).returncode == 0


def test_non_bash_tool_passes() -> None:
    assert _hook("git reset --hard", tool="Read").returncode == 0
