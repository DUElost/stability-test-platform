from datetime import datetime, timezone
import socket
from types import SimpleNamespace

import pytest

from backend.services import file_server_monitor as monitor


class _FakePrometheus:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.range_queries: list[str] = []

    def close(self) -> None:
        pass

    def scalar(self, query: str) -> float:
        self.queries.append(query)
        if query.startswith("up{"):
            return 1.0
        if "node_nfsd_server_threads" in query:
            return 16.0
        if "node_nfsd_connections_total" in query:
            return 3.0
        if "stale_file_handles" in query or "node_nfsd_rpc_errors" in query:
            return 0.0
        if "node_boot_time_seconds" in query:
            return 1700000000.0
        if "count(node_cpu_seconds_total" in query or "node_memory_MemTotal_bytes" in query:
            return 8.0
        return 1.5

    def range(self, query: str, *, start: float, end: float, step: int):
        self.range_queries.append(query)
        return [{"timestamp": start, "value": 1.0}, {"timestamp": end, "value": 2.0}]

    def label(self, query: str, label: str) -> str | None:
        return "storage-host" if label == "nodename" else None

    def vector(self, query: str):
        self.queries.append(query)
        if "stp_hostproc_anon_bytes" in query:
            # 故意乱序返回，验证服务端按 anon_bytes 降序排序
            return [
                {"metric": {"comm": "uvicorn", "unit": "stability-backend.service"}, "value": 240000000.0},
                {"metric": {"comm": "node", "unit": "app-ghostty-surface-transient-6337.scope"}, "value": 642000000.0},
            ]
        return []


def _host(host_id: str, mount_entries: dict | None, extra: dict | None = None):
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
        extra=extra if extra is not None else {},
    )


def _patch_file_server_deps(monkeypatch, tmp_path) -> _FakePrometheus:
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    monkeypatch.setenv("STP_FILE_SERVER_ADDRESS", "192.0.2.202")
    monkeypatch.delenv("STP_AEE_SHARE_ADDRESS", raising=False)
    monkeypatch.delenv("STP_STORAGE_NODE_JOB", raising=False)
    monkeypatch.delenv("STP_CONTROL_PLANE_NODE_JOB", raising=False)
    monkeypatch.delenv("STP_CONTROL_PLANE_STORAGE_ROOT", raising=False)
    monkeypatch.delenv("STP_SHARED_LOG_CAPACITY_WARNING_AVAIL_GIB", raising=False)
    monkeypatch.delenv("STP_SHARED_LOG_CAPACITY_CRITICAL_AVAIL_GIB", raising=False)
    monkeypatch.setattr(
        monitor,
        "_mount_details",
        lambda _path: {"mounted": True, "source": "/dev/sda1", "filesystem": "ext4"},
    )
    monkeypatch.setattr(monitor, "_export_targets", lambda _path: ["192.0.2.0/24", "198.51.100.0/24"])
    fake = _FakePrometheus()
    monkeypatch.setattr(monitor, "_PrometheusClient", lambda: fake)
    return fake


def test_file_server_overview_reports_capacity_nfs_and_agent_mounts(tmp_path, monkeypatch):
    _patch_file_server_deps(monkeypatch, tmp_path)

    result = monitor.collect_file_server_overview(
        [
            _host("198.51.100.124", {"/home/android/aee-nfs": {"ok": True}}),
            _host("198.51.100.128", None),
        ],
        hours=1,
    )

    assert result["storage_server"]["same_source"] is True
    assert result["control_plane"]["client_mount"]["mounted"] is True
    assert result["control_plane"]["client_mount"]["source"] == "/dev/sda1"
    assert result["storage_server"]["disk"]["mounted"] is True
    assert result["storage_server"]["disk"]["total_bytes"] > 0
    assert result["storage_server"]["nfs"]["service_ready"] is True
    assert result["storage_server"]["nfs"]["server_threads"] == 16
    assert result["control_plane"]["system"]["cpu_usage_pct"] == 1.5
    assert result["storage_server"]["system"]["cpu_usage_pct"] == 1.5
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


