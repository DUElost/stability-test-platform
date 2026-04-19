"""LogPuller — 异步文件拉取 + envelope 富化。

职责（KISS / 5B1）：
    - AEE / VENDOR_AEE 事件触发异步 `adb pull`，把设备侧 crash 文件拉到 NFS
    - 计算 sha256 / size_bytes / first_lines，通过 on_pull_done 回调把 enrichment
      传回 DeviceLogWatcher，由后者最终 emit 到 outbox
    - ANR / MOBILELOG **不走** puller（快路径仅写元数据，参考 batcher 批量 emit）

边界（YAGNI）：
    - 不做 bugreport 导出（阶段 5B2 的职责）
    - 不落 JobArtifact 表（5B2 由独立端点负责；当前 envelope.artifact_uri 仅记 NFS 路径）
    - 不做重试：单次 pull 失败即 emit 空 enrichment；事件不丢 outbox
    - 不做 LRU 清理：NFS 配额由运维层外部处理

线程模型：
    - 生产者：DeviceLogWatcher._on_immediate（batcher 的 flusher 线程）
    - 消费者：固定 max_workers 个 daemon 线程，轮询内部 queue.Queue
    - on_pull_done：在 worker 线程中同步调用；调用方（SignalEmitter.emit）自带锁

契约：
    on_pull_done(event, enrichment) 中的 enrichment：
        { "artifact_uri": str | None,
          "sha256":       str | None,
          "size_bytes":   int | None,
          "first_lines":  str | None }
    失败路径：enrichment = {} （所有字段缺省 → emit 时不会写入 envelope）
    超大文件（>max_file_mb）：仅回 size_bytes，artifact_uri=None（本地已删）
"""

from __future__ import annotations

import hashlib
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .sources import WatcherEvent

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 统计
# ----------------------------------------------------------------------

@dataclass
class PullerStats:
    """运行期统计；DeviceLogWatcher.stats 会聚合。"""

    submits_total: int = 0
    submits_dropped: int = 0      # 队列满导致的降级
    pulls_ok: int = 0
    pulls_failed: int = 0
    pulls_oversized: int = 0      # 超过 max_file_mb 的文件（仅记元数据）


# ----------------------------------------------------------------------
# 回调类型签名
# ----------------------------------------------------------------------

OnPullDone = Callable[[WatcherEvent, Dict[str, Any]], None]


# ----------------------------------------------------------------------
# LogPuller
# ----------------------------------------------------------------------

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


