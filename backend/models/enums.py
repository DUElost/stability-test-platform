from enum import Enum


class JobStatus(str, Enum):
    PENDING      = "PENDING"
    RUNNING      = "RUNNING"
    COMPLETED    = "COMPLETED"
    FAILED       = "FAILED"
    ABORTED      = "ABORTED"
    UNKNOWN      = "UNKNOWN"
    PENDING_TOOL = "PENDING_TOOL"


class WorkflowStatus(str, Enum):
    RUNNING         = "RUNNING"
    SUCCESS         = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED          = "FAILED"
    DEGRADED        = "DEGRADED"


class HostStatus(str, Enum):
    ONLINE  = "ONLINE"
    OFFLINE = "OFFLINE"


class DeviceStatus(str, Enum):
    ONLINE  = "ONLINE"
    OFFLINE = "OFFLINE"
    BUSY    = "BUSY"