def test_split_panels_when_share_address_and_storage_job_configured(tmp_path, monkeypatch):
    fake = _patch_file_server_deps(monkeypatch, tmp_path)
    monkeypatch.setenv("STP_AEE_SHARE_ADDRESS", "192.0.2.204")
    monkeypatch.setenv("STP_STORAGE_NODE_JOB", "storage-server")

    result = monitor.collect_file_server_overview([], hours=1)

    assert result["storage_server"]["same_source"] is False
    assert result["storage_server"]["node"]["address"] == "192.0.2.204"
    assert result["storage_server"]["node"]["hostname"] == "storage-host"
    assert result["storage_server"]["monitoring"]["prometheus_available"] is True
    assert any('job="storage-server"' in q for q in fake.queries)
    assert any('job="file-server"' in q for q in fake.queries)
    # 趋势图数据源跟随 storage 面板：CPU/内存/NFS 历史刮存储机，容量仍取
    # 控制面客户端挂载（NFS 客户端看到的 avail/size 即服务端磁盘本身）。
    assert any('job="storage-server"' in q for q in fake.range_queries)
    assert any('node_nfsd_requests_total{job="storage-server"}' in q for q in fake.range_queries)
    assert any('node_filesystem_avail_bytes{job="file-server"' in q for q in fake.range_queries)
    assert len(result["history"]["cpu_usage_pct"]) == 2


def test_control_plane_process_memory_top_list(tmp_path, monkeypatch):
    fake = _patch_file_server_deps(monkeypatch, tmp_path)

    result = monitor.collect_file_server_overview([], hours=1)

    processes = result["control_plane"]["processes"]
    assert processes["available"] is True
    assert processes["error"] is None
    # 服务端按 anon_bytes 降序排序（fake 故意乱序返回）
    assert [item["comm"] for item in processes["items"]] == ["node", "uvicorn"]
    assert processes["items"][0]["anon_bytes"] == 642000000
    assert processes["items"][0]["unit"] == "app-ghostty-surface-transient-6337.scope"
    assert any("topk(10, stp_hostproc_anon_bytes" in q for q in fake.queries)
    # 合计趋势固定取控制面 job，与是否分源无关
    assert any("stp_hostproc_anon_total_bytes" in q for q in fake.range_queries)
    assert len(result["history"]["hostproc_total_anon_bytes"]) == 2


def test_control_plane_process_memory_unavailable_without_sampler(tmp_path, monkeypatch):
    fake = _patch_file_server_deps(monkeypatch, tmp_path)
    monkeypatch.setattr(fake, "vector", lambda _query: [])

    result = monitor.collect_file_server_overview([], hours=1)

    processes = result["control_plane"]["processes"]
    assert processes["available"] is False
    # 采集器未部署是「无数据」而不是「查询失败」
    assert processes["error"] is None
    assert processes["items"] == []


