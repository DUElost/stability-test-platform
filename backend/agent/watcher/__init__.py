"""Agent-side Device Log Watcher package.

Subsystems:
  - policy         : WatcherPolicy dataclass + load-from-job helpers
  - exceptions     : WatcherStartError / WatcherRuntimeError
  - manager        : LogWatcherManager singleton (process-level)
  - emitter        : SignalEmitter (per-Job) + OutboxDrainer (process-level)
  - sources        : CapabilityProber + InotifydSource + WatcherEvent
  - batcher        : EventBatcher (per-device 聚合 + 即时直通)
  - puller         : LogPuller (per-device 异步 pull + envelope 富化，5B1)
  - device_watcher : DeviceLogWatcher (per-device worker，组装 source+batcher+puller+emitter)

Public API:
    from backend.agent.watcher import LogWatcherManager, WatcherPolicy, WatcherStartError
    from backend.agent.watcher import SignalEmitter, OutboxDrainer
    from backend.agent.watcher import CapabilityProber, InotifydSource, WatcherEvent, WatcherCapability
    from backend.agent.watcher import EventBatcher, DeviceLogWatcher, WatcherStats, LogPuller
"""

from .exceptions import WatcherStartError, WatcherRuntimeError
from .manager   import LogWatcherManager
from .policy    import WatcherPolicy, OnUnavailableAction
from .emitter   import SignalEmitter, OutboxDrainer
from .sources   import (
    CapabilityProber,
    InotifydSource,
    ProbeResult,
    WatcherCapability,
    WatcherEvent,
)
from .batcher        import EventBatcher, BatcherStats, DEFAULT_IMMEDIATE_CATEGORIES
from .puller         import LogPuller, PullerStats
from .device_watcher import DeviceLogWatcher, WatcherStats

__all__ = [
    "LogWatcherManager",
    "WatcherPolicy",
    "OnUnavailableAction",
    "WatcherStartError",
    "WatcherRuntimeError",
    "SignalEmitter",
    "OutboxDrainer",
    "CapabilityProber",
    "InotifydSource",
    "ProbeResult",
    "WatcherCapability",
    "WatcherEvent",
    "EventBatcher",
    "BatcherStats",
    "DEFAULT_IMMEDIATE_CATEGORIES",
    "LogPuller",
    "PullerStats",
    "DeviceLogWatcher",
    "WatcherStats",
]
