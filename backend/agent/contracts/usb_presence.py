"""vbus 槽位 USB 在位分类（#3918 / ADR-0063 D7）。

Agent 按槽位映射读 sysfs 上报每个口的 ``idVendor:idProduct``；控制面与前端用本函数把它
分成四类，避免两侧各写一份判据。纯观测，不参与派发、认领或刷机门禁。
"""

from __future__ import annotations

from typing import Any

#: MTK 设备关机（充电）态的枚举（Owner 现场 lsusb，ADR-0063 §1）。
VID_PID_POWERED_OFF = "0e8d:2046"
#: 开机后的正常 ADB 态（ADR-0063 D3 第 11 条按开机态处理）。
VID_PID_ADB_MODE = "0e8d:201c"

STATE_POWERED_OFF = "powered_off"
STATE_ADB_MODE = "adb_mode"
STATE_OTHER = "other"
STATE_ABSENT = "absent"
USB_PRESENCE_STATES = (STATE_POWERED_OFF, STATE_ADB_MODE, STATE_OTHER, STATE_ABSENT)


def classify_usb_presence(vid_pid: Any) -> str:
    """``vid_pid``（小写 ``vvvv:pppp`` 或 None）→ 四类之一。"""
    if not isinstance(vid_pid, str) or not vid_pid.strip():
        return STATE_ABSENT
    value = vid_pid.strip().lower()
    if value == VID_PID_POWERED_OFF:
        return STATE_POWERED_OFF
    if value == VID_PID_ADB_MODE:
        return STATE_ADB_MODE
    return STATE_OTHER
