"""Git must actually trigger opt-in hooks; presence alone is not acceptance."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.dev import check_git_hooks as hooks

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "posix" or not shutil.which("git"), reason="POSIX Git required")


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    repo = tmp_path / "repository"
    repo.mkdir()
    template = tmp_path / "empty-template"
    template.mkdir()
    hooks.output(repo, "init", "-q", "--template=" + str(template))
    shutil.copytree(ROOT / ".githooks", repo / ".githooks")
    tools = repo / "tools/dev"
    tools.mkdir(parents=True)
    shutil.copy2(ROOT / "tools/dev/check-internal-ip-leak.py", tools)
    shutil.copy2(ROOT / ".gitattributes", repo)
    hooks.output(repo, "add", ".githooks", "tools", ".gitattributes")
    hooks.output(repo, "-c", "user.name=Hook Probe", "-c", "user.email=probe@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "baseline")
    return repo


def enable(repo):
    hooks.output(repo, "config", "--local", "core.hooksPath", ".githooks")


def test_unset_and_other_hooks_paths_do_not_claim_repository_activation(repository):
    assert hooks.status(repository)["state"] == "DISABLED"
    hooks.output(repository, "config", "core.hooksPath", "/dev/null")
    assert hooks.status(repository)["state"] == "DISABLED"


def test_external_executable_hooks_are_unverified_without_running_them(repository, tmp_path):
    external = tmp_path / "external-hooks"
    shutil.copytree(repository / ".githooks", external)
    hooks.output(repository, "config", "core.hooksPath", str(external))
    report = hooks.status(repository)
    assert report["state"] == "UNVERIFIED"
    assert report["hooks_path"] == str(external)
    assert "outside" in report["reason"]


def test_repository_hook_directory_symlink_outside_root_is_unverified(repository, tmp_path):
    external = tmp_path / "external-hooks"
    (repository / ".githooks").rename(external)
    (repository / ".githooks").symlink_to(external, target_is_directory=True)
    enable(repository)
    assert hooks.status(repository)["state"] == "UNVERIFIED"


@pytest.mark.parametrize("absolute", [False, True])
def test_configured_real_path_is_resolved_from_deep_cwd(repository, absolute):
    path = str(repository / ".githooks") if absolute else ".githooks"
    hooks.output(repository, "config", "core.hooksPath", path)
    deep = repository / "backend/agent/aee"
    deep.mkdir(parents=True)
    report = hooks.status(deep)
    assert report["state"] == "CONFIGURED"
    assert report["root"] == str(repository)
    assert report["hooks_path"] == str(repository / ".githooks")


def test_linked_worktree_uses_its_own_hook_files(repository, tmp_path):
    enable(repository)
    worktree = tmp_path / "linked"
    hooks.output(repository, "worktree", "add", "--detach", str(worktree))
    assert hooks.status(worktree)["state"] == "CONFIGURED"
    assert hooks.status(worktree)["hooks_path"] == str(worktree / ".githooks")


@pytest.mark.parametrize("unavailable", ["missing", "not-executable"])
def test_configured_but_unusable_hook_is_unverified(repository, unavailable):
    enable(repository)
    target = repository / ".githooks/reference-transaction"
    if unavailable == "missing":
        target.unlink()
    else:
        target.chmod(0o600)
    report = hooks.status(repository)
    assert report["state"] == "UNVERIFIED"
    assert report["unavailable"] == ["reference-transaction"]


def test_real_git_triggers_both_hooks_without_changing_caller(repository, monkeypatch, tmp_path):
    enable(repository)
    config = (repository / ".git/config").read_bytes()
    index = (repository / ".git/index").read_bytes()
    refs = hooks.output(repository, "show-ref")
    # Ambient foreign config/index must not bleed into the isolated behavior repo.
    foreign_index = tmp_path / "must-not-be-created"
    monkeypatch.setenv("GIT_INDEX_FILE", str(foreign_index))
    report = hooks.self_check(repository)
    monkeypatch.delenv("GIT_INDEX_FILE")
    assert report["state"] == "PASS"
    assert all(report["checks"].values())
    assert not foreign_index.exists()
    assert (repository / ".git/config").read_bytes() == config
    assert (repository / ".git/index").read_bytes() == index
    assert hooks.output(repository, "show-ref") == refs


@pytest.mark.parametrize("mutation,failed_check", [
    ("reference-order", "stash_transaction_warned_without_blocking"),
    ("allow-pollution", "pollution_commit_blocked"),
])
def test_behavior_check_rejects_old_or_disabled_hook_logic(repository, mutation, failed_check):
    if mutation == "reference-order":
        target = repository / ".githooks/reference-transaction"
        target.write_text(target.read_text().replace("read -r old new ref", "read -r ref old new"))
    else:
        (repository / ".githooks/pre-commit").write_text("#!/bin/sh\nexit 0\n")
    report = hooks.self_check(repository)
    assert report["state"] == "FAIL"
    assert report["checks"][failed_check] is False


def test_cli_self_test_disabled_is_unverified_not_pass(repository):
    result = subprocess.run([sys.executable, hooks.__file__, "--repo", str(repository), "--self-test"],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert json.loads(result.stdout)["state"] == "UNVERIFIED"
    assert hooks.output(repository, "config", "--local", "--list").find("core.hookspath") == -1


def test_cli_enabled_self_test_from_deep_cwd(repository):
    enable(repository)
    deep = repository / "backend/agent/aee"
    deep.mkdir(parents=True)
    result = subprocess.run([sys.executable, hooks.__file__, "--self-test"], cwd=deep,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["root"] == str(repository)
    assert report["state"] == "PASS"
    assert all(report["checks"].values())


def test_tool_errors_are_unverified(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", [hooks.__file__, "--repo", str(tmp_path)])

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("git", 15)

    monkeypatch.setattr(hooks, "git", timed_out)
    assert hooks.main() == 1
    assert json.loads(capsys.readouterr().out) == {"state": "UNVERIFIED", "error": "TimeoutExpired"}


@pytest.mark.parametrize('name', hooks.HOOKS)
def test_individual_hook_symlink_outside_is_unverified_and_never_read(repository, tmp_path, monkeypatch, capsys, name):
    enable(repository)
    marker = tmp_path / 'EXTERNAL-HOOK-RAN'
    external = tmp_path / 'outside-hook'
    external.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 1\n')
    external.chmod(0o755)
    target = repository / '.githooks' / name
    target.unlink()
    target.symlink_to(external)
    report = hooks.status(repository)
    assert report['state'] == 'UNVERIFIED'
    assert report['outside'] == [name]
    assert not marker.exists()
    monkeypatch.setattr(sys, 'argv', [hooks.__file__, '--repo', str(repository), '--self-test'])
    def forbid_read(*args, **kwargs):
        raise AssertionError('external source must never reach copy/read')
    monkeypatch.setattr(hooks, 'copy_trusted', forbid_read)
    assert hooks.main() == 1
    assert json.loads(capsys.readouterr().out)['state'] == 'UNVERIFIED'
    result = subprocess.run([sys.executable, hooks.__file__, '--repo', str(repository), '--self-test'],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert json.loads(result.stdout)['state'] == 'UNVERIFIED'
    assert not marker.exists()


@pytest.mark.parametrize('name', hooks.HOOKS)
def test_direct_self_check_revalidates_hook_before_read_or_git_execution(repository, tmp_path, monkeypatch, name):
    enable(repository)
    assert hooks.status(repository)['state'] == 'CONFIGURED'
    external = tmp_path / 'outside-hook'
    external.write_text('#!/bin/sh\nexit 0\n')
    external.chmod(0o755)
    target = repository / '.githooks' / name
    target.unlink()
    target.symlink_to(external)
    original_open = hooks.os.open
    def safe_open(path, *args, **kwargs):
        assert str(path) != str(external), 'external file was opened'
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(hooks.os, 'open', safe_open)
    with pytest.raises(RuntimeError, match='outside trusted'):
        hooks.self_check(repository)


@pytest.mark.parametrize('name', hooks.HOOKS)
def test_internal_hook_symlink_still_checks_real_behavior(repository, name):
    enable(repository)
    original = repository / '.githooks' / name
    actual = original.with_name(name + '.implementation')
    original.rename(actual)
    original.symlink_to(actual.name)
    assert hooks.status(repository)['state'] == 'CONFIGURED'
    assert hooks.self_check(repository)['state'] == 'PASS'


def test_copy_rejects_leaf_symlink_swapped_after_resolution(repository, tmp_path, monkeypatch):
    source = repository / '.githooks/pre-commit'
    outside = tmp_path / 'outside-hook'
    outside.write_text('outside data\n')
    original_open = hooks.os.open
    def swapped_open(path, flags, *args, **kwargs):
        if str(path) == 'pre-commit':
            source.unlink()
            source.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(hooks.os, 'open', swapped_open)
    with pytest.raises(OSError):
        hooks.copy_trusted(repository, source, tmp_path / 'copy', repository / '.githooks')
    assert not (tmp_path / 'copy').exists()


@pytest.mark.parametrize('relative', ['tools/dev/check-internal-ip-leak.py', '.gitattributes'])
def test_other_copied_inputs_cannot_read_outside_worktree(repository, tmp_path, relative):
    outside = tmp_path / 'outside-input'
    outside.write_text('external input\n')
    source = repository / relative
    source.unlink()
    source.symlink_to(outside)
    with pytest.raises(RuntimeError, match='outside trusted'):
        hooks.self_check(repository)


def test_copy_rejects_parent_directory_swap(repository, tmp_path, monkeypatch):
    source = repository / '.githooks/pre-commit'
    original_open = hooks.os.open
    def swapped_open(path, flags, *args, **kwargs):
        if str(path) == '.githooks':
            external = tmp_path / 'moved-hooks'
            (repository / '.githooks').rename(external)
            (repository / '.githooks').symlink_to(external, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(hooks.os, 'open', swapped_open)
    with pytest.raises(OSError):
        hooks.copy_trusted(repository, source, tmp_path / 'copy', repository / '.githooks')
    assert not (tmp_path / 'copy').exists()


def test_copy_rejects_fifo_without_waiting_for_a_writer(repository, tmp_path):
    source = repository / '.githooks/pre-commit'
    source.unlink()
    os.mkfifo(source)
    with pytest.raises(RuntimeError, match='not a regular file'):
        hooks.copy_trusted(repository, source, tmp_path / 'copy', repository / '.githooks')
    assert not (tmp_path / 'copy').exists()
