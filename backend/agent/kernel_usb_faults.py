"""#2900：内核 USB 子系统故障的 host 级信号（xHCI 主控死亡 / 慢性链路劣化）。

现象：xHCI 主控报 `HC died; cleaning up` 后，**整机 USB 对内核永久不可见且不自愈**，
而 SSH / systemd / Agent 心跳全部正常 ⇒ 控制面看到 ONLINE/HEALTHY、设备产能静默归零。
fleet 内已 3 例（跨机型跨厂商），最长失明 11 天，全靠人工巡检才发现。
判据与处置见 `docs/operations/host-device-visibility-triage.md` §1 的 L1 层与
`docs/operations/incident-2026-07-29-host-8-87-xhci-death-and-adb-outage.md`。

本模块只做两件事：把内核日志**解析成事实**，再按判定口径折成 reason 字符串；
上报复用既有 `health.reasons` 通道（`capacity_reporter`），不引入新的传输面。

三条刻意的口径：

- **扫描失败返回 None（未知 ≠ 干净）**——与 `device_discovery.count_usb_devices` 同款：
  读不到日志时既不报故障、也不假装检查过，首次失败留一条 warning。
  **判「读不到」不能只看 stderr 提示串**：#2957 实测本模块真正使用的两种 argv
  （`-k --no-pager -o cat --boot` 与 `… --since @<ts>`）在非特权下都是
  **rc=0 / stdout 空 / stderr 也空**——提示串只出现在 `-n 3`、`--since -1h` 这类
  *别的*形状里。⇒ 只靠 `_BLIND_HINT_MARKERS` 会把「从没读到」判成「读到且干净」，
  连 `channel_state()` 都会报 `ok`（现网 36/36 台已升级 host 全部如此）。
  现在补一道**可读性探针**：任何一次扫描折出 0 行时，问一句「整段 boot 里有没有
  哪怕一行内核日志」（`--boot --lines=1`）；没有就是读不到，返回 None。
  同时给子进程钉 `LC_ALL=C`/`LANG=C`——systemd 的提示串是翻译的（本机
  `/usr/share/locale/zh_CN/LC_MESSAGES/systemd.mo` 存在），提示匹配不能依赖语言环境。
- **扫描走后台线程**——`journalctl -k` 在坏盘/大日志下可能秒级，心跳主循环不得被拖慢
  （心跳超时会被 session_watchdog 判 OFFLINE，那是比失明更响的误报）；
- **一个样本只声明它所覆盖区间的计数**（#2978）——boot 首扫覆盖整段 boot，其**计数**
  不入 1 小时窗（只留 INFO 取证），否则一次 agent 重启就能把干净 host 拉成 DEGRADED 最长
  1 小时并撑满 `StabilityHostUsbLinkDegraded` 的 45m 窗；它的**布尔** latch 照用 boot 全量；
- **失明是「日志证据 ∧ 此刻零设备」的合取**——单看日志会把「死过但已 unbind/rebind
  救回」的 host 永久标红；单看设备数会把「本来就没接设备」误判。设备回树即自动回落。
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 内核定式（8.87 / .102 / .63 三例同签名，逐字取自 issue 与事故复盘）：
#   xhci_hcd 0000:00:14.0: xHCI host not responding to stop endpoint command
#   xhci_hcd 0000:00:14.0: xHCI host controller not responding, assume dead
#   xhci_hcd 0000:00:14.0: HC died; cleaning up
HC_DEAD_MARKER = "hc died"
HC_NOT_RESPONDING_MARKER = "xhci host controller not responding"
CABLE_SUSPECT_MARKER = "maybe the usb cable is bad"
# 慢性链路劣化：-71(EPROTO) / -110(ETIMEDOUT)。.102 死亡前两周每日 400~3700 行。
LINK_ERROR_MARKERS = ("error -71", "error -110")

#: 扫描节流（心跳 5s 一拍，内核日志不必每拍读）。
SCAN_INTERVAL_SECONDS = 60.0
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

#: journalctl 在「看不到系统消息」时的 stderr 提示片段（小写比对）——命中即「未知」。
_BLIND_HINT_MARKERS = (
    "not seeing messages from other users and the system",
    "can see all messages",
)


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


def _journal_env() -> dict:
    """给 journalctl 钉死 C locale。

    systemd 的消息是翻译过的（本机 `locale -a` 有 `zh_CN`，且
    `/usr/share/locale/zh_CN/LC_MESSAGES/systemd.mo` 存在）⇒ 任何**按文本**匹配提示串
    的判据都必须与语言环境无关，否则中文 host 上匹配静默失效（比不匹配更坏：它看起来像"没问题"）。
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env


