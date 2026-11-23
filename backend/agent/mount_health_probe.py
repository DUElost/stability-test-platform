"""心跳挂载健康探针（ADR-0064 D5）——非阻塞快照 + 后台可达性探测。

心跳主循环禁止同步触达非本地文件系统（``stat`` / ``exists`` / ``ismount`` 等会在
NFS hard mount 拥塞时进入不可中断睡眠）。本模块：

1. 挂载在位判定只读本地内存文件 ``/proc/self/mountinfo``；
2. 可达性 / 可读写探测放到独立后台线程；
3. 心跳主循环只读带时间戳的缓存快照；探针阻塞时立即返回并标 ``probe_stale=true``。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Set

logger = logging.getLogger(__name__)

_MOUNTINFO_PATH = "/proc/self/mountinfo"
_DEFAULT_PROBE_INTERVAL_SECONDS = 30.0
_DEFAULT_STALE_AFTER_SECONDS = 90.0


def unescape_mount_path(raw: str) -> str:
    """解码 mountinfo / mounts 路径中的八进制转义（如 ``\\040`` → 空格）。"""
    out: List[str] = []
    i = 0
    while i < len(raw):
        if raw[i] == "\\" and i + 3 < len(raw):
            esc = raw[i + 1 : i + 4]
            if all(ch in "01234567" for ch in esc):
                out.append(chr(int(esc, 8)))
                i += 4
                continue
        out.append(raw[i])
        i += 1
    return "".join(out)


def parse_mountinfo_targets(text: str) -> Set[str]:
    """从 ``/proc/self/mountinfo`` 文本解析挂载目标路径集合。

    字段格式（kernel）：``… mount_id parent_id major:minor root mount_point …``；
    ``mount_point`` 是第 5 列（1-based），可能含 ``\\040`` 等转义。
    """
    targets: Set[str] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        # mountinfo 在 optional fields 后有 " - " 分隔符；目标路径在前半段第 5 列。
        head = line.split(" - ", 1)[0]
        parts = head.split(" ")
        if len(parts) < 5:
            continue
        targets.add(unescape_mount_path(parts[4]))
    return targets


def read_mountinfo_targets(
    *,
    mountinfo_path: str = _MOUNTINFO_PATH,
    reader: Optional[Callable[[str], str]] = None,
) -> Set[str]:
    """读取并解析本地 mountinfo（纯本地 procfs，不触达 NFS）。"""
    read = reader or (lambda path: open(path, "r", encoding="utf-8", errors="replace").read())
    try:
        return parse_mountinfo_targets(read(mountinfo_path))
    except OSError as exc:
        logger.warning("mountinfo_read_failed path=%s err=%s", mountinfo_path, exc)
        return set()


def normalize_mount_point(point: str) -> str:
    """规范化配置挂载点：去尾部斜杠（根 ``/`` 除外）。"""
    if not point:
        return point
    if point == "/":
        return "/"
    return point.rstrip("/")


def path_is_mounted(point: str, mount_targets: Set[str]) -> bool:
    """等价于 ``os.path.ismount`` 的本地判定：目标是否作为挂载点出现在 mountinfo。"""
    norm = normalize_mount_point(point)
    if norm in mount_targets:
        return True
    # mountinfo 可能保留尾斜杠以外的字面形式；再试原始字符串。
    return point in mount_targets


def probe_mount_reachable(point: str) -> bool:
    """后台可达性/可读写探针（允许阻塞；禁止在心跳主循环调用）。"""
    try:
        if not os.path.isdir(point):
            return False
        # listdir + access 覆盖「挂载在位但服务端不可达」；W_OK 覆盖只读挂死面。
        os.listdir(point)
        return os.access(point, os.R_OK | os.W_OK)
    except OSError:
        return False


def check_mounts_presence(
    mount_points: Iterable[str],
    *,
    mount_targets: Optional[Set[str]] = None,
    mountinfo_path: str = _MOUNTINFO_PATH,
    reader: Optional[Callable[[str], str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """仅基于 mountinfo 的非阻塞挂载在位检查（无 exists / ismount）。"""
    targets = (
        mount_targets
        if mount_targets is not None
        else read_mountinfo_targets(mountinfo_path=mountinfo_path, reader=reader)
    )
    result: Dict[str, Dict[str, Any]] = {}
    for point in mount_points:
        mounted = path_is_mounted(point, targets)
        result[point] = {
            "ok": mounted,
            "path": point,
            "mounted": mounted,
            "reachable": None,
            "probe_stale": True,
            "probed_at": None,
        }
    return result


class MountHealthProbe:
    """KernelUsbWatch 同构：``snapshot`` 非阻塞；到期时起后台探测写回缓存。"""

    def __init__(
        self,
        *,
        interval_seconds: float = _DEFAULT_PROBE_INTERVAL_SECONDS,
        stale_after_seconds: float = _DEFAULT_STALE_AFTER_SECONDS,
        probe_fn: Callable[[str], bool] = probe_mount_reachable,
        mountinfo_reader: Optional[Callable[[str], str]] = None,
        mountinfo_path: str = _MOUNTINFO_PATH,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._interval_seconds = float(interval_seconds)
        self._stale_after_seconds = float(stale_after_seconds)
        self._probe_fn = probe_fn
        self._mountinfo_reader = mountinfo_reader
        self._mountinfo_path = mountinfo_path
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._lock = threading.Lock()
        self._thread_local = threading.local()
        self._probe_generation: int = 0
        self._active_probe_generation: Optional[int] = None
        self._last_probe_monotonic: Optional[float] = None
        self._reachable_by_point: Dict[str, bool] = {}
        self._probed_at_wall: Optional[float] = None

    def current_generation(self) -> int:
        """返回当前探针代际编号，供调用方在拷贝挂载列表时同锁绑定（#4077）。"""
        with self._lock:
            return self._probe_generation

    @contextmanager
    def bound_generation(self, expected_generation: int) -> Iterator[None]:
        """在当前线程绑定本次 ``snapshot`` 必须匹配的探针代际（不改变 ``snapshot(points)`` 签名）。"""
        prev = getattr(self._thread_local, "expected_generation", None)
        self._thread_local.expected_generation = expected_generation
        try:
            yield
        finally:
            self._thread_local.expected_generation = prev

    def reset(self) -> None:
        """清除已缓存的可达性快照并递增代际，隔离旧探测后台线程（#4077）。"""
        with self._lock:
            self._probe_generation += 1
            self._active_probe_generation = None
            self._last_probe_monotonic = None
            self._reachable_by_point = {}
            self._probed_at_wall = None

    def snapshot(self, mount_points: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """非阻塞快照；必要时安排后台探测。"""
        with self._lock:
            bound_gen = getattr(self._thread_local, "expected_generation", None)
            sample_gen = self._probe_generation if bound_gen is None else bound_gen
        points = list(mount_points)
        targets = read_mountinfo_targets(
            mountinfo_path=self._mountinfo_path,
            reader=self._mountinfo_reader,
        )
        now = self._monotonic()
        with self._lock:
            superseded = self._probe_generation != sample_gen
            probing = self._active_probe_generation == self._probe_generation
            last = self._last_probe_monotonic
            reachable_map = {} if superseded else dict(self._reachable_by_point)
            probed_at = None if superseded else self._probed_at_wall
            stale = (
                superseded
                or probing
                or last is None
                or (now - last) > self._stale_after_seconds
            )
        result: Dict[str, Dict[str, Any]] = {}
        for point in points:
            mounted = path_is_mounted(point, targets)
            reachable = reachable_map.get(point)
            if not mounted:
                ok = False
            elif reachable is None:
                # 尚无探测结果：仅凭在位，标 stale，不把健康面误判为挂死。
                ok = True
            else:
                ok = bool(reachable)
            result[point] = {
                "ok": ok,
                "path": point,
                "mounted": mounted,
                "reachable": reachable,
                "probe_stale": stale,
                "probed_at": probed_at,
            }
        self._maybe_start_probe(points, expected_generation=sample_gen)
        return result

    def _maybe_start_probe(
        self,
        mount_points: List[str],
        *,
        expected_generation: Optional[int] = None,
    ) -> None:
        with self._lock:
            gen = self._probe_generation
            if expected_generation is not None and gen != expected_generation:
                return
            if self._active_probe_generation == gen:
                return
            now = self._monotonic()
            if (
                self._last_probe_monotonic is not None
                and now - self._last_probe_monotonic < self._interval_seconds
            ):
                return
            self._active_probe_generation = gen
            # 先推进节流戳：探测挂起期间心跳只读缓存并标 stale，不重复起线程。
            self._last_probe_monotonic = now
        threading.Thread(
            target=self._probe_worker,
            args=(list(mount_points), gen),
            name="mount-health-probe",
            daemon=True,
        ).start()

    def _probe_worker(self, mount_points: List[str], generation: int) -> None:
        reachable: Dict[str, bool] = {}
        try:
            for point in mount_points:
                try:
                    reachable[point] = bool(self._probe_fn(point))
                except Exception as exc:  # 探针异常不得冒进心跳线程
                    logger.debug("mount_probe_point_failed path=%s err=%s", point, exc)
                    reachable[point] = False
        finally:
            with self._lock:
                # reset() 后已进入新代际，旧后台探针的结果与节流戳一律丢弃。
                if generation == self._probe_generation:
                    self._reachable_by_point = reachable
                    self._probed_at_wall = self._wall_clock()
                    self._active_probe_generation = None
                    self._last_probe_monotonic = self._monotonic()
