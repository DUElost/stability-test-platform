from datetime import datetime, timedelta
import logging
import os
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.models.schemas import Host, HostStatus, Device, DeviceStatus
from backend.api.schemas import HeartbeatIn
from backend.api.routes.auth import verify_agent_secret

router = APIRouter(prefix="/api/v1", tags=["heartbeat"])
logger = logging.getLogger(__name__)

# 心跳快照降采样间隔（秒）：减少数据库写入压力
SNAPSHOT_INTERVAL_SECONDS = int(os.getenv("DEVICE_SNAPSHOT_INTERVAL", "30"))


def _update_if_not_none(device: Device, field: str, value) -> bool:
    """Helper to update device field only if value is not None"""
    if value is None:
        return False
    setattr(device, field, value)
    return True


def _mark_missing_devices_offline(
    db: Session, host_id: int, seen_serials: set, timeout: int = 180
) -> List[Device]:
    """Mark devices not seen in current heartbeat as offline"""
    offline_threshold = datetime.utcnow() - timedelta(seconds=timeout)
    missing_devices = (
        db.query(Device)
        .filter(
            Device.host_id == host_id,
            or_(Device.last_seen.is_(None), Device.last_seen < offline_threshold),
        )
        .all()
    )
    for device in missing_devices:
        if device.serial not in seen_serials:
            device.status = DeviceStatus.OFFLINE
            device.adb_connected = False
            device.adb_state = "offline"
            # Dispatch DEVICE_OFFLINE notification
            from backend.services.notification_service import dispatch_notification_async
            dispatch_notification_async("DEVICE_OFFLINE", {
                "device_serial": device.serial,
                "device_id": device.id,
                "host_id": host_id,
            })
    return missing_devices


