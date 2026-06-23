"""UploadManager — Agent 按需上送（scan 报告 + 事件目录）到 15.4 CIFS share。

ADR-0025 Sprint 4 Task 2: Agent 侧文件上送管理器。
    - upload_scan_report: 将 ScanRunner 产出的 _org.xls 复制到 CIFS dedup/ 目录
    - upload_event_dirs: 将 AEE 事件目录复制到 CIFS devices/ 目录
    - 进程级单例，configure 保护，_reset_for_tests

路径约定：
    dedup/{plan_run_id}/        — scan reports (org.xls files)
    devices/{plan_run_id}/      — event directories (aee_db_* dirs)
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
from pathlib import Path
from typing import List, Optional

try:
    from backend.agent.aee.paths import get_aee_nfs_root
except ImportError:
    def get_aee_nfs_root() -> "Path":
        import os as _os
        for k in ("STP_AEE_NFS_ROOT", "STP_WATCHER_NFS_BASE_DIR"):
            v = (_os.getenv(k) or "").strip()
            if v:
                return Path(v)
        nfs = (_os.getenv("STP_NFS_ROOT") or "").strip()
        if nfs:
            return Path(nfs) / "sonic_tinno"
        return Path("/mnt/hdd/aee_events")

logger = logging.getLogger(__name__)

_EVENT_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_")


class UploadManager:
    """进程级单例；Agent 启动时 configure，按需调用 upload_*。"""

    _instance: Optional["UploadManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._nfs_root: str = ""
        self._configured: bool = False

    @classmethod
    def instance(cls) -> "UploadManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def configure(self, *, nfs_root: str = "") -> None:
        if self._configured:
            logger.warning("upload_manager_reconfigure_ignored")
            return
        resolved = nfs_root or str(get_aee_nfs_root())
        self._nfs_root = resolved
        self._configured = bool(self._nfs_root)
        logger.info(
            "upload_manager_configured nfs_root=%s configured=%s",
            self._nfs_root, self._configured,
        )

    def is_configured(self) -> bool:
        return self._configured

    def upload_scan_report(
        self,
        plan_run_id: int,
        host_id: str,
        org_xls_path: str,
    ) -> Optional[str]:
        """Copy _org.xls → {nfs_root}/dedup/{plan_run_id}/{host_id}_{filename}.

        Returns dest path on success, None on failure.
        """
        if not self._configured:
            logger.warning(
                "upload_scan_report_skip_not_configured plan_run=%d host=%s",
                plan_run_id, host_id,
            )
            return None

        src = Path(org_xls_path)
        if not src.is_file():
            logger.warning(
                "upload_scan_report_source_missing plan_run=%d host=%s src=%s",
                plan_run_id, host_id, org_xls_path,
            )
            return None

        filename = src.name
        dest_dir = Path(self._nfs_root) / "dedup" / str(plan_run_id)
        dest_path = dest_dir / f"{host_id}_{filename}"

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dest_path))
        except Exception:
            logger.exception(
                "upload_scan_report_failed plan_run=%d host=%s src=%s dest=%s",
                plan_run_id, host_id, org_xls_path, dest_path,
            )
            return None

        logger.info(
            "upload_scan_report_ok plan_run=%d host=%s dest=%s",
            plan_run_id, host_id, dest_path,
        )
        return str(dest_path)

    def upload_event_dirs(
        self,
        plan_run_id: int,
        event_dir_names: List[str],
        source_root: str,
    ) -> int:
        """Copy event directories → {nfs_root}/devices/{plan_run_id}/{dirname}/.

        If event_dir_names is empty, auto-discover by iterating {source_root}
        for timestamp-prefixed event directories (e.g. 2026-06-23_14-30-00_db.01).
        Only direct children matching YYYY-MM-DD_HH-MM-SS_* are selected.
        Skip if dest already exists. Returns count copied.
        """
        if not self._configured:
            logger.warning(
                "upload_event_dirs_skip_not_configured plan_run=%d",
                plan_run_id,
            )
            return 0

        count = 0
        base_src = Path(source_root)
        base_dst = Path(self._nfs_root) / "devices" / str(plan_run_id)

        if not event_dir_names:
            for event_dir in sorted(base_src.iterdir()):
                if not event_dir.is_dir():
                    continue
                if not _EVENT_DIR_RE.match(event_dir.name):
                    continue
                dst_dir = base_dst / event_dir.name
                if dst_dir.exists():
                    continue
                try:
                    self._copytree_safe(str(event_dir), str(dst_dir))
                    count += 1
                except Exception:
                    logger.exception(
                        "upload_event_dirs_auto_copy_failed plan_run=%d dir=%s",
                        plan_run_id, event_dir,
                    )
            logger.info(
                "upload_event_dirs_auto plan_run=%d copied=%d from=%s",
                plan_run_id, count, source_root,
            )
            return count

        for dirname in event_dir_names:
            src_dir = base_src / dirname
            dst_dir = base_dst / dirname

            if not src_dir.is_dir():
                logger.warning(
                    "upload_event_dirs_source_missing plan_run=%d dir=%s",
                    plan_run_id, dirname,
                )
                continue

            if dst_dir.exists():
                logger.info(
                    "upload_event_dirs_dest_exists_skip plan_run=%d dir=%s",
                    plan_run_id, dirname,
                )
                continue

            try:
                self._copytree_safe(str(src_dir), str(dst_dir))
                count += 1
            except Exception:
                logger.exception(
                    "upload_event_dirs_copy_failed plan_run=%d dir=%s",
                    plan_run_id, dirname,
                )

        logger.info(
            "upload_event_dirs_done plan_run=%d copied=%d total=%d",
            plan_run_id, count, len(event_dir_names),
        )
        return count

    @staticmethod
    def _copytree_safe(src: str, dst: str) -> None:
        """copytree ignoring copystat EPERM on NFS/CIFS mounts."""
        src_path = Path(src)
        dst_path = Path(dst)
        dst_path.mkdir(parents=True, exist_ok=True)
        for entry in src_path.rglob("*"):
            rel = entry.relative_to(src_path)
            target = dst_path / rel
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif entry.is_file():
                shutil.copyfile(str(entry), str(target))


__all__ = ["UploadManager"]
