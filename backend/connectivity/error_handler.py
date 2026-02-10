from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ErrorReason(Enum):
    AUTH_FAILED = "auth_failed"
    TIMEOUT = "timeout"
    HOST_OFFLINE = "host_offline"
    SSH_FAILED = "ssh_failed"
    IO_ERROR = "io_error"
    UNKNOWN = "unknown"


@dataclass
class RetryConfig:
    retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    backoff: float = 2.0
    jitter: float = 0.5


def classify_error(exception: Exception) -> ErrorReason:
    exc_str = str(type(exception))
    if "Auth" in exc_str or "Permission" in exc_str:
        return ErrorReason.AUTH_FAILED
    if "Timeout" in exc_str:
        return ErrorReason.TIMEOUT
    if "Connection" in exc_str or "Host" in exc_str:
        return ErrorReason.HOST_OFFLINE
    if "OSError" in exc_str or "IO" in exc_str:
        return ErrorReason.IO_ERROR
    return ErrorReason.UNKNOWN