class LogPuller:
    """per-device 异步文件拉取器。

    用法（典型）：
        puller = LogPuller(
            adb=adb,
            nfs_base_dir="/mnt/nfs/stability",
            job_id=123, host_id="HOST", serial="SX",
            on_pull_done=watcher._on_pull_done,
        )
        puller.start()
        # 在 _on_immediate 中：
        puller.submit(event)
        ...
        puller.stop(drain=True, timeout=5.0)
    """

    def __init__(
        self,
        *,
        adb,
        nfs_base_dir: str,
        job_id: int,
        host_id: str,
        serial: str,
        on_pull_done: OnPullDone,
        max_workers: int = 2,
        queue_maxsize: int = 256,
        pull_timeout_seconds: float = 30.0,
        max_file_mb: int = 500,
        first_lines_max_lines: int = 200,
        first_lines_max_bytes: int = 4096,
    ) -> None:
        self._adb = adb
        self._nfs_base_dir = Path(nfs_base_dir)
        self._job_id = int(job_id)
        self._host_id = str(host_id)
        self._serial = str(serial)
        self._on_done = on_pull_done
        self._max_workers = max(1, int(max_workers))
        self._queue: "queue.Queue[WatcherEvent]" = queue.Queue(maxsize=int(queue_maxsize))
        self._pull_timeout = float(pull_timeout_seconds)
        self._max_file_bytes = int(max_file_mb) * 1024 * 1024
        self._first_lines_max_lines = max(1, int(first_lines_max_lines))
        self._first_lines_max_bytes = max(256, int(first_lines_max_bytes))
        self._stop_evt = threading.Event()
        self._workers: List[threading.Thread] = []
        self._started = False
        self.stats = PullerStats()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._stop_evt.clear()
        for i in range(self._max_workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"puller-{self._serial}-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)
        self._started = True
        logger.debug(
            "log_puller_started serial=%s workers=%d queue_max=%d",
            self._serial, self._max_workers, self._queue.maxsize,
        )

    def stop(self, *, drain: bool = True, timeout: float = 5.0) -> None:
        """停止 puller。

        drain=True：等内部队列排空或 timeout 秒到，再置停止位并 join；
                    超时时残余事件仍在 queue 中 → **直接降级为空 enrichment 回调**，
                    保证 outbox 不丢（事件到达即 emit，只是没有 artifact_uri）。
        drain=False：立即置停止位；worker 循环 0.2s 内退出；队列残余统一降级。
        """
        if not self._started:
            return
        deadline = time.monotonic() + max(0.0, float(timeout))
        if drain:
            while time.monotonic() < deadline:
                if self._queue.empty():
                    break
                time.sleep(0.05)

        self._stop_evt.set()

        # 等 worker 退出
        end = time.monotonic() + 1.0  # worker get timeout 是 0.2s，给 1s 足够
        for t in list(self._workers):
            remain = max(0.1, end - time.monotonic())
            t.join(timeout=remain)

        # 降级：queue 中未处理的残余事件走空 enrichment，确保 outbox 不丢
        self._drain_remaining_as_empty()

        self._workers.clear()
        self._started = False
        logger.debug(
            "log_puller_stopped serial=%s stats=%s",
            self._serial, self.stats,
        )

    def _drain_remaining_as_empty(self) -> None:
        """stop 时把队列里剩下的事件直接用空 enrichment 回调。"""
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                return
            self.stats.submits_dropped += 1
            try:
                self._on_done(event, {})
            except Exception:
                logger.exception(
                    "log_puller_on_done_failed_on_drain serial=%s file=%s",
                    self._serial, event.filename,
                )

    # ------------------------------------------------------------------
    # 生产者入口
    # ------------------------------------------------------------------

    def submit(self, event: WatcherEvent) -> None:
        """非阻塞入队。未启动或队列满时降级调用 on_done(event, {})。"""
        self.stats.submits_total += 1
        if not self._started or self._stop_evt.is_set():
            # 未启动 / 已停止 → 降级直接回调
            self.stats.submits_dropped += 1
            self._safe_on_done(event, {})
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self.stats.submits_dropped += 1
            logger.warning(
                "log_puller_queue_full serial=%s file=%s (degraded to empty enrichment)",
                self._serial, event.filename,
            )
            self._safe_on_done(event, {})

    # ------------------------------------------------------------------
    # 内部 worker
    # ------------------------------------------------------------------

    def _worker_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                event = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            enrichment: Dict[str, Any] = {}
            try:
                enrichment = self._do_pull(event)
            except Exception:
                logger.exception(
                    "log_puller_do_pull_unhandled serial=%s file=%s",
                    self._serial, event.filename,
                )
                self.stats.pulls_failed += 1

            self._safe_on_done(event, enrichment)

    def _safe_on_done(self, event: WatcherEvent, enrichment: Dict[str, Any]) -> None:
        try:
            self._on_done(event, enrichment)
        except Exception:
            logger.exception(
                "log_puller_on_done_failed serial=%s file=%s",
                self._serial, event.filename,
            )

    # ------------------------------------------------------------------
    # pull 主逻辑
    # ------------------------------------------------------------------

    def _do_pull(self, event: WatcherEvent) -> Dict[str, Any]:
        """拉文件到 NFS + 计算 enrichment。

        返回 dict：成功时含 4 个字段；失败返回 {}（emit 时不写入）。
        """
        local_path = self._compose_local_path(event)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            result = self._adb.pull(
                self._serial, event.full_path, str(local_path),
            )
        except Exception:
            logger.exception(
                "log_puller_adb_pull_exception serial=%s remote=%s",
                self._serial, event.full_path,
            )
            self.stats.pulls_failed += 1
            return {}

        rc = getattr(result, "returncode", 1)
        if rc != 0 or not local_path.exists():
            logger.warning(
                "log_puller_pull_failed serial=%s remote=%s rc=%s",
                self._serial, event.full_path, rc,
            )
            self.stats.pulls_failed += 1
            # 清理可能残留的半成品
            try:
                if local_path.exists():
                    local_path.unlink()
            except Exception:
                pass
            return {}

        try:
            size_bytes = local_path.stat().st_size
        except OSError:
            self.stats.pulls_failed += 1
            return {}

        if size_bytes > self._max_file_bytes:
            logger.warning(
                "log_puller_file_oversized serial=%s remote=%s size=%d limit=%d",
                self._serial, event.full_path, size_bytes, self._max_file_bytes,
            )
            try:
                local_path.unlink()
            except Exception:
                pass
            self.stats.pulls_oversized += 1
            return {
                "artifact_uri": None,
                "sha256":       None,
                "size_bytes":   size_bytes,
                "first_lines":  None,
            }

        sha256 = self._compute_sha256(local_path)
        first_lines = self._read_first_lines(local_path)

        self.stats.pulls_ok += 1
        return {
            "artifact_uri": str(local_path),
            "sha256":       sha256,
            "size_bytes":   size_bytes,
            "first_lines":  first_lines,
        }

    # ------------------------------------------------------------------
    # 路径 / hash / first_lines 辅助
    # ------------------------------------------------------------------

    def _compose_local_path(self, event: WatcherEvent) -> Path:
        """组装 NFS 落盘路径：
            <nfs_base>/jobs/<job_id>/<category>/<epoch_ms>_<safe_filename>
        """
        ts = event.detected_at
        try:
            epoch_ms = int(ts.timestamp() * 1000)
        except Exception:
            epoch_ms = int(time.time() * 1000)
        safe_name = _FILENAME_SAFE_RE.sub("_", event.filename) or "unnamed"
        return (
            self._nfs_base_dir
            / "jobs" / str(self._job_id)
            / event.category
            / f"{epoch_ms}_{safe_name}"
        )

    def _compute_sha256(self, path: Path) -> Optional[str]:
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            logger.exception("log_puller_sha256_failed path=%s", path)
            return None

    def _read_first_lines(self, path: Path) -> Optional[str]:
        try:
            with open(path, "rb") as f:
                data = f.read(self._first_lines_max_bytes)
            text = data.decode("utf-8", errors="replace")
            lines = text.splitlines()[: self._first_lines_max_lines]
            return "\n".join(lines)
        except Exception:
            logger.exception("log_puller_first_lines_failed path=%s", path)
            return None


__all__ = [
    "LogPuller",
    "PullerStats",
    "OnPullDone",
]
