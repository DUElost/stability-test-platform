"""#2972：门控自动 xHCI unbind/rebind（恢复层）。

本模块第一切片：开关 / 白名单 / 门控合取 / 防洗白熔断 / sysfs PCI 枚举与
可注入的 bind/unbind 动作。**不**接线 heartbeat（调度入口另切片）。

默认关；启用后仍须白名单命中才动作——与 ``STP_ADB_AUTO_REPAIR`` 同形的
显式 opt-in（仅精确 ``\"1\"``）。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Sequence

logger = logging.getLogger(__name__)

XHCI_SYSFS_DIR = Path("/sys/bus/pci/drivers/xhci_hcd")
EMPTY_TREE_TICKS_NEED = 3
MAX_RETRY_AFTER_FAIL = 1  # 首次失败后再试 1 次封顶
MAX_AUTO_PER_BOOT = 2
WINDOW_48H_SECONDS = 48 * 3600
MAX_AUTO_PER_48H = 2

REASON_DISABLED = "disabled"
REASON_NOT_WHITELISTED = "host_not_whitelisted"
REASON_TREE_NOT_EMPTY = "tree_not_empty"
REASON_TICKS_SHORT = "empty_ticks_short"
REASON_ACTIVE_JOBS = "active_jobs"
REASON_ACTIVE_DEVICES = "active_devices"
REASON_MAINTENANCE = "in_maintenance"
REASON_HOST_LEDGER_EMPTY = "host_ledger_empty"
REASON_FUSE_BOOT = "fuse_boot_limit"
REASON_FUSE_WINDOW = "fuse_48h_limit"
REASON_FUSE_RETRY = "fuse_retry_exhausted"
REASON_NO_PCI_IDS = "no_pci_ids"


class Action(str, Enum):
    NOOP = "noop"
    REBIND = "rebind"
    STOP_HARDWARE_SUSPECT = "stop_hardware_suspect"  # 重试后树不回
    STOP_NEED_HUMAN = "stop_need_human"  # 同 boot/48h 复发降级


@dataclass(frozen=True)
class GateInput:
    """一轮心跳可观测到的门控输入（平台侧 active_* 为权威）。"""

    host_id: str
    enabled: bool
    whitelist: frozenset[str]
    usb_device_count: Optional[int]
    usb_root_hub_count: Optional[int]
    #: lsusb 里被 `_NON_TARGET_USB_KEYWORDS` 排除的非 root-hub 节点数（USB 归档盘 /
    #: 网卡 / 采集卡…）。**必须单独传**：`usb_device_count` 把这类节点整个丢掉了，
    #: 只看它无法区分「没有外设」与「只有非目标外设」（#2972 复核）。
    other_usb_nodes: int
    #: 该 host 在设备台账里的行数（`stability_host_device_adb_state` 的来源）。
    #: 0 = 这台机器本来就不接设备（空柜/未接线），与「控制器死了」不可区分。
    host_ledger_device_rows: int
    discovered_devices: int
    empty_tree_ticks: int
    active_jobs: int
    active_devices: int
    in_maintenance: bool


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    block_reason: Optional[str] = None


def parse_host_whitelist(raw: str | None) -> frozenset[str]:
    """``STP_XHCI_AUTO_REBIND_HOSTS``：逗号分隔 host id；空 = 无人放行。"""
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def env_enabled(raw: str | None = None) -> bool:
    """仅精确 ``\"1\"`` 启用（同 ``STP_ADB_AUTO_REPAIR``）。"""
    if raw is None:
        raw = os.getenv("STP_XHCI_AUTO_REBIND", "0")
    return raw == "1"


