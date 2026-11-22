from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class NodeIdentity(BaseModel):
    """A monitored machine's identity shown on the health page."""

    hostname: str
    address: str
    cpu_count: Optional[int] = None
    uptime_seconds: Optional[float] = None


class NodeMonitoring(BaseModel):
    prometheus_available: bool
    error: Optional[str] = None
    # 分源且未配 STP_STORAGE_NODE_JOB 时的预期降级（外部 NAS 无采集器）。
    unmonitored_reason: Optional[str] = None


class ClientMount(BaseModel):
    """Control plane's client-side view of the shared storage mount."""

    path: str
    source: Optional[str] = None
    filesystem: Optional[str] = None
    mounted: bool
    backend_write_access: bool


class NodeSystem(BaseModel):
    cpu_usage_pct: Optional[float] = None
    memory_usage_pct: Optional[float] = None
    memory_total_bytes: Optional[int] = None
    load1: Optional[float] = None
    disk_read_bytes_per_second: Optional[float] = None
    disk_write_bytes_per_second: Optional[float] = None
    network_receive_bytes_per_second: Optional[float] = None
    network_transmit_bytes_per_second: Optional[float] = None


class FileServerStorage(BaseModel):
    """Capacity/inode view of the shared disk."""

    path: str
    source: Optional[str] = None
    filesystem: Optional[str] = None
    mounted: bool
    backend_write_access: bool
    total_bytes: int
    used_bytes: int
    available_bytes: int
    used_pct: float
    inode_total: int
    inode_used: int
    inode_available: int
    inode_used_pct: float


class FileServerNfs(BaseModel):
    service_ready: Optional[bool] = None
    exported: bool
    export_targets: list[str]
    # 实际参与 /var/lib/nfs/etab 比对的导出路径（控制面根或同源日志根）。
    export_path: Optional[str] = None
    # control_plane_root = 控制面 NFS 导出；shared_log_root = 同源过渡期日志根。
    scope: Optional[str] = None
    # false 时前端应隐藏 NFS 服务面板（预留）。
    applicable: Optional[bool] = None
    server_threads: Optional[int] = None
    requests_per_second: Optional[float] = None
    rpc_errors_per_second: Optional[float] = None
    stale_file_handles_total: Optional[int] = None
    connections_total: Optional[int] = None


class HostProcessMemory(BaseModel):
    """One process group (argv0 basename + systemd cgroup unit) by anonymous memory.

    Reported by the host sampler ``deploy/control-plane/node-exporter/stp-mem-top.sh``;
    ``unit`` 直接给出 AI 会话/终端窗口所属的 systemd scope，便于定位内存归属。
    """

    comm: str
    unit: str
    anon_bytes: int


class HostProcessMemoryPanel(BaseModel):
    available: bool
    error: Optional[str] = None
    items: list[HostProcessMemory]


class ControlPlanePanel(BaseModel):
    """Health page left column: the control plane machine (8.202)."""

    node: NodeIdentity
    system: NodeSystem
    client_mount: ClientMount
    # 控制面根本地存储容量与挂载状态（分源后独立保留控制面磁盘监控；老响应兼容 None）。
    disk: Optional[FileServerStorage] = None
    # 控制面本机 NFS 导出（切盘后对准控制面根；同源过渡期可与日志根相同）。
    nfs: Optional[FileServerNfs] = None
    monitoring: NodeMonitoring
    processes: HostProcessMemoryPanel


class StorageServerPanel(BaseModel):
    """Health page right column: the central storage machine.

    During the co-located transition (share still on the control plane) this
    reuses the control plane's exporter and ``same_source`` is True. After the
    share migrates off 8.202 it must scrape its own Prometheus job.
    """

    node: NodeIdentity
    same_source: bool
    system: NodeSystem
    disk: FileServerStorage
    nfs: FileServerNfs
    monitoring: NodeMonitoring


class FileServerMetricPoint(BaseModel):
    timestamp: float
    value: float


class FileServerNodeHistory(BaseModel):
    """Per-node Prometheus history series (capacity, CPU, memory, nfsd)."""

    capacity_usage_pct: list[FileServerMetricPoint]
    cpu_usage_pct: list[FileServerMetricPoint]
    memory_usage_pct: list[FileServerMetricPoint]
    nfs_requests_per_second: list[FileServerMetricPoint] = Field(default_factory=list)


class FileServerHistory(BaseModel):
    hours: int
    capacity_usage_pct: list[FileServerMetricPoint]
    cpu_usage_pct: list[FileServerMetricPoint]
    memory_usage_pct: list[FileServerMetricPoint]
    nfs_requests_per_second: list[FileServerMetricPoint]
    hostproc_total_anon_bytes: list[FileServerMetricPoint]
    # 控制面自身历史趋势（分源时与 storage 趋势并存；老响应兼容 None）。
    control_plane: Optional[FileServerNodeHistory] = None


class FileServerAgentMount(BaseModel):
    host_id: str
    ip: Optional[str] = None
    status: str
    mounted: Optional[bool] = None
    last_heartbeat: Optional[datetime] = None


class FileServerAgentSummary(BaseModel):
    total: int
    mounted: int
    failed: int
    unreported: int
    items: list[FileServerAgentMount]


class DeviceLogDisk(BaseModel):
    """Per-host device-log disk (STP_AEE_LOCAL_ROOT's filesystem) water level."""

    host_id: str
    ip: Optional[str] = None
    path: str
    total_bytes: int
    used_bytes: int
    available_bytes: int
    usage_percent: float
    last_heartbeat: Optional[datetime] = None


class DeviceLogDiskSummary(BaseModel):
    total: int
    reported: int
    warning: int
    critical: int
    items: list[DeviceLogDisk]


class FileServerAlert(BaseModel):
    severity: Literal["warning", "critical"]
    code: str
    message: str


class FileServerOverview(BaseModel):
    generated_at: datetime
    status: Literal["healthy", "warning", "critical"]
    control_plane: ControlPlanePanel
    storage_server: StorageServerPanel
    agents: FileServerAgentSummary
    device_log_disks: DeviceLogDiskSummary
    history: FileServerHistory
    alerts: list[FileServerAlert]
