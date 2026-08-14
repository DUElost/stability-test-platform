from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class NodeIdentity(BaseModel):
    """A monitored machine's identity shown on the health page."""

    hostname: str
    address: str
    cpu_count: Optional[int] = None
    uptime_seconds: Optional[float] = None


class NodeMonitoring(BaseModel):
    prometheus_available: bool
    error: Optional[str] = None


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
    service_ready: bool
    exported: bool
    export_targets: list[str]
    server_threads: Optional[int] = None
    requests_per_second: Optional[float] = None
    rpc_errors_per_second: Optional[float] = None
    stale_file_handles_total: Optional[int] = None
    connections_total: Optional[int] = None


class ControlPlanePanel(BaseModel):
    """Health page left column: the control plane machine (8.202)."""

    node: NodeIdentity
    system: NodeSystem
    client_mount: ClientMount
    monitoring: NodeMonitoring


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


class FileServerHistory(BaseModel):
    hours: int
    capacity_usage_pct: list[FileServerMetricPoint]
    cpu_usage_pct: list[FileServerMetricPoint]
    memory_usage_pct: list[FileServerMetricPoint]
    nfs_requests_per_second: list[FileServerMetricPoint]


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
    history: FileServerHistory
    alerts: list[FileServerAlert]