@router.post("/heartbeat")
async def heartbeat(payload: HeartbeatIn, db: Session = Depends(get_db), _: bool = Depends(verify_agent_secret)):
    host = db.get(Host, payload.host_id)

    # Auto-create host if not exists (for testing)
    if not host:
        host_info = payload.host or {}
        ip = host_info.get("ip")
        if ip:
            host = db.query(Host).filter(Host.ip == ip).first()

        if not host:
            host_name = ip or f"Host-{payload.host_id}"

            host = Host(
                name=host_name,
                ip=ip or f"unknown-{payload.host_id}",
                status=HostStatus(payload.status),
                ssh_port=22,
            )
            db.add(host)
            db.flush()
            db.commit()

    # Update host status
    host.status = HostStatus(payload.status)
    now = datetime.utcnow()
    host.last_heartbeat = now
    host.mount_status = payload.mount_status

    # Merge extra data without losing system stats
    host_extra = {}
    if payload.extra:
        host_extra.update(payload.extra)
    if payload.host:
        host_extra["host"] = payload.host
    host.extra = host_extra

    # Process devices array
    devices_data = getattr(payload, "devices", None) or []
    seen_serials = set()
    ws_device_updates: List[Dict[str, Any]] = []

    if devices_data:
        # Batch query existing devices to avoid N+1
        serials = [d.get("serial") for d in devices_data if d.get("serial")]
        existing_devices = {}
        if serials:
            for device in (
                db.query(Device).filter(Device.serial.in_(serials)).all()
            ):
                existing_devices[device.serial] = device

        now = datetime.utcnow()

        for dev_data in devices_data:
            serial = dev_data.get("serial")
            if not serial:
                continue

            seen_serials.add(serial)

            device = existing_devices.get(serial)

            if not device:
                device = Device(
                    serial=serial,
                    host_id=host.id,
                    model=dev_data.get("model"),
                )
                db.add(device)
                existing_devices[serial] = device

            device.host_id = host.id
            if dev_data.get("model") is not None:
                device.model = dev_data.get("model")

            # Update ADB connection state
            device.adb_state = dev_data.get("adb_state", "unknown")
            device.adb_connected = bool(dev_data.get("adb_connected", False))

            # Debug log for ADB state
            logger.info(f"device_adb_update: serial={serial}, adb_state={device.adb_state}, adb_connected={device.adb_connected}, network_latency={dev_data.get('network_latency')}")

            # Update hardware info
            hardware_updated = False
            hardware_updated |= _update_if_not_none(
                device, "battery_level", dev_data.get("battery_level")
            )
            hardware_updated |= _update_if_not_none(
                device, "battery_temp", dev_data.get("battery_temp")
            )
            hardware_updated |= _update_if_not_none(
                device, "temperature", dev_data.get("temperature")
            )
            hardware_updated |= _update_if_not_none(
                device, "wifi_rssi", dev_data.get("wifi_rssi")
            )
            hardware_updated |= _update_if_not_none(
                device, "wifi_ssid", dev_data.get("wifi_ssid")
            )
            hardware_updated |= _update_if_not_none(
                device, "network_latency", dev_data.get("network_latency")
            )
            if hardware_updated:
                device.hardware_updated_at = now

            # Update system resources
            _update_if_not_none(device, "cpu_usage", dev_data.get("cpu_usage"))
            _update_if_not_none(device, "mem_total", dev_data.get("mem_total"))
            _update_if_not_none(device, "mem_used", dev_data.get("mem_used"))
            _update_if_not_none(device, "disk_total", dev_data.get("disk_total"))
            _update_if_not_none(device, "disk_used", dev_data.get("disk_used"))

            # Record metric snapshot for historical tracking (with downsampling)
            # Only write snapshot every SNAPSHOT_INTERVAL_SECONDS to reduce DB load
            # NOTE: snapshot interval check uses device.last_seen BEFORE updating it
            if device.adb_connected:
                should_snapshot = True
                if device.last_seen:
                    time_since_last = (now - device.last_seen).total_seconds()
                    should_snapshot = time_since_last >= SNAPSHOT_INTERVAL_SECONDS

                if should_snapshot:
                    from backend.models.schemas import DeviceMetricSnapshot
                    snapshot = DeviceMetricSnapshot(
                        device_id=device.id,
                        timestamp=now,
                        battery_level=device.battery_level,
                        temperature=device.temperature,
                        network_latency=device.network_latency,
                        cpu_usage=device.cpu_usage,
                        mem_used=device.mem_used,
                    )
                    db.add(snapshot)

            # Update last_seen AFTER snapshot check to avoid self-overwrite bug
            device.last_seen = now

            # Business status: based on ADB connection and lock
            if not device.adb_connected:
                device.status = DeviceStatus.OFFLINE
                logger.info(f"device_status_offline: serial={serial}, reason=adb_connected_false")
            elif device.lock_run_id:
                device.status = DeviceStatus.BUSY
                logger.info(f"device_status_busy: serial={serial}, lock_run_id={device.lock_run_id}")
            else:
                device.status = DeviceStatus.ONLINE
                logger.info(f"device_status_online: serial={serial}, adb_connected=true, no_lock")

            ws_device_updates.append(
                {
                    "serial": device.serial,
                    "status": device.status.value,
                    "battery_level": device.battery_level,
                    "temperature": device.temperature,
                    "network_latency": device.network_latency,
                    "adb_state": device.adb_state,
                    "adb_connected": device.adb_connected,
                    "host_id": device.host_id,
                    "last_seen": device.last_seen.isoformat() if device.last_seen else None,
                }
            )

    # Mark missing devices as offline
    missing_devices = _mark_missing_devices_offline(db, host.id, seen_serials, timeout=180)
    for device in missing_devices:
        if device.serial in seen_serials:
            continue
        ws_device_updates.append(
            {
                "serial": device.serial,
                "status": device.status.value,
                "battery_level": device.battery_level,
                "temperature": device.temperature,
                "network_latency": device.network_latency,
                "adb_state": device.adb_state,
                "adb_connected": device.adb_connected,
                "host_id": device.host_id,
                "last_seen": device.last_seen.isoformat() if device.last_seen else None,
            }
        )

    db.commit()

    if ws_device_updates:
        try:
            from backend.api.routes.websocket import manager

            for update in ws_device_updates:
                await manager.broadcast(
                    "/ws/dashboard",
                    {"type": "DEVICE_UPDATE", "payload": update},
                )
        except Exception as exc:
            logger.warning(f"device_update_broadcast_failed: {exc}")

    return {"ok": True, "host_id": host.id, "devices_count": len(seen_serials)}
