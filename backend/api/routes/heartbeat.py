from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...core.database import get_db
from ...models.schemas import Host, HostStatus, Device, DeviceStatus
from ..schemas import HeartbeatIn

router = APIRouter(prefix="/api/v1", tags=["heartbeat"])


@router.post("/heartbeat")
def heartbeat(payload: HeartbeatIn, db: Session = Depends(get_db)):
    host = db.get(Host, payload.host_id)

    # ⚠️ 临时方案：如果主机不存在，自动创建（用于测试）
    # 生产环境应要求先通过 /api/v1/hosts 创建
    if not host:
        # 先尝试通过 IP 查找现有主机
        host_info = payload.host or {}
        ip = host_info.get("ip")
        if ip:
            host = db.query(Host).filter(Host.ip == ip).first()

        # 还是没有就创建新的
        if not host:
            hostname = host_info.get("hostname", f"Host-{payload.host_id}")
            # 生成唯一名称
            base_name = hostname
            counter = 1
            while db.query(Host).filter(Host.name == hostname).first():
                hostname = f"{base_name}-{counter}"
                counter += 1

            host = Host(
                name=hostname,
                ip=ip or f"unknown-{payload.host_id}",
                status=HostStatus(payload.status),
                ssh_port=22,
            )
            db.add(host)
            db.flush()  # 获取分配的 ID
            db.commit()

    # 更新主机状态
    host.status = HostStatus(payload.status)
    host.last_heartbeat = datetime.utcnow()
    host.mount_status = payload.mount_status
    # 保存系统统计数据（CPU、内存、磁盘）
    host.extra = payload.extra if payload.extra else {}

    # 处理设备数组
    devices_data = getattr(payload, 'devices', None) or []
    seen_serials = set()

    for dev_data in devices_data:
        serial = dev_data.get("serial")
        if not serial:
            continue

        seen_serials.add(serial)

        # 查找或创建设备
        device = db.query(Device).filter(Device.serial == serial).first()

        # 提取设备监控数据
        extra_data = {
            "battery_level": dev_data.get("battery_level"),
            "temperature": dev_data.get("temperature"),
            "network_latency": dev_data.get("network_latency"),
        }

        if not device:
            device = Device(
                serial=serial,
                host_id=host.id,
                model=dev_data.get("model"),
                status=DeviceStatus.ONLINE if dev_data.get("state") == "device" else DeviceStatus.OFFLINE,
                extra=extra_data,
            )
            db.add(device)
        else:
            # 更新现有设备
            device.host_id = host.id
            if dev_data.get("model"):
                device.model = dev_data.get("model")
            # 更新状态
            if dev_data.get("state") == "device":
                device.status = DeviceStatus.ONLINE
            else:
                device.status = DeviceStatus.OFFLINE
            # 更新监控数据
            device.extra = extra_data

        # 更新最后见到时间
        device.last_seen = datetime.utcnow()

    db.commit()
    return {"ok": True, "host_id": host.id, "devices_count": len(seen_serials)}
