"""ArtifactUploader — 进程级单例，异步上送 JobArtifact 到后端。

ADR-0018 5B2 契约（严格边界）：
    - 只处理 watcher LogPuller 成功拉到 NFS 的文件 crash artifact
    - 调用 POST /api/v1/agent/jobs/{job_id}/artifacts
    - **不**走 outbox、**不**落 SQLite：失败即丢，log_signal 主链路不受影响
    - 幂等由后端 (job_id, storage_uri) 唯一键保证，Agent 侧不做去重
    - 白名单由调用方保证；本 uploader 只转发

线程模型：
    - 单 daemon worker 线程 + bounded queue.Queue
    - submit() 非阻塞：队列满立即丢（stats.submits_dropped++）
    - stop(drain=True, timeout)：阻塞到队列排空或超时；超时后残余条目直接丢

与 watcher 主链解耦（关键不变量）：
    - submit() 永不抛：内部异常全吞
    - worker 异常不扩散：每条记录独立 try/except
    - 未 configure/未 start 时调用 submit → 静默丢（防止测试环境 crash）
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 统计
# ----------------------------------------------------------------------

@dataclass
class UploaderStats:
    submits_total: int = 0
    submits_dropped: int = 0     # 未 configure / 队列满 / 已 stop
    posts_ok: int = 0
    posts_failed: int = 0
    posts_conflict: int = 0      # 后端返回 created=False（幂等命中）

    def to_dict(self) -> Dict[str, int]:
        return {
            "submits_total":   self.submits_total,
            "submits_dropped": self.submits_dropped,
            "posts_ok":        self.posts_ok,
            "posts_failed":    self.posts_failed,
            "posts_conflict":  self.posts_conflict,
        }


# ----------------------------------------------------------------------
# Job 内部 payload（由 submit 构造，由 worker 消费）
# ----------------------------------------------------------------------

@dataclass
class _ArtifactJob:
    job_id: int
    artifact_type: str
    storage_uri: str
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None
    source_category: Optional[str] = None
    source_path_on_device: Optional[str] = None


# ----------------------------------------------------------------------
# Uploader
# ----------------------------------------------------------------------

class ArtifactUploader:
    """进程级单例；由 main.py 在 Agent 启动时 configure + start。"""

    _instance: Optional["ArtifactUploader"] = None
    _instance_lock = threading.Lock()

    DEFAULT_QUEUE_MAXSIZE = 256
    DEFAULT_TIMEOUT = 10.0

    def __init__(self) -> None:
        self._api_url: str = ""
        self._agent_secret: str = ""
        self._http_timeout: float = self.DEFAULT_TIMEOUT
        self._queue: "queue.Queue[_ArtifactJob]" = queue.Queue(
            maxsize=self.DEFAULT_QUEUE_MAXSIZE,
        )
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._configured = False
        self._started = False
        self._session: Optional[requests.Session] = None
        self.stats = UploaderStats()

    # ------------------------------------------------------------------
    # 单例
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> "ArtifactUploader":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        """仅供测试：销毁单例并尽力停掉 worker。"""
        with cls._instance_lock:
            inst = cls._instance
            cls._instance = None
        if inst is not None:
            try:
                inst.stop(drain=False, timeout=0.5)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 配置 / 启停
    # ------------------------------------------------------------------

    def configure(
        self,
        *,
        api_url: str,
        agent_secret: str = "",
        http_timeout_seconds: float = DEFAULT_TIMEOUT,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        session: Optional[requests.Session] = None,
    ) -> None:
        """由 Agent main.py 注入依赖；start() 之前必须先 configure。"""
        if self._started:
            raise RuntimeError("configure() after start() is not allowed")
        self._api_url = api_url.rstrip("/")
        self._agent_secret = agent_secret or ""
        self._http_timeout = max(1.0, float(http_timeout_seconds))
        if queue_maxsize != self.DEFAULT_QUEUE_MAXSIZE:
            self._queue = queue.Queue(maxsize=int(queue_maxsize))
        self._session = session or requests.Session()
        self._configured = True
        logger.info(
            "artifact_uploader_configured api_url=%s timeout=%.1f queue_max=%d",
            self._api_url, self._http_timeout, self._queue.maxsize,
        )

    def start(self) -> None:
        if self._started:
            return
        if not self._configured:
            raise RuntimeError(
                "ArtifactUploader not configured — call configure(api_url=...) first"
            )
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="artifact-uploader",
            daemon=True,
        )
        self._thread.start()
        self._started = True
        logger.info("artifact_uploader_started")

    def stop(self, *, drain: bool = True, timeout: float = 5.0) -> None:
        """停 worker。drain=True → 等队列排空或 timeout。"""
        if not self._started:
            return
        if drain:
            # 简单等：worker 仍在消费；超时到立刻置 stop
            import time
            deadline = time.monotonic() + max(0.0, float(timeout))
            while time.monotonic() < deadline:
                if self._queue.empty():
                    break
                time.sleep(0.05)
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(1.0, float(timeout)))
        # 丢掉残余（fire-and-forget 契约）
        dropped = 0
        while True:
            try:
                self._queue.get_nowait()
                dropped += 1
            except queue.Empty:
                break
        if dropped:
            self.stats.submits_dropped += dropped
            logger.warning(
                "artifact_uploader_stopped_with_residual_dropped count=%d", dropped,
            )
        self._started = False
        logger.info(
            "artifact_uploader_stopped stats=%s", self.stats.to_dict(),
        )

    # ------------------------------------------------------------------
    # 生产者入口
    # ------------------------------------------------------------------

    def submit(
        self,
        *,
        job_id: int,
        artifact_type: str,
        storage_uri: str,
        size_bytes: Optional[int] = None,
        checksum: Optional[str] = None,
        source_category: Optional[str] = None,
        source_path_on_device: Optional[str] = None,
    ) -> None:
        """非阻塞；永不抛。队列满 / 未启动 → 静默丢 + stats 计数。"""
        self.stats.submits_total += 1
        if not self._started or self._stop_event.is_set():
            self.stats.submits_dropped += 1
            logger.debug(
                "artifact_uploader_not_running_dropped job_id=%d uri=%s",
                job_id, storage_uri,
            )
            return
        # 客户端侧极简校验：必填字段缺失直接丢；类型白名单由后端兜底
        if not storage_uri or not artifact_type or not job_id:
            self.stats.submits_dropped += 1
            logger.warning(
                "artifact_uploader_invalid_payload_dropped "
                "job_id=%s type=%s uri=%s",
                job_id, artifact_type, storage_uri,
            )
            return
        job = _ArtifactJob(
            job_id=int(job_id),
            artifact_type=str(artifact_type),
            storage_uri=str(storage_uri),
            size_bytes=size_bytes,
            checksum=checksum,
            source_category=source_category,
            source_path_on_device=source_path_on_device,
        )
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self.stats.submits_dropped += 1
            logger.warning(
                "artifact_uploader_queue_full_dropped job_id=%d uri=%s "
                "(fire-and-forget, log_signal unaffected)",
                job_id, storage_uri,
            )

    # ------------------------------------------------------------------
    # 内部 worker
    # ------------------------------------------------------------------

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._post_one(job)
            except Exception:
                self.stats.posts_failed += 1
                logger.exception(
                    "artifact_uploader_post_unhandled job_id=%d uri=%s",
                    job.job_id, job.storage_uri,
                )

    def _post_one(self, job: _ArtifactJob) -> None:
        if self._session is None:
            self.stats.posts_failed += 1
            return
        url = f"{self._api_url}/api/v1/agent/jobs/{job.job_id}/artifacts"
        payload: Dict[str, Any] = {
            "storage_uri": job.storage_uri,
            "artifact_type": job.artifact_type,
        }
        if job.size_bytes is not None:
            payload["size_bytes"] = int(job.size_bytes)
        if job.checksum:
            payload["checksum"] = job.checksum
        if job.source_category:
            payload["source_category"] = job.source_category
        if job.source_path_on_device:
            payload["source_path_on_device"] = job.source_path_on_device

        headers: Dict[str, str] = {}
        if self._agent_secret:
            headers["X-Agent-Secret"] = self._agent_secret

        try:
            resp = self._session.post(
                url, json=payload, headers=headers, timeout=self._http_timeout,
            )
        except Exception as exc:
            self.stats.posts_failed += 1
            logger.warning(
                "artifact_uploader_http_exception job_id=%d uri=%s err=%s",
                job.job_id, job.storage_uri, exc,
            )
            return

        if 200 <= resp.status_code < 300:
            try:
                body = resp.json()
                created = bool(body.get("data", {}).get("created", True))
            except Exception:
                created = True
            if created:
                self.stats.posts_ok += 1
            else:
                self.stats.posts_conflict += 1
            return

        self.stats.posts_failed += 1
        logger.warning(
            "artifact_uploader_http_error job_id=%d uri=%s status=%d body=%s",
            job.job_id, job.storage_uri, resp.status_code, resp.text[:200],
        )


__all__ = [
    "ArtifactUploader",
    "UploaderStats",
]
