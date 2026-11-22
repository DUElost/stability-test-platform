"""File-server health aggregation for the admin storage dashboard."""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx
import psutil

logger = logging.getLogger(__name__)

_PROMETHEUS_URL_DEFAULT = "http://127.0.0.1:9091"
_NODE_JOB_DEFAULT = "file-server"
_MOUNTINFO_ESCAPES = {
    r"\040": " ",
    r"\011": "\t",
    r"\012": "\n",
    r"\134": "\\",
}

_CONTROL_PANEL_KEYS = frozenset({
    "up", "cpu", "memory", "load1", "disk_read", "disk_write",
    "network_receive", "network_transmit",
})
_STORAGE_PANEL_KEYS = frozenset({
    "up", "cpu", "memory", "mem_total", "load1", "disk_read", "disk_write",
    "network_receive", "network_transmit", "cpu_count", "boot_time",
    "nfs_requests", "nfs_errors", "nfs_stale", "nfs_threads", "nfs_connections",
})
_GIB = 1024 ** 3
_TIB = 1024 ** 4
_DEVICE_LOG_DISK_WARNING_PCT = 90.0
_DEVICE_LOG_DISK_CRITICAL_PCT = 95.0
# 共享日志根容量：小盘回退百分比；大盘（≥2 TiB）或显式 env 对齐 G0-c（#3860）。
_CAPACITY_WARNING_PCT = 80.0
_CAPACITY_CRITICAL_PCT = 90.0
_CAPACITY_ABS_WARNING_AVAIL_GIB_DEFAULT = 800.0
_CAPACITY_ABS_CRITICAL_AVAIL_GIB_DEFAULT = 300.0
_CAPACITY_ABS_AUTO_MIN_TOTAL_BYTES = 2 * _TIB
_REMOTE_CLIENT_FILESYSTEMS = frozenset({"cifs", "smb", "smb3"})
# 宿主机进程内存采样器（deploy/control-plane/node-exporter/stp-mem-top.sh）指标。
_TOP_PROCESS_LIMIT = 10
_HOSTPROC_ANON_METRIC = "stp_hostproc_anon_bytes"
_HOSTPROC_TOTAL_METRIC = "stp_hostproc_anon_total_bytes"


def _unescape_mountinfo(value: str) -> str:
    for encoded, decoded in _MOUNTINFO_ESCAPES.items():
        value = value.replace(encoded, decoded)
    return value


def _mount_details(path: Path) -> dict[str, Any]:
    target = str(path.resolve(strict=False))
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"mounted": False, "source": None, "filesystem": None}

    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if len(fields) <= separator + 2 or _unescape_mountinfo(fields[4]) != target:
            continue
        return {
            "mounted": True,
            "source": _unescape_mountinfo(fields[separator + 2]),
            "filesystem": fields[separator + 1],
        }
    return {"mounted": False, "source": None, "filesystem": None}


def _local_mount_info(path: Path) -> tuple[Path, dict[str, Any]]:
    """Resolve the enclosing mountpoint and mount details for a local directory.

    Checks ``path`` directly first (preserving ``_mount_details`` monkeypatches
    in tests), then walks ancestors so a directory residing on ``/`` still
    maps to its backing filesystem and Prometheus ``mountpoint`` label.
    """
    direct = _mount_details(path)
    if direct["mounted"]:
        return path, direct
    for parent in path.resolve(strict=False).parents:
        info = _mount_details(parent)
        if info["mounted"]:
            return parent, info
    return path, direct


def _collect_disk_metrics(path: Path, mount: dict[str, Any]) -> dict[str, Any]:
    """Collect capacity, inode, and writeability stats for a storage path."""
    total_bytes = used_bytes = available_bytes = 0
    inode_total = inode_used = inode_available = 0
    if path.exists():
        usage = shutil.disk_usage(path)
        total_bytes = usage.total
        used_bytes = usage.used
        available_bytes = usage.free
        stat = os.statvfs(path)
        inode_total = stat.f_files
        inode_available = stat.f_favail
        inode_used = max(0, inode_total - inode_available)
    used_pct = round(used_bytes / total_bytes * 100, 2) if total_bytes else 0.0
    inode_used_pct = round(inode_used / inode_total * 100, 2) if inode_total else 0.0
    backend_write_access = path.exists() and os.access(path, os.W_OK)
    return {
        "path": str(path),
        "source": mount["source"],
        "filesystem": mount["filesystem"],
        "mounted": mount["mounted"],
        "backend_write_access": backend_write_access,
        "total_bytes": total_bytes,
        "used_bytes": used_bytes,
        "available_bytes": available_bytes,
        "used_pct": used_pct,
        "inode_total": inode_total,
        "inode_used": inode_used,
        "inode_available": inode_available,
        "inode_used_pct": inode_used_pct,
    }


def _export_targets(path: Path) -> list[str]:
    try:
        lines = Path("/var/lib/nfs/etab").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    target = str(path.resolve(strict=False))
    targets: list[str] = []
    for line in lines:
        fields = line.split()
        if len(fields) < 2 or _unescape_mountinfo(fields[0]) != target:
            continue
        targets.append(fields[1].split("(", 1)[0])
    return targets


