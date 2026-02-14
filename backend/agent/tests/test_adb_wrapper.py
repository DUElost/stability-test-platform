import subprocess
from unittest.mock import MagicMock, patch

import pytest

try:
    from backend.agent import adb_wrapper as adb_module
except ModuleNotFoundError:  # pragma: no cover
    from agent import adb_wrapper as adb_module

AdbError = adb_module.AdbError
AdbWrapper = adb_module.AdbWrapper


@pytest.fixture
def adb() -> AdbWrapper:
    return AdbWrapper(adb_path="custom-adb", timeout=12.5)


def test_run_success_calls_subprocess_with_defaults(adb: AdbWrapper):
    result = MagicMock()
    result.returncode = 0
    result.stdout = "ok"
    result.stderr = ""

    with patch.object(adb_module.subprocess, "run", return_value=result) as mock_run:
        output = adb._run(["devices", "-l"])

    assert output is result
    mock_run.assert_called_once_with(
        ["custom-adb", "devices", "-l"],
        capture_output=True,
        text=True,
        timeout=12.5,
    )


def test_run_uses_explicit_timeout(adb: AdbWrapper, completed_process_factory):
    cp = completed_process_factory(stdout="ok")
    with patch.object(adb_module.subprocess, "run", return_value=cp) as mock_run:
        adb._run(["version"], timeout=5.0)

    mock_run.assert_called_once_with(
        ["custom-adb", "version"],
        capture_output=True,
        text=True,
        timeout=5.0,
    )


@pytest.mark.parametrize(
    ("stderr", "stdout", "expected_message"),
    [
        ("permission denied", "", "permission denied"),
        ("", "command failed", "command failed"),
    ],
)
def test_run_raises_adberror_with_output(
    adb: AdbWrapper,
    completed_process_factory,
    stderr: str,
    stdout: str,
    expected_message: str,
):
    cp = completed_process_factory(stdout=stdout, stderr=stderr, returncode=1)
    with patch.object(adb_module.subprocess, "run", return_value=cp):
        with pytest.raises(AdbError) as exc_info:
            adb._run(["shell", "id"])

    assert str(exc_info.value) == expected_message


def test_run_timeout_propagates(adb: AdbWrapper):
    timeout_err = subprocess.TimeoutExpired(cmd=["custom-adb", "devices"], timeout=1)
    with patch.object(adb_module.subprocess, "run", side_effect=timeout_err):
        with pytest.raises(subprocess.TimeoutExpired):
            adb._run(["devices"], timeout=1)


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        (
            "List of devices attached\n"
            "emulator-5554 device product:sdk model:Android_SDK\n"
            "R58M123456A device usb:1-1 model:SM_G9980\n",
            ["emulator-5554", "R58M123456A"],
        ),
        (
            "List of devices attached\n"
            "ZX1G22 offline\n"
            "0123456789 unauthorized usb:1-2\n"
            "just-serial\n"
            "\n",
            ["ZX1G22", "0123456789", "just-serial"],
        ),
        ("List of devices attached\n\n", []),
    ],
)
def test_devices_parses_multiple_output_formats(adb: AdbWrapper, completed_process_factory, stdout: str, expected):
    cp = completed_process_factory(stdout=stdout)
    with patch.object(adb, "_run", return_value=cp):
        assert adb.devices() == expected


def test_shell_builds_expected_command(adb: AdbWrapper, completed_process_factory):
    cp = completed_process_factory(stdout="uid=2000(shell)")
    with patch.object(adb, "_run", return_value=cp) as mock_run:
        result = adb.shell("SERIAL-01", ["id"])
    assert result is cp
    mock_run.assert_called_once_with(["-s", "SERIAL-01", "shell", "id"])


@pytest.mark.parametrize(
    ("method_name", "method_args", "expected_args"),
    [
        ("install", ("SERIAL-01", "/tmp/app.apk"), ["-s", "SERIAL-01", "install", "-r", "-g", "-t", "/tmp/app.apk"]),
        ("push", ("SERIAL-01", "/tmp/local.txt", "/data/local/tmp/local.txt"), ["-s", "SERIAL-01", "push", "/tmp/local.txt", "/data/local/tmp/local.txt"]),
        ("pull", ("SERIAL-01", "/sdcard/Download/a.txt", "/tmp/a.txt"), ["-s", "SERIAL-01", "pull", "/sdcard/Download/a.txt", "/tmp/a.txt"]),
    ],
)
def test_simple_command_wrappers(adb: AdbWrapper, completed_process_factory, method_name: str, method_args, expected_args):
    cp = completed_process_factory(stdout="Success")
    with patch.object(adb, "_run", return_value=cp) as mock_run:
        result = getattr(adb, method_name)(*method_args)
    assert result is cp
    mock_run.assert_called_once_with(expected_args)


def test_kill_process_calls_shell(adb: AdbWrapper, completed_process_factory):
    cp = completed_process_factory(stdout="")
    with patch.object(adb, "shell", return_value=cp) as mock_shell:
        result = adb.kill_process("SERIAL-01", "1234")
    assert result is cp
    mock_shell.assert_called_once_with("SERIAL-01", ["kill", "-9", "1234"])


@pytest.mark.parametrize(
    ("ps_output", "process_name", "expected"),
    [
        ("USER PID PPID NAME\nu0_a76 4321 1 com.demo.app\n", "com.demo.app", "4321"),
        ("USER PID PPID NAME\nu0_a77 9876 1 com.other.app\n", "com.demo.app", None),
        ("com.demo.app\n", "com.demo.app", None),
    ],
)
def test_get_pid_parsing(adb: AdbWrapper, completed_process_factory, ps_output: str, process_name: str, expected):
    cp = completed_process_factory(stdout=ps_output)
    with patch.object(adb, "shell", return_value=cp) as mock_shell:
        pid = adb.get_pid("SERIAL-01", process_name)

    assert pid == expected
    mock_shell.assert_called_once_with("SERIAL-01", ["ps"])
