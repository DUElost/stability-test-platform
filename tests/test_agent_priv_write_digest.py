"""write-digest 子命令（ADR-0040 D2 / #1907）：格式校验与受控写入。"""

from __future__ import annotations

import grp
import importlib.util
import os
from pathlib import Path
import pwd
from types import SimpleNamespace

import pytest


WRAPPER = Path(__file__).resolve().parents[1] / "backend/agent/stp_agent_priv.py"
DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def wrapper(monkeypatch):
    spec = importlib.util.spec_from_file_location("stp_agent_priv_digest", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_require_root", lambda: None)
    return module


@pytest.fixture
def conf(tmp_path):
    (tmp_path / "agent").mkdir()
    return {
        "INSTALL_DIR": str(tmp_path),
        "AGENT_USER": pwd.getpwuid(os.getuid()).pw_name,
        "AGENT_GROUP": grp.getgrgid(os.getgid()).gr_name,
    }


def _args(digest):
    return SimpleNamespace(digest=digest)


def test_write_digest_happy_path(wrapper, conf, tmp_path):
    rc = wrapper.cmd_write_digest(_args(DIGEST), conf)
    assert rc == 0
    written = tmp_path / "agent" / "ARTIFACT_DIGEST"
    assert written.read_text() == DIGEST + "\n"
    assert (written.stat().st_mode & 0o777) == 0o644


def test_write_digest_empty_skips(wrapper, conf, tmp_path):
    rc = wrapper.cmd_write_digest(_args(""), conf)
    assert rc == 0
    assert not (tmp_path / "agent" / "ARTIFACT_DIGEST").exists()


def test_write_digest_rejects_bad_format(wrapper, conf):
    for bad in ("deadbeef", "sha256:xyz", "md5:" + "a" * 64, "sha256:" + "A" * 64):
        with pytest.raises(wrapper.PrivError):
            wrapper.cmd_write_digest(_args(bad), conf)
