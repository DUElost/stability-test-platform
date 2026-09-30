"""G1: missing protection must never launch pytest, even when scope creation succeeds."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load("run_pytest")


def hierarchy(tmp_path, limits):
    membership = tmp_path / "membership"
    membership.write_text("0::/parent/child\n")
    mount = tmp_path / "cgroup"
    for relative, memory, swap in limits:
        directory = mount / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "memory.max").write_text(str(memory))
        (directory / "memory.swap.max").write_text(str(swap))
    return membership, mount


@pytest.mark.parametrize("memory,swap,accepted", [
    (runner.MEMORY_MAX, 0, True), (1024, 0, True),
    (runner.MEMORY_MAX + 1, 0, False), (runner.MEMORY_MAX, 1, False),
    ("max", 0, False), (runner.MEMORY_MAX, "max", False),
    ("bad", 0, False), (-1, 0, False),
])
def test_actual_limits_are_required(tmp_path, memory, swap, accepted):
    paths = hierarchy(tmp_path, [("parent/child", memory, swap), ("parent", "max", "max")])
    assert (runner.memory_boundary(*paths) is not None) is accepted


def test_ancestors_independently_enforce_memory_and_swap(tmp_path):
    paths = hierarchy(tmp_path, [
        ("parent/child", "max", 0), ("parent", runner.MEMORY_MAX, "max"),
    ])
    assert runner.memory_boundary(*paths) == (runner.MEMORY_MAX, 0)


@pytest.mark.parametrize("membership", ["1:memory:/parent/child\n", "0::/../outside\n", "0:://outside\n"])
def test_missing_or_invalid_unified_membership_blocks(tmp_path, membership):
    paths = hierarchy(tmp_path, [("parent/child", runner.MEMORY_MAX, 0)])
    paths[0].write_text(membership)
    assert runner.memory_boundary(*paths) is None


def test_unreadable_evidence_blocks(tmp_path):
    paths = hierarchy(tmp_path, [("parent/child", runner.MEMORY_MAX, 0)])
    assert runner.memory_boundary(*paths) is None  # parent evidence is missing


def test_verified_boundary_executes_current_python_with_literal_arguments(monkeypatch):
    monkeypatch.setattr(runner, "memory_boundary", lambda: (runner.MEMORY_MAX, 0))
    monkeypatch.setattr(runner.os, "chdir", lambda path: None)
    calls = []

    def execv(executable, args):
        calls.append((executable, args))
        raise OSError(13, "denied")

    monkeypatch.setattr(runner.os, "execv", execv)
    assert runner.main(["-k", "name;$(touch sentinel)"]) == 2
    assert calls == [(sys.executable, [sys.executable, "-m", "pytest", "-k", "name;$(touch sentinel)"])]


@pytest.mark.parametrize("child_code,expected", [(7, 7), (-9, 137)])
def test_scope_launcher_preserves_exit_code_and_clean_application_environment(monkeypatch, child_code, expected):
    monkeypatch.setattr(runner, "memory_boundary", lambda: None)
    monkeypatch.setattr(runner.sys, "platform", "linux")
    monkeypatch.setattr(runner.os, "environ", {"PATH": "/usr/bin", "PYTHONPATH": "."})
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=child_code)

    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.main(["test with spaces.py", "-k", "a; echo b"]) == expected
    command, kwargs = calls[0]
    assert command == [
        "systemd-run", "--user", "--scope", "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0", "--",
        sys.executable, str(ROOT / "scripts/run_pytest.py"), runner.CHILD_FLAG,
        "test with spaces.py", "-k", "a; echo b",
    ]
    assert set(kwargs["env"]) == {"PATH", "PYTHONPATH", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}
    assert kwargs["cwd"] == ROOT


def test_successful_launcher_without_real_limits_cannot_start_pytest(monkeypatch):
    monkeypatch.setattr(runner, "memory_boundary", lambda: None)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: pytest.fail("must not relaunch"))
    monkeypatch.setattr(runner.os, "execv", lambda *a: pytest.fail("must not launch pytest"))
    assert runner.main([runner.CHILD_FLAG, "--version"]) == 2


def test_missing_launcher_never_falls_back_to_bare_pytest(monkeypatch):
    monkeypatch.setattr(runner, "memory_boundary", lambda: None)
    monkeypatch.setattr(runner.sys, "platform", "linux")

    def unavailable(*a, **kw):
        raise FileNotFoundError(2, "missing")

    monkeypatch.setattr(runner.subprocess, "run", unavailable)
    monkeypatch.setattr(runner.os, "execv", lambda *a: pytest.fail("uncapped fallback"))
    assert runner.main(["--version"]) == 2


def test_gate_call_sites_use_protection_and_keep_agent_env_isolation():
    gates = load("run_gates").GATES
    targets = {"prom-alerts", "agent-tests-collect", "agent-tests", "backend-tests", "integration", "repo-tests"}
    for name in targets:
        command, cwd, env = gates[name]
        tokens = shlex.split(command)
        assert str(ROOT / "scripts/run_pytest.py") in tokens, name
        assert "pytest" not in tokens, name  # no direct -m pytest bypass
        assert cwd == str(ROOT)
        if name.startswith("agent-tests"):
            assert tokens[:2] == ["env", "-i"]
            assert env is None


def test_real_entry_keeps_arguments_environment_and_pytest_exit_code(tmp_path):
    if runner.memory_boundary() is None:
        pytest.skip("real cgroup acceptance requires pytest itself to run under the protected entry")
    # A tiny pytest stand-in avoids importing suites/DB; the entry must still
    # verify the actual cgroup before executing this module with -m pytest.
    (tmp_path / "pytest.py").write_text(
        "import json, os, sys\n"
        "print(json.dumps({'argv':sys.argv[1:], 'cwd':os.getcwd(), 'marker':os.getenv('PROBE_MARKER')}))\n"
        "sys.exit(5)\n"
    )
    env = os.environ.copy()
    env.update(PYTHONPATH=str(tmp_path), PROBE_MARKER="kept")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_pytest.py"), "-k", "literal;$(echo forbidden)"],
        cwd=ROOT / "backend/agent", env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 5, result.stderr
    assert "[pytest] cgroup memory.max=" in result.stdout
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "argv": ["-k", "literal;$(echo forbidden)"], "cwd": str(ROOT), "marker": "kept",
    }
