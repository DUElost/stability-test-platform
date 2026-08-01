"""File-server health aggregation for the admin storage dashboard."""

from __future__ import annotations

import logging
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
            return float(result[0]["value"][1])
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
                points.append({"timestamp": float(raw_ts), "value": round(float(raw_value), 3)})
            except (TypeError, ValueError):
                continue
        return points


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


def _require_shared_root() -> Path:
    """STP_AEE_NFS_ROOT 解析与门禁。

    未设 → RuntimeError；与 backend/services/dedup_scan.py:99
    同一 env var，但 dedup_scan 软降级（warning + 返回空串），本处 endpoint
    强制需要——未设时给 admin 抛错，胜过盯错路径永远 STORAGE_NOT_MOUNTED 误报。
    与 .env.example / agent paths 收敛到"必须显式设"，不再硬编码 fallback。
    须指向与 Agent 共享的同一挂载，**路径字符串可与各 Agent 的 MOUNT_POINTS 不同**
    （mount 统计按"任一 ok=True"识别，见 _host_mount_summary）。
    """
    raw = os.getenv("STP_AEE_NFS_ROOT", "").strip()
    if not raw:
        raise RuntimeError(
            "STP_AEE_NFS_ROOT is not set; the file-server dashboard refuses to "
            "report against an unconfigured shared root (would always emit "
            "STORAGE_NOT_MOUNTED). Set it to the actual NFS/CIFS mount on the "
            "control plane (must point to the same share the agents mount, "
            "path string may differ from each agent's MOUNT_POINTS)."
        )
    return Path(raw)


