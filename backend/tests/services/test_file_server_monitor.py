from datetime import datetime, timezone
import socket
from types import SimpleNamespace

import pytest

from backend.services import file_server_monitor as monitor


class _FakePrometheus:
    def close(self) -> None:
        pass

    def scalar(self, query: str) -> float:
        if query.startswith("up{"):
            return 1.0
        if "server_threads" in query:
            return 16.0
        if "connections_total" in query:
            return 3.0
        if "stale_file_handles" in query or "rpc_errors" in query:
            return 0.0
        return 1.5

    def range(self, query: str, *, start: float, end: float, step: int):
        return [{"timestamp": start, "value": 1.0}, {"timestamp": end, "value": 2.0}]


def _host(host_id: str, mount_entries: dict | None):
    """mount_entries: 其中 key 是该 Agent 自己的 MOUNT_POINTS 路径字符串
    （与控制面 STP_AEE_NFS_ROOT 不保证相同）。None 表示 host 上报空 mount_status。
    """
    return SimpleNamespace(
        id=host_id,
        ip=host_id,
        ip_address=None,
        status="ONLINE",
        mount_status=mount_entries if mount_entries is not None else {},
        last_heartbeat=datetime.now(timezone.utc),
    )


def _patch_file_server_deps(monkeypatch, tmp_path):
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    monkeypatch.setenv("STP_FILE_SERVER_ADDRESS", "172.21.15.253")
    monkeypatch.setattr(
        monitor,
        "_mount_details",
        lambda _path: {"mounted": True, "source": "/dev/sda1", "filesystem": "ext4"},
    )
    monkeypatch.setattr(monitor, "_export_targets", lambda _path: ["172.21.8.0/23"])
    monkeypatch.setattr(monitor, "_PrometheusClient", _FakePrometheus)


def test_file_server_overview_reports_capacity_nfs_and_agent_mounts(tmp_path, monkeypatch):
    _patch_file_server_deps(monkeypatch, tmp_path)

    result = monitor.collect_file_server_overview(
        [
            _host("172.21.9.124", {"/home/android/aee-nfs": {"ok": True}}),
            _host("172.21.9.128", None),
        ],
        hours=1,
    )

    assert result["storage"]["mounted"] is True
    assert result["storage"]["source"] == "/dev/sda1"
    assert result["storage"]["total_bytes"] > 0
    assert result["nfs"]["service_ready"] is True
    assert result["nfs"]["server_threads"] == 16
    # Agent mount key 与控制面 str(root) 字串不同，但只要任一 ok=True 即视为已挂
    assert result["agents"] == {
        "total": 2,
        "mounted": 1,
        "failed": 0,
        "unreported": 1,
        "items": result["agents"]["items"],
    }
    assert result["status"] == "warning"
    assert {alert["code"] for alert in result["alerts"]} == {"AGENT_MOUNT_INCOMPLETE"}
    assert len(result["history"]["capacity_usage_pct"]) == 2


def test_host_mount_summary_counts_any_ok_flag_as_mounted():
    """Agent MOUNT_POINTS 与控制面 root 字串不同时，按"任一 ok=True"统计。"""
    summary = monitor._host_mount_summary(
        [
            _host("h-ok-diff-key", {"/home/android/aee-nfs": {"ok": True}}),
            _host("h-failed", {"/mnt/storage/test-platform": {"ok": False}}),
            _host("h-mixed", {"/a": {"ok": False}, "/b": {"ok": True}}),
            _host("h-unreported", None),
        ],
    )
    assert summary["total"] == 4
    assert summary["mounted"] == 2
    assert summary["failed"] == 1
    assert summary["unreported"] == 1
    items_by_ip = {item["ip"]: item for item in summary["items"]}
    assert items_by_ip["h-ok-diff-key"]["mounted"] is True
    assert items_by_ip["h-mixed"]["mounted"] is True
    assert items_by_ip["h-failed"]["mounted"] is False
    assert items_by_ip["h-unreported"]["mounted"] is None


def test_collect_file_server_overview_requires_shared_root(monkeypatch, tmp_path):
    """未设 STP_AEE_NFS_ROOT 时拒绝构造假数据，直接抛 RuntimeError。

    防止误对错路径永远生成 STORAGE_NOT_MOUNTED（见 #4 合入评审）。
    """
    monkeypatch.delenv("STP_AEE_NFS_ROOT", raising=False)
    monkeypatch.delenv("STP_WATCHER_NFS_BASE_DIR", raising=False)
    monkeypatch.delenv("STP_AEE_CIFS_ROOT", raising=False)
    with pytest.raises(RuntimeError, match="STP_AEE_NFS_ROOT is not set"):
        monitor.collect_file_server_overview([], hours=1)


def test_block_device_normalizes_partition_names():
    assert monitor._block_device("/dev/sda1") == "sda"
    assert monitor._block_device("/dev/nvme0n1p2") == "nvme0n1"
    assert monitor._block_device("server:/share") is None


def test_server_address_prefers_env_without_touching_dns(monkeypatch):
    """env 已设时**完全不碰** DNS。

    回归：原实现写作 ``os.getenv(name, socket.gethostbyname(...))``，Python 默认
    参数立即求值，env 设了也照样解析一次 —— 每请求一次多余的阻塞查询。这里让
    gethostbyname 直接爆炸，能返回说明它没被调用。
    """
    monkeypatch.setenv("STP_FILE_SERVER_ADDRESS", "172.21.15.253")

    def _explode(_host):
        raise AssertionError("env 已配置时不应触发 DNS 解析")

    monkeypatch.setattr(monitor.socket, "gethostbyname", _explode)
    assert monitor._server_address() == "172.21.15.253"


def test_server_address_survives_unresolvable_hostname(monkeypatch):
    """主机名无法解析时退回主机名本身，不抛异常。

    回归：原实现会让 socket.gaierror 冒到 endpoint 变成 500 —— 一个「监控别人
    是否健康」的页面因为自己的主机名没进 /etc/hosts 而整体不可用。
    """
    monkeypatch.delenv("STP_FILE_SERVER_ADDRESS", raising=False)
    monkeypatch.setattr(monitor.socket, "gethostname", lambda: "no-such-host")
    monkeypatch.setattr(
        monitor.socket,
        "gethostbyname",
        lambda _host: (_ for _ in ()).throw(socket.gaierror("Name or service not known")),
    )
    assert monitor._server_address() == "no-such-host"


def test_server_address_falls_back_to_resolved_ip(monkeypatch):
    """env 未设且能解析时，用解析出的 IP。"""
    monkeypatch.delenv("STP_FILE_SERVER_ADDRESS", raising=False)
    monkeypatch.setattr(monitor.socket, "gethostname", lambda: "debian13")
    monkeypatch.setattr(monitor.socket, "gethostbyname", lambda _host: "127.0.1.1")
    assert monitor._server_address() == "127.0.1.1"
