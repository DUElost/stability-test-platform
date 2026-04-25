"""Host identity and auto-registration helpers for the agent."""

import logging
import os
import socket
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


def get_host_info() -> Dict[str, Any]:
    """Return the local network identity reported to the backend."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
    except Exception:
        ip = "127.0.0.1"

    return {"ip": ip}


def load_required_host_id() -> Optional[str]:
    raw_value = os.getenv("HOST_ID", "").strip()
    if not raw_value:
        raise ValueError("HOST_ID is required and cannot be empty")

    if raw_value.upper() == "AUTO":
        return None

    return raw_value


def auto_register_host(api_url: str, host_info: Dict[str, Any]) -> str:
    """Auto-register the current host through the heartbeat endpoint."""
    heartbeat_url = f"{api_url.rstrip('/')}/api/v1/heartbeat"
    payload = {
        "host_id": 0,
        "status": "ONLINE",
        "mount_status": {},
        "extra": {},
        "host": host_info,
        "devices": [],
    }

    agent_secret = os.getenv("AGENT_SECRET", "")
    headers = {"x-agent-secret": agent_secret} if agent_secret else {}

    try:
        logger.info(
            "auto_register_host: url=%s, ip=%s",
            heartbeat_url,
            host_info.get("ip"),
        )
        response = requests.post(
            heartbeat_url, json=payload, headers=headers, timeout=10
        )
        response.raise_for_status()
        data = response.json()
        host_id = data.get("host_id")
        if not host_id:
            raise ValueError(f"Heartbeat response missing host_id: {data}")
        logger.info(
            "auto_register_host_success: host_id=%s, ip=%s",
            host_id,
            host_info.get("ip"),
        )
        return host_id
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response else None
        body = exc.response.text[:500] if exc.response else None
        logger.error(
            "auto_register_host_failed: status=%s, body=%s, error=%s",
            status_code,
            body,
            exc,
        )
        raise
    except Exception as exc:
        logger.error("auto_register_host_failed: error=%s", exc)
        raise