def collect_file_server_overview(hosts: Iterable[Any], *, hours: int = 6) -> dict[str, Any]:
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

    job = _safe_prom_label(os.getenv("STP_FILE_SERVER_NODE_JOB", _NODE_JOB_DEFAULT), _NODE_JOB_DEFAULT)
    selector = f'job="{job}"'
    mount_selector = f'{selector},mountpoint="{_prom_string(str(root))}"'
    device = _block_device(mount.get("source"))
    device_selector = f'{selector},device="{_prom_string(device)}"' if device else selector

    current: dict[str, float | None] = {
        "up": None,
        "cpu": None,
        "memory": None,
        "load1": None,
        "disk_read": None,
        "disk_write": None,
        "network_receive": None,
        "network_transmit": None,
        "nfs_requests": None,
        "nfs_errors": None,
        "nfs_stale": None,
        "nfs_threads": None,
        "nfs_connections": None,
    }
    history = {
        "hours": hours,
        "capacity_usage_pct": [],
        "cpu_usage_pct": [],
        "memory_usage_pct": [],
        "nfs_requests_per_second": [],
    }
    prometheus_error: str | None = None
    prom = _PrometheusClient()
    try:
        queries = {
            "up": f"up{{{selector}}}",
            "cpu": f'100 * (1 - avg(rate(node_cpu_seconds_total{{{selector},mode="idle"}}[5m])))',
            "memory": f"100 * (1 - node_memory_MemAvailable_bytes{{{selector}}} / node_memory_MemTotal_bytes{{{selector}}})",
            "load1": f"node_load1{{{selector}}}",
            "disk_read": f"rate(node_disk_read_bytes_total{{{device_selector}}}[5m])",
            "disk_write": f"rate(node_disk_written_bytes_total{{{device_selector}}}[5m])",
            "network_receive": f'sum(rate(node_network_receive_bytes_total{{{selector},device!~"lo|docker.*|br-.*|veth.*"}}[5m]))',
            "network_transmit": f'sum(rate(node_network_transmit_bytes_total{{{selector},device!~"lo|docker.*|br-.*|veth.*"}}[5m]))',
            "nfs_requests": f"sum(rate(node_nfsd_requests_total{{{selector}}}[5m]))",
            "nfs_errors": f"sum(rate(node_nfsd_rpc_errors_total{{{selector}}}[5m]))",
            "nfs_stale": f"node_nfsd_file_handles_stale_total{{{selector}}}",
            "nfs_threads": f"node_nfsd_server_threads{{{selector}}}",
            "nfs_connections": f"node_nfsd_connections_total{{{selector}}}",
        }
        for key, query in queries.items():
            current[key] = prom.scalar(query)

        end = datetime.now(timezone.utc).timestamp()
        start = end - hours * 3600
        step = max(60, hours * 3600 // 72)
        range_queries = {
            "capacity_usage_pct": f"100 * (1 - node_filesystem_avail_bytes{{{mount_selector}}} / node_filesystem_size_bytes{{{mount_selector}}})",
            "cpu_usage_pct": queries["cpu"],
            "memory_usage_pct": queries["memory"],
            "nfs_requests_per_second": queries["nfs_requests"],
        }
        for key, query in range_queries.items():
            history[key] = prom.range(query, start=start, end=end, step=step)
    except (httpx.HTTPError, KeyError, RuntimeError, ValueError) as exc:
        prometheus_error = type(exc).__name__
        logger.warning("file_server_prometheus_query_failed error=%s", prometheus_error)
    finally:
        prom.close()

    memory = psutil.virtual_memory()
    cpu_usage = current["cpu"] if current["cpu"] is not None else psutil.cpu_percent(interval=None)
    memory_usage = current["memory"] if current["memory"] is not None else memory.percent
    load1 = current["load1"] if current["load1"] is not None else os.getloadavg()[0]
    agents = _host_mount_summary(hosts)
    prometheus_available = current["up"] == 1
    service_ready = bool(exports) and (current["nfs_threads"] or 0) > 0
    used_pct = round(used_bytes / total_bytes * 100, 2) if total_bytes else 0.0
    inode_used_pct = round(inode_used / inode_total * 100, 2) if inode_total else 0.0

    alerts: list[dict[str, str]] = []
    if not mount["mounted"]:
        alerts.append({"severity": "critical", "code": "STORAGE_NOT_MOUNTED", "message": f"{root} is not mounted"})
    if not exports:
        alerts.append({"severity": "critical", "code": "NFS_EXPORT_MISSING", "message": f"{root} is not exported"})
    if not prometheus_available:
        alerts.append({"severity": "warning", "code": "METRICS_UNAVAILABLE", "message": "File-server metrics are unavailable"})
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

    status = "critical" if any(item["severity"] == "critical" for item in alerts) else (
        "warning" if alerts else "healthy"
    )
    try:
        uptime_seconds = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        uptime_seconds = None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "server": {
            "hostname": socket.gethostname(),
            "address": _server_address(),
            "cpu_count": os.cpu_count() or 0,
            "uptime_seconds": uptime_seconds,
        },
        "storage": {
            "path": str(root),
            "source": mount["source"],
            "filesystem": mount["filesystem"],
            "mounted": mount["mounted"],
            "backend_write_access": root.exists() and os.access(root, os.W_OK),
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "available_bytes": available_bytes,
            "used_pct": used_pct,
            "inode_total": inode_total,
            "inode_used": inode_used,
            "inode_available": inode_available,
            "inode_used_pct": inode_used_pct,
        },
        "system": {
            "cpu_usage_pct": round(float(cpu_usage), 2),
            "memory_usage_pct": round(float(memory_usage), 2),
            "memory_total_bytes": memory.total,
            "load1": round(float(load1), 2),
            "disk_read_bytes_per_second": current["disk_read"],
            "disk_write_bytes_per_second": current["disk_write"],
            "network_receive_bytes_per_second": current["network_receive"],
            "network_transmit_bytes_per_second": current["network_transmit"],
        },
        "nfs": {
            "service_ready": service_ready,
            "exported": bool(exports),
            "export_targets": exports,
            "server_threads": int(current["nfs_threads"] or 0),
            "requests_per_second": current["nfs_requests"],
            "rpc_errors_per_second": current["nfs_errors"],
            "stale_file_handles_total": int(current["nfs_stale"] or 0),
            "connections_total": int(current["nfs_connections"] or 0),
        },
        "agents": agents,
        "history": history,
        "monitoring": {
            "prometheus_available": prometheus_available,
            "error": prometheus_error,
        },
        "alerts": alerts,
    }
