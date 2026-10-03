"""#3516: scoped Claude settings are references, and the hook resolves the Git root.

These offline checks exercise the shipped hook command, not Claude's event loop.
Real CLI startup/PreToolUse evidence is recorded separately in the Agent Note.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = Path(".claude/settings.json")
GUARD = Path("tools/dev/check_destructive_git.py")
SCOPES = (Path("."), Path("backend/agent"), Path("backend/agent/aee"))


def _command(settings: Path) -> str:
    data = json.loads(settings.read_text())
    handlers = [
        handler
        for group in data["hooks"]["PreToolUse"]
        if group["matcher"] == "Bash"
        for handler in group["hooks"]
    ]
    assert len(handlers) == 1
    return handlers[0]["command"]


@pytest.mark.parametrize("scope", SCOPES[1:])
def test_scoped_settings_are_single_source_links(scope: Path) -> None:
    entry = ROOT / scope / SETTINGS
    assert entry.is_symlink(), f"missing shared-settings reference: {entry}"
    assert entry.resolve(strict=True) == ROOT / SETTINGS
    assert json.loads(entry.read_text()) == json.loads((ROOT / SETTINGS).read_text())


@pytest.fixture(params=[False, True], ids=["git-directory", "git-file"])
def checkout(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "checkout with spaces"
    root.mkdir()
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("GIT_"):
            env.pop(key)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    init = ["git", "init", "--quiet"]
    if request.param:
        init += ["--separate-git-dir", str(tmp_path / "git metadata")]
    subprocess.run([*init, str(root)], env=env, check=True, capture_output=True)
    for scope in SCOPES:
        (root / scope).mkdir(parents=True, exist_ok=True)
    (root / GUARD).parent.mkdir(parents=True)
    shutil.copyfile(ROOT / GUARD, root / GUARD)
    # The shipping hook uses python3; bind that name to this test interpreter.
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "python3").symlink_to(sys.executable)
    env["PATH"] = str(binaries) + os.pathsep + env["PATH"]
    return root, env


def _invoke(root: Path, env: dict[str, str], scope: Path, command: str,
            *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", _command(ROOT / SETTINGS)],
        cwd=cwd or root / scope,
        env={**env, "CLAUDE_PROJECT_DIR": str(root / scope)},
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        text=True, capture_output=True, timeout=10, check=False,
    )


@pytest.mark.parametrize("scope", SCOPES, ids=["root", "agent", "aee"])
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git status --short", 0),
        ("git stash list", 0),
        ("git stash push", 2),
        ('git reset "--hard"', 2),
        ("bash -c 'git stash push'", 2),
        ("printf '%s\\n' 'git stash push is data'", 0),
    ],
)
def test_hook_command_finds_checkout_root(checkout, scope, command, expected) -> None:
    root, env = checkout
    result = _invoke(root, env, scope, command)
    assert result.returncode == expected, result.stderr
    if expected == 2:
        assert "[BLOCKED]" in result.stderr
    else:
        assert result.stderr == ""


def test_hook_uses_session_directory_not_shell_cwd(checkout, tmp_path: Path) -> None:
    root, env = checkout
    outside = tmp_path / "outside"
    outside.mkdir()
    result = _invoke(root, env, SCOPES[-1], "git stash push", cwd=outside)
    assert result.returncode == 2, result.stderr
    assert "[BLOCKED]" in result.stderr


def test_missing_checker_keeps_nonblocking_error(checkout) -> None:
    root, env = checkout
    (root / GUARD).unlink()
    result = _invoke(root, env, SCOPES[-1], "git status --short")
    assert result.returncode == 1
    assert "guard script missing" in result.stderr
    assert "guard NOT active" in result.stderr


def test_non_git_directory_reports_guard_inactive(tmp_path: Path) -> None:
    result = _invoke(tmp_path, os.environ.copy(), Path("."), "git status --short")
    assert result.returncode == 1
    assert "Git root unavailable" in result.stderr
    assert "guard NOT active" in result.stderr


def test_scoped_settings_links_do_not_enter_agent_payload(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "claude_hook_artifact_contract", ROOT / "backend/agent/contracts/artifact_digest.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    agent = tmp_path / "backend/agent"
    agent.mkdir(parents=True)
    (agent / "worker.py").write_text("pass\n")
    before = module.collect_artifact_entries(str(agent))
    settings = tmp_path / SETTINGS
    settings.parent.mkdir()
    shutil.copyfile(ROOT / SETTINGS, settings)
    for scope in SCOPES[1:]:
        entry = tmp_path / scope / SETTINGS
        entry.parent.mkdir(parents=True)
        entry.symlink_to(os.readlink(ROOT / scope / SETTINGS))
    assert module.collect_artifact_entries(str(agent)) == before
