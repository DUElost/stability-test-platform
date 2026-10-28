"""Exercise the real CI commands through a line reader before a blocked phase ends."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
JOBS = ("backend-test", "pr-backend-tests-informational")


def _start(job: str, probe: Path, *extra: str, **variables: str) -> subprocess.Popen:
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    step = next(s for s in jobs[job]["steps"] if s.get("name") == "Run backend tests")
    command = shlex.split(step["run"])
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONPATH": os.pathsep.join((str(ROOT), os.environ.get("PYTHONPATH", ""))),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PY_COLORS": "0",
        **variables,
    }
    while "=" in command[0]:
        key, value = command.pop(0).split("=", 1)
        env[key] = value
    assert command[:3] == ["python", "-m", "pytest"]
    args = command[3:]
    assert args.count("backend/tests/") == 1
    args[args.index("backend/tests/")] = str(probe)
    config = probe.parent / "pytest.ini"
    config.write_text("[pytest]\n")
    # This nested probe inherits the outer test run's cgroup. Local top-level
    # execution uses run_pytest.py; CI uses the existing isolated runner job.
    return subprocess.Popen(
        [sys.executable, "-m", "pytest", *args,
         "-c", str(config), "--color=no", *extra],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )


@pytest.mark.parametrize("job", JOBS)
@pytest.mark.parametrize("phase", ("setup", "call"))
def test_current_case_is_a_complete_line_while_phase_is_blocked(tmp_path, job, phase):
    entered, release = tmp_path / "entered", tmp_path / "release"
    probe = tmp_path / "test_line_delivery.py"
    probe.write_text(f"""
import os
from pathlib import Path
import time
import pytest

def block(phase):
    if phase != {phase!r}:
        return
    Path(os.environ["PROGRESS_ENTERED"]).touch()
    deadline = time.monotonic() + 30
    while not Path(os.environ["PROGRESS_RELEASE"]).exists():
        if time.monotonic() >= deadline:
            raise AssertionError("controller did not release the phase")
        time.sleep(0.01)

@pytest.fixture
def slow_fixture():
    block("setup")

def test_previous():
    pass

def test_current(slow_fixture):
    print("CAPTURE_ONLY_SENTINEL")
    block("call")
""")
    process = _start(job, probe, PROGRESS_ENTERED=str(entered), PROGRESS_RELEASE=str(release))
    lines: list[str] = []
    visible = threading.Event()

    def read_lines():
        assert process.stdout is not None
        # Read complete lines, matching Actions runner's StreamReader.ReadLine.
        for line in process.stdout:
            lines.append(line)
            if line == "[pytest-start] test_line_delivery.py::test_current\n":
                visible.set()

    reader = threading.Thread(target=read_lines, daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + 15
        while not entered.exists():
            assert process.poll() is None, "child exited before controlled phase:\n" + "".join(lines)
            assert time.monotonic() < deadline, "child did not reach controlled phase"
            time.sleep(0.01)
        assert visible.wait(3), "current case was not delivered as a complete line:\n" + "".join(lines)
        assert process.poll() is None
        assert not release.exists(), "phase must still be blocked when the line is observed"
        assert any("test_previous PASSED" in line for line in lines)
        assert not any("CAPTURE_ONLY_SENTINEL" in line for line in lines)
        print(f"line_visible_while_blocked: job={job}; phase={phase}; child_alive=True; release_absent=True")
    finally:
        release.touch()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise
        finally:
            reader.join(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
    assert not reader.is_alive()
    assert process.returncode == 0
    assert "CAPTURE_ONLY_SENTINEL" not in "".join(lines)
    assert f"pytest-{pytest.__version__}" in "".join(lines)


def test_progress_preserves_selection_capture_and_failure_exit(tmp_path):
    probe = tmp_path / "test_selection.py"
    probe.write_text("""
def test_unselected():
    raise RuntimeError("must be deselected")

def test_selected_failure():
    print("CAPTURED_FAILURE_SENTINEL")
    assert False, "controlled failure"
""")
    process = _start("backend-test", probe, "-k", "selected_failure")
    try:
        output, _ = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)
        raise
    assert process.returncode == 1
    assert "1 failed, 1 deselected" in output
    assert "[pytest-start] test_selection.py::test_selected_failure\n" in output
    assert "[pytest-start] test_selection.py::test_unselected" not in output
    assert "Captured stdout call" in output
    assert output.index("Captured stdout call") < output.rindex("CAPTURED_FAILURE_SENTINEL")