def test_control_plane_process_memory_query_failure_is_reported(tmp_path, monkeypatch):
    _patch_file_server_deps(monkeypatch, tmp_path)
    monkeypatch.setattr(
        monitor,
        "_top_process_items",
        lambda _prom, _job: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = monitor.collect_file_server_overview([], hours=1)

    processes = result["control_plane"]["processes"]
    assert processes["available"] is False
    assert processes["error"] == "RuntimeError"


def test_share_address_equal_to_control_plane_stays_co_located(tmp_path, monkeypatch):
    fake = _patch_file_server_deps(monkeypatch, tmp_path)
    monkeypatch.setenv("STP_AEE_SHARE_ADDRESS", "192.0.2.202")

    result = monitor.collect_file_server_overview([], hours=1)

    assert result["storage_server"]["same_source"] is True
    assert result["storage_server"]["monitoring"]["prometheus_available"] is True
    assert "STORAGE_METRICS_UNAVAILABLE" not in {a["code"] for a in result["alerts"]}
    assert any('job="file-server"' in q for q in fake.queries)


def test_share_address_hostname_resolving_to_control_ip_stays_co_located(tmp_path, monkeypatch):
    """STP_AEE_SHARE_ADDRESS 写主机名、STP_FILE_SERVER_ADDRESS 写 IP 时仍判同机。

    回归：字面比较会误判为分源 → 右栏永远 STORAGE_METRICS_UNAVAILABLE（#205）。
    """
    _patch_file_server_deps(monkeypatch, tmp_path)
    monkeypatch.setenv("STP_AEE_SHARE_ADDRESS", "storage.internal")
    monkeypatch.setattr(
        monitor.socket,
        "gethostbyname",
        lambda host: {"storage.internal": "192.0.2.202"}.get(host, host),
    )

    result = monitor.collect_file_server_overview([], hours=1)

    assert result["storage_server"]["same_source"] is True
    assert "STORAGE_METRICS_UNAVAILABLE" not in {a["code"] for a in result["alerts"]}


def test_share_address_unresolvable_hostname_does_not_crash(tmp_path, monkeypatch):
    """share 地址解析失败时回落原串比较，不抛异常（同机判定为假→外部未监控降级）。"""
    _patch_file_server_deps(monkeypatch, tmp_path)
    monkeypatch.setenv("STP_AEE_SHARE_ADDRESS", "no-such-host")
    monkeypatch.setattr(
        monitor.socket,
        "gethostbyname",
        lambda _host: (_ for _ in ()).throw(socket.gaierror("Name or service not known")),
    )

    result = monitor.collect_file_server_overview([], hours=1)

    assert result["storage_server"]["same_source"] is False
    assert result["storage_server"]["monitoring"]["unmonitored_reason"] == (
        "external_share_no_collector"
    )
    assert "STORAGE_METRICS_UNAVAILABLE" not in {a["code"] for a in result["alerts"]}


def test_split_without_storage_job_is_external_unmonitored(tmp_path, monkeypatch):
    """分源未配 STP_STORAGE_NODE_JOB：外部 NAS 预期降级，不发 STORAGE_METRICS_UNAVAILABLE。"""
    _patch_file_server_deps(monkeypatch, tmp_path)
    monkeypatch.setenv("STP_AEE_SHARE_ADDRESS", "192.0.2.204")

    result = monitor.collect_file_server_overview([], hours=1)

    assert result["storage_server"]["same_source"] is False
    assert result["storage_server"]["node"]["address"] == "192.0.2.204"
    assert result["storage_server"]["monitoring"]["prometheus_available"] is False
    assert result["storage_server"]["monitoring"]["unmonitored_reason"] == (
        "external_share_no_collector"
    )
    assert result["storage_server"]["system"]["cpu_usage_pct"] is None
    assert "STORAGE_METRICS_UNAVAILABLE" not in {a["code"] for a in result["alerts"]}
    # 主机/nfsd 历史仍 fail-closed；容量可从控制面客户端挂载点取。
    assert result["history"]["cpu_usage_pct"] == []
    assert result["history"]["memory_usage_pct"] == []
    assert result["history"]["nfs_requests_per_second"] == []
    assert len(result["history"]["capacity_usage_pct"]) == 2


def test_cifs_remote_log_root_checks_control_plane_export(tmp_path, monkeypatch):
    """CIFS 日志根不得拿去查本机 etab；NFS 导出对准控制面根（#205 / G0-b）。"""
    log_root = tmp_path / "stp-log"
    cp_root = tmp_path / "stp-aee"
    log_root.mkdir()
    cp_root.mkdir()
    fake = _patch_file_server_deps(monkeypatch, log_root)
    monkeypatch.setenv("STP_CONTROL_PLANE_STORAGE_ROOT", str(cp_root))
    monkeypatch.setenv("STP_AEE_SHARE_ADDRESS", "192.0.2.154")
    export_calls: list[str] = []

    def _fake_exports(path):
        export_calls.append(str(path))
        if str(path) == str(cp_root):
            return ["192.0.2.0/24"]
        return []

    monkeypatch.setattr(
        monitor,
        "_mount_details",
        lambda _path: {
            "mounted": True,
            "source": "//192.0.2.154/jxtinno/sonic_tinno/stp",
            "filesystem": "cifs",
        },
    )
    monkeypatch.setattr(monitor, "_export_targets", _fake_exports)

    result = monitor.collect_file_server_overview([], hours=1)

    assert export_calls == [str(cp_root)]
    assert result["storage_server"]["same_source"] is False
    assert result["storage_server"]["disk"]["filesystem"] == "cifs"
    assert result["storage_server"]["disk"]["mounted"] is True
    assert result["storage_server"]["nfs"]["scope"] == "control_plane_root"
    assert result["storage_server"]["nfs"]["export_path"] == str(cp_root)
    assert result["storage_server"]["nfs"]["exported"] is True
    assert result["storage_server"]["nfs"]["service_ready"] is True
    assert result["control_plane"]["nfs"]["scope"] == "control_plane_root"
    assert result["control_plane"]["nfs"]["export_path"] == str(cp_root)
    assert result["storage_server"]["monitoring"]["unmonitored_reason"] == (
        "external_share_no_collector"
    )
    assert "NFS_EXPORT_MISSING" not in {a["code"] for a in result["alerts"]}
    assert "STORAGE_METRICS_UNAVAILABLE" not in {a["code"] for a in result["alerts"]}
    # 远程日志根时向控制面 job 多查 nfs_threads
    assert any("node_nfsd_server_threads" in q and 'job="file-server"' in q for q in fake.queries)


def test_cifs_log_root_does_not_false_export_missing_when_only_cp_exported(
    tmp_path, monkeypatch,
):
    """回归：日志根 /mnt/stp-log 不在 etab，但控制面根已导出 → 不得 NFS_EXPORT_MISSING。"""
    log_root = tmp_path / "stp-log"
    cp_root = tmp_path / "stp-aee"
    log_root.mkdir()
    cp_root.mkdir()
    _patch_file_server_deps(monkeypatch, log_root)
    monkeypatch.setenv("STP_CONTROL_PLANE_STORAGE_ROOT", str(cp_root))
    monkeypatch.setenv("STP_AEE_SHARE_ADDRESS", "192.0.2.154")
    monkeypatch.setattr(
        monitor,
        "_mount_details",
        lambda _path: {
            "mounted": True,
            "source": "//192.0.2.154/share",
            "filesystem": "cifs",
        },
    )
    monkeypatch.setattr(
        monitor,
        "_export_targets",
        lambda path: ["192.0.2.0/24"] if str(path) == str(cp_root) else [],
    )

    result = monitor.collect_file_server_overview([], hours=1)
    assert "NFS_EXPORT_MISSING" not in {a["code"] for a in result["alerts"]}
    assert result["status"] in {"healthy", "warning"}


def test_capacity_absolute_thresholds_override_percentage(tmp_path, monkeypatch):
    """显式绝对可用 GiB 阈值优先于 used_pct（对齐 G0-c）。"""
    _patch_file_server_deps(monkeypatch, tmp_path)
    monkeypatch.setenv("STP_SHARED_LOG_CAPACITY_WARNING_AVAIL_GIB", "800")
    monkeypatch.setenv("STP_SHARED_LOG_CAPACITY_CRITICAL_AVAIL_GIB", "300")
    # 盘很小但 used_pct 会很高；绝对阈值按 available 判，不应因百分比误报 critical。
    total = 100 * monitor._GIB
    used = 95 * monitor._GIB
    available = 5 * monitor._GIB

    class _Usage:
        def __init__(self) -> None:
            self.total = total
            self.used = used
            self.free = available

    monkeypatch.setattr(monitor.shutil, "disk_usage", lambda _path: _Usage())
    monkeypatch.setattr(
        monitor.os,
        "statvfs",
        lambda _path: type("S", (), {"f_files": 1000, "f_favail": 900})(),
    )

    result = monitor.collect_file_server_overview([], hours=1)
    codes = {a["code"] for a in result["alerts"]}
    assert "CAPACITY_CRITICAL" in codes
    assert result["storage_server"]["disk"]["used_pct"] >= 90


def test_capacity_percentage_fallback_on_small_disk(tmp_path, monkeypatch):
    """小盘且未配绝对阈值：仍走 used_pct 80/90。"""
    _patch_file_server_deps(monkeypatch, tmp_path)
    monkeypatch.delenv("STP_SHARED_LOG_CAPACITY_WARNING_AVAIL_GIB", raising=False)
    monkeypatch.delenv("STP_SHARED_LOG_CAPACITY_CRITICAL_AVAIL_GIB", raising=False)
    total = 100 * monitor._GIB
    used = 85 * monitor._GIB
    available = 15 * monitor._GIB

    class _Usage:
        def __init__(self) -> None:
            self.total = total
            self.used = used
            self.free = available

    monkeypatch.setattr(monitor.shutil, "disk_usage", lambda _path: _Usage())
    monkeypatch.setattr(
        monitor.os,
        "statvfs",
        lambda _path: type("S", (), {"f_files": 1000, "f_favail": 900})(),
    )

    result = monitor.collect_file_server_overview([], hours=1)
    codes = {a["code"]: a for a in result["alerts"]}
    assert "CAPACITY_WARNING" in codes
    assert "CAPACITY_CRITICAL" not in codes
    assert "%" in codes["CAPACITY_WARNING"]["message"]


def test_capacity_large_disk_auto_uses_g0c_absolute_thresholds(tmp_path, monkeypatch):
    """≥2 TiB 盘自动启用 800/300 GiB：剩余 >800 GiB 时即使 used_pct≥90 也不 critical。"""
    _patch_file_server_deps(monkeypatch, tmp_path)
    monkeypatch.delenv("STP_SHARED_LOG_CAPACITY_WARNING_AVAIL_GIB", raising=False)
    monkeypatch.delenv("STP_SHARED_LOG_CAPACITY_CRITICAL_AVAIL_GIB", raising=False)
    total = 14 * monitor._TIB
    available = 1300 * monitor._GIB
    used = total - available

    class _Usage:
        def __init__(self) -> None:
            self.total = total
            self.used = used
            self.free = available

    monkeypatch.setattr(monitor.shutil, "disk_usage", lambda _path: _Usage())
    monkeypatch.setattr(
        monitor.os,
        "statvfs",
        lambda _path: type("S", (), {"f_files": 1000, "f_favail": 900})(),
    )

    result = monitor.collect_file_server_overview([], hours=1)
    used_pct = result["storage_server"]["disk"]["used_pct"]
    assert used_pct >= 90
    assert "CAPACITY_CRITICAL" not in {a["code"] for a in result["alerts"]}
    assert "CAPACITY_WARNING" not in {a["code"] for a in result["alerts"]}


def test_device_log_disk_summary_and_threshold_alerts(tmp_path, monkeypatch):
    _patch_file_server_deps(monkeypatch, tmp_path)
    hosts = [
        _host("h1", None, extra={"disk_usage_aee": {
            "path": "/mnt/hdd/aee_events", "total_gb": 900.0, "used_gb": 90.0,
            "free_gb": 810.0, "usage_percent": 10.0,
        }}),
        _host("h2", None, extra={"disk_usage_aee": {
            "path": "/data/hdd/aee_events", "total_gb": 900.0, "used_gb": 860.4,
            "free_gb": 39.6, "usage_percent": 95.6,
        }}),
        _host("h-nan", None, extra={"disk_usage_aee": {
            "path": "/data/hdd/aee_events", "total_gb": "Inf", "used_gb": 0.0,
            "free_gb": 0.0, "usage_percent": "NaN",
        }}),
        _host("h-neg", None, extra={"disk_usage_aee": {
            "path": "/data/hdd/aee_events", "total_gb": 100.0, "used_gb": 1.0,
            "free_gb": 99.0, "usage_percent": -1.0,
        }}),
        _host("h-over", None, extra={"disk_usage_aee": {
            "path": "/data/hdd/aee_events", "total_gb": 100.0, "used_gb": 1.0,
            "free_gb": 99.0, "usage_percent": 101.0,
        }}),
        _host("h3", None),  # 老 Agent：未上报该字段
    ]

    result = monitor.collect_file_server_overview(hosts, hours=1)
    summary = result["device_log_disks"]

    assert summary["total"] == 6
    assert summary["reported"] == 2
    assert summary["warning"] == 0
    assert summary["critical"] == 1
    assert [item["host_id"] for item in summary["items"]] == ["h1", "h2"]
    assert summary["items"][1]["usage_percent"] == 95.6
    assert summary["items"][1]["total_bytes"] == int(round(900.0 * 1024 ** 3))
    assert result["status"] == "critical"
    assert "DEVICE_LOG_DISK_CRITICAL" in {a["code"] for a in result["alerts"]}


def test_invalid_storage_job_name_is_rejected_not_fallen_back(tmp_path, monkeypatch):
    _patch_file_server_deps(monkeypatch, tmp_path)
    monkeypatch.setenv("STP_AEE_SHARE_ADDRESS", "192.0.2.204")
    monkeypatch.setenv("STP_STORAGE_NODE_JOB", "bad job name")

    result = monitor.collect_file_server_overview([], hours=1)

    assert result["storage_server"]["same_source"] is False
    assert result["storage_server"]["monitoring"]["prometheus_available"] is False
    assert result["storage_server"]["system"]["cpu_usage_pct"] is None
    assert {"STORAGE_METRICS_UNAVAILABLE"} <= {a["code"] for a in result["alerts"]}


def test_finite_float_rejects_nan_and_inf():
    assert monitor._finite_float("NaN") is None
    assert monitor._finite_float("+Inf") is None
    assert monitor._finite_float("-Inf") is None
    assert monitor._finite_float("1.5") == 1.5
    assert monitor._finite_float("not-a-number") is None


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


def test_host_mount_summary_prefers_authoritative_shared_root_over_stale_entry():
    """#4077: stale MOUNT_POINTS (/mnt/stp-aee ok=True) must not mask unmounted/invalid STP_AEE_NFS_ROOT."""
    summary = monitor._host_mount_summary(
        [
            _host("h-stale-ok", {"/mnt/stp-aee": {"ok": True}, "/mnt/stp-log": {"ok": False}}),
            _host("h-both-ok", {"/mnt/stp-aee": {"ok": True}, "/mnt/stp-log/": {"ok": True}}),
            _host("h-invalid-bool", {"/mnt/stp-aee": {"ok": True}, "/mnt/stp-log": {"ok": "false"}}),
            _host("h-legacy-only", {"/mnt/stp-aee": {"ok": True}}),
        ],
        shared_root="/mnt/stp-log",
    )
    assert summary["mounted"] == 2
    assert summary["failed"] == 1
    assert summary["unreported"] == 1
    items_by_ip = {item["ip"]: item for item in summary["items"]}
    assert items_by_ip["h-stale-ok"]["mounted"] is False
    assert items_by_ip["h-both-ok"]["mounted"] is True
    assert items_by_ip["h-invalid-bool"]["mounted"] is None
    assert items_by_ip["h-legacy-only"]["mounted"] is True


def test_collect_file_server_overview_passes_shared_root(tmp_path, monkeypatch):
    """#4077 入口接线：collect_file_server_overview 传入权威 shared_root。"""
    _patch_file_server_deps(monkeypatch, tmp_path)
    result = monitor.collect_file_server_overview(
        [
            _host("198.51.100.201", {"/mnt/stp-aee": {"ok": True}, str(tmp_path): {"ok": False}}),
            _host("198.51.100.202", {"/mnt/stp-aee": {"ok": True}, str(tmp_path): {"ok": True}}),
        ],
        hours=1,
    )
    assert result["agents"]["mounted"] == 1
    assert result["agents"]["failed"] == 1
    by_ip = {item["ip"]: item for item in result["agents"]["items"]}
    assert by_ip["198.51.100.201"]["mounted"] is False
    assert by_ip["198.51.100.202"]["mounted"] is True


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
    monkeypatch.setenv("STP_FILE_SERVER_ADDRESS", "192.0.2.202")

    def _explode(_host):
        raise AssertionError("env 已配置时不应触发 DNS 解析")

    monkeypatch.setattr(monitor.socket, "gethostbyname", _explode)
    assert monitor._server_address() == "192.0.2.202"


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


def test_split_cifs_with_storage_job_preserves_control_plane_nfs_and_monitoring(
    tmp_path, monkeypatch,
):
    """分源 CIFS + STP_STORAGE_NODE_JOB + storage_threads=0：不误报控制面 NFS 异常，且保留控制面存储/趋势（#4093）。"""
    log_root = tmp_path / "stp-log"
    cp_root = tmp_path / "stp-aee"
    log_root.mkdir()
    cp_root.mkdir()
    fake = _patch_file_server_deps(monkeypatch, log_root)
    monkeypatch.setenv("STP_CONTROL_PLANE_STORAGE_ROOT", str(cp_root))
    monkeypatch.setenv("STP_AEE_SHARE_ADDRESS", "192.0.2.154")
    monkeypatch.setenv("STP_STORAGE_NODE_JOB", "storage-server")

    def _mount(path):
        if path == log_root:
            return {"mounted": True, "source": "//192.0.2.154/share", "filesystem": "cifs"}
        if path == cp_root:
            return {"mounted": True, "source": "/dev/sdb1", "filesystem": "ext4"}
        return {"mounted": False, "source": None, "filesystem": None}

    monkeypatch.setattr(monitor, "_mount_details", _mount)
    monkeypatch.setattr(
        monitor,
        "_export_targets",
        lambda path: ["192.0.2.0/24"] if str(path) == str(cp_root) else [],
    )
    orig_scalar = fake.scalar

    def _scalar(query: str) -> float:
        if "node_nfsd_server_threads" in query and 'job="storage-server"' in query:
            fake.queries.append(query)
            return 0.0
        return orig_scalar(query)

    monkeypatch.setattr(fake, "scalar", _scalar)

    result = monitor.collect_file_server_overview([], hours=1)

    # #4093：控制面根 NFS 就绪态与线程数只取控制面 job，不被 storage_threads=0 覆盖
    cp_nfs = result["control_plane"]["nfs"]
    assert cp_nfs["service_ready"] is True
    assert cp_nfs["server_threads"] == 16
    assert cp_nfs["scope"] == "control_plane_root"
    assert cp_nfs["export_path"] == str(cp_root)

    # 控制面本地磁盘容量与控制面独立历史趋势同步保留
    cp_disk = result["control_plane"]["disk"]
    assert cp_disk["path"] == str(cp_root)
    assert cp_disk["source"] == "/dev/sdb1"
    assert cp_disk["filesystem"] == "ext4"
    assert cp_disk["mounted"] is True
    assert cp_disk["total_bytes"] > 0

    cp_history = result["history"]["control_plane"]
    assert len(cp_history["cpu_usage_pct"]) == 2
    assert len(cp_history["memory_usage_pct"]) == 2
    assert len(cp_history["capacity_usage_pct"]) == 2
    assert len(cp_history["nfs_requests_per_second"]) == 2
    assert any(
        'job="file-server"' in q and f'mountpoint="{cp_root}"' in q
        for q in fake.range_queries
    )
    # 中心存储机趋势仍按 storage-server + 共享日志根查询
    assert len(result["history"]["cpu_usage_pct"]) == 2
    assert any('job="storage-server"' in q for q in fake.range_queries)
    assert any(
        'job="file-server"' in q and f'mountpoint="{log_root}"' in q
        for q in fake.range_queries
    )


def test_local_mount_info_falls_back_to_enclosing_mountpoint(tmp_path, monkeypatch):
    """控制面根若只是某个父挂载点下的子目录，_local_mount_info 回溯匹配父挂载点。"""
    cp_dir = tmp_path / "var" / "stp-aee"
    cp_dir.mkdir(parents=True)

    def _mount(path):
        if path == tmp_path:
            return {"mounted": True, "source": "/dev/nvme0n1p1", "filesystem": "ext4"}
        return {"mounted": False, "source": None, "filesystem": None}

    monkeypatch.setattr(monitor, "_mount_details", _mount)
    mount_path, details = monitor._local_mount_info(cp_dir)
    assert mount_path == tmp_path
    assert details["mounted"] is True
    assert details["source"] == "/dev/nvme0n1p1"
