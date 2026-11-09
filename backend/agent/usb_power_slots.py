"""本机是否是 vbus 刷机站（#3892 / ADR-0063 D1、D4 第 5 条）。

槽位映射文件由 Ansible 写入（root:root 0644），wrapper ``usb-power`` 按它查槽位。
Agent 只用它的存在与否决定认领时是否申请 vbus 名额：设备全部关机的刷机主机
``effective_slots`` 恒为 0，不申请就永远认领不到 vbus 刷机作业。
路径与 ``stp_agent_priv.USB_POWER_SLOTS_PATH`` 同源（测试锁定）。
"""

from __future__ import annotations

import os

USB_POWER_SLOTS_PATH = "/etc/stp-agent/usb-power-slots.json"
#: 每次认领最多申请的 vbus 作业数（单主机严格串行，控制面另有在跑即不发的守卫）。
VBUS_CLAIM_SLOTS = 1


def vbus_claim_slots(path: str = USB_POWER_SLOTS_PATH) -> int:
    """映射文件存在时返回 1，否则 0（不读内容；内容由 wrapper 校验）。"""
    try:
        return VBUS_CLAIM_SLOTS if os.path.isfile(path) else 0
    except OSError:
        return 0
