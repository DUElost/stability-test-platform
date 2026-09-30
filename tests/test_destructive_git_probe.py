"""The probe must reject missing facts and distinguish checker failures from gaps."""
from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

from tools.dev import destructive_git_probe as probe


@pytest.fixture
def oracle(tmp_path):
    trace = tmp_path / "trace"
    binary = tmp_path / "bin"
    binary.mkdir()
    mock = binary / "git"
    mock.write_text(f"#!{sys.executable}\nimport json, sys\n"
                    f"with open({str(trace)!r}, 'a') as f:\n"
                    " f.write(json.dumps(sys.argv[1:])+'\\n')\n")
    mock.chmod(0o700)
    return shutil.which("bash"), {"PATH": f"{binary}:/usr/bin:/bin"}, tmp_path, trace


@pytest.mark.parametrize("style", probe.STYLES)
def test_quotes_reach_mock_git_as_exact_literal_argv(oracle, style):
    argv = ["commit", "-m", "data; $(echo text) `git stash` 'quoted'"]
    command = " ".join(probe.quote(w, style) for w in ["git", *argv])
    case = probe.Case(style, command, argv, False)
    assert probe.compare(case, *oracle, lambda c: False)["state"] == "PASS"


@pytest.mark.parametrize("command,argv,checker,expected", [
    ("git reset --hard", ["reset", "--hard"], False, "MISS"),
    ("git status", ["status"], True, "FALSE_POSITIVE"),
    ("false", ["reset", "--hard"], False, "UNVERIFIED"),
    ("true", ["reset", "--hard"], False, "UNVERIFIED"),
    ("git status", ["reset", "--hard"], False, "UNVERIFIED"),
    ("git '", ["reset", "--hard"], False, "UNVERIFIED"),
])
def test_execution_failures_cannot_turn_into_pass(oracle, command, argv, checker, expected):
    case = probe.Case("negative", command, argv, argv[0] == "reset")
    assert probe.compare(case, *oracle, lambda c: checker)["state"] == expected


def test_only_declared_boundaries_are_exempt_from_failure(oracle):
    case = probe.Case("dynamic", "git reset --hard", ["reset", "--hard"], True, known_gap=True)
    assert probe.compare(case, *oracle, lambda c: False)["state"] == "KNOWN_GAP"
    case.argv = ["status"]
    assert probe.compare(case, *oracle, lambda c: False)["state"] == "UNVERIFIED"


def test_missing_bash_is_unverified(monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda name: None)
    assert probe.run(lambda c: False)["counts"] == {"UNVERIFIED": 1}


def test_probe_ignores_ambient_shell_startup(monkeypatch, tmp_path):
    marker = tmp_path / "startup-was-read"
    startup = tmp_path / "startup"
    startup.write_text("touch " + str(marker) + "\n")
    monkeypatch.setenv("BASH_ENV", str(startup))
    results = probe.run(lambda c: False, small=True)
    assert results["counts"].get("MISS", 0) > 0
    assert not marker.exists()


def test_every_supported_case_uses_a_mock_git_name(tmp_path):
    mock = tmp_path / "bin/git"
    selected = list(probe.cases(mock, tmp_path / "payload.sh"))
    assert selected
    assert all("/usr/bin/git" not in case.command for case in selected)
    assert len({case.name for case in selected}) == len(selected)
    assert all(case.known_gap is False for case in selected if "/" in case.name)
    # Generators contain no captured ambient environment or production paths.
    assert os.environ.get("HOME", "/not-the-probe") not in json.dumps([c.command for c in selected])
