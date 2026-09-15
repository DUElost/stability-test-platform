"""MTK PlatformCollector — metadata parsing from AEE event directories."""

from __future__ import annotations

from pathlib import Path

from ..collector import EventMetadata
from ..metadata import (
    infer_aee_subtype_from_paths,
    normalize_package_name,
    parse_exp_main_summary,
    resolve_device_log_event_type,
)


class MtkPlatformCollector:
    platform = "MTK"

    def parse_metadata(self, event_dir: Path) -> EventMetadata:
        summary = parse_exp_main_summary(event_dir)
        subtype = summary.get("event_subtype") or infer_aee_subtype_from_paths(str(event_dir))
        pkg = normalize_package_name(
            summary.get("package_name") or summary.get("current_process") or "",
        )
        event_type = resolve_device_log_event_type(
            summary.get("event_type"),
            subtype,
            paths=(str(event_dir),),
        )
        return EventMetadata(
            event_type=str(event_type),
            event_subtype=subtype or None,
            package_name=pkg or None,
            device_timestamp=None,
        )
