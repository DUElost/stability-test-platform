"""设备 serial 合法性判定（issue #1356）。

背景：部分设备不上报真实 adb serial（USB 描述符异常/工程机），Agent 侧
统一填占位值（如 ``0123456789ABCDEF``）。占位 serial 会被**多台 host 的
adb 同时识别**——device.host_id 随各 host 心跳反复漂移——派发时被
``device_host_drift`` 保护拦截（2026-09-11 run 360/362，1 台设备阻断
368 台压测）。

此处提供统一判定，供：
- 心跳归属更新处告警（``api/routes/heartbeat.py``）
- 设备读路径标记（``DeviceOut.serial_suspect``）
"""

from __future__ import annotations

# 已知占位/无意义 serial（小写比较）。含 Android 调试桥常见默认值、
# 端口序列号占位（0123456789ABCDEF 是 MTK 工程模式/无 serial 设备的典型值）。
PLACEHOLDER_SERIALS = frozenset({
    "0123456789abcdef",
    "1234567890abcdef",
    "0000000000",
    "0000000000000000",
    "unknown",
    "none",
    "null",
    "android",
    "0123456789",
})


def is_placeholder_serial(serial: str | None) -> bool:
    """serial 是否为空/占位值（不唯一、跨 host 可重复）。"""
    if not serial:
        return True
    s = serial.strip().lower()
    if not s:
        return True
    if s in PLACEHOLDER_SERIALS:
        return True
    # 全 0 / 全 f 等单字符重复（长度 >= 8）
    if len(s) >= 8 and len(set(s)) == 1:
        return True
    return False
