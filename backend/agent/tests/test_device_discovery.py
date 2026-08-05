import os
import signal
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


def test_get_adb_server_port_default(monkeypatch):
    monkeypatch.delenv("ANDROID_ADB_SERVER_PORT", raising=False)
    assert device_module.get_adb_server_port() == 5037


def test_get_adb_server_port_from_env(monkeypatch):
    monkeypatch.setenv("ANDROID_ADB_SERVER_PORT", "5039")
    assert device_module.get_adb_server_port() == 5039


def test_get_adb_server_port_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("ANDROID_ADB_SERVER_PORT", "not-a-port")
    assert device_module.get_adb_server_port() == 5037


def test_list_adb_fork_servers_parses_ports(completed_process_factory):
    stdout = (
        "962318 1000 adb -L tcp:5039 fork-server server --reply-fd 4\n"
        "962551 1000 adb -L tcp:5037 fork-server server --reply-fd 4\n"
        "999999 1000 /usr/bin/adb devices -l\n"
        "1234 1000 /usr/bin/adb -P 5555 fork-server server --reply-fd 4\n"
        "not-a-pid line\n"
    )
    cp = completed_process_factory(stdout=stdout)

    with patch.object(device_module.subprocess, "run", return_value=cp) as mock_run:
        servers = device_module.list_adb_fork_servers()

    assert servers == [
        {"pid": 962318, "uid": 1000, "port": 5039, "cmdline": "adb -L tcp:5039 fork-server server --reply-fd 4"},
        {"pid": 962551, "uid": 1000, "port": 5037, "cmdline": "adb -L tcp:5037 fork-server server --reply-fd 4"},
        {"pid": 1234, "uid": 1000, "port": 5555, "cmdline": "/usr/bin/adb -P 5555 fork-server server --reply-fd 4"},
    ]
    mock_run.assert_called_once_with(
        ["ps", "-eo", "pid=,uid=,args="],
        capture_output=True,
        text=True,
        timeout=10,
    )


def _fork_server(pid: int, port: int, uid: int | None = None) -> dict:
    return {
        "pid": pid,
        "uid": uid if uid is not None else os.geteuid(),
        "port": port,
        "cmdline": f"adb -L tcp:{port} fork-server server",
    }


def test_ensure_single_adb_server_skipped_in_static_mode(monkeypatch):
    monkeypatch.setenv("STP_STATIC_DEVICE_SERIALS", "SERIAL-1,SERIAL-2")
    with patch.object(
        device_module,
        "list_adb_fork_servers",
        return_value=[_fork_server(111, 5039)],
    ) as mock_list:
        with patch.object(device_module.subprocess, "run") as mock_run:
            result = device_module.ensure_single_adb_server(port=5037)

    assert result["skipped"] is True
    assert result["killed"] == []
    mock_list.assert_called_once()
    mock_run.assert_not_called()


def test_ensure_single_adb_server_kills_foreign_and_restarts_target(
    adb_path, completed_process_factory
):
    ok = completed_process_factory(returncode=0)
    servers = [_fork_server(111, 5039), _fork_server(222, 5037)]

    with patch.object(device_module, "list_adb_fork_servers", return_value=servers):
        with patch.object(device_module.subprocess, "run", return_value=ok) as mock_run:
            result = device_module.ensure_single_adb_server(adb_path, port=5037)

    assert [server["port"] for server in result["killed"]] == [5039]
    assert result["started"] is True
    assert mock_run.call_count == 2

    kill_call = mock_run.call_args_list[0]
    assert kill_call.args[0] == [adb_path, "kill-server"]
    assert kill_call.kwargs["env"]["ANDROID_ADB_SERVER_PORT"] == "5039"

    start_call = mock_run.call_args_list[1]
    assert start_call.args[0] == [adb_path, "start-server"]
    assert start_call.kwargs["env"]["ANDROID_ADB_SERVER_PORT"] == "5037"


def test_ensure_single_adb_server_restarts_when_target_missing(
    adb_path, completed_process_factory
):
    ok = completed_process_factory(returncode=0)
    with patch.object(
        device_module,
        "list_adb_fork_servers",
        return_value=[_fork_server(111, 5039)],
    ):
        with patch.object(device_module.subprocess, "run", return_value=ok) as mock_run:
            result = device_module.ensure_single_adb_server(adb_path, port=5037)

    assert [server["port"] for server in result["killed"]] == [5039]
    assert result["started"] is True
    assert mock_run.call_count == 2


