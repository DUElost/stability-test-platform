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
    ONLINE   = "ONLINE"
    OFFLINE  = "OFFLINE"
    DEGRADED = "DEGRADED"


class DeviceStatus(str, Enum):
    ONLINE  = "ONLINE"
    OFFLINE = "OFFLINE"
    BUSY    = "BUSY"


# Legacy enums — used by tasks.py compatibility layer (status mapping).
# Canonical source since Wave 4; schemas.py deleted, legacy.py re-exports.

class TaskStatus(str, Enum):
    PENDING   = "PENDING"
    QUEUED    = "QUEUED"
    RUNNING   = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    CANCELED  = "CANCELED"


class RunStatus(str, Enum):
    QUEUED     = "QUEUED"
    DISPATCHED = "DISPATCHED"
    RUNNING    = "RUNNING"
    FINISHED   = "FINISHED"
    FAILED     = "FAILED"
    CANCELED   = "CANCELED"


class RunStepStatus(str, Enum):
    PENDING   = "PENDING"
    RUNNING   = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    SKIPPED   = "SKIPPED"
    CANCELED  = "CANCELED"
