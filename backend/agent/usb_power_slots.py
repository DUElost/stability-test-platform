"""本机是否是 vbus 刷机站（#3892 / ADR-0063 D1、D4 第 5 条）。

槽位映射文件由 Ansible 写入（root:root 0644），wrapper ``usb-power`` 按它查槽位。
Agent 只用它的存在与否决定认领时是否申请 vbus 名额：设备全部关机的刷机主机
``effective_slots`` 恒为 0，不申请就永远认领不到 vbus 刷机作业。
路径与 ``stp_agent_priv.USB_POWER_SLOTS_PATH`` 同源（测试锁定）。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

USB_POWER_SLOTS_PATH = "/etc/stp-agent/usb-power-slots.json"
#: 每次认领最多申请的 vbus 作业数（单主机严格串行，控制面另有在跑即不发的守卫）。
VBUS_CLAIM_SLOTS = 1


def vbus_claim_slots(path: str = USB_POWER_SLOTS_PATH) -> int:
    """映射文件存在时返回 1，否则 0（不读内容；内容由 wrapper 校验）。"""
    try:
        return VBUS_CLAIM_SLOTS if os.path.isfile(path) else 0
    except OSError:
        return 0


# ── #3918 / ADR-0063 D7：槽位 USB 在位观测 ─────────────────────────────────────
#
# 关机态 0e8d:2046 在 lsusb 里没有 serial，只有映射文件能把 USB 口对到设备行。
# Agent 每次心跳按映射读一次 sysfs（agent 用户可读，不需要 root，不调用 wrapper），
# 上报 ``{serial: [slot, vid_pid|None]}``。纯观测：不参与 capacity、健康闸门或认领。
# 映射文件的权威校验在 wrapper（``stp_agent_priv.parse_usb_power_slots``）。这里按同一套
# D1 数据约束整份判定（复核 #3919 第 1 轮）：wrapper 拒绝的映射，这里同样不上报，否则会
# 把两个 serial 对到同一物理口、在页面上误判关机态；wrapper 接受的映射，这里也必须逐条
# 上报（不另设主机级条数上限，只有与 wrapper 相同的 64 KiB 文件上限）。任何问题都按
# 「不上报」处理，不影响心跳；权限与属主检查仍只在 wrapper。两者一致性由差分测试锁定。

_SYSFS_USB_DEVICES = "/sys/bus/usb/devices"
# 与 stp_agent_priv 的 _USB_PORT_RE / _USB_POWER_SERIAL_RE / USB_POWER_MAX_PORT /
# MAX_USB_POWER_SLOTS_BYTES 同值（测试锁定）。
_HUB_LOCATION_RE = re.compile(r"^[0-9]+-[0-9]+(?:\.[0-9]+)*\Z")
_SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}\Z")
_MAX_PORT = 16
_MAX_FILE_BYTES = 64 * 1024
_HEX4_RE = re.compile(r"^[0-9a-f]{4}\Z")


def _parse_slot_entries(body: str) -> Optional[List[Tuple[str, str, int]]]:
    """按 ADR-0063 D1 第 4 条解析；任一条目非法或 serial / 槽位重复时返回 None（整份拒绝）。"""
    try:
        doc = json.loads(body)
    except ValueError:
        return None
    if (not isinstance(doc, dict) or set(doc) != {"schema", "slots"}
            or type(doc["schema"]) is not int or doc["schema"] != 1
            or not isinstance(doc["slots"], list)):
        return None
    entries: List[Tuple[str, str, int]] = []
    serials = set()
    slots = set()
    for entry in doc["slots"]:
        if not isinstance(entry, dict) or set(entry) != {"serial", "hub_location", "port"}:
            return None
        serial, location, port = entry["serial"], entry["hub_location"], entry["port"]
        if (not isinstance(serial, str) or not _SERIAL_RE.match(serial)
                or not isinstance(location, str) or not _HUB_LOCATION_RE.match(location)
                or isinstance(port, bool) or not isinstance(port, int)
                or not 1 <= port <= _MAX_PORT):
            return None
        if serial in serials or (location, port) in slots:
            return None
        serials.add(serial)
        slots.add((location, port))
        entries.append((serial, location, port))
    return entries


def _read_slot_entries(path: str) -> Optional[List[Tuple[str, str, int]]]:
    # 按原始字节判断上限（与 wrapper 的 st_size 检查同口径）：文本模式会把 CRLF 折成 LF，
    # 折后再计数会放过 wrapper 拒绝的文件（复核 #3919 第 2 轮）。
    try:
        with open(path, "rb") as fh:
            raw = fh.read(_MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("usb_power_slots_unreadable path=%s err=%s", path, exc)
        return None
    entries = None
    if len(raw) <= _MAX_FILE_BYTES:
        try:
            entries = _parse_slot_entries(raw.decode("utf-8"))
        except UnicodeDecodeError:
            entries = None
    if entries is None:
        logger.warning("usb_power_slots_malformed path=%s", path)
    return entries


def _read_vid_pid(device_dir: str) -> Optional[str]:
    try:
        with open(os.path.join(device_dir, "idVendor"), encoding="utf-8") as fh:
            vid = fh.read().strip().lower()
        with open(os.path.join(device_dir, "idProduct"), encoding="utf-8") as fh:
            pid = fh.read().strip().lower()
    except OSError:
        return None
    if not _HEX4_RE.match(vid) or not _HEX4_RE.match(pid):
        return None
    return f"{vid}:{pid}"


def read_usb_power_presence(
    path: str = USB_POWER_SLOTS_PATH,
    sysfs_root: str = _SYSFS_USB_DEVICES,
) -> Optional[Dict[str, List[Optional[str]]]]:
    """映射文件存在且可读时返回 ``{serial: [slot, vid_pid|None]}``，否则 None（不上报）。

    ``slot`` 为 ``<hub_location>:<port>``，与映射条目同口径；口下没有设备时 ``vid_pid`` 为 None。
    """
    entries = _read_slot_entries(path)
    if entries is None:
        return None
    return {
        serial: [f"{location}:{port}", _read_vid_pid(os.path.join(sysfs_root, f"{location}.{port}"))]
        for serial, location, port in entries
    }
