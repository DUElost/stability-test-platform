"""InotifydSource + CapabilityProber — Agent 侧"报错文件探测"数据源。

职责边界（KISS / YAGNI）：
    CapabilityProber   能力探测：检查 adb root + 4 目录可读性，返回 ProbeResult。
                       不做任何状态保存；输出供 DeviceLogWatcher 启动决策。

    InotifydSource     长连接订阅：`adb -s SERIAL shell inotifyd - <paths>:<mask>`
                       后台线程读 stdout 按行解析为 WatcherEvent → 通过 callback 发出。
                       不做事件聚合、不做文件 pull、不做重连（交 DeviceLogWatcher 决策）。

契约：
    category 集合与 policy.DEFAULT_PATHS 一致（ANR / AEE / VENDOR_AEE / MOBILELOG）
    event_mask: 'n' (IN_CREATE) / 'w' (IN_CLOSE_WRITE) / 'x' (IN_MOVED_TO) — 见 inotifyd 源码
    WatcherEvent.detected_at 始终 UTC 带时区

设计取舍：
    - 一个 InotifydSource 对应一个 adb 连接、一个 Popen、一个读线程；单一职责好测试
    - 能力探测返回 `unavailable` 不抛异常；Job 生死由上层 policy.on_unavailable 决策
    - stop() 发 SIGTERM 本地 Popen；设备侧 inotifyd 进程 best-effort pkill（不阻塞调用方）
    - Windows 兼容：subprocess.Popen text=True + bufsize=1 行缓冲
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Dict, List, Optional

from .policy import DEFAULT_PATHS, ROOT_REQUIRED_CATEGORIES, WatcherPolicy

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 数据类型
# ----------------------------------------------------------------------

class WatcherCapability(str, Enum):
    """Watcher 能力等级；与 DB 列 `watcher_capability` 契约一致。

    等级从高到低：inotifyd_root > inotifyd_shell > polling > unavailable
    """

    INOTIFYD_ROOT = "inotifyd_root"       # adb root + inotifyd 可用（首选）
    INOTIFYD_SHELL = "inotifyd_shell"     # 非 root 但 shell 能读目标目录 + inotifyd
    POLLING = "polling"                   # inotifyd 失败但 ls 轮询可用（降级）
    UNAVAILABLE = "unavailable"           # 完全无法访问（由 policy 决定 Job 生死）


@dataclass(frozen=True)
class WatcherEvent:
    """单个文件事件。InotifydSource 的输出单位。

    字段语义（不可变）：
        category      ANR / AEE / VENDOR_AEE / MOBILELOG（从 dir_path 反查）
        event_mask    inotifyd 单字符 mask：n/w/x/...
        dir_path      被监听的目录（与 policy.paths 中的路径之一相同）
        filename      事件涉及的文件名（相对 dir_path）
        full_path     dir_path + '/' + filename，方便后续 pull
        detected_at   Agent 本地接收到该行的 UTC 时间
    """

    category: str
    event_mask: str
    dir_path: str
    filename: str
    full_path: str
    detected_at: datetime


@dataclass
class ProbeResult:
    """CapabilityProber.probe() 的返回值。

    capability                最终能力等级（供 Manager/JobSession 决策）
    accessible_categories     policy.required_categories 中实际可访问的子集
    inaccessible_categories   探测失败的分类及原因
    is_root                   adb shell id 返回 uid=0
    reasons                   自由格式诊断信息（供运维查看日志用）
    """

    capability: WatcherCapability
    accessible_categories: List[str]
    inaccessible_categories: Dict[str, str]
    is_root: bool
    reasons: List[str] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        """capability != unavailable 即可用（polling 也算）。"""
        return self.capability is not WatcherCapability.UNAVAILABLE


# ----------------------------------------------------------------------
# CapabilityProber — 无状态纯函数封装
# ----------------------------------------------------------------------

class CapabilityProber:
    """探测 Watcher 在指定设备上的可用能力。不保存状态。

    用法：
        prober = CapabilityProber(adb, timeout=5.0)
        result = prober.probe(serial, policy)
        if result.capability is WatcherCapability.UNAVAILABLE:
            ...  # 根据 policy.on_unavailable 决策
    """

    def __init__(self, adb, *, timeout_seconds: float = 5.0) -> None:
        self._adb = adb
        self._timeout = float(timeout_seconds)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def probe(self, serial: str, policy: WatcherPolicy) -> ProbeResult:
        """对指定 serial 执行能力探测。

        步骤：
            1. 检查 adb root 状态（决定 ROOT_REQUIRED 分类能否访问）
            2. 对 policy.paths 每个分类的每个目录做 readable 测试
            3. 检查 inotifyd 二进制是否存在（toybox/busybox 内置）
            4. 综合得出最终 capability

        永远返回 ProbeResult；不会抛 WatcherStartError（交上层按 policy 决策）。
        """
        is_root = self._detect_root(serial)
        inotifyd_available = self._detect_inotifyd(serial)

        accessible: List[str] = []
        inaccessible: Dict[str, str] = {}
        for category in policy.required_categories:
            paths = policy.paths.get(category) or DEFAULT_PATHS.get(category, [])
            if not paths:
                inaccessible[category] = "no_path_configured"
                continue

            # root 权限校验（部分分类非 root 无法读）
            if category in ROOT_REQUIRED_CATEGORIES and not is_root:
                # 仍尝试 ls —— 某些工厂固件 shell 就能读
                pass

            if self._any_readable(serial, paths):
                accessible.append(category)
            else:
                reason = "not_readable"
                if category in ROOT_REQUIRED_CATEGORIES and not is_root:
                    reason = "requires_root"
                inaccessible[category] = reason

        capability = self._resolve_capability(
            is_root=is_root,
            inotifyd_available=inotifyd_available,
            accessible=accessible,
            required=policy.required_categories,
        )

        reasons: List[str] = []
        if not is_root:
            reasons.append("adb_not_rooted")
        if not inotifyd_available:
            reasons.append("inotifyd_missing")
        for cat, why in inaccessible.items():
            reasons.append(f"{cat}:{why}")

        return ProbeResult(
            capability=capability,
            accessible_categories=accessible,
            inaccessible_categories=inaccessible,
            is_root=is_root,
            reasons=reasons,
        )

    # ------------------------------------------------------------------
    # 探测原子
    # ------------------------------------------------------------------

    def _detect_root(self, serial: str) -> bool:
        """adb -s S shell id → 解析 uid=0 判 root。"""
        try:
            result = self._adb.shell(serial, "id", timeout=self._timeout)
            # 典型输出: "uid=0(root) gid=0(root) groups=0(root) ..." 或 "uid=2000(shell) ..."
            return "uid=0" in (result.stdout or "")
        except Exception as exc:
            logger.debug("probe_root_failed serial=%s err=%s", serial, exc)
            return False

    def _detect_inotifyd(self, serial: str) -> bool:
        """检查设备上是否存在 inotifyd 可执行（toybox/busybox 内置）。"""
        try:
            # `which inotifyd` 在 toybox 下返回路径；不存在时 returncode != 0 → AdbError
            self._adb.shell(serial, "which inotifyd", timeout=self._timeout)
            return True
        except Exception as exc:
            logger.debug("probe_inotifyd_missing serial=%s err=%s", serial, exc)
            return False

    def _any_readable(self, serial: str, paths: List[str]) -> bool:
        """paths 中任意一个目录可读（ls -d 成功）即视为分类可访问。"""
        for path in paths:
            try:
                # -d 仅测试目录本身；ls 的目录如果不可读会在 stderr 报错并 non-zero
                self._adb.shell(
                    serial, f"ls -d {shlex.quote(path)}", timeout=self._timeout,
                )
                return True
            except Exception:
                continue
        return False

    @staticmethod
    def _resolve_capability(
        *,
        is_root: bool,
        inotifyd_available: bool,
        accessible: List[str],
        required: List[str],
    ) -> WatcherCapability:
        """根据探测到的原子信号综合得出 capability。"""
        # required 至少有一项可访问才算"可用"
        covered_required = [c for c in required if c in accessible]
        if not covered_required:
            # 没有任何必需分类可读 → 完全不可用
            return WatcherCapability.UNAVAILABLE

        if inotifyd_available:
            return (
                WatcherCapability.INOTIFYD_ROOT
                if is_root else WatcherCapability.INOTIFYD_SHELL
            )
        # inotifyd 不可用但目录可读 → 降级轮询
        return WatcherCapability.POLLING


# ----------------------------------------------------------------------
# InotifydSource — 长连接订阅
# ----------------------------------------------------------------------

# 默认订阅 mask：创建 + 关闭写入 + 移入（对 crash 文件足够）
# 参考 AOSP toybox inotifyd event chars:
#   n = IN_CREATE, w = IN_CLOSE_WRITE, x = IN_MOVED_TO
DEFAULT_EVENT_MASK = "nwx"
VALID_EVENT_CHARS = set("acdemnowx")


class InotifydSource:
    """`adb shell inotifyd -` 长连接，后台线程读 stdout 解析为 WatcherEvent。

    生命周期：
        s = InotifydSource(adb_path=..., serial=..., paths_by_category=..., on_event=cb)
        s.start()        # 启动 Popen + 读线程
        ...
        s.stop(timeout)  # 发 SIGTERM + join；不抛

    回调契约：
        on_event(event: WatcherEvent) 在后台线程中被调用；调用方需自己保证线程安全
        回调抛异常不会打断读循环（仅日志警告）；保证 inotifyd stdout 不会被反压

    不在本类职责：
        - 重连：上层（DeviceLogWatcher）自己观察 is_running() 重启本实例
        - 去重：emitter 侧 (job_id, seq_no) 幂等 + 后端 ON CONFLICT DO NOTHING 兜底
        - 事件批处理：交 EventBatcher（阶段 4）
    """

    def __init__(
        self,
        *,
        adb_path: str,
        serial: str,
        paths_by_category: Dict[str, List[str]],
        on_event: Callable[[WatcherEvent], None],
        event_mask: str = DEFAULT_EVENT_MASK,
        startup_timeout_seconds: float = 3.0,
    ) -> None:
        self._adb_path = str(adb_path)
        self._serial = str(serial)
        self._on_event = on_event
        self._event_mask = self._normalize_mask(event_mask)
        self._startup_timeout = float(startup_timeout_seconds)

        # 扁平化 path → category 反查表（供 stdout 解析时 O(1) 回溯）
        self._dir_to_category: Dict[str, str] = {}
        for category, paths in paths_by_category.items():
            for p in paths:
                self._dir_to_category[p] = category
        if not self._dir_to_category:
            raise ValueError("paths_by_category must not be empty")

        self._process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动 adb Popen + 读线程。重复 start 为 no-op。"""
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return
            self._stop_evt.clear()
            cmd = self._build_command()
            logger.info(
                "inotifyd_start serial=%s dirs=%s mask=%s",
                self._serial, list(self._dir_to_category.keys()), self._event_mask,
            )
            try:
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,  # 行缓冲
                )
            except (OSError, FileNotFoundError) as exc:
                # adb 二进制不存在/权限问题 —— 直接抛，上层决定降级 polling
                raise RuntimeError(
                    f"inotifyd_spawn_failed serial={self._serial} err={exc}"
                ) from exc
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                name=f"inotifyd-reader-{self._serial}",
                daemon=True,
            )
            self._reader_thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """请求停止；不抛。

        步骤：
            1. 标记 stop flag（读线程观察到 Popen 退出会自动结束）
            2. SIGTERM 本地 Popen，最多等 timeout 秒；超时 SIGKILL
            3. best-effort `adb shell pkill inotifyd`（不阻塞调用方）
            4. join 读线程
        """
        with self._lock:
            self._stop_evt.set()
            proc, thread = self._process, self._reader_thread
            self._process, self._reader_thread = None, None

        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning("inotifyd_terminate_timeout serial=%s → kill", self._serial)
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
                except Exception:
                    logger.exception("inotifyd_kill_failed serial=%s", self._serial)
            except Exception:
                logger.exception("inotifyd_terminate_failed serial=%s", self._serial)

        # 设备侧残留 inotifyd 清理（best-effort，短超时；失败只记日志）
        self._cleanup_device_process()

        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

        logger.info("inotifyd_stopped serial=%s", self._serial)

    def is_running(self) -> bool:
        with self._lock:
            return (
                self._process is not None
                and self._process.poll() is None
                and not self._stop_evt.is_set()
            )

    # ------------------------------------------------------------------
    # 命令构造与解析
    # ------------------------------------------------------------------

    def _build_command(self) -> List[str]:
        """构造 `adb -s SERIAL shell inotifyd - /path1:mask /path2:mask ...`。

        inotifyd 参数形式:
            inotifyd PROG DIR:MASK [DIR:MASK ...]
            PROG="-" 表示把事件输出到 stdout（每行 "mask\\tdir\\tfile"）
        """
        targets = [f"{path}:{self._event_mask}" for path in self._dir_to_category.keys()]
        # 注意：单进程订阅所有目录；避免多 Popen 管理复杂度
        shell_cmd = " ".join(["inotifyd", "-"] + [shlex.quote(t) for t in targets])
        return [self._adb_path, "-s", self._serial, "shell", shell_cmd]

    @staticmethod
    def _normalize_mask(mask: str) -> str:
        """保留 VALID_EVENT_CHARS 中的字符，保持原顺序去重。"""
        seen: set = set()
        out: List[str] = []
        for c in mask:
            if c in VALID_EVENT_CHARS and c not in seen:
                seen.add(c)
                out.append(c)
        if not out:
            out = list(DEFAULT_EVENT_MASK)
        return "".join(out)

    def _parse_line(self, line: str) -> Optional[WatcherEvent]:
        """将 inotifyd stdout 一行转成 WatcherEvent；解析失败返回 None。

        期望格式（toybox inotifyd 默认）:
            "<mask>\\t<dir>\\t<file>"
        """
        line = line.rstrip("\r\n")
        if not line:
            return None
        parts = line.split("\t")
        if len(parts) < 3:
            logger.debug("inotifyd_line_unparseable serial=%s line=%r", self._serial, line)
            return None
        mask, dir_path, filename = parts[0], parts[1], parts[2]
        category = self._dir_to_category.get(dir_path)
        if category is None:
            # 未知目录 —— 理论上不会（订阅的就是这些）；保守丢弃
            logger.debug("inotifyd_unknown_dir serial=%s dir=%s", self._serial, dir_path)
            return None
        return WatcherEvent(
            category=category,
            event_mask=mask,
            dir_path=dir_path,
            filename=filename,
            full_path=f"{dir_path.rstrip('/')}/{filename}" if filename else dir_path,
            detected_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # 读循环（后台线程）
    # ------------------------------------------------------------------

    def _reader_loop(self) -> None:
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        try:
            for line in iter(proc.stdout.readline, ""):
                if self._stop_evt.is_set():
                    break
                try:
                    event = self._parse_line(line)
                except Exception:
                    logger.exception("inotifyd_parse_error serial=%s", self._serial)
                    continue
                if event is None:
                    continue
                try:
                    self._on_event(event)
                except Exception:
                    # callback 异常不中断 stdout 消费（反压会阻塞 inotifyd）
                    logger.exception(
                        "inotifyd_callback_error serial=%s category=%s file=%s",
                        self._serial, event.category, event.filename,
                    )
        except Exception:
            logger.exception("inotifyd_reader_loop_crash serial=%s", self._serial)
        finally:
            logger.debug("inotifyd_reader_exit serial=%s", self._serial)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _cleanup_device_process(self) -> None:
        """`adb shell pkill inotifyd` —— best-effort，失败只记日志。

        使用 subprocess.run 直接调用；避免依赖 AdbWrapper 抛 AdbError 中断本流程。
        """
        try:
            subprocess.run(
                [self._adb_path, "-s", self._serial, "shell", "pkill", "inotifyd"],
                capture_output=True, text=True, timeout=3.0,
            )
        except Exception as exc:
            logger.debug("inotifyd_pkill_best_effort_failed serial=%s err=%s", self._serial, exc)


__all__ = [
    "WatcherCapability",
    "WatcherEvent",
    "ProbeResult",
    "CapabilityProber",
    "InotifydSource",
    "DEFAULT_EVENT_MASK",
]
