"""内核 USB 子系统故障的解析、词表与判定契约（#2900；ADR-0054 D1/D2）。

现象：xHCI 主控报 `HC died; cleaning up` 后，**整机 USB 对内核永久不可见且不自愈**，
而 SSH / systemd / Agent 心跳全部正常 ⇒ 控制面看到 ONLINE/HEALTHY、设备产能静默归零。
判据与处置见 `docs/operations/host-device-visibility-triage.md` §1 的 L1 层与
`docs/operations/incident-2026-07-29-host-8-87-xhci-death-and-adb-outage.md`。

契约模块（ADR-0054 第 4 步）：控制面与 Agent 共用**同一实现**——Agent 侧经
``from ..contracts.kernel_usb_faults import …`` 相对导入，控制面
（``services/host_health_probe`` 的 journal 解析）经
``backend.agent.contracts.kernel_usb_faults``。只收三类定义：

1. 内核**签名词表**（`HC_DEAD_MARKER` / `CABLE_SUSPECT_MARKER` / `LINK_ERROR_MARKERS` 等）
   与慢性劣化的统计窗/阈值；
2. **纯解析**：``parse_kernel_usb_faults``（日志行 → 计数）、
   ``usb_kernel_fault_reasons``（计数 → reason，含「失明 = 日志证据 ∧ 此刻零设备」合取）；
3. **两套词表**：`REASON_*`（进 health.reasons，控制面 gauge 分桶与告警选择器按它建）
   与 `CHANNEL_*`/`CHANNEL_STATES`（日志通道可用性；与 reason 刻意分开——reason 表示
   「设备出事」，channel 表示「我看不看得到设备出事」）。

**采集留在 agent**（`backend/agent/kernel_usb_faults.py`：journalctl 调用、可读性探针、
低频扫描线程）——ADR-0054 D2：运行逻辑不属于契约，即使控制面也要用。
本模块只依赖标准库、import 期无 I/O。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

# 内核定式（8.87 / .102 / .63 三例同签名，逐字取自 issue 与事故复盘）：
#   xhci_hcd 0000:00:14.0: xHCI host not responding to stop endpoint command
#   xhci_hcd 0000:00:14.0: xHCI host controller not responding, assume dead
#   xhci_hcd 0000:00:14.0: HC died; cleaning up
HC_DEAD_MARKER = "hc died"
HC_NOT_RESPONDING_MARKER = "xhci host controller not responding"
CABLE_SUSPECT_MARKER = "maybe the usb cable is bad"
# 慢性链路劣化：-71(EPROTO) / -110(ETIMEDOUT)。.102 死亡前两周每日 400~3700 行。
LINK_ERROR_MARKERS = ("error -71", "error -110")

#: 慢性劣化的统计窗与阈值（窗口内事件数）。阈值取「明显高于正常热插拔抖动」的量级：
#: .102 的风暴 400~3700 行/天 ≈ 17~154 行/小时，20/小时可提前定位，而单次插拔的
#: 偶发 -71 不会触发（验收要求观察窗 0 误报）。
LINK_WINDOW_SECONDS = 3600.0
LINK_ERROR_THRESHOLD = 20
CABLE_SUSPECT_THRESHOLD = 5

#: reason 词表（进 health.reasons，前端 ExpandableHostTable 的 REASON_LABELS 需同步）。
REASON_HC_DEAD = "usb_host_controller_dead"
REASON_LINK_DEGRADED = "usb_link_degraded"

#: 内核日志**通道可用性**（#2957）。刻意与 reason 分开：reason 表示「设备出事」，
#: 本字段表示「我看不看得到设备出事」。二者混在一个词表里，运维就会把
#: 「恒未知」读成「恒干净」——正是 #2900 的失效形状在本单里的复发形态。
CHANNEL_UNKNOWN = "unknown"          # 首次扫描尚未完成（进程启动后的头几拍）
CHANNEL_OK = "ok"                    # 最近一次扫描真的读到了内核日志
CHANNEL_UNAVAILABLE = "unavailable"  # 最近一次扫描读不到（无 adm/systemd-journal 组成员资格等）

#: 词表（控制面按它建 gauge 分桶，两侧不得各写一套字面量）。
CHANNEL_STATES = (CHANNEL_OK, CHANNEL_UNAVAILABLE, CHANNEL_UNKNOWN)


@dataclass(frozen=True)
class KernelUsbFaults:
    """一段内核日志里与 USB 子系统有关的计数。"""

    hc_dead: int = 0
    not_responding: int = 0
    link_errors: int = 0
    cable_suspect: int = 0
    lines: int = 0

    @property
    def hc_dead_seen(self) -> bool:
        """主控死亡证据：`HC died` 或 `…controller not responding` 任一命中。

        两行是同一死亡定式的第 2/3 行；只认其中一行会在日志尾部保留窗口截断时漏判。
        """
        return self.hc_dead > 0 or self.not_responding > 0


def parse_kernel_usb_faults(lines: Iterable[str]) -> KernelUsbFaults:
    """纯函数：内核日志行 → 计数（供扫描与离线回放共用）。"""
    hc_dead = not_responding = link_errors = cable_suspect = total = 0
    for raw in lines:
        line = (raw or "").strip().lower()
        if not line:
            continue
        total += 1
        if HC_DEAD_MARKER in line:
            hc_dead += 1
        elif HC_NOT_RESPONDING_MARKER in line:
            not_responding += 1
        if CABLE_SUSPECT_MARKER in line:
            cable_suspect += 1
        if any(marker in line for marker in LINK_ERROR_MARKERS):
            link_errors += 1
    return KernelUsbFaults(
        hc_dead=hc_dead,
        not_responding=not_responding,
        link_errors=link_errors,
        cable_suspect=cable_suspect,
        lines=total,
    )


def usb_kernel_fault_reasons(
    *,
    hc_dead_latched: bool,
    usb_device_count: int | None,
    link_errors_in_window: int,
    cable_suspect_in_window: int,
) -> List[str]:
    """把内核事实折成 reason；**纯函数**（离线回放取证与单测共用同一判据）。"""
    reasons: List[str] = []
    # 失明 = 「死过」 ∧ 「此刻 USB 上一台都看不到」。
    # usb_device_count 为 None 表示枚举失败：不主张失明（未知 ≠ 0）。
    if hc_dead_latched and usb_device_count == 0:
        reasons.append(REASON_HC_DEAD)
    if (
        link_errors_in_window >= LINK_ERROR_THRESHOLD
        or cable_suspect_in_window >= CABLE_SUSPECT_THRESHOLD
    ):
        reasons.append(REASON_LINK_DEGRADED)
    return reasons
