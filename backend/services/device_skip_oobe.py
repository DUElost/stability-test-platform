"""设备页批量快捷跳过 OOBE（复用脚本库 ``oobe_skip``）。

控制面从脚本库选取最新已启用的 ``oobe_skip`` 版本，按 ``host_id`` 分组并经白名单
Agent control ``skip_device_oobe`` 并发执行；Agent 侧按 ADR-0051 包身份解析并
拉起同一个 ``oobe_skip`` 脚本包，不在控制面或 Agent 复制 OOBE ADB 配方。
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from typing import List, Sequence

from sqlalchemy.orm import Session, joinedload

from backend.api.schemas.device import (
    BulkSkipOobeDeviceResult,
    BulkSkipOobeOut,
)
from backend.models.host import Device, Host
from backend.models.script import Script
from backend.realtime.socketio_server import (
    AgentNotConnectedError,
    AgentRpcError,
    call_agent_rpc,
)
from backend.services.errors import Conflict, NotFound
from backend.services.script_retirement import version_key

logger = logging.getLogger(__name__)

_OOBE_SKIP_SCRIPT_NAME = "oobe_skip"
# 快捷操作面向已连机设备：把刷机后等待开机预算收紧到 5s、核验重试收紧到 1 次，
# 避免批量调用被离线设备单台拖住 120s+。
_QUICK_SKIP_WAIT_SECONDS = 5
_QUICK_SKIP_VERIFY_RETRIES = 1
_AGENT_PARALLEL_WORKERS = 8


def _rpc_timeout_seconds(serial_count: int) -> float:
    batches = max(1, math.ceil(serial_count / _AGENT_PARALLEL_WORKERS))
    return float(max(60, min(300, batches * 95 + 15)))


def _resolve_active_oobe_skip_script(db: Session) -> Script:
    """选取脚本库中最新且已启用的 ``oobe_skip`` 脚本行。"""
    candidates = (
        db.query(Script)
        .filter(
            Script.name == _OOBE_SKIP_SCRIPT_NAME,
            Script.is_active.is_(True),
        )
        .all()
    )
    if not candidates:
        raise Conflict(
            {
                "code": "OOBE_SKIP_SCRIPT_NOT_FOUND",
                "message": "脚本库中未找到已启用的 oobe_skip 脚本，请先在脚本管理页扫描并启用该脚本",
            }
        )

    # 优先选择已登记 package_sha256 的最高语义版本（ADR-0051 strict 包执行）。
    chosen = max(
        candidates,
        key=lambda row: (bool(row.package_sha256), version_key(row.version)),
    )
    if not chosen.package_sha256:
        raise Conflict(
            {
                "code": "OOBE_SKIP_SCRIPT_UNPACKAGED",
                "message": f"脚本 oobe_skip@{chosen.version} 尚未登记包校验，请先在脚本管理页执行扫描",
            }
        )
    return chosen


def _build_quick_skip_params(default_params: dict | None) -> dict:
    params = dict(default_params or {})
    params["wait_for_device_seconds"] = _QUICK_SKIP_WAIT_SECONDS
    params["verify_retries"] = _QUICK_SKIP_VERIFY_RETRIES
    return params


async def _dispatch_host_skip_oobe(
    host_id: str,
    host_devices: List[Device],
    *,
    script_payload: dict,
    step_params: dict,
) -> List[BulkSkipOobeDeviceResult]:
    serials = [d.serial for d in host_devices]
    serial_to_device = {d.serial: d for d in host_devices}
    results: List[BulkSkipOobeDeviceResult] = []
    try:
        ack = await call_agent_rpc(
            host_id,
            "control",
            {
                "command": "skip_device_oobe",
                "payload": {
                    "serials": serials,
                    "script": script_payload,
                    "params": step_params,
                },
            },
            timeout=_rpc_timeout_seconds(len(serials)),
        )
    except AgentNotConnectedError:
        return [
            BulkSkipOobeDeviceResult(
                device_id=device.id,
                serial=device.serial,
                status="failed",
                error="agent not connected",
            )
            for device in host_devices
        ]
    except AgentRpcError as exc:
        return [
            BulkSkipOobeDeviceResult(
                device_id=device.id,
                serial=device.serial,
                status="failed",
                error=f"agent rpc failed: {exc}",
            )
            for device in host_devices
        ]

    if not isinstance(ack, dict) or not ack.get("ok"):
        err = (
            (ack.get("error") if isinstance(ack, dict) else None)
            or "agent rejected"
        )
        return [
            BulkSkipOobeDeviceResult(
                device_id=device.id,
                serial=device.serial,
                status="failed",
                error=str(err)[:200],
            )
            for device in host_devices
        ]

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
                BulkSkipOobeDeviceResult(
                    device_id=device.id,
                    serial=serial,
                    status="ok",
                )
            )
        else:
            results.append(
                BulkSkipOobeDeviceResult(
                    device_id=device.id,
                    serial=serial,
                    status="failed",
                    error=str(row.get("error") or "oobe_skip failed")[:200],
                )
            )

    for serial, device in serial_to_device.items():
        if serial not in seen:
            results.append(
                BulkSkipOobeDeviceResult(
                    device_id=device.id,
                    serial=serial,
                    status="failed",
                    error="missing agent result",
                )
            )
    return results


async def bulk_skip_device_oobe(
    db: Session,
    *,
    device_ids: Sequence[int],
) -> BulkSkipOobeOut:
    """Group selected devices by host and RPC ``skip_device_oobe`` concurrently."""
    devices = (
        db.query(Device)
        .options(joinedload(Device.host))
        .filter(Device.id.in_(list(device_ids)))
        .all()
    )
    if len(devices) != len(set(device_ids)):
        raise NotFound("one or more devices not found")

    script = _resolve_active_oobe_skip_script(db)
    script_payload = {
        "name": script.name,
        "version": script.version,
        "script_type": script.script_type,
        "nfs_path": script.nfs_path,
        "package_sha256": script.package_sha256,
    }
    step_params = _build_quick_skip_params(script.default_params)

    results: List[BulkSkipOobeDeviceResult] = []
    by_host: dict[str, list[Device]] = defaultdict(list)

    for device in devices:
        host: Host | None = device.host
        if not device.host_id or host is None:
            results.append(
                BulkSkipOobeDeviceResult(
                    device_id=device.id,
                    serial=device.serial,
                    status="skipped",
                    error="no host",
                )
            )
            continue
        if host.status != "ONLINE":
            results.append(
                BulkSkipOobeDeviceResult(
                    device_id=device.id,
                    serial=device.serial,
                    status="skipped",
                    error="host offline",
                )
            )
            continue
        if device.status == "BUSY":
            results.append(
                BulkSkipOobeDeviceResult(
                    device_id=device.id,
                    serial=device.serial,
                    status="skipped",
                    error="device busy",
                )
            )
            continue
        by_host[device.host_id].append(device)

    if by_host:
        per_host_results = await asyncio.gather(
            *(
                _dispatch_host_skip_oobe(
                    host_id,
                    host_devices,
                    script_payload=script_payload,
                    step_params=step_params,
                )
                for host_id, host_devices in by_host.items()
            )
        )
        for batch in per_host_results:
            results.extend(batch)

    ok_n = sum(1 for r in results if r.status == "ok")
    failed_n = sum(1 for r in results if r.status == "failed")
    skipped_n = sum(1 for r in results if r.status == "skipped")
    logger.info(
        "bulk_skip_oobe script_version=%s ok=%d failed=%d skipped=%d",
        script.version,
        ok_n,
        failed_n,
        skipped_n,
    )
    return BulkSkipOobeOut(
        script_version=script.version,
        ok=ok_n,
        failed=failed_n,
        skipped=skipped_n,
        results=results,
    )
