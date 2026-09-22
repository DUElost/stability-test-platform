"""批量开关设备滑动留痕（show_touches + pointer_location）。

控制面只下发白名单 control 命令；Agent 侧硬编码两条 settings put。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import List, Sequence

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from backend.api.schemas.device import (
    BulkSwipeTrailDeviceResult,
    BulkSwipeTrailOut,
)
from backend.models.host import Device, Host
from backend.realtime.socketio_server import (
    AgentNotConnectedError,
    AgentRpcError,
    call_agent_rpc,
)

logger = logging.getLogger(__name__)


def _rpc_timeout_seconds(serial_count: int) -> float:
    return float(max(15, min(120, serial_count * 3)))


async def bulk_set_swipe_trail(
    db: Session,
    *,
    device_ids: Sequence[int],
    enabled: bool,
) -> BulkSwipeTrailOut:
    """Group selected devices by host and RPC ``set_device_swipe_trail``."""
    devices = (
        db.query(Device)
        .options(joinedload(Device.host))
        .filter(Device.id.in_(list(device_ids)))
        .all()
    )
    if len(devices) != len(set(device_ids)):
        raise HTTPException(status_code=404, detail="one or more devices not found")

    results: List[BulkSwipeTrailDeviceResult] = []
    by_host: dict[str, list[Device]] = defaultdict(list)

    for device in devices:
        host: Host | None = device.host
        if not device.host_id or host is None:
            results.append(
                BulkSwipeTrailDeviceResult(
                    device_id=device.id,
                    serial=device.serial,
                    status="skipped",
                    error="no host",
                )
            )
            continue
        if host.status != "ONLINE":
            results.append(
                BulkSwipeTrailDeviceResult(
                    device_id=device.id,
                    serial=device.serial,
                    status="skipped",
                    error="host offline",
                )
            )
            continue
        by_host[device.host_id].append(device)

    for host_id, host_devices in by_host.items():
        serials = [d.serial for d in host_devices]
        serial_to_device = {d.serial: d for d in host_devices}
        try:
            ack = await call_agent_rpc(
                host_id,
                "control",
                {
                    "command": "set_device_swipe_trail",
                    "payload": {"serials": serials, "enabled": enabled},
                },
                timeout=_rpc_timeout_seconds(len(serials)),
            )
        except AgentNotConnectedError:
            for device in host_devices:
                results.append(
                    BulkSwipeTrailDeviceResult(
                        device_id=device.id,
                        serial=device.serial,
                        status="failed",
                        error="agent not connected",
                    )
                )
            continue
        except AgentRpcError as exc:
            for device in host_devices:
                results.append(
                    BulkSwipeTrailDeviceResult(
                        device_id=device.id,
                        serial=device.serial,
                        status="failed",
                        error=f"agent rpc failed: {exc}",
                    )
                )
            continue

        if not isinstance(ack, dict) or not ack.get("ok"):
            err = (
                (ack.get("error") if isinstance(ack, dict) else None)
                or "agent rejected"
            )
            for device in host_devices:
                results.append(
                    BulkSwipeTrailDeviceResult(
                        device_id=device.id,
                        serial=device.serial,
                        status="failed",
                        error=str(err),
                    )
                )
            continue

        ack_results = ack.get("results") or []
        seen: set[str] = set()
        for row in ack_results:
            if not isinstance(row, dict):
                continue
            serial = row.get("serial")
            if not isinstance(serial, str) or serial not in serial_to_device:
                continue
            seen.add(serial)
            device = serial_to_device[serial]
            if row.get("ok"):
                results.append(
                    BulkSwipeTrailDeviceResult(
                        device_id=device.id,
                        serial=serial,
                        status="ok",
                    )
                )
            else:
                results.append(
                    BulkSwipeTrailDeviceResult(
                        device_id=device.id,
                        serial=serial,
                        status="failed",
                        error=str(row.get("error") or "adb failed")[:200],
                    )
                )
        for serial, device in serial_to_device.items():
            if serial not in seen:
                results.append(
                    BulkSwipeTrailDeviceResult(
                        device_id=device.id,
                        serial=serial,
                        status="failed",
                        error="missing agent result",
                    )
                )

    ok_n = sum(1 for r in results if r.status == "ok")
    failed_n = sum(1 for r in results if r.status == "failed")
    skipped_n = sum(1 for r in results if r.status == "skipped")
    logger.info(
        "bulk_swipe_trail enabled=%s ok=%d failed=%d skipped=%d",
        enabled,
        ok_n,
        failed_n,
        skipped_n,
    )
    return BulkSwipeTrailOut(
        enabled=enabled,
        ok=ok_n,
        failed=failed_n,
        skipped=skipped_n,
        results=results,
    )
