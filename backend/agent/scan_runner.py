"""ScanRunner — Agent 本地 scan 执行器（ADR-0025 Sprint 4 Task 1）。

主扫描包装 start_log_scan.py -m 0（AEE_TNE，扫 hdd_root，同步 subprocess.run），
产物 _org.xls 在 hdd_root 下；UploadManager（Task 2）负责上送到控制面 NFS。
`-dedup_org` 仅在 `run_dedup_org()` 中作为对已产出 _org.xls 的二次去重调用，非主扫描模式。
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

try:
    from backend.agent.aee.paths import get_aee_local_root
except ImportError:
    def get_aee_local_root() -> "Path":
        for k in ("STP_AEE_LOCAL_ROOT", "STP_AEE_NFS_ROOT", "STP_WATCHER_NFS_BASE_DIR"):
            v = (os.getenv(k) or "").strip()
            if v:
                return Path(v)
        nfs = (os.getenv("STP_NFS_ROOT") or "").strip()
        if nfs:
            return Path(nfs) / "sonic_tinno"
        return Path("/mnt/hdd/aee_events")

logger = logging.getLogger(__name__)

_SCAN_SUBPROCESS_TIMEOUT = 600


@dataclass(frozen=True)
class _ScanJob:
    plan_run_id: int
    host_id: str
    is_final: bool


class ScanRunner:
    """进程级单例；Agent 启动时 configure，scan 任务到来时 run_local_scan。"""

    _instance: Optional["ScanRunner"] = None
    _instance_lock = threading.Lock()
    _SUBPROCESS_TIMEOUT = _SCAN_SUBPROCESS_TIMEOUT
    # One start_log_scan pipeline per host at a time (scan + dedup_org subprocesses).
    _host_scan_semaphore = threading.Semaphore(1)
    _queue_lock = threading.Lock()
    _worker_lock = threading.Lock()
    _pending: OrderedDict[int, _ScanJob] = OrderedDict()
    _worker_started = False

    def __init__(self) -> None:
        self._scan_tool_python: str = ""
        self._scan_tool_script: str = ""
        self._hdd_root: str = ""
        self._side: str = "shanghai"
        self._configured: bool = False

    @classmethod
    def instance(cls) -> "ScanRunner":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        with cls._instance_lock:
            cls._instance = None
        with cls._queue_lock:
            cls._pending.clear()
        with cls._worker_lock:
            cls._worker_started = False
        cls._host_scan_semaphore = threading.Semaphore(1)

    @classmethod
    def enqueue_scan_now(cls, plan_run_id: int, host_id: str, *, is_final: bool) -> None:
        """Queue scan_now; coalesce duplicate plan_run_id entries (keep latest)."""
        job = _ScanJob(plan_run_id=plan_run_id, host_id=host_id, is_final=is_final)
        with cls._queue_lock:
            if plan_run_id in cls._pending:
                cls._pending[plan_run_id] = job
                logger.info(
                    "control_scan_now_coalesced plan_run=%d final=%s queue_depth=%d",
                    plan_run_id, is_final, len(cls._pending),
                )
            else:
                cls._pending[plan_run_id] = job
                logger.info(
                    "control_scan_now_queued plan_run=%d final=%s queue_depth=%d",
                    plan_run_id, is_final, len(cls._pending),
                )
        cls._ensure_worker()

    @classmethod
    def _ensure_worker(cls) -> None:
        with cls._worker_lock:
            if cls._worker_started:
                return
            cls._worker_started = True
            threading.Thread(
                target=cls._worker_loop,
                name="scan-queue-worker",
                daemon=True,
            ).start()

    @classmethod
    def _dequeue_next(cls) -> Optional[_ScanJob]:
        with cls._queue_lock:
            if not cls._pending:
                return None
            _, job = cls._pending.popitem(last=False)
            return job

    @classmethod
    def pending_count(cls) -> int:
        with cls._queue_lock:
            return len(cls._pending)

    @classmethod
    def _worker_loop(cls) -> None:
        while True:
            job = cls._dequeue_next()
            if job is None:
                with cls._worker_lock:
                    with cls._queue_lock:
                        if cls._pending:
                            continue
                        cls._worker_started = False
                return
            cls._execute_job(job)

    @classmethod
    def _execute_job(cls, job: _ScanJob) -> None:
        cls._host_scan_semaphore.acquire(blocking=True)
        try:
            cls.instance().run_scan_and_upload(
                job.plan_run_id, job.host_id, is_final=job.is_final,
            )
        finally:
            cls._host_scan_semaphore.release()

    def run_scan_and_upload(
        self, plan_run_id: int, host_id: str, *, is_final: bool,
    ) -> None:
        if not self.is_configured():
            logger.warning("control_scan_now_skip_runner_not_configured")
            return
        org_xls = self.run_local_scan(
            plan_run_id=plan_run_id,
            host_id=host_id,
            is_final=is_final,
        )
        if not org_xls:
            logger.warning("control_scan_now_scan_failed plan_run=%d", plan_run_id)
            return
        dedup_xls = self.run_dedup_org(org_xls, plan_run_id, host_id)
        try:
            from backend.agent.upload_manager import UploadManager
        except ImportError:
            from agent.upload_manager import UploadManager
        uploader = UploadManager.instance()
        if not uploader.is_configured():
            logger.warning("control_scan_now_skip_uploader_not_configured")
            return
        uploader.upload_scan_report(plan_run_id, host_id, org_xls)
        if dedup_xls:
            uploader.upload_scan_report(plan_run_id, host_id, dedup_xls)
        logger.info("control_scan_now_done plan_run=%d host=%s", plan_run_id, host_id)

    @classmethod
    def try_begin_host_scan(cls) -> bool:
        """Acquire host-wide scan slot (non-blocking). Returns False if busy."""
        return cls._host_scan_semaphore.acquire(blocking=False)

    @classmethod
    def end_host_scan(cls) -> None:
        cls._host_scan_semaphore.release()

    def configure(
        self,
        *,
        scan_tool_python: str = "",
        scan_tool_script: str = "",
        hdd_root: str = "",
        side: str = "shanghai",
        force: bool = False,
    ) -> None:
        if self._configured and not force:
            logger.warning("scan_runner_reconfigure_ignored")
            return
        self._scan_tool_python = scan_tool_python or os.getenv("STP_DEDUP_SCAN_PYTHON", "").strip()
        self._scan_tool_script = scan_tool_script or os.getenv("STP_DEDUP_SCAN_SCRIPT", "").strip()
        self._hdd_root = hdd_root or str(get_aee_local_root())
        self._side = side
        self._configured = bool(self._scan_tool_python and self._scan_tool_script)
        logger.info(
            "scan_runner_configured python=%s script=%s hdd_root=%s side=%s configured=%s",
            self._scan_tool_python, self._scan_tool_script, self._hdd_root,
            self._side, self._configured,
        )

    def is_configured(self) -> bool:
        return self._configured

    def _build_argv(self, *, is_final: bool = False) -> List[str]:
        argv = [
            self._scan_tool_python,
            self._scan_tool_script,
            "-m", "0",
            "-d", self._hdd_root,
            "-side",
            self._side,
        ]
        if is_final:
            argv.append("-end")
        return argv

    def run_local_scan(
        self, plan_run_id: int, host_id: str, *, is_final: bool = False
    ) -> Optional[str]:
        if not self._configured:
            logger.warning(
                "scan_runner_skip_not_configured plan_run=%d host=%s",
                plan_run_id, host_id,
            )
            return None

        scan_start = time.time()
        argv = self._build_argv(is_final=is_final)
        cwd = str(Path(self._scan_tool_script).parent)
        logger.info(
            "scan_runner_start plan_run=%d host=%s final=%s argv=%s",
            plan_run_id, host_id, is_final, argv,
        )
        try:
            result = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self._SUBPROCESS_TIMEOUT,
            )
        except Exception as exc:
            logger.exception(
                "scan_runner_exception plan_run=%d host=%s err=%s",
                plan_run_id, host_id, exc,
            )
            return None

        if result.returncode != 0:
            logger.warning(
                "scan_runner_failed plan_run=%d host=%s rc=%d stderr=%s",
                plan_run_id, host_id, result.returncode,
                (result.stderr or "")[:500],
            )
            return None

        hdd = Path(self._hdd_root)
        all_candidates = list(hdd.glob("**/Result_*_org.xls"))
        fresh = [
            c for c in all_candidates
            if c.stat().st_mtime >= scan_start - 1
        ]
        if not fresh:
            logger.warning(
                "scan_runner_no_fresh_org_xls plan_run=%d host=%s hdd_root=%s total_candidates=%d",
                plan_run_id, host_id, self._hdd_root, len(all_candidates),
            )
            return None

        latest = max(fresh, key=lambda p: p.stat().st_mtime)
        org_xls = str(latest.resolve())
        logger.info(
            "scan_runner_success plan_run=%d host=%s org_xls=%s fresh=%d total=%d",
            plan_run_id, host_id, org_xls, len(fresh), len(all_candidates),
        )
        return org_xls

    def run_dedup_org(
        self, org_xls_path: str, plan_run_id: int, host_id: str,
    ) -> Optional[str]:
        if not self._configured:
            return None
        argv = [
            self._scan_tool_python,
            self._scan_tool_script,
            "-dedup_org", org_xls_path,
            "-side", self._side,
        ]
        cwd = str(Path(self._scan_tool_script).parent)
        logger.info(
            "dedup_runner_start plan_run=%d host=%s argv=%s",
            plan_run_id, host_id, argv,
        )
        try:
            result = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self._SUBPROCESS_TIMEOUT,
            )
        except Exception as exc:
            logger.exception(
                "dedup_runner_exception plan_run=%d host=%s err=%s",
                plan_run_id, host_id, exc,
            )
            return None

        if result.returncode != 0:
            logger.warning(
                "dedup_runner_failed plan_run=%d host=%s rc=%d stderr=%s",
                plan_run_id, host_id, result.returncode,
                (result.stderr or "")[:500],
            )
            return None

        output_path = (result.stdout or "").strip()
        if output_path and Path(output_path).exists():
            dedup_xls = str(Path(output_path).resolve())
            logger.info(
                "dedup_runner_success plan_run=%d host=%s dedup_xls=%s",
                plan_run_id, host_id, dedup_xls,
            )
            return dedup_xls

        logger.warning(
            "dedup_runner_no_output plan_run=%d host=%s stdout=%s",
            plan_run_id, host_id, (result.stdout or "")[:200],
        )
        return None


__all__ = ["ScanRunner"]
