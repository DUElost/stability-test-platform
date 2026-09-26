"""#2900：内核 USB 子系统故障的 host 级信号——**采集侧**（journalctl 扫描 + 低频监视）。

解析、签名词表与判定口径在契约包
``backend/agent/contracts/kernel_usb_faults.py``（ADR-0054 第 4 步：两侧共用同一
解析实现）；本模块只留运行逻辑——journalctl 调用、可读性探针、扫描节流与
``KernelUsbWatch`` 线程模型。上报复用既有 ``health.reasons`` 通道
（`capacity_reporter`），不引入新的传输面。

五条刻意的口径（采集侧）：

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
- **提权读路径（#2957 / ADR-0037 D7）**：Agent 以 `User=android` 运行时内核日志恒不可读
  （`dmesg_restrict=1`、`/dev/kmsg` EPERM），两条内核证据类规则因此恒绿。当本机 wrapper
  具备 `read-kernel-log` 子命令时，扫描与探针改走 `sudo -n stp-agent-priv read-kernel-log …`
  （固定 argv，只有两个整数参数）；wrapper 不在场/旧版则维持非特权调用（结果仍是
  `unavailable`），两态混跑安全。**截断样本按不可用处理**：wrapper 在输出超 8 MiB 时
  非零退出 + `STP_READ_KERNEL_LOG_*` 标记，本模块据此返回 None，绝不把偏小的计数
  当完整结果。
- **扫描走后台线程**——`journalctl -k` 在坏盘/大日志下可能秒级，心跳主循环不得被拖慢
  （心跳超时会被 session_watchdog 判 OFFLINE，那是比失明更响的误报）；
- **首扫 = 最近 1 小时，不读整段 boot**（#2957 实施规格）：实测一台主机
  `journalctl -k --boot` 有 373,600 行（远超 8 MiB 上限），整段读必然截断，
  而截断样本既不能作证据也不能当「干净」。首扫改读 `[now-3600s, now]` 后，
  #2978 的「boot 累计不能冒充最近一小时」问题从结构上消失（不再有 boot 计数入窗）。
  **代价**：进程启动前 1 小时以外的死亡不再进 latch；该形态由结果层
  `usb_tree_empty`（#2902 + #2967 的账实合取）承接，见 ADR-0037 D7。
- **一个样本只声明它所覆盖区间的计数**（#2978）——样本按**区间起点**打戳，
  窗口求和只取 1 小时内的样本；boot 形态（`since=None`，仅供离线/取证调用）
  的计数仍不入窗。
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import deque
from typing import Callable, Optional, Tuple

from .contracts.kernel_usb_faults import (
    CHANNEL_OK,
    CHANNEL_UNAVAILABLE,
    CHANNEL_UNKNOWN,
    KernelUsbFaults,
    LINK_WINDOW_SECONDS,
    parse_kernel_usb_faults,
    usb_kernel_fault_reasons,
)

logger = logging.getLogger(__name__)

#: 扫描节流（心跳 5s 一拍，内核日志不必每拍读）。
SCAN_INTERVAL_SECONDS = 60.0

#: journalctl 在「看不到系统消息」时的 stderr 提示片段（小写比对）——命中即「未知」。
_BLIND_HINT_MARKERS = (
    "not seeing messages from other users and the system",
    "can see all messages",
)

# ── #2957：wrapper 只读内核日志窄面（ADR-0037 D7）──────────────────────────
#: wrapper 由 install/update_agent.yml 逐台下发（不随热更新，`host_updater` 有判据）。
PRIV_WRAPPER = "/usr/local/sbin/stp-agent-priv"
KERNEL_LOG_SUBCOMMAND = "read-kernel-log"
#: 探针：能力清单含子命令才切提权面；结果缓存到进程退出（wrapper 不会热变更）。
PRIV_PROBE_TIMEOUT_SECONDS = 10.0
#: wrapper 内部超时 30s；外层给 35s，让 wrapper 自己的 TIMEOUT 标记先于调用方 kill 生效。
WRAPPER_SCAN_TIMEOUT_SECONDS = 35.0
#: 首扫回看窗（#2957 实施规格）：不读整段 boot，见模块 docstring。
FIRST_SCAN_LOOKBACK_SECONDS = 3600.0
#: 截断标记（wrapper stderr 侧，跨进程契约）：命中即「这一次不可用」。
TRUNCATED_MARKER = "STP_READ_KERNEL_LOG_TRUNCATED"

_priv_wrapper_cache: Optional[bool] = None


def priv_wrapper_has_kernel_log() -> bool:
    """本机 wrapper 是否具备 `read-kernel-log` 子命令（进程内缓存）。

    探针必须带**子命令名**而不是 `--help`（#2011 教训：`--help` 形态的能力探针
    抓不到「探针过、真调用挂」）。旧 wrapper / 无 wrapper / 无免密 sudo → False，
    调用方回落非特权路径，行为与本单之前一致。
    """
    global _priv_wrapper_cache
    if _priv_wrapper_cache is None:
        try:
            proc = subprocess.run(
                ["sudo", "-n", PRIV_WRAPPER, "capabilities"],
                capture_output=True,
                text=True,
                timeout=PRIV_PROBE_TIMEOUT_SECONDS,
            )
            _priv_wrapper_cache = proc.returncode == 0 and KERNEL_LOG_SUBCOMMAND in (
                proc.stdout or ""
            ).split()
        except (OSError, subprocess.SubprocessError):
            _priv_wrapper_cache = False
    return _priv_wrapper_cache


def _scan_argv(since: Optional[float], journalctl: str) -> Tuple[list, bool]:
    """构造扫描 argv；返回 ``(argv, via_wrapper)``。

    提权面可用时固定为
    `sudo -n stp-agent-priv read-kernel-log (--boot | --since-epoch <int>)`；
    否则维持非特权 journalctl（现状形态）。
    """
    if priv_wrapper_has_kernel_log():
        argv = ["sudo", "-n", PRIV_WRAPPER, KERNEL_LOG_SUBCOMMAND]
        argv += ["--boot"] if since is None else ["--since-epoch", str(int(since))]
        return argv, True
    argv = [journalctl, "-k", "--no-pager", "-o", "cat"]
    argv += ["--since", f"@{int(since)}"] if since else ["--boot"]
    return argv, False


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
    与「这台机开机后一行内核日志都没有」完全同形。`--lines=1` 让成本与日志总量无关
    （#2957 的 wrapper 路径同款：`read-kernel-log --boot --lines 1`）。
    """
    if priv_wrapper_has_kernel_log():
        argv = ["sudo", "-n", PRIV_WRAPPER, KERNEL_LOG_SUBCOMMAND, "--boot", "--lines", "1"]
        effective_timeout = WRAPPER_SCAN_TIMEOUT_SECONDS
    else:
        argv = [journalctl, "-k", "--no-pager", "-o", "cat", "--boot", "--lines=1"]
        effective_timeout = timeout
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            env=_journal_env(),
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

    ``since`` 给出（wall clock 秒）读该时间点之后的增量；为空读**整段 boot**
    （离线/取证形态；``KernelUsbWatch`` 自 #2957 起首扫用 1 小时回看窗，不再经过此路径）。

    wrapper 具备 `read-kernel-log` 时经 `sudo -n` 走提权窄面；wrapper 对**截断样本**
    以非零退出 + ``STP_READ_KERNEL_LOG_*`` 标记告知，本函数一律返回 None
    ——偏小的计数不得冒充「读到且干净」（#2957）。
    """
    argv, via_wrapper = _scan_argv(since, journalctl)
    effective_timeout = WRAPPER_SCAN_TIMEOUT_SECONDS if via_wrapper else timeout
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            env=_journal_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("kernel_usb_scan_failed: %s", exc)
        return None
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if TRUNCATED_MARKER in stderr:
            # #2957：输出超 8 MiB ⇒ 截断样本。只记明确原因，不当「干净」也不留部分计数。
            logger.warning(
                "kernel_usb_scan_truncated since=%r stderr=%s — 截断样本判为不可用",
                since, stderr[-200:],
            )
        else:
            logger.debug(
                "kernel_usb_scan_rc=%s stderr=%s",
                proc.returncode, stderr[-200:],
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


class KernelUsbWatch:
    """低频扫描内核日志并给出 USB 故障 reason（心跳每拍 ``poll``，内部节流）。

    线程模型：``poll`` 只在到期时**起一次**后台线程扫描，自身只读快照与做窗口求和，
    绝不阻塞调用方；扫描结果由后台线程写回，下一拍可见（最坏晚一拍，可接受）。

    首扫窗（#2957）：游标初始化为 ``now - FIRST_SCAN_LOOKBACK_SECONDS``，即首扫读
    「最近 1 小时」而不是整段 boot——boot 日志实测可达 373,600 行（远超 wrapper 的
    8 MiB 上限），截断样本不可用；这也让全部样本天然落在计数窗内（#2978 的结构化解）。
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
        # #2957：首扫 = 最近 1 小时（不再读整段 boot，见类 docstring）
        self._since_wall: Optional[float] = wall_clock() - FIRST_SCAN_LOOKBACK_SECONDS
        self._hc_dead_latched = False
        #: 样本 = ``(区间起点, 区间终点, link_errors, cable_suspect)``。留**终点**是
        #: #2957 首扫的需要：首扫覆盖 [now-3600, now] 一小时，若按起点裁剪，
        #: 下一次扫描就会被当作「过期」丢掉，而它覆盖的事件其实还在窗内。
        self._samples: deque[Tuple[float, float, int, int]] = deque()
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
    def poll(self, *, usb_device_count: Optional[int]) -> list[str]:
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

    def _scan_worker(self, *, since: float) -> None:
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
            # #2957 起首扫窗 = 最近 1 小时、之后每 60s 一段，全部样本的区间都落在
            # 计数窗（3600s）内；时间戳取**区间起点**（= 发起扫描时的游标），不是
            # 扫描完成时刻——否则样本会比它真正覆盖的区间「年轻」一个扫描周期。
            if faults.hc_dead_seen:
                # 布尔事实在样本层给（窗内出现即锁存）；进程启动前 1 小时以外的
                # 死亡不在本窗内，由结果层 usb_tree_empty（#2902/#2967）承接（#2957）。
                self._hc_dead_latched = True
            self._samples.append(
                (since, self._wall_clock(), faults.link_errors, faults.cable_suspect)
            )
            self._prune_locked()

    def _prune_locked(self) -> None:
        cutoff = self._wall_clock() - self._window_seconds
        while self._samples and self._samples[0][1] < cutoff:
            # 覆盖区间**整体**滑出窗口才丢（按终点判；首扫是 1 小时一段，不是 60s）
            self._samples.popleft()

    def _window_counts_locked(self) -> Tuple[int, int]:
        cutoff = self._wall_clock() - self._window_seconds
        in_window = [s for s in self._samples if s[1] >= cutoff]
        return (
            sum(s[2] for s in in_window),
            sum(s[3] for s in in_window),
        )
