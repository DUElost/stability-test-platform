"""HTTP client for control-plane DeviceLogEvent API (ADR-0028 D1)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


def _env_truthy(name: str, default: bool = True) -> bool:
    raw = (os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class DeviceLogEventClient:
    api_url: str
    agent_secret: str
    host_id: str
    timeout: float = 15.0

    @classmethod
    def from_env(cls, *, api_url: str, agent_secret: str, host_id: str) -> Optional["DeviceLogEventClient"]:
        if not _env_truthy("STP_DEVICE_LOG_EVENT_ENABLED", default=False):
            return None
        if not api_url or not agent_secret or not host_id:
            return None
        return cls(api_url=api_url.rstrip("/"), agent_secret=agent_secret, host_id=host_id)

    def _headers(self) -> Dict[str, str]:
        return {"X-Agent-Secret": self.agent_secret, "Content-Type": "application/json"}

    def create_local_event(
        self,
        *,
        serial: str,
        platform: str,
        event_type: str,
        event_subtype: Optional[str],
        detected_at: datetime,
        device_timestamp: Optional[datetime],
        local_path: Path,
        plan_run_id: Optional[int],
        job_id: Optional[int],
        link_signal_seq_no: Optional[int] = None,
        size_bytes: Optional[int] = None,
    ) -> Optional[str]:
        """POST 新事件 state=LOCAL；返回 event id 字符串。"""
        payload: Dict[str, Any] = {
            "serial": serial,
            "platform": platform,
            "event_type": event_type,
            "event_subtype": event_subtype,
            "detected_at": detected_at.isoformat(),
            "device_timestamp": device_timestamp.isoformat() if device_timestamp else None,
            "state": "LOCAL",
            "local_path": str(local_path),
            "host_id": self.host_id,
            "job_id": job_id,
            "plan_run_id": plan_run_id,
            "size_bytes": size_bytes,
            "link_signal_seq_no": link_signal_seq_no,
        }
        try:
            resp = requests.post(
                f"{self.api_url}/api/v1/agent/device-log-events",
                json={"events": [payload]},
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                logger.warning(
                    "device_log_event_create_failed status=%s body=%s",
                    resp.status_code, resp.text[:200],
                )
                return None
            ids = resp.json().get("data", {}).get("event_ids") or []
            return str(ids[0]) if ids else None
        except Exception:
            logger.exception("device_log_event_create_error path=%s", local_path)
            return None

    def list_events(self, *, state: str, limit: int | None = 1) -> List[Dict[str, Any]]:
        try:
            params: Dict[str, Any] = {"host_id": self.host_id, "state": state}
            if limit is not None:
                params["limit"] = limit
            resp = requests.get(
                f"{self.api_url}/api/v1/agent/device-log-events",
                params=params,
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                return []
            return list(resp.json().get("data", {}).get("events") or [])
        except Exception:
            logger.exception("device_log_event_list_error state=%s", state)
            return []

    @staticmethod
    def dir_size_bytes(path: Path) -> int:
        total = 0
        if not path.is_dir():
            return 0
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
        return total