def kernel_log_is_readable(
    *,
    timeout: float = 20.0,
    journalctl: str = "journalctl",
) -> bool:
    """整段 boot 里是否存在**至少一行**内核日志——「读得到」的正向证据。

    为什么必须有这道探针：非特权 `journalctl -k --no-pager -o cat --boot` 的实测输出是
    rc=0 + stdout 空 + stderr 空（`#2957`，同机 sudo 对照为 373,600 行），
    与「这台机开机后一行内核日志都没有」完全同形。`--lines=1` 让成本与日志总量无关。
    """
    argv = [journalctl, "-k", "--no-pager", "-o", "cat", "--boot", "--lines=1"]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, env=_journal_env()
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    return bool((proc.stdout or "").strip())


def scan_kernel_usb_faults(
    *,
    since: Optional[float] = None,
    timeout: float = 20.0,
    journalctl: str = "journalctl",
) -> Optional[KernelUsbFaults]:
    """读内核日志（journald）并解析。**任何失败返回 None**（未知 ≠ 干净）。

    ``since`` 为空读**整段 boot**——覆盖「开机即死」与「agent 重启时主机已死」两种
    「死亡发生在本次进程之前」的形态；给出 ``since``（wall clock 秒）则只读增量。
    """
    argv = [journalctl, "-k", "--no-pager", "-o", "cat"]
    argv += ["--since", f"@{int(since)}"] if since else ["--boot"]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, env=_journal_env()
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("kernel_usb_scan_failed: %s", exc)
        return None
    if proc.returncode != 0:
        logger.debug(
            "kernel_usb_scan_rc=%s stderr=%s",
            proc.returncode, (proc.stderr or "").strip()[-200:],
        )
        return None
    # journalctl 在**看不到系统消息**时仍然 exit 0，只在 stderr 打一行提示
    # （「You are currently not seeing messages from other users and the system /
    # Users in groups 'adm', 'systemd-journal' can see all messages.」）。本机实测：
    # 非 adm/systemd-journal 组的用户 stdout 只有 1 行、零内核消息——**与「内核干净」
    # 长得一模一样**。这正是 #2900 要治的失明形态，故按「未知」处理，不返回空结果。
    stderr = (proc.stderr or "").lower()
    if any(marker in stderr for marker in _BLIND_HINT_MARKERS):
        logger.debug("kernel_usb_scan_blind stderr=%s", stderr.strip()[-200:])
        return None
    faults = parse_kernel_usb_faults(proc.stdout.splitlines())
    if faults.lines == 0:
        # 两种成因同形：①增量窗确实没有新内核日志（可读）；②非特权下 rc=0/空 stdout/空 stderr
        # （读不到）。只有 ①能被探针放行——探针也拿不到一行就按未知处理（#2957）。
        if not kernel_log_is_readable(timeout=timeout, journalctl=journalctl):
            logger.debug("kernel_usb_scan_empty_and_unreadable since=%r", since)
            return None
    return faults