def is_usb_tree_verifiably_empty(
    usb_device_count: Optional[int],
    usb_root_hub_count: Optional[int],
    other_usb_nodes: int,
    discovered_devices: int,
) -> bool:
    """动作门控专用判据：USB 树**可证**为空（而不是「不排除为空」）。

    与告警侧 `capacity_reporter._usb_tree_empty` 的宽松谓词**刻意不同**：告警漏报的
    代价是少一次告警；rebind 是**破坏性**动作（unbind 会把该控制器下的设备全部从总线
    摘掉），误判的代价是拔掉正在用的设备。因此这里要求：

    - ``usb_device_count == 0``：原式 ``<= usb_root_hub_count`` 会把「2 台处于非 ADB
      模式的手机」（L4 拓扑，正是 #3046 记录的那类）读成空树并放行；
    - ``other_usb_nodes == 0``：`parse_lsusb_output` 按关键词排除了存储/网卡/摄像头等，
      旧谓词对它们完全不可见 ⇒「空柜 + 一块 USB 归档盘」也读成空树，rebind 会把盘拔掉；
    - ``usb_root_hub_count > 0``：一个 root hub 都读不到 = lsusb 没采到，那是「未知」不是「空」；
    - ``discovered_devices == 0``：agent 一台设备都没发现。
    """
    if usb_device_count is None or usb_root_hub_count is None:
        return False
    if usb_root_hub_count <= 0:
        return False
    return usb_device_count == 0 and other_usb_nodes == 0 and discovered_devices == 0


def evaluate_gate(inp: GateInput) -> GateDecision:
    """门控真值表：任一条件不满足 → 不动作。"""
    if not inp.enabled:
        return GateDecision(False, REASON_DISABLED)
    if inp.host_id not in inp.whitelist:
        return GateDecision(False, REASON_NOT_WHITELISTED)
    if not is_usb_tree_verifiably_empty(
        inp.usb_device_count, inp.usb_root_hub_count,
        inp.other_usb_nodes, inp.discovered_devices,
    ):
        return GateDecision(False, REASON_TREE_NOT_EMPTY)
    if inp.host_ledger_device_rows <= 0:
        # #2967 给**告警**加的「本应有设备」合取，门控侧同样需要：空柜/闲置/未接线的
        # 机器满足其它全部条件，却没有可判别的故障证据，据此 rebind 只会烧掉熔断额度。
        return GateDecision(False, REASON_HOST_LEDGER_EMPTY)
    if inp.empty_tree_ticks < EMPTY_TREE_TICKS_NEED:
        return GateDecision(False, REASON_TICKS_SHORT)
    if inp.active_jobs > 0:
        return GateDecision(False, REASON_ACTIVE_JOBS)
    if inp.active_devices > 0:
        return GateDecision(False, REASON_ACTIVE_DEVICES)
    if inp.in_maintenance:
        return GateDecision(False, REASON_MAINTENANCE)
    return GateDecision(True, None)


