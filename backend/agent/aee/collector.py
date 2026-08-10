"""PlatformCollector protocol — ADR-0028 D4."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Protocol, runtime_checkable


class CollectorError(Exception):
    """Collector 操作失败；Reconciler 捕获后记 tick_errors，不 crash 线程。"""


@dataclass(frozen=True)
class TriggerInfo:
    aee_type: str
    entry_line: str
    device_path: str


@dataclass
class EventMetadata:
    event_type: str
    event_subtype: Optional[str] = None
    package_name: Optional[str] = None
    device_timestamp: Optional[datetime] = None


@runtime_checkable
class PlatformCollector(Protocol):
    platform: str

    def detect(self, shell_fn: Callable[[str, int], Optional[str]], serial: str) -> bool: ...

    def parse_metadata(self, event_dir: Path) -> EventMetadata: ...


def get_collector_for_platform(platform: str) -> Optional[PlatformCollector]:
    """按平台名返回 Collector；未知平台返回 None。"""
    from .collectors.mtk import MtkPlatformCollector
    from .collectors.qcom import QcomPlatformCollector
    from .collectors.unisoc import UnisocPlatformCollector

    key = (platform or "").strip().upper()
    mapping = {
        "MTK": MtkPlatformCollector,
        "UNISOC": UnisocPlatformCollector,
        "QCOM": QcomPlatformCollector,
        "UNKNOWN": MtkPlatformCollector,
    }
    cls = mapping.get(key)
    if cls is None:
        return None
    return cls()
