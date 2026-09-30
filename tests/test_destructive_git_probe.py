"""The probe must reject missing facts and distinguish checker failures from gaps."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from tools.dev import destructive_git_probe as probe
from tools.dev import check_destructive_git as guard


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


def test_checker_exception_is_unverified(oracle):
    def broken(command):
        raise RuntimeError("checker unavailable")

    case = probe.Case("checker-error", "git reset --hard", ["reset", "--hard"], True)
    assert probe.compare(case, *oracle, broken)["state"] == "UNVERIFIED"


@pytest.mark.parametrize("source,suffix,error", [
    (None, ".py", "FileNotFoundError"),
    ("def broken(\n", ".py", "SyntaxError"),
    ("raise ImportError('private-error-text')\n", ".py", "ImportError"),
    ("raise SystemExit(0)\n", ".py", "SystemExit"),
    ("raise SystemExit(1)\n", ".py", "SystemExit"),
    ("name = 1\n", ".py", "AttributeError"),
    ("find_blocked = None\n", ".py", "TypeError"),
    ("find_blocked = None\n", ".txt", "ImportError"),
])
def test_checker_load_failure_emits_unverified_report(tmp_path, source, suffix, error):
    checker = tmp_path / ("checker" + suffix)
    if source is not None:
        checker.write_text(source)
    evidence = tmp_path / "evidence.json"
    result = subprocess.run(
        [sys.executable, probe.__file__, "--checker", str(checker),
         "--self-test", "--json", str(evidence)],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert result.stderr == ""
    assert "private-error-text" not in result.stdout
    report = json.loads(evidence.read_text())
    assert report["counts"] == {"UNVERIFIED": 1}
    assert report["details"] == [
        {"state": "UNVERIFIED", "stage": "checker-load", "error": error},
    ]
    assert json.loads(result.stdout.splitlines()[0])["counts"] == report["counts"]
    assert report["checker"] == str(checker)
    assert (report["sha256"] is None) == (source is None)


def test_checker_read_permission_error_is_unverified(monkeypatch, tmp_path, capsys):
    checker = tmp_path / "checker.py"
    monkeypatch.setattr(sys, "argv", [probe.__file__, "--checker", str(checker)])

    def denied(path):
        raise PermissionError("private-error-text")

    def must_not_run(*args):
        pytest.fail("loader failure must not run Bash cases")

    monkeypatch.setattr(probe.Path, "read_bytes", denied)
    monkeypatch.setattr(probe, "run", must_not_run)
    assert probe.main() == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert "private-error-text" not in output.out
    assert json.loads(output.out.splitlines()[0])["counts"] == {"UNVERIFIED": 1}
    detail = json.loads(output.out.splitlines()[1])
    assert detail == {"state": "UNVERIFIED", "stage": "checker-load", "error": "PermissionError"}


@pytest.mark.parametrize("change,error", [("remove", "FileNotFoundError"), ("rewrite", "ValueError")])
def test_checker_source_failure_after_probe_is_unverified(monkeypatch, tmp_path, capsys, change, error):
    checker = tmp_path / "checker.py"
    checker.write_text("def find_blocked(command): return False\n")
    monkeypatch.setattr(sys, "argv", [probe.__file__, "--checker", str(checker)])

    def change_source(*args):
        if change == "remove":
            checker.unlink()
        else:
            checker.write_text("def find_blocked(command): return True\n")
        return {"counts": {"PASS": 1}, "details": []}

    monkeypatch.setattr(probe, "run", change_source)
    assert probe.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.out.splitlines()[0])["counts"] == {"PASS": 1, "UNVERIFIED": 1}
    assert json.loads(output.out.splitlines()[1]) == {
        "state": "UNVERIFIED", "stage": "checker-source", "error": error,
    }


@pytest.mark.parametrize("inner,argv,dangerous", [
    (r"\\g\\i\\t \\r\\e\\s\\e\\t \\-\\-\\h\\a\\r\\d", ["reset", "--hard"], True),
    ("git\\\\\n reset\\\\\n --hard", ["reset", "--hard"], True),
    (r"\\g\\i\\t \\s\\t\\a\\s\\h \\d\\r\\o\\p", ["stash", "drop"], True),
    (r"\\g\\i\\t \\s\\t\\a\\s\\h \\l\\i\\s\\t", ["stash", "list"], False),
    ("git commit -m 'data; git stash'", ["commit", "-m", "data; git stash"], False),
])
@pytest.mark.parametrize("host", ["plain", "double-quoted", "heredoc"])
def test_backquote_escape_layers_follow_actual_bash(oracle, inner, argv, dangerous, host):
    command = {
        "plain": "printf '%s' `" + inner + "`",
        "double-quoted": "printf '%s' \"`" + inner + "`\"",
        "heredoc": "cat <<EOF\n`" + inner + "`\nEOF\n",
    }[host]
    case = probe.Case(host, command, argv, dangerous)
    assert probe.compare(case, *oracle, guard.find_blocked)["state"] == "PASS"


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
