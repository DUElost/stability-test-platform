import io
from collections import namedtuple
from unittest.mock import MagicMock, mock_open, patch

import pytest

try:
    from backend.agent import system_monitor as monitor_module
except ModuleNotFoundError:  # pragma: no cover
    from agent import system_monitor as monitor_module


@pytest.fixture
def disk_usage_type():
    return namedtuple("DiskUsage", ["total", "used", "free"])


@pytest.mark.parametrize(
    ("stat_line", "expected"),
    [
        ("cpu 100 50 50 300 0 0 0\n", 30.0),
        ("cpu 0 0 0 0\n", 0.0),
    ],
)
def test_get_cpu_usage_success(stat_line: str, expected: float):
    with patch("builtins.open", mock_open(read_data=stat_line)):
        assert monitor_module.get_cpu_usage() == expected


def test_get_cpu_usage_returns_zero_for_invalid_format():
    with patch("builtins.open", mock_open(read_data="intr 1 2 3\n")):
        assert monitor_module.get_cpu_usage() == 0.0


def test_get_cpu_usage_returns_zero_when_file_missing():
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert monitor_module.get_cpu_usage() == 0.0


@pytest.mark.parametrize(
    ("meminfo_text", "expected"),
    [
        (
            "MemTotal: 1000 kB\n"
            "MemFree: 100 kB\n"
            "MemAvailable: 250 kB\n"
            "Buffers: 10 kB\n"
            "Cached: 20 kB\n",
            75.0,
        ),
        (
            "MemTotal: 2000 kB\n"
            "MemFree: 1000 kB\n"
            "Buffers: 10 kB\n"
            "Cached: 20 kB\n"
            "SwapTotal: 0 kB\n",
            50.0,
        ),
        (
            "MemTotal: 0 kB\n"
            "MemFree: 0 kB\n"
            "MemAvailable: 0 kB\n"
            "Buffers: 0 kB\n"
            "Cached: 0 kB\n",
            0.0,
        ),
    ],
)
def test_get_memory_usage_success(meminfo_text: str, expected: float):
    with patch("builtins.open", mock_open(read_data=meminfo_text)):
        assert monitor_module.get_memory_usage() == expected


def test_get_memory_usage_returns_zero_for_bad_content():
    with patch("builtins.open", mock_open(read_data="MemTotal: not_number kB\n")):
        assert monitor_module.get_memory_usage() == 0.0


def test_get_memory_usage_returns_zero_when_file_missing():
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert monitor_module.get_memory_usage() == 0.0


def test_get_disk_usage_success(disk_usage_type):
    gb = 1024 ** 3
    usage = disk_usage_type(total=100 * gb, used=25 * gb, free=75 * gb)

    with patch.object(monitor_module.shutil, "disk_usage", return_value=usage) as mock_disk:
        result = monitor_module.get_disk_usage("/data")

    assert result == {
        "total_gb": 100.0,
        "used_gb": 25.0,
        "free_gb": 75.0,
        "usage_percent": 25.0,
    }
    mock_disk.assert_called_once_with("/data")


def test_get_disk_usage_handles_zero_total(disk_usage_type):
    usage = disk_usage_type(total=0, used=0, free=0)
    with patch.object(monitor_module.shutil, "disk_usage", return_value=usage):
        result = monitor_module.get_disk_usage("/")

    assert result == {
        "total_gb": 0.0,
        "used_gb": 0.0,
        "free_gb": 0.0,
        "usage_percent": 0.0,
    }


def test_get_disk_usage_returns_zero_dict_on_exception():
    with patch.object(monitor_module.shutil, "disk_usage", side_effect=OSError("disk error")):
        result = monitor_module.get_disk_usage("/")

    assert result == {
        "total_gb": 0,
        "used_gb": 0,
        "free_gb": 0,
        "usage_percent": 0.0,
    }


def test_get_network_connections_success():
    def _open_side_effect(path, *_args, **_kwargs):
        if path == "/proc/net/snmp":
            return io.StringIO("Tcp: RtoAlgorithm RtoMin RtoMax\n")
        if path == "/proc/net/tcp":
            return io.StringIO("header\nrow1\nrow2\nrow3\n")
        raise FileNotFoundError(path)

    with patch("builtins.open", side_effect=_open_side_effect):
        result = monitor_module.get_network_connections()

    assert result == {"tcp_connections": 3}


def test_get_network_connections_tcp_file_missing():
    def _open_side_effect(path, *_args, **_kwargs):
        if path == "/proc/net/snmp":
            return io.StringIO("Tcp: anything\n")
        if path == "/proc/net/tcp":
            raise FileNotFoundError(path)
        raise FileNotFoundError(path)

    with patch("builtins.open", side_effect=_open_side_effect):
        result = monitor_module.get_network_connections()

    assert result == {"tcp_connections": 0}


def test_get_network_connections_returns_zero_on_snmp_error():
    with patch("builtins.open", side_effect=FileNotFoundError):
        result = monitor_module.get_network_connections()
    assert result == {"tcp_connections": 0}


def test_collect_system_stats_aggregates_all_parts():
    fake_disk = {"total_gb": 10.0, "used_gb": 3.0, "free_gb": 7.0, "usage_percent": 30.0}
    fake_network = {"tcp_connections": 8}

    cpu_mock = MagicMock(return_value=12.34)
    mem_mock = MagicMock(return_value=56.78)
    disk_mock = MagicMock(return_value=fake_disk)
    net_mock = MagicMock(return_value=fake_network)

    with patch.object(monitor_module, "get_cpu_usage", cpu_mock):
        with patch.object(monitor_module, "get_memory_usage", mem_mock):
            with patch.object(monitor_module, "get_disk_usage", disk_mock):
                with patch.object(monitor_module, "get_network_connections", net_mock):
                    result = monitor_module.collect_system_stats()

    assert result == {
        "cpu_load": 12.34,
        "ram_usage": 56.78,
        "disk_usage": fake_disk,
        "network": fake_network,
    }
    cpu_mock.assert_called_once_with()
    mem_mock.assert_called_once_with()
    disk_mock.assert_called_once_with("/")
    net_mock.assert_called_once_with()
