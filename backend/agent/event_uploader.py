"""EventUploader — 连续上送 DeviceLogEvent（ADR-0028 D2）。

事件在 ``state=LOCAL`` 后入队，后台 copytree 到中心存储并回写 ``REMOTE``。
由 ``STP_EVENT_UPLOADER_ENABLED=1`` 门控；默认关闭，与 PlanRun 触发上送并存。
"""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import requests

try:
    from backend.agent.aee.paths import (
        PathOutsideRootError,
        get_aee_local_root,
        get_aee_nfs_root,
        resolve_path_under_aee_local,
        resolve_upload_devices_dir,
    )
    from backend.agent.upload_manager import UploadManager
except ImportError:
    from agent.aee.paths import (
        PathOutsideRootError,
        get_aee_local_root,
        get_aee_nfs_root,
        resolve_path_under_aee_local,
        resolve_upload_devices_dir,
    )
    from agent.upload_manager import UploadManager

logger = logging.getLogger(__name__)

_MAX_CONCURRENT = 2
_MAX_RETRIES = 5
_RETRY_FAILED_INTERVAL = 600.0
# ADR-0028 方案 A：UPLOAD_PENDING 事件由控制面 upload_task 在 PlanRun 终态后标记，
# Agent 侧需周期轮询（一次性启动轮询会错过后续标记）。
_RECOVER_POLL_INTERVAL = 30.0
# #380: 轮询 GET 加 limit，防止大 backlog 时每周期全量拉取。
_RECOVER_POLL_LIMIT = 200
_RETRY_FAILED_LIMIT = 200


def _event_uploader_enabled() -> bool:
    return os.getenv("STP_EVENT_UPLOADER_ENABLED", "0").strip().lower() in ("1", "true", "yes")


def _event_uploader_continuous() -> bool:
    """ADR-0028 方案 A：0=仅上传 UPLOAD_PENDING（过滤模型）；1=上传全部 LOCAL（全量模型）。"""
    return os.getenv("STP_EVENT_UPLOADER_CONTINUOUS", "0").strip().lower() in ("1", "true", "yes")


def _recover_states() -> str:
    """30s 快速轮询的状态集合（#380）。

    只覆盖「等待首次上送」的事件；UPLOADING/UPLOAD_FAILED 的恢复交给
    600s 慢速循环（`_retry_failed_loop`）——快速轮询若也拉这两个状态，
    会把在途/已达重试上限的事件反复以 attempt=0 重入队（重试上限失效、
    CIFS 上 rmtree-vs-copy 抖动）。in-flight 去重（`_active_ids`）兜底，
    但状态集合本身先收敛。

    continuous=0（默认过滤模型）：UPLOAD_PENDING；
    continuous=1（逃生阀）：LOCAL,UPLOAD_PENDING（模式切换不漏待传事件）。
    """
    if _event_uploader_continuous():
        return "LOCAL,UPLOAD_PENDING"
    return "UPLOAD_PENDING"


@dataclass
class _UploadJob:
    event_id: str
    local_path: str
    plan_run_id: Optional[int]
    serial: str
    platform: str
    event_type: str
    detected_at: str
    host_id: str
    job_id: Optional[int] = None
    attempt: int = 0
    # #382: HddSpill 溢出事件上送成功后必须释放本地磁盘，不受
    # STP_EVENT_UPLOADER_PRUNE_LOCAL（默认 0、按机灰度）约束。
    prune_after_upload: bool = False
    # #380: 重试 Timer 已排队（job 会回到队列）期间保留 in-flight 标记，
    # 防止轮询循环在退避窗口内重复入队。
    rescheduled: bool = False


