"""刷机作业判据（#3892 / ADR-0063 D4 & D5；#4222）。

- ``is_vbus_flash_init`` / ``is_vbus_flash_lifecycle``：控制面（派发准入、认领）与
  vbus 刷机共用判据，严格要求 ``lifecycle.init[0]`` 是 ``script:flash_firmware`` 且
  ``trigger_mode == "vbus_cycle"``（ADR-0063 D4，不可放宽至 ``adb_reboot``）。
- ``deferred_flash_init_prefix_len``：Agent 侧 init 残留检查推迟判据（#4222），
  覆盖 ``[script:flash_firmware, ...]`` 与 ``[script:flash_preflight, script:flash_firmware, ...]``
  （``vbus_cycle`` 或 ``adb_reboot`` / 缺省），以及紧随 ``flash_firmware`` 之后的可选
  ``script:oobe_skip``（避免刷后滞留 OOBE 触发 ``shutdown,provisioning_shutdown_req``
  自动关机，与 ADR-0063 D3 第 6 条 ``vbus_cycle`` 刷后先跳 OOBE 再核验对齐）。
"""

from __future__ import annotations

from typing import Any

VBUS_FLASH_ACTION = "script:flash_firmware"
VBUS_TRIGGER_MODE = "vbus_cycle"
FLASH_PREFLIGHT_ACTION = "script:flash_preflight"
OOBE_SKIP_ACTION = "script:oobe_skip"
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
    """步骤是否为可位于推迟残留检查前缀内的刷机、宿主预检或 OOBE 跳过步骤（#4222）。"""
    if not isinstance(step, dict) or step.get("enabled") is False:
        return False
    action = step.get("action")
    if action in (FLASH_PREFLIGHT_ACTION, OOBE_SKIP_ACTION):
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

    支持两种整机刷机前缀及紧随刷机后的可选 ``script:oobe_skip``（均不建立专项足迹，
    刷完新包并跳过 OOBE 后再执行残留回读，防止 OOBE 触发 ``provisioning_shutdown_req``）：
    - ``[script:flash_firmware, ...]`` → ``1``（若后接 ``script:oobe_skip`` → ``2``）；
    - ``[script:flash_preflight, script:flash_firmware, ...]`` → ``2``（若后接 ``script:oobe_skip`` → ``3``）。
    """
    if not isinstance(init, list) or not init:
        return 0
    first = init[0]
    if not isinstance(first, dict):
        return 0
    prefix = 0
    if first.get("action") == VBUS_FLASH_ACTION:
        prefix = 1 if is_deferred_flash_step(first) else 0
    elif (
        len(init) >= 2
        and first.get("action") == FLASH_PREFLIGHT_ACTION
        and isinstance(init[1], dict)
        and init[1].get("action") == VBUS_FLASH_ACTION
        and is_deferred_flash_step(init[1])
    ):
        prefix = 2
    if (
        prefix > 0
        and len(init) > prefix
        and isinstance(init[prefix], dict)
        and init[prefix].get("action") == OOBE_SKIP_ACTION
    ):
        prefix += 1
    return prefix
