from typing import Iterable, Optional, Dict, Any, List
import logging
import os

import requests

logger = logging.getLogger(__name__)


def check_mounts(mount_points: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """
    简化的挂载点检查，直接在 agent 模块内实现
    """
    result = {}
    for point in mount_points:
        ok = os.path.ismount(point) and os.path.exists(point)
        result[point] = {"ok": ok, "path": point}
    return result


def send_heartbeat(
    api_url: str,
    host_id: int,
    mount_points: Optional[Iterable[str]] = None,
    status: str = "ONLINE",
    host_info: Optional[Dict[str, Any]] = None,
    devices: Optional[List[Dict[str, Any]]] = None,
    tool_catalog_version: str = "",
    script_catalog_version: str = "",
    # ADR-0019 Phase 1: capacity reporting
    available_slots: int = 0,
    max_concurrent_jobs: int = 2,
    online_healthy_devices: int = 0,
) -> Optional[Dict[str, Any]]:
    """发送心跳到服务器，包含系统统计信息和设备数据

    Returns:
        成功时返回响应字典 (含 host_id, devices_count 等)，失败返回 None
    """
    from .system_monitor import collect_system_stats

    mount_status = check_mounts(mount_points or [])
    system_stats = collect_system_stats()

    # 记录设备数据用于调试
    if devices:
        for dev in devices:
            logger.info(f"heartbeat_device: serial={dev.get('serial')}, network_latency={dev.get('network_latency')}, battery={dev.get('battery_level')}, temp={dev.get('temperature')}")

    payload = {
        "host_id": host_id,
        "status": status,
        "mount_status": mount_status,
        "tool_catalog_version": tool_catalog_version,
        "script_catalog_version": script_catalog_version,
        "extra": system_stats,
        "host": host_info,  # 包含主机信息用于自动创建
        "devices": devices or [],  # 设备列表
        # ADR-0019 Phase 1
        "capacity": {
            "available_slots": available_slots,
            "max_concurrent_jobs": max_concurrent_jobs,
            "online_healthy_devices": online_healthy_devices,
        },
    }

    agent_secret = os.getenv("AGENT_SECRET", "")
    headers = {}
    if agent_secret:
        headers["x-agent-secret"] = agent_secret

    try:
        resp = requests.post(f"{api_url}/api/v1/heartbeat", json=payload, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"heartbeat_success: host_id={host_id}, devices_count={len(devices or [])}, response={data}")
        return data
    except Exception as e:
        logger.error(f"heartbeat_failed: {e}, payload_devices={len(devices or [])}")
        return None
