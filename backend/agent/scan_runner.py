"""ScanRunner — Agent 本地 dedup_org scan 执行器（ADR-0025 Sprint 4 Task 1）。

包装 start_log_scan.py -dedup_org 调用，同步 subprocess.run。
产物 _org.xls 在 hdd_root 下；UploadManager（Task 2）负责上送到控制面 NFS。
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
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


class ScanRunner:
    """进程级单例；Agent 启动时 configure，scan 任务到来时 run_local_scan。"""

    _instance: Optional["ScanRunner"] = None
    _instance_lock = threading.Lock()
    _SUBPROCESS_TIMEOUT = _SCAN_SUBPROCESS_TIMEOUT

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

    def configure(
        self,
        *,
        scan_tool_python: str = "",
        scan_tool_script: str = "",
        hdd_root: str = "",
        side: str = "shanghai",
    ) -> None:
        if self._configured:
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
            "-dedup_org",
            self._hdd_root,
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
        candidates = fresh or all_candidates
        if not candidates:
            logger.warning(
                "scan_runner_no_org_xls plan_run=%d host=%s hdd_root=%s",
                plan_run_id, host_id, self._hdd_root,
            )
            return None

        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        org_xls = str(latest.resolve())
        logger.info(
            "scan_runner_success plan_run=%d host=%s org_xls=%s fresh=%d total=%d",
            plan_run_id, host_id, org_xls, len(fresh), len(all_candidates),
        )
        return org_xls


__all__ = ["ScanRunner"]
