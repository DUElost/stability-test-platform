"""vbus 刷机作业判据（#3892 / ADR-0063 D4）。

控制面（派发准入、认领）与 Agent host（init 残留检查推迟，D5 第 3 条）共用同一判据，
避免两侧漂移：``lifecycle.init`` 的第一个步骤是 ``script:flash_firmware`` 且参数
``trigger_mode == "vbus_cycle"``。
"""

from __future__ import annotations

from typing import Any

VBUS_FLASH_ACTION = "script:flash_firmware"
VBUS_TRIGGER_MODE = "vbus_cycle"


def is_vbus_flash_init(init: Any) -> bool:
    """``init[0]`` 是否为 ``trigger_mode=vbus_cycle`` 的 flash_firmware 步骤。"""
    if not isinstance(init, list) or not init:
        return False
    first = init[0]
    if not isinstance(first, dict) or first.get("action") != VBUS_FLASH_ACTION:
        return False
    params = first.get("params")
    return isinstance(params, dict) and params.get("trigger_mode") == VBUS_TRIGGER_MODE


def is_vbus_flash_lifecycle(lifecycle: Any) -> bool:
    """``lifecycle.init[0]`` 是否为 ``trigger_mode=vbus_cycle`` 的 flash_firmware 步骤。"""
    if not isinstance(lifecycle, dict):
        return False
    return is_vbus_flash_init(lifecycle.get("init"))
