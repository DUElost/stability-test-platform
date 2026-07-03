from enum import Enum


class JobStatus(str, Enum):
    PENDING      = "PENDING"
    RUNNING      = "RUNNING"
    COMPLETED    = "COMPLETED"
    FAILED       = "FAILED"
    ABORTED      = "ABORTED"
    UNKNOWN      = "UNKNOWN"


class PlanRunStatus(str, Enum):
    RUNNING         = "RUNNING"
    SUCCESS         = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED          = "FAILED"
    DEGRADED        = "DEGRADED"


class HostStatus(str, Enum):
    ONLINE   = "ONLINE"
    OFFLINE  = "OFFLINE"
    DEGRADED = "DEGRADED"


class DeviceStatus(str, Enum):
    ONLINE  = "ONLINE"
    OFFLINE = "OFFLINE"
    BUSY    = "BUSY"
    ERROR   = "ERROR"  # ADB 已发现设备但状态非 "device"（如 unauthorized），非物理离线


# ADR-0019: Device Lease enums

class LeaseType(str, Enum):
    JOB         = "JOB"
    SCRIPT      = "SCRIPT"
    MAINTENANCE = "MAINTENANCE"


class LeaseStatus(str, Enum):
    ACTIVE   = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED  = "EXPIRED"
