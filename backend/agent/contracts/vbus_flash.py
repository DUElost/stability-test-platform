"""刷机作业判据（#3892 / ADR-0063 D4 & D5；#4222）。

- ``is_vbus_flash_init`` / ``is_vbus_flash_lifecycle``：控制面（派发准入、认领）与
  vbus 刷机共用判据，严格要求 ``lifecycle.init[0]`` 是 ``script:flash_firmware`` 且
  ``trigger_mode == "vbus_cycle"``（ADR-0063 D4，不可放宽至 ``adb_reboot``）。
- ``deferred_flash_init_prefix_len``：Agent 侧 init 残留检查推迟判据（#4222），
  覆盖 ``[script:flash_firmware, ...]`` 与 ``[script:flash_preflight, script:flash_firmware, ...]``
  （``vbus_cycle`` 或 ``adb_reboot`` / 缺省），刷机前不要求 ``sys.boot_completed=1``，
  刷后开机再执行残留检查。
"""

from __future__ import annotations

from typing import Any

VBUS_FLASH_ACTION = "script:flash_firmware"
VBUS_TRIGGER_MODE = "vbus_cycle"
FLASH_PREFLIGHT_ACTION = "script:flash_preflight"
ADB_FLASH_TRIGGER_MODE = "adb_reboot"
_DEFERRED_FLASH_TRIGGER_MODES = frozenset((VBUS_TRIGGER_MODE, ADB_FLASH_TRIGGER_MODE))


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


def is_deferred_flash_step(step: Any) -> bool:
    """步骤是否为可位于推迟残留检查前缀内的刷机或宿主预检步骤（#4222）。"""
    if not isinstance(step, dict) or step.get("enabled") is False:
        return False
    action = step.get("action")
    if action == FLASH_PREFLIGHT_ACTION:
        return True
    if action != VBUS_FLASH_ACTION:
        return False
    params = step.get("params")
    if params is None:
        return True
    if not isinstance(params, dict):
        return False
    trigger_mode = params.get("trigger_mode")
    if trigger_mode is None or str(trigger_mode).strip() == "":
        return True
    return str(trigger_mode).strip().lower() in _DEFERRED_FLASH_TRIGGER_MODES


def deferred_flash_init_prefix_len(init: Any) -> int:
    """返回 ``lifecycle.init`` 中在 init 残留检查之前执行的刷机前缀长度（0=不推迟）。

    支持两种整机刷机前缀（刷前不建立专项足迹，刷后系统重刷开机再执行残留回读）：
    - ``[script:flash_firmware, ...]`` → ``1``；
    - ``[script:flash_preflight, script:flash_firmware, ...]`` → ``2``。
    """
    if not isinstance(init, list) or not init:
        return 0
    first = init[0]
    if not isinstance(first, dict):
        return 0
    if first.get("action") == VBUS_FLASH_ACTION:
        return 1 if is_deferred_flash_step(first) else 0
    if (
        len(init) >= 2
        and first.get("action") == FLASH_PREFLIGHT_ACTION
        and isinstance(init[1], dict)
        and init[1].get("action") == VBUS_FLASH_ACTION
        and is_deferred_flash_step(init[1])
    ):
        return 2
    return 0
