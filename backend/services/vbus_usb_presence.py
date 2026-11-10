"""vbus 槽位 USB 在位（#3918 / ADR-0063 D7 第 5 条）：从主机心跳派生，不落库。

Agent 在存在槽位映射文件的主机上，把 ``{serial: [slot, vid_pid|null]}`` 放进心跳
``capacity.usb_power_slots``，控制面原样存进 ``host.extra["capacity"]``。本模块只读这份
数据，按 serial 给出设备的在位状态；分类只用共享契约 ``classify_usb_presence``。
纯观测：不参与派发准入或认领（D7 第 3 条）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from backend.agent.contracts.usb_presence import STATE_POWERED_OFF, classify_usb_presence
from backend.core.job_timeout_config import HOST_HEARTBEAT_TIMEOUT_SECONDS


def host_is_live(host: Any, now: Optional[datetime] = None) -> bool:
    """主机此刻是否在线：状态为 ONLINE **且**心跳未超时（与主机详情的离线判据同一阈值）。

    复核 #3921 第 1 轮 P2：只看已存的 ``Host.status`` 会把心跳早已过期、状态尚未被
    归一化的主机当成在线，徽标标成新鲜，陈旧豁免也会放出长期失联主机上的设备。
    徽标的 ``host_online`` 与设备列表的陈旧豁免都必须用这一个判据。
    """
    if host is None or getattr(host, "status", None) != "ONLINE":
        return False
    last = getattr(host, "last_heartbeat", None)
    if last is None:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)  # 兼容历史/测试数据中的 naive 时间
    now = now or datetime.now(timezone.utc)
    return last >= now - timedelta(seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS)


def host_usb_power_slots(host: Any) -> Optional[Dict[str, Any]]:
    """主机最近一次心跳上报的槽位在位表；未上报或形态不对时为 None。"""
    extra = getattr(host, "extra", None)
    capacity = extra.get("capacity") if isinstance(extra, dict) else None
    slots = capacity.get("usb_power_slots") if isinstance(capacity, dict) else None
    return slots if isinstance(slots, dict) else None


def usb_presence_for(host: Any, serial: str) -> Optional[Dict[str, Any]]:
    """设备在其主机槽位表里的在位状态；主机未上报或设备不在映射里时为 None。"""
    slots = host_usb_power_slots(host) if host is not None else None
    entry = slots.get(serial) if slots else None
    if not isinstance(entry, (list, tuple)) or len(entry) != 2:
        return None
    slot, vid_pid = entry
    if not isinstance(slot, str):
        return None
    vid_pid = vid_pid if isinstance(vid_pid, str) else None
    reported_at: Optional[datetime] = getattr(host, "last_heartbeat", None)
    return {
        "slot": slot,
        "vid_pid": vid_pid,
        "state": classify_usb_presence(vid_pid),
        "reported_at": reported_at,
        "host_online": host_is_live(host),
    }


def powered_off_slot_keys(hosts: Iterable[Any]) -> Set[Tuple[str, str]]:
    """在线主机上报为关机态的 ``(host_id, serial)`` 集合（设备列表的陈旧豁免用）。"""
    keys: Set[Tuple[str, str]] = set()
    now = datetime.now(timezone.utc)
    for host in hosts:
        if not host_is_live(host, now):
            continue
        for serial, entry in (host_usb_power_slots(host) or {}).items():
            if (isinstance(serial, str) and isinstance(entry, (list, tuple)) and len(entry) == 2
                    and classify_usb_presence(entry[1]) == STATE_POWERED_OFF):
                keys.add((host.id, serial))
    return keys
