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
_DEVICE_LOG_DISK_WARNING_PCT = 90.0
_DEVICE_LOG_DISK_CRITICAL_PCT = 95.0


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


def _require_shared_root() -> Path:
    """STP_AEE_NFS_ROOT 解析与门禁。

    未设 → RuntimeError；与 backend/services/dedup_scan.py:99
    同一 env var，但 dedup_scan 软降级（warning + 返回空串），本处 endpoint
    强制需要——未设时给 admin 抛错，胜过盯错路径永远 STORAGE_NOT_MOUNTED 误报。
    与 .env.example / agent paths 收敛到"必须显式设"，不再硬编码 fallback。
    须指向与 Agent 共享的同一挂载，**路径字符串可与各 Agent 的 MOUNT_POINTS 不同**
    （mount 统计按"任一 ok=True"识别，见 _host_mount_summary）。
    """
    from backend.core.storage_root import resolve_shared_storage_root

    raw = resolve_shared_storage_root()
    if not raw:
        raise RuntimeError(
            "STP_AEE_NFS_ROOT is not set; the file-server dashboard refuses to "
            "report against an unconfigured shared root (would always emit "
            "STORAGE_NOT_MOUNTED). Set it to the actual NFS/CIFS mount on the "
            "control plane (must point to the same share the agents mount, "
            "path string may differ from each agent's MOUNT_POINTS)."
        )
    return Path(raw)


def _panel_jobs() -> tuple[str, str | None, str]:
    """Resolve Prometheus job labels for the two health-page panels.

    - control job: ``STP_CONTROL_PLANE_NODE_JOB`` (new) with
      ``STP_FILE_SERVER_NODE_JOB`` / ``file-server`` as legacy fallback.
    - storage job: ``STP_STORAGE_NODE_JOB``. When unset and no share address is
      configured (co-located transition) it reuses the control job; when the
      share has moved away but no storage job is given it returns None so the
      storage panel reports missing metrics instead of silently scraping the
      wrong machine (see #205).
    """
    control_raw = (
        os.getenv("STP_CONTROL_PLANE_NODE_JOB", "").strip()
        or os.getenv("STP_FILE_SERVER_NODE_JOB", _NODE_JOB_DEFAULT).strip()
    )
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
    root = _require_shared_root()
    mount = _mount_details(root)
    exports = _export_targets(root)

    total_bytes = used_bytes = available_bytes = 0
    inode_total = inode_used = inode_available = 0
    if root.exists():
        usage = shutil.disk_usage(root)
        total_bytes = usage.total
        used_bytes = usage.used
        available_bytes = usage.free
        stat = os.statvfs(root)
        inode_total = stat.f_files
        inode_available = stat.f_favail
        inode_used = max(0, inode_total - inode_available)

    control_job, storage_job, share_addr = _panel_jobs()
    control_addr = _server_address()
    storage_addr = share_addr or control_addr
    same_source = _share_is_co_located(share_addr)

    control_selector = f'job="{control_job}"'
    mount_selector = f'{control_selector},mountpoint="{_prom_string(str(root))}"'
    device = _block_device(mount.get("source"))
    device_selector = f'{control_selector},device="{_prom_string(device)}"' if device else None

    history: dict[str, Any] = {
        "hours": hours,
        "capacity_usage_pct": [],
        "cpu_usage_pct": [],
        "memory_usage_pct": [],
        "nfs_requests_per_second": [],
    }

    prom = _PrometheusClient()
    try:
        control_queries = {
            key: query
            for key, query in _node_current_queries(control_job, device_selector).items()
            if key in _CONTROL_PANEL_KEYS
        }
        control_current, control_error = _run_queries(prom, control_queries)

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
        range_queries: dict[str, str] = {}
        if history_job:
            history_selector = f'job="{history_job}"'
            history_queries = _node_current_queries(history_job, None)
            range_queries = {
                "capacity_usage_pct": (
                    f"100 * (1 - node_filesystem_avail_bytes{{{mount_selector}}} / "
                    f"node_filesystem_size_bytes{{{mount_selector}}})"
                ),
                "cpu_usage_pct": history_queries["cpu"],
                "memory_usage_pct": history_queries["memory"],
                "nfs_requests_per_second": (
                    f"sum(rate(node_nfsd_requests_total{{{history_selector}}}[5m]))"
                ),
            }
        try:
            for key, query in range_queries.items():
                history[key] = prom.range(query, start=start, end=end, step=step)
        except (httpx.HTTPError, KeyError, RuntimeError, ValueError) as exc:
            logger.warning("file_server_history_query_failed error=%s", type(exc).__name__)
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

    used_pct = round(used_bytes / total_bytes * 100, 2) if total_bytes else 0.0
    inode_used_pct = round(inode_used / inode_total * 100, 2) if inode_total else 0.0
    backend_write_access = root.exists() and os.access(root, os.W_OK)

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
        "monitoring": {
            "prometheus_available": control_current.get("up") == 1,
            "error": control_error,
        },
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
        "disk": {
            "path": str(root),
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
        },
        "nfs": {
            "service_ready": bool(exports) and (storage_threads or 0) > 0,
            "exported": bool(exports),
            "export_targets": exports,
            "server_threads": storage_threads,
            "requests_per_second": storage_current.get("nfs_requests"),
            "rpc_errors_per_second": storage_current.get("nfs_errors"),
            "stale_file_handles_total": (
                int(storage_current["nfs_stale"])
                if storage_current.get("nfs_stale") is not None
                else None
            ),
            "connections_total": (
                int(storage_current["nfs_connections"])
                if storage_current.get("nfs_connections") is not None
                else None
            ),
        },
        "monitoring": {
            "prometheus_available": storage_current.get("up") == 1,
            "error": storage_error,
        },
    }

    agents = _host_mount_summary(hosts)
    device_log_disks = _device_log_disk_summary(hosts)
    alerts: list[dict[str, str]] = []
    if not mount["mounted"]:
        alerts.append({"severity": "critical", "code": "STORAGE_NOT_MOUNTED", "message": f"{root} is not mounted"})
    if not exports:
        alerts.append({"severity": "critical", "code": "NFS_EXPORT_MISSING", "message": f"{root} is not exported"})
    if control_panel["monitoring"]["prometheus_available"] is False:
        alerts.append({"severity": "warning", "code": "METRICS_UNAVAILABLE", "message": "Control-plane metrics are unavailable"})
    if storage_panel["monitoring"]["prometheus_available"] is False:
        alerts.append({"severity": "warning", "code": "STORAGE_METRICS_UNAVAILABLE", "message": "Storage-server metrics are unavailable"})
    if used_pct >= 90:
        alerts.append({"severity": "critical", "code": "CAPACITY_CRITICAL", "message": f"Storage usage is {used_pct:.1f}%"})
    elif used_pct >= 80:
        alerts.append({"severity": "warning", "code": "CAPACITY_WARNING", "message": f"Storage usage is {used_pct:.1f}%"})
    if agents["failed"] or agents["unreported"]:
        alerts.append({
            "severity": "warning",
            "code": "AGENT_MOUNT_INCOMPLETE",
            "message": f'{agents["mounted"]}/{agents["total"]} active Agents report the NFS mount',
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
