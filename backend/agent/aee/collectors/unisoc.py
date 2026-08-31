"""UNISOC PlatformCollector — uniview event metadata (ADR-0032 B5)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from ..collector import CollectorError, EventMetadata
from ..timestamp import parse_timestamp, to_utc

logger = logging.getLogger(__name__)


class UnisocPlatformCollector:
    platform = "UNISOC"

    def detect(self, shell_fn: Callable[[str, int], Optional[str]], serial: str) -> bool:
        del serial
        for remote in ("/data/uniview", "/data/vendor/uniview"):
            if shell_fn(f"ls {remote} 2>/dev/null", 10):
                return True
        return False

    def parse_metadata(self, event_dir: Path) -> EventMetadata:
        info_path = event_dir / "unievent_info.json"
        if not info_path.is_file():
            raise CollectorError(f"missing unievent_info.json under {event_dir}")
        try:
            raw: dict[str, Any] = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CollectorError(f"invalid unievent_info.json: {exc}") from exc

        event_name = raw.get("event_name") or raw.get("eventName") or raw.get("type") or ""
        package_name = raw.get("package_name") or raw.get("packageName") or raw.get("pkg")
        ts_raw = raw.get("timestamp") or raw.get("event_time") or raw.get("time") or raw.get("aee_ts")
        device_ts: Optional[datetime] = None
        if ts_raw:
            try:
                parsed = parse_timestamp(str(ts_raw))
                device_ts = to_utc(parsed) if parsed else None
            except Exception:
                logger.debug("unisoc_collector_ts_parse_failed dir=%s", event_dir, exc_info=True)

        return EventMetadata(
            event_type="UNIVIEW",
            event_subtype=str(event_name).strip() or None,
            package_name=str(package_name).strip() if package_name else None,
            device_timestamp=device_ts,
        )
