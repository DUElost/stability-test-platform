"""Descriptor-relative privilege targets withstand path replacement (#1821)."""

from __future__ import annotations

import ast
import base64
import grp
import importlib.util
import json
import os
from pathlib import Path
import pwd
from types import SimpleNamespace

import pytest


WRAPPER = Path(__file__).resolve().parents[1] / "backend/agent/stp_agent_priv.py"
WRITERS = [
    ("write_version", "agent", "VERSION"),
    ("install_schema", "schemas", "pipeline_schema.json"),
    ("deps_marker", "", ".deps_installed_sha"),
    ("sync_env", "", ".env"),
]


@pytest.fixture
def wrapper(monkeypatch):
    spec = importlib.util.spec_from_file_location("stp_agent_priv_targets", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_require_root", lambda: None)
    monkeypatch.setattr(module, "_caller_uid", lambda: (os.getuid(), os.getgid()))
    monkeypatch.setattr(module, "RSYNC_BIN", "/bin/true")
    return module


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / "install"
    (root / "agent").mkdir(parents=True)
    (root / "schemas").mkdir()
    (root / ".env").write_text("HOST_ID=test\n")
    staged = tmp_path / "staged"
    staged.mkdir()
    schema = staged / "schema.json"
    schema.write_text(json.dumps({"$schema": "test", "properties": {"lifecycle": {}}}))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("untouched")
    conf = {"INSTALL_DIR": str(root), "AGENT_USER": pwd.getpwuid(os.getuid()).pw_name,
            "AGENT_GROUP": grp.getgrgid(os.getgid()).gr_name}
    args = SimpleNamespace(staged=str(staged), file=str(schema), version="abc1234", sha="a" * 64,
                           secret_b64=base64.b64encode(b"test-secret").decode(),
                           overrides_b64="", path_keys_b64="")
    return root, outside, conf, args


def test_wrapper_remains_python36_compatible():
    ast.parse(WRAPPER.read_text(), feature_version=(3, 6))


@pytest.mark.parametrize("command,child", [
    ("apply_code", "agent"), ("install_schema", "schemas"), ("write_version", "agent"),
])
def test_child_symlink_is_rejected(wrapper, installation, command, child, monkeypatch):
    root, outside, conf, args = installation
    (root / child).rmdir()
    (root / child).symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(wrapper, "_run", lambda *args, **kwargs: pytest.fail("executed transfer"))
    with pytest.raises((OSError, wrapper.PrivError)):
        getattr(wrapper, "cmd_" + command)(args, conf)
    assert sorted(entry.name for entry in outside.iterdir()) == ["sentinel"]
    assert (outside / "sentinel").read_text() == "untouched"


@pytest.mark.parametrize("command", [
    "apply_code", "install_schema", "write_version", "sync_env", "deps_marker", "fix_ownership",
])
@pytest.mark.parametrize("ancestor", [False, True])
def test_anchor_symlink_is_rejected(wrapper, installation, command, ancestor, monkeypatch):
    root, outside, conf, args = installation
    alias = root.parent / "alias"
    alias.symlink_to(root.parent if ancestor else root, target_is_directory=True)
    conf["INSTALL_DIR"] = str(alias / root.name if ancestor else alias)
    monkeypatch.setattr(wrapper, "_run", lambda *args, **kwargs: pytest.fail("executed transfer"))
    with pytest.raises((OSError, wrapper.PrivError)):
        getattr(wrapper, "cmd_" + command)(args, conf)
    assert (outside / "sentinel").read_text() == "untouched"


def test_rsync_pins_directories_and_drops_root(wrapper, installation, monkeypatch):
    root, outside, conf, args = installation
    calls = []
    monkeypatch.setattr(wrapper.os, "initgroups", lambda *values: calls.append(("groups", values)))
    monkeypatch.setattr(wrapper.os, "setgid", lambda value: calls.append(("gid", value)))
    monkeypatch.setattr(wrapper.os, "setuid", lambda value: calls.append(("uid", value)))
    monkeypatch.setattr(wrapper, "_agent_identity", lambda conf: (os.getuid() or 1000, os.getgid()))
    monkeypatch.setattr(wrapper.os, "fchown", lambda *values: None)

    def run(argv, pass_fds, preexec_fn):
        original = root / "original-agent"
        (root / "agent").rename(original)
        (root / "agent").symlink_to(outside, target_is_directory=True)
        assert argv[-2:] == ["/proc/self/fd/%d/" % descriptor for descriptor in pass_fds]
        assert "--no-owner" in argv and "--no-group" in argv
        assert Path(argv[-1]).resolve() == original
        (Path(argv[-1]) / "main.py").write_text("new-code")
        preexec_fn()
        return 0, "", ""

    monkeypatch.setattr(wrapper, "_run", run)
    assert wrapper.cmd_apply_code(args, conf) == 0
    assert calls == [("groups", (conf["AGENT_USER"], os.getgid())), ("gid", os.getgid()),
                     ("uid", os.getuid() or 1000)]
    assert not (outside / "main.py").exists()


def test_rsync_never_runs_as_root(wrapper, installation, monkeypatch):
    _, _, conf, args = installation
    monkeypatch.setattr(wrapper, "_agent_identity", lambda conf: (0, 0))
    with pytest.raises(wrapper.PrivError, match="non-root"):
        wrapper.cmd_apply_code(args, conf)


@pytest.mark.parametrize("command,parent,target", WRITERS)
def test_writes_pin_parent_across_replacement(wrapper, installation, monkeypatch, command, parent, target):
    root, outside, conf, args = installation
    directory = root / parent
    moved = root.parent / "moved"
    replace = os.replace

    def swap_then_replace(source, dest, **kwargs):
        directory.rename(moved)
        directory.symlink_to(outside, target_is_directory=True)
        assert kwargs["src_dir_fd"] == kwargs["dst_dir_fd"]
        return replace(source, dest, **kwargs)

    monkeypatch.setattr(wrapper.os, "replace", swap_then_replace)
    assert getattr(wrapper, "cmd_" + command)(args, conf) == 0
    assert (moved / target).is_file()
    assert not (outside / target).exists()


@pytest.mark.parametrize("command,parent,target", WRITERS[:3])
def test_target_symlink_is_replaced_not_followed(wrapper, installation, command, parent, target):
    root, outside, conf, args = installation
    destination = root / parent / target
    destination.symlink_to(outside / "sentinel")
    assert getattr(wrapper, "cmd_" + command)(args, conf) == 0
    assert not destination.is_symlink()
    assert (outside / "sentinel").read_text() == "untouched"


def test_temp_swap_cannot_redirect_metadata_changes(wrapper, installation, monkeypatch):
    root, outside, conf, args = installation
    sentinel = outside / "sentinel"
    sentinel.chmod(0o600)
    original = sentinel.stat()
    fchown = os.fchown

    def swap_temp(descriptor, uid, gid):
        temporary = next((root / "agent").glob(".stp-priv-*"))
        temporary.unlink()
        temporary.symlink_to(sentinel)
        fchown(descriptor, uid, gid)

    monkeypatch.setattr(wrapper.os, "fchown", swap_temp)
    wrapper.cmd_write_version(args, conf)
    after = sentinel.stat()
    assert (after.st_mode, after.st_uid, after.st_gid) == (original.st_mode, original.st_uid, original.st_gid)
    assert sentinel.read_text() == "untouched"


def test_schema_copies_only_validated_bytes(wrapper, installation, monkeypatch):
    root, outside, conf, args = installation
    source = Path(args.file)
    validated = source.read_text()
    write = wrapper._atomic_write_at

    def replace_source(*values, **kwargs):
        source.unlink()
        source.symlink_to(outside / "sentinel")
        return write(*values, **kwargs)

    monkeypatch.setattr(wrapper, "_atomic_write_at", replace_source)
    wrapper.cmd_install_schema(args, conf)
    assert (root / "schemas/pipeline_schema.json").read_text() == validated


def test_env_symlink_is_never_read(wrapper, installation):
    root, outside, conf, args = installation
    (root / ".env").unlink()
    (root / ".env").symlink_to(outside / "sentinel")
    with pytest.raises(OSError):
        wrapper.cmd_sync_env(args, conf)
    assert (outside / "sentinel").read_text() == "untouched"


def test_env_preserves_metadata(wrapper, installation):
    root, _, conf, args = installation
    env = root / ".env"
    env.chmod(0o640)
    before = env.stat()
    wrapper.cmd_sync_env(args, conf)
    after = env.stat()
    assert env.read_text() == "HOST_ID=test\nAGENT_SECRET=test-secret\n"
    assert (after.st_mode, after.st_uid, after.st_gid) == (before.st_mode, before.st_uid, before.st_gid)


def test_fix_ownership_never_traverses_symlinks(wrapper, installation, monkeypatch):
    root, outside, conf, args = installation
    (root / "agent/escape").symlink_to(outside, target_is_directory=True)
    changed = []
    chown = os.chown

    def record(name, *owner, dir_fd, follow_symlinks):
        assert follow_symlinks is False
        changed.append(name)
        chown(name, *owner, dir_fd=dir_fd, follow_symlinks=False)

    monkeypatch.setattr(wrapper.os, "chown", record)
    wrapper.cmd_fix_ownership(args, conf)
    assert "escape" in changed
    assert "sentinel" not in changed