def usb_kernel_fault_reasons(
    *,
    hc_dead_latched: bool,
    usb_device_count: Optional[int],
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


class KernelUsbWatch:
    """低频扫描内核日志并给出 USB 故障 reason（心跳每拍 ``poll``，内部节流）。

    线程模型：``poll`` 只在到期时**起一次**后台线程扫描，自身只读快照与做窗口求和，
    绝不阻塞调用方；扫描结果由后台线程写回，下一拍可见（最坏晚一拍，可接受）。
    """

    def __init__(
        self,
        *,
        interval_seconds: float = SCAN_INTERVAL_SECONDS,
        window_seconds: float = LINK_WINDOW_SECONDS,
        scanner: Callable[..., Optional[KernelUsbFaults]] = scan_kernel_usb_faults,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._interval_seconds = float(interval_seconds)
        self._window_seconds = float(window_seconds)
        self._scanner = scanner
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._lock = threading.Lock()
        self._scanning = False
        self._last_scan_monotonic: Optional[float] = None
        self._since_wall: Optional[float] = None
        self._hc_dead_latched = False
        self._samples: deque[Tuple[float, int, int]] = deque()
        self._warned_unavailable = False
        self._channel_state = CHANNEL_UNKNOWN

    def channel_state(self) -> str:
        """最近一次扫描的**通道**结论（与「有没有故障」正交）。

        #2957 实测：Agent 以 `User=android` 运行（`install_agent.sh` 写死），从未加入
        `adm`/`systemd-journal`；非特权用户 `journalctl -k` **退出码 0、stdout 只有
        `-- No entries --`**，与「内核干净」同形——本模块按未知处理（返回 None）。
        若这个「未知」不单独上报，控制面看到的就是「48/48 台没有任何 USB 故障」，
        而真相是「48/48 台从未检查过」。
        """
        with self._lock:
            return self._channel_state

    # ── 调用方接口 ───────────────────────────────────────────────────────
    def poll(self, *, usb_device_count: Optional[int]) -> List[str]:
        """本拍的 reason 列表（非阻塞）；到期时安排一次后台扫描。"""
        self._maybe_start_scan()
        with self._lock:
            if usb_device_count:  # 设备回树 ⇒ 失明解除（可自愈，不需人工清位）
                self._hc_dead_latched = False
            link_errors, cable_suspect = self._window_counts_locked()
            return usb_kernel_fault_reasons(
                hc_dead_latched=self._hc_dead_latched,
                usb_device_count=usb_device_count,
                link_errors_in_window=link_errors,
                cable_suspect_in_window=cable_suspect,
            )

    # ── 内部 ─────────────────────────────────────────────────────────────
    def _maybe_start_scan(self) -> None:
        with self._lock:
            if self._scanning:
                return
            now = self._monotonic()
            if (
                self._last_scan_monotonic is not None
                and now - self._last_scan_monotonic < self._interval_seconds
            ):
                return
            self._scanning = True
            self._last_scan_monotonic = now
            since = self._since_wall
            # 游标先推进再扫描：扫描耗时期间产生的事件归下一次（宁可重复读，不可漏读）
            self._since_wall = self._wall_clock()
        threading.Thread(
            target=self._scan_worker,
            kwargs={"since": since},
            name="kernel-usb-scan",
            daemon=True,
        ).start()

    def _scan_worker(self, *, since: Optional[float]) -> None:
        try:
            faults = self._scanner(since=since)
        except Exception as exc:  # 扫描器自身异常不得冒进心跳线程
            logger.debug("kernel_usb_scan_crashed: %s", exc)
            faults = None
        with self._lock:
            self._scanning = False
            if faults is None:
                self._channel_state = CHANNEL_UNAVAILABLE
                if not self._warned_unavailable:
                    self._warned_unavailable = True
                    logger.warning(
                        "kernel_usb_scan_unavailable — 内核日志不可读（权限/journald），"
                        "USB 主控故障不参与 health（#2900）"
                    )
                return
            self._channel_state = CHANNEL_OK
            # 不变量（#2978）：**一个样本只能声明它所覆盖区间的计数**。
            # `since is None` 的 boot 扫描覆盖「整段 boot」（可能是 3 天），它越过阈值
            # 不代表「最近 1 小时」越过阈值——把它塞进 3600s 窗就是拿量纲不同的数当增量用，
            # 现网后果：每次 agent 热更新/服务重启都会重跑 boot 首扫，一台当前干净、只是
            # 本 boot 早期有过插拔风暴的 host 会被拉成 DEGRADED 最长 1h，并可能点亮
            # `StabilityHostUsbLinkDegraded`（for: 45m ⇒ 单条样本就够撑满整窗）。
            if faults.hc_dead_seen:
                self._hc_dead_latched = True   # 布尔事实用 boot 全量是**对的**：覆盖
                                               # 「死亡发生在本次进程启动之前」
            if since is None:
                if faults.link_errors or faults.cable_suspect:
                    # 不入窗，但也不是丢掉：留一条 INFO 供取证（现网真在风暴时看得见它），
                    # 只是它不再冒充"最近一小时"去驱动 reason/告警。
                    logger.info(
                        "kernel_usb_boot_counts_not_windowed link_errors=%d "
                        "cable_suspect=%d lines=%d window=%ds（整段 boot 累计，"
                        "不参与计数窗，见 #2978）",
                        faults.link_errors,
                        faults.cable_suspect,
                        faults.lines,
                        int(self._window_seconds),
                    )
                self._prune_locked()
                return
            # 增量扫描：时间戳取**区间起点**（= 上次扫描的游标），不是扫描时刻——与 boot
            # 那条不变量同一条规则，顺带去掉样本比其区间"年轻"一个扫描周期的偏差。
            self._samples.append(
                (since, faults.link_errors, faults.cable_suspect)
            )
            self._prune_locked()

    def _prune_locked(self) -> None:
        cutoff = self._wall_clock() - self._window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def _window_counts_locked(self) -> Tuple[int, int]:
        cutoff = self._wall_clock() - self._window_seconds
        in_window = [s for s in self._samples if s[0] >= cutoff]
        return (
            sum(s[1] for s in in_window),
            sum(s[2] for s in in_window),
        )
