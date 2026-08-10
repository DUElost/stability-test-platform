"""MTK PlatformCollector — metadata parsing from AEE event directories."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from ..collector import EventMetadata
from ..metadata import infer_aee_subtype_from_paths, normalize_package_name, parse_exp_main_summary


class MtkPlatformCollector:
    platform = "MTK"

    def detect(self, shell_fn: Callable[[str, int], Optional[str]], serial: str) -> bool:
        del serial
        for remote in ("/data/aee_exp/db_history", "/data/vendor/aee_exp/db_history"):
            content = shell_fn(f"cat {remote}", 15)
            if content is not None:
                return True
        return False

    def parse_metadata(self, event_dir: Path) -> EventMetadata:
        summary = parse_exp_main_summary(event_dir)
        subtype = summary.get("event_subtype") or infer_aee_subtype_from_paths(str(event_dir))
        pkg = normalize_package_name(
            summary.get("package_name") or summary.get("current_process") or "",
        )
        event_type = summary.get("event_type") or "UNKNOWN"
        return EventMetadata(
            event_type=str(event_type),
            event_subtype=subtype or None,
            package_name=pkg or None,
            device_timestamp=None,
        )
