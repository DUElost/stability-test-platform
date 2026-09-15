"""PlatformCollector protocol — ADR-0028 D4."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable


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
    # #785: 设备侧时间戳原文（与 MTK extra.aee_ts 对齐）；UTC 换算见 device_timestamp
    device_timestamp_raw: Optional[str] = None


@runtime_checkable
class PlatformCollector(Protocol):
    """平台采集器：只负责**从事件目录解析元数据**（R4-a a1 裁决，2026-09-15）。

    协议曾声明 ``detect(shell_fn, serial)`` 但**全仓零调用点**——平台判定的唯一权威
    是 ``backend.agent.device_platform.detect_device_platform``（调用点
    ``job_session._maybe_start_aee_reconciler``）。删除该方法的理由见
    ``docs/notes/architecture/2026-09-15-adr0032-v08-platform-routing-revision.md`` §R4-a：
    不得保留"定义了却从不调用"的第三种状态。
    """

    platform: str

    def parse_metadata(self, event_dir: Path) -> EventMetadata: ...


def get_collector_for_platform(platform: str) -> Optional[PlatformCollector]:
    """按平台名返回 Collector；未知平台返回 None。

    Production scans MTK by default. UNISOC uses UnisocPlatformCollector when
    ``device.platform`` is UNISOC (ADR-0032 D6/D8). QCOM remains stub-only.
    """
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
