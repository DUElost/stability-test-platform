"""心跳守护线程 —— 设备发现 + HTTP 心跳 + 可选的 WS 推送。

提取自 backend.agent.main，供 main.py barrel re-export。
"""

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import device_discovery
from .heartbeat import send_heartbeat

logger = logging.getLogger(__name__)


class HeartbeatThread:
    """Daemon thread: device discovery + heartbeat every poll_interval seconds.

    Channel design (Phase 0 state-closure):
      - HTTP POST /api/v1/heartbeat is the SOLE authority for persisting
        host and device state to the DB (last_heartbeat, device.last_seen,
        battery, temperature, etc.).
      - WS heartbeat is supplementary: pushes real-time device metrics to
        dashboard subscribers for instant UI refresh.  The server-side WS
        handler does NOT write to the DB.
    """

    def __init__(
        self,
        api_url: str,
        host_id: str,
        adb_path: str,
        mount_points: List[str],
        host_info: Dict[str, Any],
        poll_interval: float,
        ws_client=None,
        catalog_versions: Optional[Callable[[], Dict[str, str]]] = None,
        on_scripts_outdated: Optional[Callable[[], None]] = None,
        # ADR-0019 Phase 1
        max_concurrent_jobs: int = 2,
        get_active_job_count: Optional[Callable[[], int]] = None,
    ):
        self._api_url = api_url
        self._host_id = host_id
        self._adb_path = adb_path
        self._mount_points = mount_points
        self._host_info = host_info
        self._poll_interval = poll_interval
        self._ws_client = ws_client
        self._catalog_versions = catalog_versions
        self._on_scripts_outdated = on_scripts_outdated
        self._max_concurrent_jobs = max_concurrent_jobs
        self._get_active_job_count = get_active_job_count
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest_devices: List[Dict[str, Any]] = []
        self._devices_lock = threading.Lock()

    @property
    def latest_devices(self) -> List[Dict[str, Any]]:
        """Return the most recent device list (thread-safe)."""
        with self._devices_lock:
            return list(self._latest_devices)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="heartbeat"
        )
        self._thread.start()
        logger.info("heartbeat_thread_started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("heartbeat_thread_stopped")

    def _loop(self) -> None:
        self._tick()

        while not self._stop_event.is_set():
            self._stop_event.wait(self._poll_interval)
            if self._stop_event.is_set():
                break
            self._tick()

    def _tick(self) -> None:
        """Single heartbeat cycle: discover → HTTP POST (authoritative) → WS push (display-only)."""
        devices_list = []
        try:
            discovered = device_discovery.discover_devices(self._adb_path)
            for dev in discovered:
                info = device_discovery.collect_device_info(
                    self._adb_path, dev["serial"]
                )
                device_data = {
                    "serial": dev["serial"],
                    "model": dev.get("model"),
                    "state": dev["adb_state"],
                    "adb_state": info.get("adb_state", dev.get("adb_state", "unknown")),
                    "adb_connected": info.get("adb_connected", False),
                    "battery_level": info.get("battery_level"),
                    "temperature": info.get("temperature"),
                    "network_latency": info.get("network_latency"),
                    "build_display_id": info.get("build_display_id"),
                }
                devices_list.append(device_data)
                logger.info(
                    f"device_collected: {dev['serial']}, "
                    f"adb_connected={info.get('adb_connected')}, "
                    f"network_latency={info.get('network_latency')}, "
                    f"battery={info.get('battery_level')}, temp={info.get('temperature')}"
                )
            logger.debug(f"discovered_{len(devices_list)}_devices")
        except Exception as e:
            logger.warning(f"device_discovery_failed: {e}")

        with self._devices_lock:
            self._latest_devices = devices_list

        versions = self._catalog_versions() if self._catalog_versions else {}

        # ADR-0019 Phase 1: compute capacity for heartbeat
        active_count = self._get_active_job_count() if self._get_active_job_count else 0
        available_slots = max(0, self._max_concurrent_jobs - active_count)
        online_healthy = sum(
            1 for d in devices_list
            if d.get("adb_connected") is True
            and d.get("adb_state") not in ("offline", "unknown", "")
        )

        response = send_heartbeat(
            self._api_url,
            self._host_id,
            self._mount_points,
            host_info=self._host_info,
            devices=devices_list,
            tool_catalog_version=versions.get("tool_catalog_version", ""),
            script_catalog_version=versions.get("script_catalog_version", ""),
            available_slots=available_slots,
            max_concurrent_jobs=self._max_concurrent_jobs,
            online_healthy_devices=online_healthy,
        )
        if response and response.get("script_catalog_outdated") and self._on_scripts_outdated:
            try:
                self._on_scripts_outdated()
            except Exception as exc:
                logger.warning("script_catalog_refresh_failed: %s", exc)

        if self._ws_client and self._ws_client.connected:
            try:
                from .heartbeat import check_mounts
                from .system_monitor import collect_system_stats

                stats = collect_system_stats()
                stats["devices"] = devices_list
                stats["mount_status"] = check_mounts(self._mount_points)
                self._ws_client.send_heartbeat(stats)
                logger.debug("heartbeat_ws_push_sent")
            except Exception as e:
                logger.debug("heartbeat_ws_push_failed: %s", e)
