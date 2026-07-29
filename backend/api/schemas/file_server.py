from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class FileServerNode(BaseModel):
    hostname: str
    address: str
    cpu_count: int
    uptime_seconds: Optional[float] = None


class FileServerStorage(BaseModel):
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


class FileServerSystem(BaseModel):
    cpu_usage_pct: float
    memory_usage_pct: float
    memory_total_bytes: int
    load1: float
    disk_read_bytes_per_second: Optional[float] = None
    disk_write_bytes_per_second: Optional[float] = None
    network_receive_bytes_per_second: Optional[float] = None
    network_transmit_bytes_per_second: Optional[float] = None


class FileServerNfs(BaseModel):
    service_ready: bool
    exported: bool
    export_targets: list[str]
    server_threads: int
    requests_per_second: Optional[float] = None
    rpc_errors_per_second: Optional[float] = None
    stale_file_handles_total: int
    connections_total: int


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


class FileServerMetricPoint(BaseModel):
    timestamp: float
    value: float


class FileServerHistory(BaseModel):
    hours: int
    capacity_usage_pct: list[FileServerMetricPoint]
    cpu_usage_pct: list[FileServerMetricPoint]
    memory_usage_pct: list[FileServerMetricPoint]
    nfs_requests_per_second: list[FileServerMetricPoint]


class FileServerMonitoring(BaseModel):
    prometheus_available: bool
    error: Optional[str] = None


class FileServerAlert(BaseModel):
    severity: Literal["warning", "critical"]
    code: str
    message: str


class FileServerOverview(BaseModel):
    generated_at: datetime
    status: Literal["healthy", "warning", "critical"]
    server: FileServerNode
    storage: FileServerStorage
    system: FileServerSystem
    nfs: FileServerNfs
    agents: FileServerAgentSummary
    history: FileServerHistory
    monitoring: FileServerMonitoring
    alerts: list[FileServerAlert]