@dataclass
class RebindFuse:
    """防洗白：同 boot / 48h 次数上限 + 单 episode 重试封顶。"""

    boot_attempts: int = 0
    window_attempts: list[float] = field(default_factory=list)
    episode_failures: int = 0
    open_reason: Optional[str] = None

    def prune(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self.window_attempts = [
            ts for ts in self.window_attempts if now - ts <= WINDOW_48H_SECONDS
        ]

    def check(self, now: Optional[float] = None) -> GateDecision:
        if self.open_reason:
            return GateDecision(False, self.open_reason)
        self.prune(now)
        if self.boot_attempts >= MAX_AUTO_PER_BOOT:
            return GateDecision(False, REASON_FUSE_BOOT)
        if len(self.window_attempts) >= MAX_AUTO_PER_48H:
            return GateDecision(False, REASON_FUSE_WINDOW)
        if self.episode_failures > MAX_RETRY_AFTER_FAIL:
            return GateDecision(False, REASON_FUSE_RETRY)
        return GateDecision(True, None)

    def record_attempt(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self.boot_attempts += 1
        self.window_attempts.append(now)
        self.prune(now)
        if self.boot_attempts >= MAX_AUTO_PER_BOOT or len(self.window_attempts) >= MAX_AUTO_PER_48H:
            self.open_reason = (
                REASON_FUSE_BOOT
                if self.boot_attempts >= MAX_AUTO_PER_BOOT
                else REASON_FUSE_WINDOW
            )

    def record_still_empty(self) -> Action:
        """rebind 后树仍空：累计失败；超重试 → 疑似硬件。"""
        self.episode_failures += 1
        if self.episode_failures > MAX_RETRY_AFTER_FAIL:
            self.open_reason = REASON_FUSE_RETRY
            return Action.STOP_HARDWARE_SUSPECT
        return Action.REBIND

    def record_recovered(self) -> None:
        """树回了：清 episode 失败计数（boot/48h 计数保留）。"""
        self.episode_failures = 0


def decide(
    gate: GateDecision,
    fuse: RebindFuse,
    *,
    now: Optional[float] = None,
) -> tuple[Action, Optional[str]]:
    """门控 ∧ 熔断 → 动作。熔断已开且原因为复发 → NEED_HUMAN。"""
    if not gate.allowed:
        return Action.NOOP, gate.block_reason
    fuse_gate = fuse.check(now)
    if not fuse_gate.allowed:
        reason = fuse_gate.block_reason
        if reason in (REASON_FUSE_BOOT, REASON_FUSE_WINDOW):
            return Action.STOP_NEED_HUMAN, reason
        if reason == REASON_FUSE_RETRY:
            return Action.STOP_HARDWARE_SUSPECT, reason
        return Action.NOOP, reason
    return Action.REBIND, None


def list_xhci_pci_ids(sysfs_dir: Path | str = XHCI_SYSFS_DIR) -> list[str]:
    """运行时枚举 ``xhci_hcd`` 下已绑定的 PCI 地址（不写死 0000:00:14.0）。"""
    root = Path(sysfs_dir)
    if not root.is_dir():
        return []
    ids: list[str] = []
    for entry in sorted(root.iterdir()):
        name = entry.name
        # PCI 形如 0000:00:14.0；跳过 unbind/bind/module 等文件
        if ":" in name and name[0].isdigit():
            ids.append(name)
    return ids


WriteFn = Callable[[Path, str], None]


def _default_sysfs_write(path: Path, payload: str) -> None:
    path.write_text(payload, encoding="utf-8")


def rebind_controllers(
    pci_ids: Sequence[str],
    *,
    sysfs_dir: Path | str = XHCI_SYSFS_DIR,
    write: WriteFn = _default_sysfs_write,
) -> list[tuple[str, str]]:
    """对每个 PCI id 依次 unbind → bind。返回 ``[(pci_id, \"ok\"|error), ...]``。

    不吞异常到静默成功：单 id 失败记入结果，继续下一个。
    """
    root = Path(sysfs_dir)
    results: list[tuple[str, str]] = []
    for pci_id in pci_ids:
        try:
            write(root / "unbind", pci_id)
            write(root / "bind", pci_id)
            results.append((pci_id, "ok"))
            logger.info("xhci_auto_rebind_ok pci_id=%s", pci_id)
        except OSError as exc:
            results.append((pci_id, f"error:{exc}") )
            logger.warning(
                "xhci_auto_rebind_failed pci_id=%s error=%s", pci_id, exc,
            )
    return results


def run_if_allowed(
    inp: GateInput,
    fuse: RebindFuse,
    *,
    sysfs_dir: Path | str = XHCI_SYSFS_DIR,
    write: WriteFn = _default_sysfs_write,
    list_ids: Callable[[Path | str], list[str]] = list_xhci_pci_ids,
    now: Optional[float] = None,
) -> tuple[Action, Optional[str], list[tuple[str, str]]]:
    """门控通过则枚举并 rebind；返回动作、阻断因、逐 id 结果。"""
    gate = evaluate_gate(inp)
    action, reason = decide(gate, fuse, now=now)
    if action != Action.REBIND:
        return action, reason, []
    pci_ids = list_ids(sysfs_dir)
    if not pci_ids:
        logger.warning("xhci_auto_rebind_no_pci_ids host=%s", inp.host_id)
        return Action.NOOP, REASON_NO_PCI_IDS, []
    fuse.record_attempt(now)
    results = rebind_controllers(pci_ids, sysfs_dir=sysfs_dir, write=write)
    return Action.REBIND, None, results
