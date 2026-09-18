"""UploadManager — Agent 按需上送 scan 报告到中心存储（CIFS）。

ADR-0025 Sprint 4 Task 2: Agent 侧 scan xls 上送管理器。
    - upload_scan_report: 将 ScanRunner 产出的 _org.xls 复制到 CIFS dedup/ 目录
    - 进程级单例，configure 保护，_reset_for_tests

路径约定：
    dedup/{plan_run_id}/        — scan reports (org.xls files)
    _meta/{plan_run_id}/{host}.json — 写侧登记分片（#2188 D 步；与 run 同生命周期）
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

try:
    from backend.agent.aee.paths import resolve_shared_storage_root
except ImportError:  # Agent install layout (no ``backend.`` package)
    from agent.aee.paths import resolve_shared_storage_root

logger = logging.getLogger(__name__)

# legacy R1 的注册谓词（dedup_scan.py glob），在写侧冻结求值（#2188 设计稿 §2）：
# dedup_org 产物名由外部工具决定，读侧零文件名解析，分片注册面 ≡ legacy 注册面。
_META_SCHEMA_VERSION = 1
_ORG_XLS_PATTERNS = ("*_org.xls", "*_org_*.xls")


class ShardRegistrationError(RuntimeError):
    """分片登记失败（#2188 D 步写侧契约 / #739 面②）。

    文件已复制进 ``dedup/`` 但 ``_meta/{run}/{host}.json`` 未写入——即
    「文件已落、清单未写」的半交付。typed 异常让 ScanRunner worker 能把
    这类失败与普通 scan 失败区分开（可观测、可归因），而不是混进同一个
    ``scan_queue_job_failed`` 日志里被静默吞掉。
    """


class UploadManager:
    """进程级单例；Agent 启动时 configure，按需调用 upload_scan_report。"""

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

    def configure(self, *, nfs_root: str = "", force: bool = False) -> None:
        if self._configured and not force:
            logger.warning("upload_manager_reconfigure_ignored")
            return
        resolved = (nfs_root or resolve_shared_storage_root()).strip()
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
        *,
        platform_subdir: str = "",
    ) -> Optional[str]:
        """Copy _org.xls → {nfs_root}/dedup/{plan_run_id}/[{platform}/]{host_id}_{filename}.

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
        if platform_subdir:
            dest_dir = dest_dir / platform_subdir
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
        # #2188 D 步（单2 #2474）：登记失败必须 raise，不得吞成 None——
        # ScanRunner 不接返回值，吞掉 = 「文件已落、清单未写」的静默半交付。
        # #739 面②：scan_now 是入队即返回（ack≠完成），worker 线程无人接异常，
        # raise 无法「沿 scan_now 传播」——故以 ShardRegistrationError typed 上抛，
        # 由 worker 落可观测失败（专用日志标记 + 心跳计数器）；下轮 scan_now
        # 整段幂等重试（copy 覆盖写、分片幂等重写）。
        try:
            self._record_shard_entry(plan_run_id, host_id, dest_path, platform_subdir)
        except Exception as exc:
            raise ShardRegistrationError(
                f"shard register failed plan_run={plan_run_id} host={host_id} "
                f"dest={dest_path}"
            ) from exc
        return str(dest_path)

    @staticmethod
    def _shard_path(nfs_root: str, plan_run_id: int, host_id: str) -> Path:
        return Path(nfs_root) / "_meta" / str(int(plan_run_id)) / f"{host_id}.json"

    def _record_shard_entry(
        self, plan_run_id: int, host_id: str, dest_path: Path, platform_subdir: str,
    ) -> None:
        """写侧登记（#2188 设计稿 §2）：分片写入是上送动作的完成标志。

        每分片唯一写者 = 所属 host 的 Agent（无锁）；按 file_key 幂等合并、
        原子替换（tmp + rename）。`registerable` 按现行 legacy glob 谓词在
        写侧冻结求值，保证分片注册面 ≡ legacy 注册面（读侧零文件名解析）。
        """
        file_key = (
            f"{platform_subdir}/{dest_path.name}" if platform_subdir else dest_path.name
        )
        entry = {
            "file_key": file_key,
            "platform": platform_subdir or "",
            "size_bytes": dest_path.stat().st_size,
            "registerable": any(
                fnmatch(dest_path.name, pattern) for pattern in _ORG_XLS_PATTERNS
            ),
        }
        shard = self._shard_path(self._nfs_root, plan_run_id, host_id)
        shard.parent.mkdir(parents=True, exist_ok=True)

        artifacts: dict = {}
        if shard.is_file():
            try:
                existing = json.loads(shard.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                existing = None
            # 仅沿用同版本分片；版本不符 = 他版写侧产物，整片重建（schema_version 是演进锚点）
            if isinstance(existing, dict) and existing.get("schema_version") == _META_SCHEMA_VERSION:
                artifacts = {
                    a["file_key"]: a
                    for a in existing.get("artifacts", [])
                    if isinstance(a, dict) and "file_key" in a
                }
        artifacts[entry["file_key"]] = entry

        payload = {
            "schema_version": _META_SCHEMA_VERSION,
            "host_id": host_id,
            "plan_run_id": int(plan_run_id),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "artifacts": sorted(artifacts.values(), key=lambda a: a["file_key"]),
        }
        tmp = shard.with_name(shard.name + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        os.replace(tmp, shard)

    @staticmethod
    def _copytree_safe(src: str, dst: str) -> None:
        """copytree ignoring copystat EPERM on NFS/CIFS mounts.

        Shared by EventUploader (event dir promote). Kept after #213 Track A
        removed ``upload_event_dirs``.
        """
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


__all__ = ["ShardRegistrationError", "UploadManager"]
