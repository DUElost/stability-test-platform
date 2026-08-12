"""QCOM PlatformCollector stub — entry only (#220); real collect deferred (#73)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from ..collector import CollectorError, EventMetadata


class QcomPlatformCollector:
    platform = "QCOM"

    def detect(self, shell_fn: Callable[[str, int], Optional[str]], serial: str) -> bool:
        del shell_fn, serial
        return False

    def parse_metadata(self, event_dir: Path) -> EventMetadata:
        del event_dir
        raise CollectorError("QCOM collector not implemented")