def test_ensure_single_adb_server_noop_when_only_target(adb_path):
    with patch.object(
        device_module,
        "list_adb_fork_servers",
        return_value=[_fork_server(222, 5037)],
    ):
        with patch.object(device_module.subprocess, "run") as mock_run:
            result = device_module.ensure_single_adb_server(adb_path, port=5037)

    assert result["killed"] == []
    assert result["started"] is True
    mock_run.assert_not_called()


def test_ensure_single_adb_server_skips_other_user_server(
    adb_path, completed_process_factory
):
    other_uid = os.geteuid() + 1_000_000
    ok = completed_process_factory(returncode=0)
    with patch.object(
        device_module,
        "list_adb_fork_servers",
        return_value=[_fork_server(111, 5039, uid=other_uid)],
    ):
        with patch.object(device_module.subprocess, "run", return_value=ok) as mock_run:
            result = device_module.ensure_single_adb_server(adb_path, port=5037)

    assert result["killed"] == []
    assert result["started"] is True
    mock_run.assert_called_once()  # 只启动目标端口 server，不杀他用户进程


def test_ensure_single_adb_server_falls_back_to_sigterm(
    adb_path, completed_process_factory
):
    failed = completed_process_factory(returncode=1, stderr="cannot kill")
    with patch.object(
        device_module,
        "list_adb_fork_servers",
        return_value=[_fork_server(111, 5039), _fork_server(222, 5037)],
    ):
        with patch.object(device_module.subprocess, "run", return_value=failed):
            with patch.object(device_module.os, "kill") as mock_kill:
                result = device_module.ensure_single_adb_server(adb_path, port=5037)

    assert [server["port"] for server in result["killed"]] == [5039]
    mock_kill.assert_called_once_with(111, signal.SIGTERM)


def test_cli_repair_uses_ensure_single_adb_server(capsys):
    with patch.object(
        device_module,
        "ensure_single_adb_server",
        return_value={
            "port": 5037,
            "servers": [],
            "killed": [],
            "started": True,
            "skipped": False,
        },
    ):
        assert device_module.main(["repair"]) == 0
    assert "target adb server ready" in capsys.readouterr().out


def test_collect_device_info_success(adb_path: str, serial: str, completed_process_factory):
    check_result = completed_process_factory(stdout="test\n", returncode=0)
    battery_result = completed_process_factory(stdout="level: 87\ntemperature: 356\n", returncode=0)
    build_result = completed_process_factory(stdout="SP-L2-20260518\n", returncode=0)

    with patch.object(
        device_module.subprocess,
        "run",
        side_effect=[check_result, battery_result, build_result],
    ) as mock_run:
        with patch.object(device_module, "_ping_with_fallback", return_value=23.4) as mock_ping:
            # #73: 平台探测走 device_platform 自己的 subprocess,这里独立打桩
            with patch.object(device_module, "detect_device_platform", return_value="MTK"):
                info = device_module.collect_device_info(adb_path, serial)

    assert info == {
        "serial": serial,
        "adb_state": "device",
        "adb_connected": True,
        "model": None,
        "battery_level": 87,
        "temperature": 35,
        "network_latency": 23.4,
        "build_display_id": "SP-L2-20260518",
        "platform": "MTK",
    }
    assert mock_run.call_count == 3
    assert mock_run.call_args_list[0].args[0] == [adb_path, "-s", serial, "shell", "echo", "test"]
    assert mock_run.call_args_list[1].args[0] == [adb_path, "-s", serial, "shell", "dumpsys", "battery"]
    assert mock_run.call_args_list[2].args[0] == [
        adb_path, "-s", serial, "shell", "getprop", "ro.build.display.id",
    ]
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
        "build_display_id": None,
        # 早退路径不探测平台 — 保持初始 UNKNOWN
        "platform": "UNKNOWN",
    }
    mock_run.assert_called_once()
    mock_ping.assert_not_called()


def test_collect_device_info_short_circuits_on_unauthorized_raw_state(adb_path: str, serial: str):
    """adb devices -l 已报告 unauthorized 时直接判定 error,不再探测 shell（必然失败）。"""
    with patch.object(device_module.subprocess, "run") as mock_run:
        info = device_module.collect_device_info(adb_path, serial, raw_adb_state="unauthorized")

    assert info == {
        "serial": serial,
        "adb_state": "unauthorized",
        "adb_connected": False,
        "model": None,
        "battery_level": None,
        "temperature": None,
        "network_latency": None,
        "build_display_id": None,
        # 短路路径不探测平台 — 保持初始 UNKNOWN
        "platform": "UNKNOWN",
    }
    mock_run.assert_not_called()


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
