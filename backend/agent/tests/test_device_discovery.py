import subprocess
from unittest.mock import patch

import pytest

try:
    from backend.agent import device_discovery as device_module
except ModuleNotFoundError:  # pragma: no cover
    from agent import device_discovery as device_module


@pytest.fixture
def adb_path() -> str:
    return "/usr/bin/adb"


@pytest.fixture
def serial() -> str:
    return "SERIAL-001"


def test_discover_devices_success(adb_path: str, completed_process_factory):
    stdout = (
        "List of devices attached\n"
        "SERIAL-1 device product:foo model:Pixel_7 transport_id:1\n"
        "SERIAL-2 unauthorized transport_id:2\n"
        "badline\n"
        "SERIAL-3 device\n"
    )
    cp = completed_process_factory(stdout=stdout)

    with patch.object(device_module.subprocess, "run", return_value=cp) as mock_run:
        devices = device_module.discover_devices(adb_path)

    assert devices == [
        {"serial": "SERIAL-1", "adb_state": "device", "model": "Pixel_7"},
        {"serial": "SERIAL-2", "adb_state": "unauthorized", "model": None},
        {"serial": "SERIAL-3", "adb_state": "device", "model": None},
    ]
    mock_run.assert_called_once_with(
        [adb_path, "devices", "-l"],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_discover_devices_returns_empty_on_exception(adb_path: str):
    with patch.object(device_module.subprocess, "run", side_effect=RuntimeError("adb failed")):
        assert device_module.discover_devices(adb_path) == []


def test_collect_device_info_success(adb_path: str, serial: str, completed_process_factory):
    check_result = completed_process_factory(stdout="test\n", returncode=0)
    battery_result = completed_process_factory(stdout="level: 87\ntemperature: 356\n", returncode=0)

    with patch.object(device_module.subprocess, "run", side_effect=[check_result, battery_result]) as mock_run:
        with patch.object(device_module, "_ping_with_fallback", return_value=23.4) as mock_ping:
            info = device_module.collect_device_info(adb_path, serial)

    assert info == {
        "serial": serial,
        "adb_state": "device",
        "adb_connected": True,
        "model": None,
        "battery_level": 87,
        "temperature": 35,
        "network_latency": 23.4,
    }
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0].args[0] == [adb_path, "-s", serial, "shell", "echo", "test"]
    assert mock_run.call_args_list[1].args[0] == [adb_path, "-s", serial, "shell", "dumpsys", "battery"]
    mock_ping.assert_called_once_with(adb_path, serial, "223.5.5.5", fallback="8.8.8.8")


def test_collect_device_info_returns_early_when_adb_check_failed(adb_path: str, serial: str, completed_process_factory):
    check_result = completed_process_factory(stdout="", stderr="offline", returncode=1)

    with patch.object(device_module.subprocess, "run", return_value=check_result) as mock_run:
        with patch.object(device_module, "_ping_with_fallback", return_value=100.0) as mock_ping:
            info = device_module.collect_device_info(adb_path, serial)

    assert info == {
        "serial": serial,
        "adb_state": "offline",
        "adb_connected": False,
        "model": None,
        "battery_level": None,
        "temperature": None,
        "network_latency": None,
    }
    mock_run.assert_called_once()
    mock_ping.assert_not_called()


