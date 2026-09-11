"""UnisocScanRunner — UNISOC archive chain (ADR-0032 D4c)."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence

try:
    from backend.agent.scan_runner import ScanRunner, _SCAN_SUBPROCESS_TIMEOUT
except ImportError:
    from agent.scan_runner import ScanRunner, _SCAN_SUBPROCESS_TIMEOUT

logger = logging.getLogger(__name__)


class UnisocScanRunner:
    """Agent-side scan_log_gt (timed poll) → scan_result.py -d → upload(unisoc/)."""

    _instance: Optional["UnisocScanRunner"] = None

    def __init__(self) -> None:
        self._scan_python = ""
        self._scan_script = ""
        self._result_python = ""
        self._result_script = ""
        self._configured = False
        # #805-5：记录最近一次 log_scan_gt 是否超时（超时=工具被 runner 主动
        # 终止，此时产物可能是半成品），供后续完整性校验/降级使用。
        self._last_scan_timed_out = False

    @classmethod
    def instance(cls) -> "UnisocScanRunner":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        cls._instance = None

    def configure(
        self,
        *,
        scan_tool_python: str = "",
        scan_tool_script: str = "",
        result_python: str = "",
        result_script: str = "",
        hdd_root: str = "",
        side: Optional[str] = None,
        force: bool = False,
    ) -> None:
        del hdd_root, side
        if self._configured and not force:
            return
        self._scan_python = scan_tool_python or os.getenv("STP_UNISOC_LOG_SCAN_PYTHON", "").strip()
        self._scan_script = scan_tool_script or os.getenv("STP_UNISOC_LOG_SCAN_SCRIPT", "").strip()
        self._result_python = result_python or os.getenv("STP_UNISOC_SCAN_RESULT_PYTHON", "").strip()
        self._result_script = result_script or os.getenv("STP_UNISOC_SCAN_RESULT_SCRIPT", "").strip()
        self._configured = bool(
            self._scan_python and self._scan_script
            and self._result_python and self._result_script
        )

    def is_configured(self) -> bool:
        return self._configured

    def run_scan_and_upload(
        self,
        plan_run_id: int,
        host_id: str,
        *,
        is_final: bool,
        device_serials: Sequence[str] = (),
        run_date_stamps: Sequence[str] = (),
    ) -> None:
        del is_final
        if not self.is_configured():
            return
        scan_root = ScanRunner.instance()._prepare_scan_root(
            plan_run_id, device_serials, run_date_stamps,
        )
        if scan_root is None:
            logger.warning("unisoc_scan_skip_bad_scan_root plan_run=%d", plan_run_id)
            return
        if not self._run_log_scan_gt(scan_root, plan_run_id, host_id):
            return
        org_xls = self.run_scan_result(scan_root, plan_run_id, host_id)
        if not org_xls:
            return
        try:
            from backend.agent.upload_manager import UploadManager
        except ImportError:
            from agent.upload_manager import UploadManager
        uploader = UploadManager.instance()
        if not uploader.is_configured():
            return
        uploader.upload_scan_report(plan_run_id, host_id, org_xls, platform_subdir="unisoc")
        if self._last_scan_timed_out:
            # #805-5：超时终止后的原始 Result_*.xls 可能是半成品，跳过二次上送，
            # 只保留经 scan_result 处理且通过完整性校验的 org 文件。
            logger.warning(
                "unisoc_scan_dedup_upload_skipped_after_timeout plan_run=%d", plan_run_id,
            )
            return
        dedup_candidates = [
            p for p in Path(scan_root).glob("**/*.xls")
            if p.name.endswith(".xls") and "_org.xls" not in p.name and "Result_" in p.name
        ]
        if dedup_candidates:
            dedup_xls = max(dedup_candidates, key=lambda p: p.stat().st_mtime)
            uploader.upload_scan_report(
                plan_run_id, host_id, str(dedup_xls), platform_subdir="unisoc",
            )

    def _build_argv(self, *, scan_root: str) -> List[str]:
        poll_s = os.getenv("STP_UNISOC_LOG_SCAN_POLL_SECONDS", "60").strip() or "60"
        return [
            self._scan_python,
            self._scan_script,
            "-p", scan_root,
            "-m", "sprd",
            "-i", poll_s,
        ]

    def _run_log_scan_gt(self, scan_root: str, plan_run_id: int, host_id: str) -> bool:
        self._last_scan_timed_out = False
        argv = self._build_argv(scan_root=scan_root)
        cwd = str(Path(self._scan_script).parent)
        poll_s = int(os.getenv("STP_UNISOC_LOG_SCAN_POLL_SECONDS", "60") or "60")
        timeout = max(poll_s + 90, 120)
        logger.info(
            "unisoc_scan_gt_start plan_run=%d host=%s timeout=%ds argv=%s",
            plan_run_id, host_id, timeout, argv,
        )
        try:
            result = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            # 工具按设计持续轮询，runner 到点主动终止——属预期路径，但产物可能
            # 是半成品（#805-5）：置标供上游只做「完整性校验通过」的上送。
            self._last_scan_timed_out = True
            logger.info(
                "unisoc_scan_gt_timeout plan_run=%d host=%s after=%ds (expected)",
                plan_run_id, host_id, timeout,
            )
            return True
        except Exception:
            logger.exception("unisoc_scan_gt_exception plan_run=%d host=%s", plan_run_id, host_id)
            return False
        if result.returncode != 0:
            logger.warning(
                "unisoc_scan_gt_failed plan_run=%d host=%s rc=%d stderr=%s",
                plan_run_id, host_id, result.returncode, (result.stderr or "")[:500],
            )
            return False
        return True

    def run_local_scan(
        self,
        plan_run_id: int,
        host_id: str,
        *,
        is_final: bool = False,
        device_serials: Sequence[str] = (),
        run_date_stamps: Sequence[str] = (),
    ) -> Optional[str]:
        del is_final
        scan_root = ScanRunner.instance()._prepare_scan_root(
            plan_run_id, device_serials, run_date_stamps,
        )
        if scan_root is None or not self._run_log_scan_gt(scan_root, plan_run_id, host_id):
            return None
        return self.run_scan_result(scan_root, plan_run_id, host_id)

    @staticmethod
    def _artifact_looks_complete(path: Path) -> bool:
        """#805-5：产物非空且大小稳定（未被仍在写入的进程持续追加）。

        超时终止后工具可能留半成品；只读一次 size 无法区分「写完了」与
        「正写到一半」，短暂复读大小一致才认为落定。
        """
        import time as _time

        try:
            first = path.stat().st_size
            if first <= 0:
                return False
            _time.sleep(0.2)
            second = path.stat().st_size
        except OSError:
            return False
        return first == second

    def run_scan_result(
        self, scan_root: str, plan_run_id: int, host_id: str,
    ) -> Optional[str]:
        del host_id
        argv = [self._result_python, self._result_script, "-d", scan_root]
        cwd = str(Path(self._result_script).parent)
        logger.info("unisoc_scan_result_start plan_run=%d dir=%s", plan_run_id, scan_root)
        try:
            result = subprocess.run(
                argv, cwd=cwd, capture_output=True, text=True, timeout=_SCAN_SUBPROCESS_TIMEOUT,
            )
        except Exception:
            logger.exception("unisoc_scan_result_exception plan_run=%d", plan_run_id)
            return None
        if result.returncode != 0:
            logger.warning(
                "unisoc_scan_result_failed plan_run=%d rc=%d stderr=%s",
                plan_run_id, result.returncode, (result.stderr or "")[:500],
            )
            return None
        org_files = sorted(
            Path(scan_root).glob("**/*_org.xls"),
            key=lambda p: p.stat().st_mtime,
        )
        if not org_files:
            logger.warning("unisoc_scan_result_no_org_xls plan_run=%d dir=%s", plan_run_id, scan_root)
            return None
        chosen = org_files[-1].resolve()
        if self._last_scan_timed_out and not self._artifact_looks_complete(chosen):
            logger.warning(
                "unisoc_scan_result_incomplete_after_timeout plan_run=%d path=%s",
                plan_run_id, chosen,
            )
            return None
        return str(chosen)


__all__ = ["UnisocScanRunner"]
