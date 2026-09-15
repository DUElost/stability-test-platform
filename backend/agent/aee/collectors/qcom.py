"""QCOM PlatformCollector stub — entry only (#220); real collect deferred (#73)."""

from __future__ import annotations

from pathlib import Path

from ..collector import CollectorError, EventMetadata


class QcomPlatformCollector:
    platform = "QCOM"

    def parse_metadata(self, event_dir: Path) -> EventMetadata:
        del event_dir
        raise CollectorError("QCOM collector not implemented")
