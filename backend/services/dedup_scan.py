"""Dedup scan/merge service — ADR-0025 Sprint 4 归档-2。

各 agent 单独 scan（start_log_scan）→ 集中 merge（-merge_files）。
产物（Result_*.xls）写 plan_run_artifact 表。
config-gated：未配置 scan 工具 env 则跳过 + 503。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.job import JobInstance, JobLogSignal
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services.run_console import RunConsole, RunConsoleError

logger = logging.getLogger(__name__)

ARTIFACT_TYPE_SCAN = "scan_result_xls"
ARTIFACT_TYPE_MERGE = "merge_result_xls"


def resolve_scan_tool() -> Optional[Dict[str, str]]:
    """从 env 解析 scan 工具解释器 + 脚本路径。未配置返回 None。"""
    python = os.getenv("STP_DEDUP_SCAN_PYTHON", "").strip()
    script = os.getenv("STP_DEDUP_SCAN_SCRIPT", "").strip()
    if not python or not script:
        return None
    return {"python": python, "script": script}


def get_scan_env_defaults() -> Dict[str, str]:
    """scan 工具的部署级 env 默认值。"""
    return {
        "place": os.getenv("STP_DEDUP_PLACE", "SH"),
        "tag": os.getenv("STP_DEDUP_SCAN_TAG", ""),
    }


def check_archive_completed(db: Session, plan_run_id: int) -> tuple[bool, int, int]:
    """检查该 PlanRun 的初筛选数据是否就绪（方案 C：存在 log_signal 即可 scan）。

    返回 (completed, signal_job_count, total_count)。
    scan 实际执行在 Sprint 4;Sprint 3 仅判断数据就绪。
    """
    from sqlalchemy import func

    total = db.execute(
        select(func.count(JobInstance.id)).where(JobInstance.plan_run_id == plan_run_id)
    ).scalar_one()
    if total == 0:
        return False, 0, 0
    job_ids_subq = select(JobInstance.id).where(JobInstance.plan_run_id == plan_run_id)
    signal_job_count = db.execute(
        select(func.count(func.distinct(JobLogSignal.job_id))).where(
            JobLogSignal.job_id.in_(job_ids_subq),
        )
    ).scalar_one()
    return signal_job_count >= total, int(signal_job_count or 0), int(total)


_HOST_PREFIX_RE = re.compile(r"^([A-Za-z0-9_-]+?)_")


def _register_scan_artifacts_from_nfs(
    db: Session, plan_run_id: int, dedup_dir: Path
) -> int:
    """扫 dedup_dir 取 *_org.xls → 提取 host_id → 写 plan_run_artifact。

    文件名约定: {host_id}_Result_*_org.xls (由 UploadManager.fill 放置)。
    返回注册数。
    """
    count = 0
    for xls in sorted(dedup_dir.glob("*_org.xls")):
        existing = db.execute(
            select(PlanRunArtifact).where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.storage_uri == str(xls),
            )
        ).scalar_one_or_none()
        if existing:
            continue

        m = _HOST_PREFIX_RE.match(xls.name)
        host_id = m.group(1) if m else None
        size = xls.stat().st_size if xls.exists() else 0
        db.add(PlanRunArtifact(
            plan_run_id=plan_run_id,
            host_id=host_id,
            storage_uri=str(xls),
            artifact_type=ARTIFACT_TYPE_SCAN,
            size_bytes=size,
        ))
        count += 1
    if count:
        db.commit()
    return count


def run_scan_sync(plan_run_id: int, *, is_final: bool = False) -> str:
    """扫描 15.4 CIFS dedup/{plan_run_id}/ 目录，注册已上送的 *_org.xls 产物。

    Agent 已通过 scan_now → run_local_scan → UploadManager 上送文件到 NFS。
    本函数仅做文件发现 + DB 注册，不再调 subprocess / RunConsole。
    返回注册产物数（字符串化），空串表示无新产物。
    """
    from backend.core.database import SessionLocal

    db = SessionLocal()
    try:
        nfs_root = os.getenv("STP_AEE_NFS_ROOT", os.getenv("STP_WATCHER_NFS_BASE_DIR", "")).strip()
        if not nfs_root:
            logger.warning("scan_skip_nfs_root_not_set plan_run=%d", plan_run_id)
            return ""

        dedup_dir = Path(nfs_root) / "dedup" / str(plan_run_id)
        if not dedup_dir.is_dir():
            logger.warning("scan_skip_dedup_dir_missing plan_run=%d dir=%s", plan_run_id, dedup_dir)
            return ""

        n = _register_scan_artifacts_from_nfs(db, plan_run_id, dedup_dir)
        logger.info("scan_artifacts_registered plan_run=%d count=%d", plan_run_id, n)
        return str(n) if n else ""
    finally:
        db.close()


def run_merge_sync(plan_run_id: int) -> str:
    """同步执行 merge（-merge_files 集中合并各 agent 的 _org.xls）。

    返回 console_run_id。
    """
    tool = resolve_scan_tool()
    if tool is None:
        logger.warning("merge_skip_tool_not_configured plan_run=%d", plan_run_id)
        return ""

    from backend.core.database import SessionLocal

    db = SessionLocal()
    try:
        rows = db.execute(
            select(PlanRunArtifact.storage_uri).where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.artifact_type == ARTIFACT_TYPE_SCAN,
            )
        ).all()
        org_files = [r[0] for r in rows if "_org.xls" in r[0]]
        if not org_files:
            logger.warning("merge_skip_no_org_files plan_run=%d", plan_run_id)
            return ""
    finally:
        db.close()

    argv = [tool["python"], tool["script"], "-merge_files"] + org_files
    side = os.getenv("STP_DEDUP_SCAN_TAG", "shanghai")
    if "factory" in side.lower():
        argv += ["-side", "factory"]
    else:
        argv += ["-side", "shanghai"]

    def _on_complete(run):
        if run.status != "SUCCESS":
            return
        try:
            merge_dir = Path(tool["script"]).parent / "merge_result"
            latest = max(merge_dir.glob("*/"), key=lambda p: p.stat().st_mtime) if merge_dir.exists() else None
            if latest:
                inner_db = SessionLocal()
                try:
                    n = _register_merge_artifacts(inner_db, plan_run_id, latest)
                    logger.info("merge_artifacts_registered plan_run=%d count=%d", plan_run_id, n)
                finally:
                    inner_db.close()
        except Exception:
            logger.exception("merge_register_artifacts_failed plan_run=%d", plan_run_id)

    console_run_id = RunConsole.instance().start(
        run_key=f"merge:{plan_run_id}",
        cmd=argv,
        cwd=str(Path(tool["script"]).parent),
        label=f"merge-plan_run_{plan_run_id}",
        on_complete=_on_complete,
    )
    logger.info("merge_started plan_run=%d run_id=%s files=%d", plan_run_id, console_run_id, len(org_files))
    return console_run_id


def _register_merge_artifacts(db: Session, plan_run_id: int, merge_dir: Path) -> int:
    """扫 merge_dir 取 Result_MergeFiles*.xls → 写 plan_run_artifact。"""
    count = 0
    for xls in sorted(merge_dir.glob("Result_MergeFiles*.xls")):
        existing = db.execute(
            select(PlanRunArtifact).where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.storage_uri == str(xls),
            )
        ).scalar_one_or_none()
        if existing:
            continue
        size = xls.stat().st_size if xls.exists() else 0
        db.add(PlanRunArtifact(
            plan_run_id=plan_run_id,
            host_id=None,
            storage_uri=str(xls),
            artifact_type=ARTIFACT_TYPE_MERGE,
            size_bytes=size,
        ))
        count += 1
    if count:
        db.commit()
    return count


# ── 终态触发 helpers（供 aggregator / aggregator_sync 调用）─────────────

_DEDUP_AUTO_ENV = "STP_DEDUP_AUTO_SCAN"
_PLAN_RUN_TERMINAL = {"SUCCESS", "PARTIAL_SUCCESS", "FAILED", "DEGRADED"}


def should_trigger_dedup(run_status: str) -> bool:
    """是否应触发终态去重（env 开关 + PlanRun 终态）。"""
    if os.getenv(_DEDUP_AUTO_ENV, "1") != "1":
        return False
    return run_status in _PLAN_RUN_TERMINAL


async def enqueue_dedup_terminal_async(plan_run_id: int) -> None:
    """异步 enqueue scan_task（scan_task 完成后自行串行 enqueue upload + merge）。"""
    try:
        from backend.tasks.saq_worker import get_queue
        from saq import Job as SaqJob

        queue = get_queue()
        await queue.enqueue(
            SaqJob(
                function="scan_task",
                kwargs={"plan_run_id": plan_run_id, "is_final": True},
                key=f"scan:{plan_run_id}",
                timeout=900,
                retries=2,
                retry_delay=10.0,
                retry_backoff=True,
            )
        )
    except Exception as e:
        logger.error("enqueue_dedup_terminal_async failed plan_run=%d: %s", plan_run_id, e)


def enqueue_dedup_terminal_sync(plan_run_id: int) -> None:
    """同步 enqueue scan_task（scan_task 完成后自行串行 enqueue upload + merge）。"""
    try:
        from backend.tasks.saq_worker import enqueue_sync

        enqueue_sync(
            "scan_task",
            key=f"scan:{plan_run_id}",
            timeout=900,
            retries=2,
            plan_run_id=plan_run_id,
            is_final=True,
        )
    except Exception as e:
        logger.error("enqueue_dedup_terminal_sync failed plan_run=%d: %s", plan_run_id, e)