class EventUploader:
    """进程级单例；configure + start 后消费上传队列。"""

    _instance: Optional["EventUploader"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._api_url = ""
        self._agent_secret = ""
        self._host_id = ""
        self._nfs_root = ""
        self._configured = False
        self._stop_evt = threading.Event()
        self._queue: queue.Queue[Optional[_UploadJob]] = queue.Queue(maxsize=512)
        self._slot = threading.Semaphore(_MAX_CONCURRENT)
        self._dispatcher: Optional[threading.Thread] = None
        self._retry_thread: Optional[threading.Thread] = None
        # #380: in-flight / queued 去重 —— 同一 event_id 至多一个 job
        # （队列中、在传、或退避重试等待中），轮询/重试/溢出三源重入队在此收敛。
        self._active_lock = threading.Lock()
        self._active_ids: set[str] = set()

    @classmethod
    def instance(cls) -> "EventUploader":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        with cls._instance_lock:
            inst = cls._instance
            cls._instance = None
        if inst is not None:
            inst.stop(timeout=0.5)

    @classmethod
    def is_enabled(cls) -> bool:
        return _event_uploader_enabled()

    def configure(
        self,
        *,
        api_url: str,
        agent_secret: str,
        host_id: str,
        nfs_root: str = "",
        force: bool = False,
    ) -> None:
        if self._configured and not force:
            return
        self._api_url = api_url.rstrip("/")
        self._agent_secret = agent_secret
        self._host_id = host_id
        self._nfs_root = nfs_root or str(get_aee_nfs_root())
        self._configured = bool(self._api_url and self._agent_secret and self._host_id)
        logger.info(
            "event_uploader_configured enabled=%s host=%s nfs_root=%s",
            _event_uploader_enabled(),
            self._host_id,
            self._nfs_root,
        )

    def is_configured(self) -> bool:
        return self._configured

    def start(self) -> None:
        # ADR-0028 方案 A：EventUploader 在两种模式下都启动——continuous=1 上传 LOCAL，
        # continuous=0 仅上传 UPLOAD_PENDING。仅当 ENABLED=0 时完全关闭。
        if not self._configured or not _event_uploader_enabled():
            logger.info("event_uploader_start_skipped enabled=%s configured=%s",
                        _event_uploader_enabled(), self._configured)
            return
        if self._dispatcher is not None and self._dispatcher.is_alive():
            return
        self._stop_evt.clear()
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop, name="event-uploader-dispatch", daemon=True,
        )
        self._dispatcher.start()
        self._retry_thread = threading.Thread(
            target=self._retry_failed_loop, name="event-uploader-retry", daemon=True,
        )
        self._retry_thread.start()
        threading.Thread(
            target=self._recover_pending, name="event-uploader-recover", daemon=True,
        ).start()
        logger.info("event_uploader_started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_evt.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._dispatcher is not None:
            self._dispatcher.join(timeout=timeout)

    def enqueue_local_event(
        self,
        *,
        event: Dict[str, Any],
        force: bool = False,
        prune_after_upload: bool = False,
    ) -> bool:
        """将事件入队。ADR-0028 方案 A：continuous=1 接受 LOCAL；continuous=0 默认拒绝
        （仅 UPLOAD_PENDING 经 _recover_pending 入队）。``force=True`` 用于 HddSpill——
        磁盘压力溢出不受过滤模型限制，必须始终可上送；溢出事件同时要求
        ``prune_after_upload=True``（#382：上送校验后必须释放本地磁盘）。

        #380: 同一 event_id 已在队列/在传/退避重试中时拒绝重复入队。
        """
        if not _event_uploader_enabled() or not self._configured:
            return False
        if not _event_uploader_continuous() and not force:
            # Plan A: Reconciler 不自动入队；upload_task 标记 UPLOAD_PENDING 后经 _recover_pending 轮询入队
            return False
        event_id = str(event["id"])
        job = _UploadJob(
            event_id=event_id,
            local_path=str(event["local_path"]),
            plan_run_id=event.get("plan_run_id"),
            serial=str(event.get("serial", "")),
            platform=str(event.get("platform", "UNKNOWN")),
            event_type=str(event.get("event_type", "UNKNOWN")),
            detected_at=str(event.get("detected_at", "")),
            host_id=str(event.get("host_id", self._host_id)),
            job_id=event.get("job_id"),
            prune_after_upload=prune_after_upload,
        )
        with self._active_lock:
            if event_id in self._active_ids:
                return False
            self._active_ids.add(event_id)
        try:
            self._queue.put_nowait(job)
            return True
        except queue.Full:
            self._forget_active(event_id)
            logger.warning("event_uploader_queue_full event_id=%s", job.event_id)
            return False

    def _forget_active(self, event_id: str) -> None:
        with self._active_lock:
            self._active_ids.discard(event_id)

    def _dispatch_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                job = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if job is None:
                break
            # #389: 拿到并发槽再起线程 —— 线程数被 _MAX_CONCURRENT 封顶，
            # 不再每 job 一线程趴在信号量上（洪峰时最多 512 个 parked 线程）。
            while not self._slot.acquire(timeout=1.0):
                if self._stop_evt.is_set():
                    self._forget_active(job.event_id)
                    return
            threading.Thread(
                target=self._run_upload_holding_slot,
                args=(job,),
                name=f"event-upload-{job.event_id[:8]}",
                daemon=True,
            ).start()

    def _run_upload_holding_slot(self, job: _UploadJob) -> None:
        try:
            self._upload_one(job)
        finally:
            self._slot.release()
            # 重试 Timer 排队期间保留 in-flight 标记（防止轮询在退避窗口重入队）；
            # 其余出口（终态/异常）都释放。
            if not job.rescheduled:
                self._forget_active(job.event_id)

    def _upload_one(self, job: _UploadJob) -> None:
        try:
            src = resolve_path_under_aee_local(job.local_path)
        except PathOutsideRootError:
            logger.warning(
                "event_uploader_unsafe_local_path event_id=%s path=%s",
                job.event_id, job.local_path,
            )
            return

        if job.plan_run_id is not None:
            dst_base = resolve_upload_devices_dir(self._nfs_root, int(job.plan_run_id))
        else:
            dst_base = Path(self._nfs_root) / "devices" / "unassigned" / job.event_id
        dst = dst_base / src.name

        if not src.is_dir():
            # #380: 本地目录缺失必须落到终态，否则 UPLOAD_PENDING 永久卡死、
            # merge_task 每轮烧满等待预算、且被轮询无限重入队。
            if dst.is_dir():
                # 本地已删（prune 后 REMOTE patch 失败的竞态）而远端仍在：
                # 信任远端副本，恢复 REMOTE 使 extract 可见。
                logger.warning(
                    "event_uploader_missing_local_remote_present event_id=%s dest=%s "
                    "— trusting existing remote copy",
                    job.event_id, dst,
                )
                self._patch_state(
                    job, state="REMOTE", remote_path=str(dst),
                    checksum=self._dir_sha256(dst),
                )
            else:
                logger.warning(
                    "event_uploader_missing_local event_id=%s path=%s — patch PULL_FAILED",
                    job.event_id, src,
                )
                self._patch_state(job, state="PULL_FAILED")
            return

        if dst.exists():
            remote_path = str(dst)
            dst_checksum = self._dir_sha256(dst)
            src_checksum = self._dir_sha256(src)
            if dst_checksum != src_checksum:
                logger.warning(
                    "event_uploader_remote_mismatch event_id=%s dest=%s — re-uploading",
                    job.event_id, dst,
                )
                shutil.rmtree(dst, ignore_errors=True)
            else:
                self._patch_state(
                    job, state="REMOTE", remote_path=remote_path, checksum=dst_checksum,
                )
                self._maybe_prune_local(job, remote_path=remote_path)
                return

        self._patch_state(job, state="UPLOADING")
        try:
            UploadManager._copytree_safe(str(src), str(dst))
            checksum = self._dir_sha256(dst)
            self._patch_state(job, state="REMOTE", remote_path=str(dst), checksum=checksum)
            self._maybe_prune_local(job, remote_path=str(dst))
            logger.info("event_uploader_ok event_id=%s dest=%s", job.event_id, dst)
        except Exception:
            logger.exception("event_uploader_failed event_id=%s attempt=%d", job.event_id, job.attempt)
            if job.attempt + 1 < _MAX_RETRIES:
                job.attempt += 1
                job.rescheduled = True
                delay = min(300.0, 2.0 ** job.attempt)
                threading.Timer(delay, lambda: self._queue.put(job)).start()
            else:
                self._patch_state(job, state="UPLOAD_FAILED")

    def _patch_state(
        self,
        job: _UploadJob,
        *,
        state: str,
        remote_path: Optional[str] = None,
        checksum: Optional[str] = None,
    ) -> None:
        payload = {
            "id": job.event_id,
            "serial": job.serial,
            "platform": job.platform,
            "event_type": job.event_type,
            "detected_at": job.detected_at,
            "state": state,
            "local_path": job.local_path,
            "remote_path": remote_path,
            "checksum": checksum,
            "plan_run_id": job.plan_run_id,
            "host_id": job.host_id,
            "job_id": job.job_id,
        }
        try:
            resp = requests.post(
                f"{self._api_url}/api/v1/agent/device-log-events",
                json={"events": [payload]},
                headers={"X-Agent-Secret": self._agent_secret},
                timeout=15.0,
            )
            if resp.status_code >= 400:
                logger.warning(
                    "event_uploader_patch_failed event_id=%s status=%s body=%s",
                    job.event_id, resp.status_code, resp.text[:200],
                )
        except Exception:
            logger.exception("event_uploader_patch_error event_id=%s", job.event_id)

    def _maybe_prune_local(self, job: _UploadJob, *, remote_path: str) -> None:
        """上送成功后可选删除本地目录并回写 PRUNED。

        两个触发条件（#382）：
        - ``STP_EVENT_UPLOADER_PRUNE_LOCAL=1``：按机灰度开关（#217，默认 0）；
        - ``job.prune_after_upload``：HddSpill 溢出事件 —— 磁盘压力阀要求
          上送校验后必须释放本地空间，不受灰度开关约束（恢复 #213 改造前
          「验证拷贝后 rmtree」的保证）。

        Order (#217 / CodeRabbit): ``rmtree`` first; patch ``PRUNED`` only after
        local delete succeeds so ``state=PRUNED`` always means local is gone.
        """
        env_prune = os.getenv("STP_EVENT_UPLOADER_PRUNE_LOCAL", "0").strip().lower() in (
            "1", "true", "yes",
        )
        if not (job.prune_after_upload or env_prune):
            return
        try:
            src = resolve_path_under_aee_local(job.local_path)
        except PathOutsideRootError:
            return
        local_root = get_aee_local_root().resolve(strict=False)
        if not src.is_dir() or src == local_root:
            logger.warning(
                "event_uploader_prune_refused event_id=%s path=%s",
                job.event_id, src,
            )
            return
        if not src.exists():
            return
        try:
            shutil.rmtree(src)
        except Exception:
            logger.exception(
                "event_uploader_prune_failed event_id=%s path=%s — leave state unchanged",
                job.event_id, src,
            )
            return
        try:
            from .aee.device_log_event_client import DeviceLogEventClient
        except ImportError:
            from agent.aee.device_log_event_client import DeviceLogEventClient
        client = DeviceLogEventClient.from_env(
            api_url=self._api_url,
            agent_secret=self._agent_secret,
            host_id=self._host_id,
        )
        if client is None:
            logger.warning(
                "event_uploader_prune_patch_skipped_no_client event_id=%s "
                "(local already deleted)",
                job.event_id,
            )
            return
        if not client.patch_event_state(
            event_id=job.event_id,
            state="PRUNED",
            serial=job.serial,
            platform=job.platform,
            event_type=job.event_type,
            detected_at=job.detected_at,
            local_path=job.local_path,
            plan_run_id=job.plan_run_id,
            job_id=job.job_id,
            remote_path=remote_path,
        ):
            logger.warning(
                "event_uploader_prune_patch_failed event_id=%s "
                "(local already deleted; remote_path still extractable)",
                job.event_id,
            )

    def _recover_pending(self) -> None:
        """30s 快速轮询：把「等待首次上送」的事件入队（#380）。

        continuous=1：LOCAL,UPLOAD_PENDING；continuous=0：UPLOAD_PENDING。
        UPLOADING/UPLOAD_FAILED 的恢复（含 Agent 重启后卡在 UPLOADING 的行）
        由 600s 的 ``_retry_failed_loop`` 承担 —— 两循环状态集不重叠，
        重试上限才真正生效。
        """
        while not self._stop_evt.wait(_RECOVER_POLL_INTERVAL):
            if not self._configured:
                continue
            try:
                _states = _recover_states()
                resp = requests.get(
                    f"{self._api_url}/api/v1/agent/device-log-events",
                    params={
                        "host_id": self._host_id,
                        "state": _states,
                        "limit": _RECOVER_POLL_LIMIT,
                    },
                    headers={"X-Agent-Secret": self._agent_secret},
                    timeout=15.0,
                )
                if resp.status_code >= 400:
                    logger.warning("event_uploader_recover_failed status=%s", resp.status_code)
                    continue
                for item in resp.json().get("data", {}).get("events", []):
                    self.enqueue_local_event(event={
                        "id": item["id"],
                        "local_path": item["local_path"],
                        "host_id": item.get("host_id", self._host_id),
                        "serial": item.get("serial", ""),
                        "platform": item.get("platform", "UNKNOWN"),
                        "event_type": item.get("event_type", "UNKNOWN"),
                        "detected_at": item.get("detected_at", ""),
                        "plan_run_id": item.get("plan_run_id"),
                        "job_id": item.get("job_id"),
                    }, force=True)
            except Exception:
                logger.exception("event_uploader_recover_error")

    def _retry_failed_loop(self) -> None:
        """600s 慢速恢复：重试 UPLOAD_FAILED + 找回卡在 UPLOADING 的行（#380）。

        in-flight 去重（`_active_ids`）保证正在传/退避中的事件不会被本循环
        重复入队；只有终态失败或中断（Agent 重启）的行才会真正回到队列。
        """
        while not self._stop_evt.wait(_RETRY_FAILED_INTERVAL):
            if not self._configured:
                continue
            try:
                resp = requests.get(
                    f"{self._api_url}/api/v1/agent/device-log-events",
                    params={
                        "host_id": self._host_id,
                        "state": "UPLOAD_FAILED,UPLOADING",
                        "limit": _RETRY_FAILED_LIMIT,
                    },
                    headers={"X-Agent-Secret": self._agent_secret},
                    timeout=15.0,
                )
                if resp.status_code >= 400:
                    continue
                for item in resp.json().get("data", {}).get("events", []):
                    self.enqueue_local_event(event={
                        "id": item["id"],
                        "local_path": item["local_path"],
                        "host_id": item.get("host_id", self._host_id),
                        "serial": item.get("serial", ""),
                        "platform": item.get("platform", "UNKNOWN"),
                        "event_type": item.get("event_type", "UNKNOWN"),
                        "detected_at": item.get("detected_at", ""),
                        "plan_run_id": item.get("plan_run_id"),
                        "job_id": item.get("job_id"),
                    }, force=True)
            except Exception:
                logger.exception("event_uploader_retry_scan_error")

    @staticmethod
    def _dir_sha256(root: Path) -> str:
        h = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            if path.is_file():
                h.update(str(path.relative_to(root)).encode())
                with open(path, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
        return h.hexdigest()


__all__ = ["EventUploader"]