def _safe_prom_label(value: str, default: str) -> str:
    return value if re.fullmatch(r"[A-Za-z0-9_.:-]+", value) else default


def _prom_string(value: str) -> str:
    return value.replace("\\", r"\\").replace('"', r'\"')


def _round_opt(value: float | None) -> float | None:
    return round(float(value), 2) if value is not None else None


def _finite_float(raw: Any) -> float | None:
    """Parse a Prometheus numeric value, rejecting NaN / ±Inf as None.

    The Prometheus HTTP API encodes NaN / +Inf / -Inf as JSON strings; ``float``
    happily accepts them, but they would poison ``int()`` conversions and the
    response JSON (strict serializers reject non-finite numbers).
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


class _PrometheusClient:
    def __init__(self) -> None:
        self._base_url = os.getenv("STP_PROMETHEUS_URL", _PROMETHEUS_URL_DEFAULT).rstrip("/")
        self._client = httpx.Client(timeout=3.0, trust_env=False)

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self._client.get(f"{self._base_url}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise RuntimeError("Prometheus query was not successful")
        return payload["data"]

    def scalar(self, query: str) -> float | None:
        data = self._get("/api/v1/query", {"query": query})
        result = data.get("result") or []
        if not result:
            return None
        try:
            return _finite_float(result[0]["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def range(self, query: str, *, start: float, end: float, step: int) -> list[dict[str, Any]]:
        data = self._get(
            "/api/v1/query_range",
            {"query": query, "start": start, "end": end, "step": step},
        )
        result = data.get("result") or []
        if not result:
            return []
        points: list[dict[str, Any]] = []
        for raw_ts, raw_value in result[0].get("values") or []:
            try:
                timestamp = _finite_float(raw_ts)
                value = _finite_float(raw_value)
                if timestamp is None or value is None:
                    continue
                points.append({"timestamp": timestamp, "value": round(value, 3)})
            except (TypeError, ValueError):
                continue
        return points

    def label(self, query: str, label: str) -> str | None:
        """Return one label value from the first instant-vector sample, or None."""
        data = self._get("/api/v1/query", {"query": query})
        result = data.get("result") or []
        if not result:
            return None
        value = result[0].get("metric", {}).get(label)
        return value if isinstance(value, str) else None

    def vector(self, query: str) -> list[dict[str, Any]]:
        """Return all instant-vector samples as ``{"metric": ..., "value": ...}``.

        Used for multi-series queries (``topk``) where :meth:`scalar` would
        silently keep only the first sample. Non-finite values are dropped.
        """
        data = self._get("/api/v1/query", {"query": query})
        samples: list[dict[str, Any]] = []
        for sample in data.get("result") or []:
            try:
                value = _finite_float(sample["value"][1])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            if value is None:
                continue
            samples.append({"metric": sample.get("metric") or {}, "value": value})
        return samples


def _block_device(source: str | None) -> str | None:
    if not source or not source.startswith("/dev/"):
        return None
    return re.sub(r"p?\d+$", "", Path(source).name)


def _host_mount_summary(hosts: Iterable[Any]) -> dict[str, Any]:
    """Aggregate per-Agent mount_status上报。Agent 心跳 mount_status 的 key 来自
    各自 MOUNT_POINTS env，与控制面 STP_AEE_NFS_ROOT 路径**不保证同字符串**；
    此处按"任一上报项 ok=True 即视为已挂"统计，避免 key 字串不匹配导致全员 unreported。
    """
    items: list[dict[str, Any]] = []
    mounted = 0
    failed = 0
    unreported = 0
    for host in hosts:
        raw_status = getattr(host, "mount_status", None) or {}
        mounted_entries = (
            entry for entry in raw_status.values() if isinstance(entry, dict)
        )
        ok_flags = [bool(entry.get("ok")) for entry in mounted_entries if isinstance(entry.get("ok"), bool)]
        if ok_flags:
            mount_ok = any(ok_flags)
            if mount_ok:
                mounted += 1
            else:
                failed += 1
        else:
            mount_ok = None
            unreported += 1
        heartbeat = getattr(host, "last_heartbeat", None)
        items.append({
            "host_id": str(getattr(host, "id", "")),
            "ip": getattr(host, "ip", None) or getattr(host, "ip_address", None),
            "status": str(getattr(host, "status", "UNKNOWN")),
            "mounted": mount_ok,
            "last_heartbeat": heartbeat.isoformat() if heartbeat else None,
        })
    items.sort(key=lambda item: item["ip"] or item["host_id"])
    return {
        "total": len(items),
        "mounted": mounted,
        "failed": failed,
        "unreported": unreported,
        "items": items,
    }


def _server_address() -> str:
    """本机对外地址。`STP_FILE_SERVER_ADDRESS` 未设时才回落到 DNS 解析。

    **不能**写成 ``os.getenv(name, socket.gethostbyname(socket.gethostname()))``：
    Python 默认参数是**立即求值**的，env 已设时那次 DNS 查询照样执行 —— 每个请求
    白付一次阻塞解析，且主机名不可解析时抛 ``socket.gaierror`` 直接 500（整个
    overview 就此报废，而它本该是个「监控别人是否健康」的页面）。

    回落值本身也不可靠：Debian 默认 ``/etc/hosts`` 把主机名指向 ``127.0.1.1``，
    页面会显示环回地址而非共享盘的真实地址。所以生产**必须显式配置**
    ``STP_FILE_SERVER_ADDRESS``（见 backend/.env.example），这里的回落只保证
    不炸、不保证有意义。
    """
    configured = os.getenv("STP_FILE_SERVER_ADDRESS", "").strip()
    if configured:
        return configured
    hostname = socket.gethostname()
    try:
        return socket.gethostbyname(hostname)
    except OSError:
        # 主机名不在 /etc/hosts 且无 DNS —— 退回主机名本身，聊胜于 500
        return hostname


def _share_is_co_located(share_addr: str) -> bool:
    """共享盘是否与控制面同机（IP/hostname 归一化后比较）。

    直接字面比较会在「STP_AEE_SHARE_ADDRESS 写主机名、STP_FILE_SERVER_ADDRESS
    写 IP」（或反之）时误判为分源，健康页右栏因此永远不可用（#205）。解析失败
    回落原串比较，保证不炸；未配 share_addr 视为同机过渡期。
    """
    if not share_addr:
        return True
    try:
        share_ip = socket.gethostbyname(share_addr)
    except OSError:
        share_ip = share_addr
    local = _server_address()
    try:
        local_ip = socket.gethostbyname(local)
    except OSError:
        local_ip = local
    return share_ip == local_ip


def _require_storage_roots() -> tuple[Path, Path]:
    """日志根 + 控制面根。日志根未设 → RuntimeError（健康页拒绝盯错路径）。

    与 backend/services/dedup_scan.py 同一主键，但 dedup_scan 软降级，本处强制需要。
    控制面根未设时回落到日志根（B8 G0-a）。路径字符串可与各 Agent 的
    MOUNT_POINTS 不同（mount 统计按"任一 ok=True"识别，见 _host_mount_summary）。
    """
    from backend.core.storage_root import (
        resolve_control_plane_storage_root,
        resolve_shared_storage_root,
    )

    raw = resolve_shared_storage_root()
    if not raw:
        raise RuntimeError(
            "STP_AEE_NFS_ROOT is not set; the file-server dashboard refuses to "
            "report against an unconfigured shared root (would always emit "
            "STORAGE_NOT_MOUNTED). Set it to the actual NFS/CIFS mount on the "
            "control plane (must point to the same share the agents mount, "
            "path string may differ from each agent's MOUNT_POINTS)."
        )
    log_root = Path(raw)
    cp_raw = resolve_control_plane_storage_root()
    control_plane_root = Path(cp_raw) if cp_raw else log_root
    return log_root, control_plane_root


def _is_remote_client_share(
    *,
    filesystem: str | None,
    same_source: bool,
    log_root: Path,
    control_plane_root: Path,
) -> bool:
    """日志根是否为远程客户端挂载（CIFS/SMB 或分源且路径不同于控制面根）。

    此类挂载不是控制面本机 NFS 导出目录，不得拿它去查 ``/var/lib/nfs/etab``。
    """
    fs = (filesystem or "").lower()
    if fs in _REMOTE_CLIENT_FILESYSTEMS:
        return True
    if same_source:
        return False
    return log_root.resolve(strict=False) != control_plane_root.resolve(strict=False)


def _env_positive_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _capacity_avail_thresholds_gib(total_bytes: int) -> tuple[float | None, float | None]:
    """Resolve absolute free-space thresholds (GiB) for the shared log root.

    - Explicit ``STP_SHARED_LOG_CAPACITY_*_AVAIL_GIB`` win when set.
    - Else, disks ``>= 2 TiB`` auto-use G0-c defaults (800 / 300) so a 13 TB
      share with >1 TiB free is not stuck on ``used_pct >= 90%``.
    - Smaller disks keep the legacy percentage path (tests + small volumes).
    """
    warn = _env_positive_float("STP_SHARED_LOG_CAPACITY_WARNING_AVAIL_GIB")
    crit = _env_positive_float("STP_SHARED_LOG_CAPACITY_CRITICAL_AVAIL_GIB")
    if warn is None and crit is None:
        if total_bytes >= _CAPACITY_ABS_AUTO_MIN_TOTAL_BYTES:
            return (
                _CAPACITY_ABS_WARNING_AVAIL_GIB_DEFAULT,
                _CAPACITY_ABS_CRITICAL_AVAIL_GIB_DEFAULT,
            )
        return None, None
    if warn is None:
        warn = _CAPACITY_ABS_WARNING_AVAIL_GIB_DEFAULT
    if crit is None:
        crit = _CAPACITY_ABS_CRITICAL_AVAIL_GIB_DEFAULT
    if crit > warn:
        crit = warn
    return warn, crit


def _capacity_alerts(
    *,
    available_bytes: int,
    total_bytes: int,
    used_pct: float,
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    warn_gib, crit_gib = _capacity_avail_thresholds_gib(total_bytes)
    if warn_gib is not None and crit_gib is not None:
        available_gib = available_bytes / _GIB
        if available_gib < crit_gib:
            alerts.append({
                "severity": "critical",
                "code": "CAPACITY_CRITICAL",
                "message": (
                    f"Shared log root has {available_gib:.1f} GiB free "
                    f"(critical < {crit_gib:g} GiB)"
                ),
            })
        elif available_gib < warn_gib:
            alerts.append({
                "severity": "warning",
                "code": "CAPACITY_WARNING",
                "message": (
                    f"Shared log root has {available_gib:.1f} GiB free "
                    f"(warning < {warn_gib:g} GiB)"
                ),
            })
        return alerts
    if used_pct >= _CAPACITY_CRITICAL_PCT:
        alerts.append({
            "severity": "critical",
            "code": "CAPACITY_CRITICAL",
            "message": f"Storage usage is {used_pct:.1f}%",
        })
    elif used_pct >= _CAPACITY_WARNING_PCT:
        alerts.append({
            "severity": "warning",
            "code": "CAPACITY_WARNING",
            "message": f"Storage usage is {used_pct:.1f}%",
        })
    return alerts


def _panel_jobs() -> tuple[str, str | None, str]:
    """Resolve Prometheus job labels for the two health-page panels.

    - control job: ``STP_CONTROL_PLANE_NODE_JOB`` (#289: the
      ``STP_FILE_SERVER_NODE_JOB`` legacy alias is gone), default
      ``file-server``.
    - storage job: ``STP_STORAGE_NODE_JOB``. When unset and no share address is
      configured (co-located transition) it reuses the control job; when the
      share has moved away but no storage job is given it returns None so the
      storage panel reports missing metrics instead of silently scraping the
      wrong machine (see #205).
    """
    control_raw = os.getenv("STP_CONTROL_PLANE_NODE_JOB", "").strip() or _NODE_JOB_DEFAULT
    control = _safe_prom_label(control_raw, _NODE_JOB_DEFAULT)
    share_addr = os.getenv("STP_AEE_SHARE_ADDRESS", "").strip()
    co_located = _share_is_co_located(share_addr)
    storage_raw = os.getenv("STP_STORAGE_NODE_JOB", "").strip()
    if storage_raw:
        # 非法 job 名不得回退到控制面 job：来源错了比没有更糟（假分源，见 #205）。
        storage: str | None = _safe_prom_label(storage_raw, "") or None
    elif not co_located:
        storage = None
    else:
        storage = control
    return control, storage, share_addr


def _node_current_queries(job: str, device_selector: str | None) -> dict[str, str]:
    """PromQL instant queries for one machine's node exporter."""
    selector = f'job="{job}"'
    disk_sel = device_selector or selector
    return {
        "up": f"up{{{selector}}}",
        "cpu": f'100 * (1 - avg(rate(node_cpu_seconds_total{{{selector},mode="idle"}}[5m])))',
        "memory": f"100 * (1 - node_memory_MemAvailable_bytes{{{selector}}} / node_memory_MemTotal_bytes{{{selector}}})",
        "mem_total": f"node_memory_MemTotal_bytes{{{selector}}}",
        "load1": f"node_load1{{{selector}}}",
        "disk_read": f"sum(rate(node_disk_read_bytes_total{{{disk_sel}}}[5m]))",
        "disk_write": f"sum(rate(node_disk_written_bytes_total{{{disk_sel}}}[5m]))",
        "network_receive": f'sum(rate(node_network_receive_bytes_total{{{selector},device!~"lo|docker.*|br-.*|veth.*"}}[5m]))',
        "network_transmit": f'sum(rate(node_network_transmit_bytes_total{{{selector},device!~"lo|docker.*|br-.*|veth.*"}}[5m]))',
        "cpu_count": f'count(node_cpu_seconds_total{{{selector},mode="idle"}})',
        "boot_time": f"node_boot_time_seconds{{{selector}}}",
        "nfs_requests": f"sum(rate(node_nfsd_requests_total{{{selector}}}[5m]))",
        "nfs_errors": f"sum(rate(node_nfsd_rpc_errors_total{{{selector}}}[5m]))",
        "nfs_stale": f"node_nfsd_file_handles_stale_total{{{selector}}}",
        "nfs_threads": f"node_nfsd_server_threads{{{selector}}}",
        "nfs_connections": f"node_nfsd_connections_total{{{selector}}}",
    }


def _capacity_range_query(job: str, mountpoint: Path) -> str:
    """Build PromQL range query for filesystem usage percentage on ``job``."""
    mount_selector = f'job="{job}",mountpoint="{_prom_string(str(mountpoint))}"'
    return (
        f"100 * (1 - node_filesystem_avail_bytes{{{mount_selector}}} / "
        f"node_filesystem_size_bytes{{{mount_selector}}})"
    )


def _node_history_queries(
    job: str,
    *,
    capacity_job: str | None = None,
    mountpoint: Path | None = None,
) -> dict[str, str]:
    """Build PromQL range queries for one host's CPU, memory, nfsd, and optional disk."""
    selector = f'job="{job}"'
    current = _node_current_queries(job, None)
    queries: dict[str, str] = {
        "cpu_usage_pct": current["cpu"],
        "memory_usage_pct": current["memory"],
        "nfs_requests_per_second": f"sum(rate(node_nfsd_requests_total{{{selector}}}[5m]))",
    }
    if mountpoint is not None:
        queries["capacity_usage_pct"] = _capacity_range_query(capacity_job or job, mountpoint)
    return queries


def _top_process_items(prom: _PrometheusClient, job: str) -> list[dict[str, Any]]:
    """Top-N process groups by anonymous memory from the host sampler.

    Sorted descending so the UI table and any future chart share one order.
    """
    selector = f'job="{job}"'
    samples = prom.vector(
        f"topk({_TOP_PROCESS_LIMIT}, {_HOSTPROC_ANON_METRIC}{{{selector}}})"
    )
    items: list[dict[str, Any]] = []
    for sample in samples:
        metric = sample["metric"]
        items.append({
            "comm": str(metric.get("comm") or "unknown"),
            "unit": str(metric.get("unit") or "-"),
            "anon_bytes": int(sample["value"]),
        })
    items.sort(key=lambda item: item["anon_bytes"], reverse=True)
    return items


def _run_queries(
    prom: _PrometheusClient,
    queries: dict[str, str],
) -> tuple[dict[str, float | None], str | None]:
    current: dict[str, float | None] = {}
    try:
        for key, query in queries.items():
            current[key] = prom.scalar(query)
    except (httpx.HTTPError, KeyError, RuntimeError, ValueError) as exc:
        error = type(exc).__name__
        logger.warning("file_server_prometheus_query_failed error=%s", error)
        return current, error
    return current, None


def _device_log_disk_summary(hosts: list[Any]) -> dict[str, Any]:
    """Aggregate per-host ``extra.disk_usage_aee`` (#273).

    只统计已上报 usage_percent 的 host；未上报不计入 warning/critical，也不进
    列表（老 Agent 无该字段，页面显示「未上报」由 total-reported 表达）。
    """
    items: list[dict[str, Any]] = []
    reported = 0
    warning = 0
    critical = 0
    for host in hosts:
        raw = (getattr(host, "extra", None) or {}).get("disk_usage_aee") or {}
        usage = _finite_float(raw.get("usage_percent"))
        total_gb = _finite_float(raw.get("total_gb"))
        used_gb = _finite_float(raw.get("used_gb"))
        free_gb = _finite_float(raw.get("free_gb"))
        if usage is None or total_gb is None or used_gb is None or free_gb is None:
            continue
        if usage < 0.0 or usage > 100.0:
            continue
        total_bytes = int(round(total_gb * _GIB))
        used_bytes = int(round(used_gb * _GIB))
        available_bytes = int(round(free_gb * _GIB))
        reported += 1
        if usage >= _DEVICE_LOG_DISK_CRITICAL_PCT:
            critical += 1
        elif usage >= _DEVICE_LOG_DISK_WARNING_PCT:
            warning += 1
        heartbeat = getattr(host, "last_heartbeat", None)
        items.append({
            "host_id": str(getattr(host, "id", "")),
            "ip": getattr(host, "ip", None) or getattr(host, "ip_address", None),
            "path": str(raw.get("path") or ""),
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "available_bytes": available_bytes,
            "usage_percent": round(usage, 2),
            "last_heartbeat": heartbeat.isoformat() if heartbeat else None,
        })
    items.sort(key=lambda item: item["ip"] or item["host_id"])
    return {
        "total": len(hosts),
        "reported": reported,
        "warning": warning,
        "critical": critical,
        "items": items,
    }


def collect_file_server_overview(hosts: Iterable[Any], *, hours: int = 6) -> dict[str, Any]:
    hosts = list(hosts)
    root, control_plane_root = _require_storage_roots()
    mount = _mount_details(root)
    storage_disk = _collect_disk_metrics(root, mount)
    if control_plane_root.resolve(strict=False) == root.resolve(strict=False):
        cp_mount_path = root
        control_disk = dict(storage_disk)
    else:
        cp_mount_path, cp_mount = _local_mount_info(control_plane_root)
        control_disk = _collect_disk_metrics(control_plane_root, cp_mount)

    total_bytes = storage_disk["total_bytes"]
    available_bytes = storage_disk["available_bytes"]
    used_pct = storage_disk["used_pct"]
    backend_write_access = storage_disk["backend_write_access"]

    control_job, storage_job, share_addr = _panel_jobs()
    control_addr = _server_address()
    storage_addr = share_addr or control_addr
    same_source = _share_is_co_located(share_addr)
    remote_log_share = _is_remote_client_share(
        filesystem=mount.get("filesystem"),
        same_source=same_source,
        log_root=root,
        control_plane_root=control_plane_root,
    )
    storage_job_env = os.getenv("STP_STORAGE_NODE_JOB", "").strip()
    # 分源且未配 storage job：外部 NAS/CIFS 无 node_exporter 的预期态（#205）。
    # 配了但非法 job 名仍算 misconfigured，保留 STORAGE_METRICS_UNAVAILABLE。
    storage_external_unmonitored = (
        (not same_source) and storage_job is None and not storage_job_env
    )

    # 控制面本机 etab 只反映本机 NFS 导出。远程 CIFS 日志根不是导出目录——
    # 必须对准控制面根（如 /mnt/stp-aee），否则会误报 NFS_EXPORT_MISSING。
    if remote_log_share:
        nfs_export_root = control_plane_root
        nfs_scope = "control_plane_root"
    else:
        nfs_export_root = root
        nfs_scope = "shared_log_root"
    exports = _export_targets(nfs_export_root)

    control_selector = f'job="{control_job}"'
    device = _block_device(mount.get("source"))
    device_selector = f'{control_selector},device="{_prom_string(device)}"' if device else None

    control_history: dict[str, Any] = {
        "capacity_usage_pct": [],
        "cpu_usage_pct": [],
        "memory_usage_pct": [],
        "nfs_requests_per_second": [],
    }
    history: dict[str, Any] = {
        "hours": hours,
        "capacity_usage_pct": [],
        "cpu_usage_pct": [],
        "memory_usage_pct": [],
        "nfs_requests_per_second": [],
        "hostproc_total_anon_bytes": [],
        "control_plane": control_history,
    }

    prom = _PrometheusClient()
    try:
        control_queries = {
            key: query
            for key, query in _node_current_queries(control_job, device_selector).items()
            if key in _CONTROL_PANEL_KEYS
        }
        # 远程日志根时，控制面根 NFS 的 nfsd 线程取自控制面 job（不再依赖 storage job）。
        if remote_log_share:
            control_queries["nfs_threads"] = _node_current_queries(control_job, None)["nfs_threads"]
        control_current, control_error = _run_queries(prom, control_queries)

        # 进程级内存（宿主机采样器，见 deploy/control-plane/node-exporter）：
        # 采集器未部署时 topk 为空 → 面板降级为 available=False，不算查询错误。
        top_processes: list[dict[str, Any]] = []
        processes_error: str | None = control_error
        if control_error is None:
            try:
                top_processes = _top_process_items(prom, control_job)
            except (httpx.HTTPError, KeyError, RuntimeError, ValueError) as exc:
                processes_error = type(exc).__name__
                logger.warning("file_server_process_query_failed error=%s", processes_error)

        storage_current: dict[str, float | None] = {}
        storage_error: str | None = None
        storage_hostname: str | None = None
        if storage_job:
            storage_queries = {
                key: query
                for key, query in _node_current_queries(storage_job, None).items()
                if key in _STORAGE_PANEL_KEYS
            }
            storage_current, storage_error = _run_queries(prom, storage_queries)
            if not storage_error:
                try:
                    storage_hostname = prom.label(
                        f'node_uname_info{{job="{storage_job}"}}', "nodename"
                    )
                except (httpx.HTTPError, KeyError, RuntimeError, ValueError):
                    storage_hostname = None

        end = datetime.now(timezone.utc).timestamp()
        start = end - hours * 3600
        # 约 72 个采样点：6h→5m、24h→20m、168h(7d)→140m；下限 60s。
        step = max(60, hours * 3600 // 72)
        # 趋势数据源跟随 storage 面板：分源已配 job 时刮存储机，避免把控制面
        # 负载冒充存储机（#205）；分源未配 job 时历史留空（fail-closed），不刮
        # 控制面。容量趋势例外——它来自控制面客户端挂载（NFS 客户端看到的
        # avail/size 就是服务端磁盘本身），与控制面/存储机归属无关，始终按
        # 控制面挂载点查询。
        if storage_job:
            history_job = storage_job
        elif share_addr:
            history_job = None
        else:
            history_job = control_job
        if history_job:
            range_queries = _node_history_queries(
                history_job,
                capacity_job=control_job,
                mountpoint=root,
            )
        elif storage_external_unmonitored or remote_log_share:
            # 外部共享无 host 采集器：容量仍可从控制面客户端挂载点取趋势。
            range_queries = {"capacity_usage_pct": _capacity_range_query(control_job, root)}
        else:
            range_queries = {}
        try:
            for key, query in range_queries.items():
                history[key] = prom.range(query, start=start, end=end, step=step)
        except (httpx.HTTPError, KeyError, RuntimeError, ValueError) as exc:
            logger.warning("file_server_history_query_failed error=%s", type(exc).__name__)
        # 控制面自身 CPU/内存/控制面根容量趋势始终独立保留（分源时不随存储面板切换而丢失）。
        same_cp_mount = cp_mount_path.resolve(strict=False) == root.resolve(strict=False)
        if history_job == control_job:
            for key in ("cpu_usage_pct", "memory_usage_pct", "nfs_requests_per_second"):
                control_history[key] = list(history[key])
            if same_cp_mount:
                control_history["capacity_usage_pct"] = list(history["capacity_usage_pct"])
            else:
                try:
                    control_history["capacity_usage_pct"] = prom.range(
                        _capacity_range_query(control_job, cp_mount_path),
                        start=start,
                        end=end,
                        step=step,
                    )
                except (httpx.HTTPError, KeyError, RuntimeError, ValueError) as exc:
                    logger.warning(
                        "file_server_control_history_query_failed error=%s",
                        type(exc).__name__,
                    )
        else:
            cp_range_queries = _node_history_queries(control_job, mountpoint=cp_mount_path)
            if same_cp_mount and history["capacity_usage_pct"]:
                control_history["capacity_usage_pct"] = list(history["capacity_usage_pct"])
                cp_range_queries.pop("capacity_usage_pct", None)
            try:
                for key, query in cp_range_queries.items():
                    control_history[key] = prom.range(query, start=start, end=end, step=step)
            except (httpx.HTTPError, KeyError, RuntimeError, ValueError) as exc:
                logger.warning(
                    "file_server_control_history_query_failed error=%s",
                    type(exc).__name__,
                )
        # 进程匿名内存合计趋势固定取控制面 job：采样器装在控制面，
        # 与存储面板是否分源无关（history_job 分源时仍应能看控制面内存走势）。
        try:
            if control_error is None:
                history["hostproc_total_anon_bytes"] = prom.range(
                    f'{_HOSTPROC_TOTAL_METRIC}{{job="{control_job}"}}',
                    start=start,
                    end=end,
                    step=step,
                )
        except (httpx.HTTPError, KeyError, RuntimeError, ValueError) as exc:
            logger.warning(
                "file_server_process_history_query_failed error=%s", type(exc).__name__
            )
    finally:
        prom.close()

    memory = psutil.virtual_memory()
    cpu_usage = control_current.get("cpu")
    if cpu_usage is None:
        cpu_usage = psutil.cpu_percent(interval=None)
    memory_usage = control_current.get("memory")
    if memory_usage is None:
        memory_usage = memory.percent
    load1 = control_current.get("load1")
    if load1 is None:
        load1 = os.getloadavg()[0]
    try:
        local_uptime = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        local_uptime = None

    now = datetime.now(timezone.utc).timestamp()
    storage_boot = storage_current.get("boot_time")
    storage_cpu_count = (
        int(storage_current["cpu_count"])
        if storage_current.get("cpu_count") is not None
        else None
    )
    storage_threads = (
        int(storage_current["nfs_threads"])
        if storage_current.get("nfs_threads") is not None
        else None
    )
    control_nfs_threads = (
        int(control_current["nfs_threads"])
        if control_current.get("nfs_threads") is not None
        else None
    )
    control_nfs_service_ready = bool(exports) and (
        (control_nfs_threads or 0) > 0 if control_nfs_threads is not None else True
    )
    # 远程日志根：控制面根导出只看控制面 nfsd；同源过渡：仍取 storage（=control）job。
    nfs_threads = control_nfs_threads if remote_log_share else storage_threads
    if remote_log_share:
        nfs_service_ready = control_nfs_service_ready
        nfs_requests = None
        nfs_errors = None
        nfs_stale = None
        nfs_connections = None
        # 有 storage job 时仅补充存储机侧 nfsd 观测指标，不得反过来覆盖控制面 NFS 就绪态（#4093）。
        if storage_job:
            nfs_requests = storage_current.get("nfs_requests")
            nfs_errors = storage_current.get("nfs_errors")
            nfs_stale = (
                int(storage_current["nfs_stale"])
                if storage_current.get("nfs_stale") is not None
                else None
            )
            nfs_connections = (
                int(storage_current["nfs_connections"])
                if storage_current.get("nfs_connections") is not None
                else None
            )
            if storage_threads is not None:
                nfs_threads = storage_threads
    else:
        nfs_service_ready = bool(exports) and (nfs_threads or 0) > 0
        nfs_requests = storage_current.get("nfs_requests")
        nfs_errors = storage_current.get("nfs_errors")
        nfs_stale = (
            int(storage_current["nfs_stale"])
            if storage_current.get("nfs_stale") is not None
            else None
        )
        nfs_connections = (
            int(storage_current["nfs_connections"])
            if storage_current.get("nfs_connections") is not None
            else None
        )

    control_panel = {
        "node": {
            "hostname": socket.gethostname(),
            "address": control_addr,
            "cpu_count": os.cpu_count() or 0,
            "uptime_seconds": local_uptime,
        },
        "system": {
            "cpu_usage_pct": round(float(cpu_usage), 2),
            "memory_usage_pct": round(float(memory_usage), 2),
            "memory_total_bytes": memory.total,
            "load1": round(float(load1), 2),
            "disk_read_bytes_per_second": control_current.get("disk_read"),
            "disk_write_bytes_per_second": control_current.get("disk_write"),
            "network_receive_bytes_per_second": control_current.get("network_receive"),
            "network_transmit_bytes_per_second": control_current.get("network_transmit"),
        },
        "client_mount": {
            "path": str(root),
            "source": mount["source"],
            "filesystem": mount["filesystem"],
            "mounted": mount["mounted"],
            "backend_write_access": backend_write_access,
        },
        "disk": control_disk,
        "nfs": {
            # 控制面本机 NFS 导出（切盘后对准控制面根；同源过渡期即日志根）。
            "service_ready": control_nfs_service_ready if remote_log_share else nfs_service_ready,
            "exported": bool(exports),
            "export_targets": list(exports),
            "export_path": str(nfs_export_root),
            "scope": nfs_scope,
            "server_threads": control_nfs_threads if remote_log_share else nfs_threads,
            "applicable": True,
        },
        "processes": {
            "available": bool(top_processes),
            "error": processes_error,
            "items": top_processes,
        },
        "monitoring": {
            "prometheus_available": control_current.get("up") == 1,
            "error": control_error,
            "unmonitored_reason": None,
        },
    }
    storage_monitoring: dict[str, Any]
    if storage_external_unmonitored:
        storage_monitoring = {
            "prometheus_available": False,
            "error": None,
            "unmonitored_reason": "external_share_no_collector",
        }
    else:
        storage_monitoring = {
            "prometheus_available": storage_current.get("up") == 1,
            "error": storage_error,
            "unmonitored_reason": None,
        }
    storage_panel = {
        "node": {
            "hostname": storage_hostname
            or (socket.gethostname() if same_source else storage_addr),
            "address": storage_addr,
            "cpu_count": storage_cpu_count,
            "uptime_seconds": (now - storage_boot) if storage_boot is not None else None,
        },
        "same_source": same_source,
        "system": {
            "cpu_usage_pct": _round_opt(storage_current.get("cpu")),
            "memory_usage_pct": _round_opt(storage_current.get("memory")),
            "memory_total_bytes": (
                int(storage_current["mem_total"])
                if storage_current.get("mem_total") is not None
                else None
            ),
            "load1": _round_opt(storage_current.get("load1")),
            "disk_read_bytes_per_second": storage_current.get("disk_read"),
            "disk_write_bytes_per_second": storage_current.get("disk_write"),
            "network_receive_bytes_per_second": storage_current.get("network_receive"),
            "network_transmit_bytes_per_second": storage_current.get("network_transmit"),
        },
        "disk": storage_disk,
        "nfs": {
            # 右栏在远程 CIFS 场景展示的是「控制面根 NFS 导出」摘要（同源时即日志根）。
            "service_ready": nfs_service_ready,
            "exported": bool(exports),
            "export_targets": exports,
            "export_path": str(nfs_export_root),
            "scope": nfs_scope,
            "server_threads": nfs_threads,
            "requests_per_second": nfs_requests,
            "rpc_errors_per_second": nfs_errors,
            "stale_file_handles_total": nfs_stale,
            "connections_total": nfs_connections,
            "applicable": True,
        },
        "monitoring": storage_monitoring,
    }

    agents = _host_mount_summary(hosts)
    device_log_disks = _device_log_disk_summary(hosts)
    alerts: list[dict[str, str]] = []
    if not mount["mounted"]:
        alerts.append({
            "severity": "critical",
            "code": "STORAGE_NOT_MOUNTED",
            "message": f"{root} is not mounted",
        })
    elif not backend_write_access:
        alerts.append({
            "severity": "critical",
            "code": "STORAGE_NOT_WRITABLE",
            "message": f"{root} is mounted but not writable by the control plane",
        })
    if not exports:
        alerts.append({
            "severity": "critical",
            "code": "NFS_EXPORT_MISSING",
            "message": (
                f"{nfs_export_root} is not exported"
                + (" (control-plane root)" if nfs_scope == "control_plane_root" else "")
            ),
        })
    if control_panel["monitoring"]["prometheus_available"] is False:
        alerts.append({
            "severity": "warning",
            "code": "METRICS_UNAVAILABLE",
            "message": "Control-plane metrics are unavailable",
        })
    # 外部共享未配采集器是预期降级，不发 STORAGE_METRICS_UNAVAILABLE 常驻 warning。
    if (
        not storage_external_unmonitored
        and storage_panel["monitoring"]["prometheus_available"] is False
    ):
        alerts.append({
            "severity": "warning",
            "code": "STORAGE_METRICS_UNAVAILABLE",
            "message": "Storage-server metrics are unavailable",
        })
    alerts.extend(_capacity_alerts(
        available_bytes=available_bytes,
        total_bytes=total_bytes,
        used_pct=used_pct,
    ))
    if agents["failed"] or agents["unreported"]:
        alerts.append({
            "severity": "warning",
            "code": "AGENT_MOUNT_INCOMPLETE",
            "message": f'{agents["mounted"]}/{agents["total"]} active Agents report the shared mount',
        })
    if device_log_disks["critical"]:
        alerts.append({
            "severity": "critical",
            "code": "DEVICE_LOG_DISK_CRITICAL",
            "message": f'{device_log_disks["critical"]} hosts have device-log disk usage >= 95%',
        })
    elif device_log_disks["warning"]:
        alerts.append({
            "severity": "warning",
            "code": "DEVICE_LOG_DISK_WARNING",
            "message": f'{device_log_disks["warning"]} hosts have device-log disk usage >= 90%',
        })

    status = "critical" if any(item["severity"] == "critical" for item in alerts) else (
        "warning" if alerts else "healthy"
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "control_plane": control_panel,
        "storage_server": storage_panel,
        "agents": agents,
        "device_log_disks": device_log_disks,
        "history": history,
        "alerts": alerts,
    }