def test_collect_device_info_handles_adb_check_exception(adb_path: str, serial: str):
    with patch.object(
        device_module.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd="adb shell echo test", timeout=5),
    ) as mock_run:
        with patch.object(device_module, "_ping_with_fallback", return_value=50.0) as mock_ping:
            info = device_module.collect_device_info(adb_path, serial)

    assert info["adb_state"] == "offline"
    assert info["adb_connected"] is False
    assert info["battery_level"] is None
    assert info["temperature"] is None
    assert info["network_latency"] is None
    mock_run.assert_called_once()
    mock_ping.assert_not_called()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("level: 80\n", 80),
        ("foo\n  level:   15\nbar\n", 15),
        ("level: not-a-number\n", 0),
        ("status: unknown\n", 0),
    ],
)
def test_parse_battery_level(text: str, expected: int):
    assert device_module._parse_battery_level(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("temperature: 365\n", 36),
        ("temperature: 400\n", 40),
        ("temperature: invalid\n", 0),
        ("health: good\n", 0),
    ],
)
def test_parse_battery_temp(text: str, expected: int):
    assert device_module._parse_battery_temp(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "PING 8.8.8.8 (8.8.8.8): 56 data bytes\n"
            "rtt min/avg/max/mdev = 11.234/22.345/33.456/1.234 ms\n",
            22.345,
        ),
        (
            "PING 8.8.8.8\n"
            "round-trip min/avg/max = 1.000/2.500/3.000 ms\n",
            2.5,
        ),
        (
            "64 bytes from 8.8.8.8: seq=1 ttl=117 time=40.4ms\n"
            "64 bytes from 8.8.8.8: seq=2 ttl=117 time=41.6ms\n",
            41.6,
        ),
        ("64 bytes from 8.8.8.8: seq=1 ttl=117\n", None),
        ("rtt min/avg/max/mdev = 1.0/notnum/3.0/0.5 ms\n", None),
    ],
)
def test_parse_ping_time(text: str, expected):
    result = device_module._parse_ping_time(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_ping_with_fallback_primary_success(adb_path: str, serial: str, completed_process_factory):
    primary = completed_process_factory(stdout="ping ok", returncode=0)

    with patch.object(device_module.subprocess, "run", return_value=primary) as mock_run:
        with patch.object(device_module, "_parse_ping_time", return_value=12.3) as mock_parse:
            latency = device_module._ping_with_fallback(adb_path, serial, "223.5.5.5", fallback="8.8.8.8")

    assert latency == 12.3
    mock_run.assert_called_once_with(
        [adb_path, "-s", serial, "shell", "ping", "-c", "3", "223.5.5.5"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    mock_parse.assert_called_once_with("ping ok")


def test_ping_with_fallback_switches_to_fallback_on_primary_failure(adb_path: str, serial: str, completed_process_factory):
    primary_failed = completed_process_factory(stdout="failed", returncode=1)
    fallback_ok = completed_process_factory(stdout="fallback ok", returncode=0)

    with patch.object(device_module.subprocess, "run", side_effect=[primary_failed, fallback_ok]) as mock_run:
        with patch.object(device_module, "_parse_ping_time", return_value=45.6) as mock_parse:
            latency = device_module._ping_with_fallback(adb_path, serial, "223.5.5.5", fallback="8.8.8.8")

    assert latency == 45.6
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0].args[0][-1] == "223.5.5.5"
    assert mock_run.call_args_list[1].args[0][-1] == "8.8.8.8"
    mock_parse.assert_called_once_with("fallback ok")


def test_ping_with_fallback_no_fallback_returns_none_on_packet_loss(adb_path: str, serial: str, completed_process_factory):
    packet_loss = completed_process_factory(
        stdout="3 packets transmitted, 0 received, 100% packet loss",
        returncode=0,
    )

    with patch.object(device_module.subprocess, "run", return_value=packet_loss) as mock_run:
        with patch.object(device_module, "_parse_ping_time") as mock_parse:
            latency = device_module._ping_with_fallback(adb_path, serial, "223.5.5.5")

    assert latency is None
    mock_run.assert_called_once()
    mock_parse.assert_not_called()


def test_ping_with_fallback_returns_none_when_all_parse_failed(adb_path: str, serial: str, completed_process_factory):
    first_ok = completed_process_factory(stdout="first", returncode=0)
    second_ok = completed_process_factory(stdout="second", returncode=0)

    with patch.object(device_module.subprocess, "run", side_effect=[first_ok, second_ok]) as mock_run:
        with patch.object(device_module, "_parse_ping_time", side_effect=[None, None]) as mock_parse:
            latency = device_module._ping_with_fallback(adb_path, serial, "223.5.5.5", fallback="8.8.8.8")

    assert latency is None
    assert mock_run.call_count == 2
    assert mock_parse.call_count == 2
