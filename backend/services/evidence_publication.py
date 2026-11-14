"""B8 G6：S8 代表证据传输、校验回执与交付接线（ADR-0062 D8/D9）。

发布记录以 (实际 PlanRun, 平台, 代表 DLE) 为唯一身份独立持久化；多个问题复用
同一条记录。普通代表由持有原件的 Agent 上送，HddSpill 代表由控制面从中心已
校验的溢出副本复制；两条路径共用同一目的路径规则、哈希算法、五次尝试上限与
终态判据。DLE.remote_path 保持原语义（溢出补扫继续读 devices/），证据目的路径
只存在于本台账。

只覆盖 MTK 的 DEDUP_FIRST 分区；没有模式记录的存量分区按 CURRENT 解释，本模块
不介入。运行模式选择仍由阶段二激活闸门控制。
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.agent.contracts.evidence_layout import (
    NAMING_RULE_VERSION,
    EvidenceLayoutError,
    assert_no_symlinks,
    collection_folder_from_event_path,
    copytree_no_symlinks,
    dir_sha256_and_size,
    ensure_dir_under_root,
    evidence_rel_path,
)
from backend.core.artifact_paths import (
    ArtifactPathError,
    ArtifactPathNotFoundError,
    resolve_device_event_remote_path,
    resolve_evidence_event_path,
)
from backend.core.database import SessionLocal
from backend.core.settings.scheduler import get_scheduler_settings
from backend.core.storage_root import resolve_shared_storage_root
from backend.core.task_queue import enqueue_sync
from backend.models.device_log_event import DeviceLogEvent
from backend.models.issue_fact import EvidencePublication, SpillLedger
from backend.models.plan_run import PlanRun
from backend.services import issue_fact_store as facts
from backend.services import shadow_delivery

logger = logging.getLogger(__name__)

PLATFORM_MTK = "mtk"
MAX_ATTEMPTS = 5
# 中心复制的任务租约：同一 publication 在租约内不会被第二个任务重复领取。
_COPY_LEASE_SECONDS = 900
_RETRY_BACKOFF_SECONDS = (60, 120, 240, 480)
_SWEEP_LIMIT = 50

_REASON_NAMING_SOURCE_MISSING = "naming_source_missing"
_REASON_SOURCE_UNAVAILABLE = "source_unavailable"
_REASON_SOURCE_MISMATCH = "source_mismatch"
_REASON_DESTINATION_CONFLICT = "destination_conflict"
_REASON_HASH_MISMATCH = "hash_mismatch"
_REASON_COPY_FAILED = "copy_failed"
_TERMINAL_REASONS = frozenset({
    _REASON_DESTINATION_CONFLICT,
    _REASON_NAMING_SOURCE_MISSING,
    _REASON_SOURCE_MISMATCH,
})


class EvidenceReceiptIn(BaseModel):
    """Agent 回执载荷；接收端逐项与发布记录比对。"""

    host_id: str
    dle_id: str
    destination_rel_path: str
    state: str
    checksum: Optional[str] = None
    size_bytes: Optional[int] = Field(default=None, ge=0)
    attempts: int = Field(default=0, ge=0)
    reason: Optional[str] = None


class EvidencePublicationError(Exception):
    """发布/回执域异常；路由层按 code 翻译为 4xx。"""

    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_dedup_first(db: Session, *, plan_run_id: int, platform: str) -> bool:
    partition = facts.get_partition_mode(
        db, plan_run_id=plan_run_id, platform=platform,
    )
    return partition is not None and partition.mode == facts.PARTITION_MODE_DEDUP_FIRST


def _root_run_id(db: Session, plan_run_id: int) -> Optional[int]:
    run = db.get(PlanRun, plan_run_id)
    if run is None:
        return None
    return int(run.root_plan_run_id or run.id)


def _spill_source(db: Session, dle: DeviceLogEvent) -> Optional[str]:
    ledger = db.execute(
        select(SpillLedger).where(SpillLedger.dle_id == dle.id)
    ).scalar_one_or_none()
    return dle.remote_path if ledger is not None else None


def _identity_fields(
    db: Session, *, dle: DeviceLogEvent, root_plan_run_id: int,
) -> dict:
    """计算来源类型、固化目录名与固定目的相对路径；失败返回显式原因。"""
    source_kind = (
        facts.PUBLICATION_SOURCE_SPILL
        if _spill_source(db, dle) is not None
        else facts.PUBLICATION_SOURCE_LOCAL
    )
    source_path = _spill_source(db, dle) if source_kind == facts.PUBLICATION_SOURCE_SPILL else dle.local_path
    if source_kind == facts.PUBLICATION_SOURCE_SPILL and not source_path:
        return {
            "source_kind": source_kind,
            "source_path": None,
            "failure_reason": _REASON_SOURCE_UNAVAILABLE,
        }
    try:
        folder, event_dir = collection_folder_from_event_path(
            dle.local_path or "", serial=dle.serial,
        )
        rel_path = evidence_rel_path(
            root_plan_run_id=root_plan_run_id,
            collection_folder=folder,
            serial=dle.serial,
            event_dir=event_dir,
        )
    except EvidenceLayoutError:
        return {
            "source_kind": source_kind,
            "source_path": source_path,
            "failure_reason": _REASON_NAMING_SOURCE_MISSING,
        }
    if source_kind == facts.PUBLICATION_SOURCE_SPILL:
        try:
            resolved_source = resolve_device_event_remote_path(
                source_path, plan_run_id=dle.plan_run_id, must_exist=False,
            )
            if resolved_source.is_symlink() or not resolved_source.is_dir():
                raise ArtifactPathNotFoundError(str(resolved_source))
        except (ArtifactPathError, OSError):
            return {
                "source_kind": source_kind,
                "source_path": source_path,
                "failure_reason": _REASON_SOURCE_UNAVAILABLE,
            }
        if Path(source_path).name != event_dir:
            return {
                "source_kind": source_kind,
                "source_path": source_path,
                "failure_reason": _REASON_SOURCE_MISMATCH,
            }
    return {
        "source_kind": source_kind,
        "source_path": source_path,
        "collection_folder": folder,
        "destination_rel_path": rel_path,
    }


def _record_publication(
    db: Session,
    *,
    plan_run_id: int,
    platform: str,
    dle: DeviceLogEvent,
    root_plan_run_id: int,
    fields: dict,
) -> EvidencePublication:
    failure_reason = fields.get("failure_reason")
    state = facts.PUBLICATION_STATE_FAILED if failure_reason else facts.PUBLICATION_STATE_PENDING
    try:
        with db.begin_nested():
            return facts.record_evidence_publication(
                db,
                plan_run_id=plan_run_id,
                platform=platform,
                dle_id=dle.id,
                root_plan_run_id=root_plan_run_id,
                source_kind=fields["source_kind"],
                source_path=fields.get("source_path"),
                collection_folder=fields.get("collection_folder"),
                naming_rule_version=(
                    NAMING_RULE_VERSION if fields.get("collection_folder") else None
                ),
                destination_rel_path=fields.get("destination_rel_path"),
                executor_host_id=(
                    dle.host_id
                    if fields["source_kind"] == facts.PUBLICATION_SOURCE_LOCAL
                    else None
                ),
                state=state,
                failure_reason=failure_reason,
            )
    except IntegrityError:
        existing = facts.get_evidence_publication_for_dle(
            db, plan_run_id=plan_run_id, platform=platform, dle_id=dle.id,
        )
        if existing is not None:
            return existing
        # 目的路径已被另一个身份占用：拒绝覆盖，以 FAILED 落库保留诊断路径。
        with db.begin_nested():
            return facts.record_evidence_publication(
                db,
                plan_run_id=plan_run_id,
                platform=platform,
                dle_id=dle.id,
                root_plan_run_id=root_plan_run_id,
                source_kind=fields["source_kind"],
                source_path=fields.get("source_path"),
                collection_folder=fields.get("collection_folder"),
                naming_rule_version=(
                    NAMING_RULE_VERSION if fields.get("collection_folder") else None
                ),
                destination_rel_path=fields.get("destination_rel_path"),
                executor_host_id=(
                    dle.host_id
                    if fields["source_kind"] == facts.PUBLICATION_SOURCE_LOCAL
                    else None
                ),
                state=facts.PUBLICATION_STATE_FAILED,
                failure_reason=_REASON_DESTINATION_CONFLICT,
            )


def ensure_partition_publications(
    db: Session, *, plan_run_id: int, platform: str,
) -> int:
    """为 DEDUP_FIRST 分区的已选代表登记发布记录；幂等，返回新增条数。"""
    if platform.lower() != PLATFORM_MTK or not _is_dedup_first(
        db, plan_run_id=plan_run_id, platform=platform,
    ):
        return 0
    root_plan_run_id = _root_run_id(db, plan_run_id)
    if root_plan_run_id is None:
        return 0
    created = 0
    for selection in facts.list_partition_selections(
        db, plan_run_id=plan_run_id, platform=platform,
    ):
        if selection.selection_status != facts.SELECTION_SELECTED or selection.dle_id is None:
            continue
        if facts.get_evidence_publication_for_dle(
            db, plan_run_id=plan_run_id, platform=platform, dle_id=selection.dle_id,
        ) is not None:
            continue
        dle = facts.get_device_log_event(db, dle_id=selection.dle_id)
        if dle is None:
            continue
        fields = _identity_fields(db, dle=dle, root_plan_run_id=root_plan_run_id)
        _record_publication(
            db,
            plan_run_id=plan_run_id,
            platform=platform,
            dle=dle,
            root_plan_run_id=root_plan_run_id,
            fields=fields,
        )
        created += 1
    return created


def list_agent_tasks(
    db: Session, *, host_id: str, limit: int = 50,
) -> list[dict]:
    """Agent 待办：只返回仍有效的 DEDUP_FIRST 本地代表，含复制所需字段。"""
    tasks: list[dict] = []
    for row in facts.list_agent_evidence_publications(db, host_id=host_id, limit=limit):
        if not _is_dedup_first(db, plan_run_id=row.plan_run_id, platform=row.platform):
            continue
        dle = facts.get_device_log_event(db, dle_id=row.dle_id)
        if dle is None or not row.destination_rel_path:
            continue
        tasks.append({
            "publication_id": row.id,
            "dle_id": str(row.dle_id),
            "local_path": dle.local_path,
            "destination_rel_path": row.destination_rel_path,
            "root_plan_run_id": row.root_plan_run_id,
            "serial": dle.serial,
            "event_dir": Path(dle.local_path or "").name,
            "attempts": int(row.attempts or 0),
            "naming_rule_version": row.naming_rule_version,
        })
    return tasks


def _retry_at(now: datetime, attempts: int) -> datetime:
    index = min(max(attempts - 1, 0), len(_RETRY_BACKOFF_SECONDS) - 1)
    return now + timedelta(seconds=_RETRY_BACKOFF_SECONDS[index])


def record_agent_receipt(
    db: Session,
    *,
    publication_id: int,
    host_id: str,
    dle_id: str,
    destination_rel_path: str,
    state: str,
    checksum: Optional[str],
    size_bytes: Optional[int],
    attempts: int,
    reason: Optional[str] = None,
) -> dict:
    """接收端校验并落一条 Agent 回执（D9 第 5 条的输入）。

    VERIFIED 必须由接收端重新读取目的目录并计算哈希；RETRYING 只更新尝试
    次数；FAILED 只接受达到上限或不可重试的硬冲突。回执丢失时同一身份的
    重放收敛到同一条记录（终态同态幂等）。
    """
    row = facts.get_evidence_publication(db, publication_id=publication_id, for_update=True)
    if row is None:
        raise EvidencePublicationError("EVIDENCE_PUBLICATION_NOT_FOUND", "publication not found", status_code=404)
    if row.executor_host_id != host_id:
        raise EvidencePublicationError("EVIDENCE_EXECUTOR_MISMATCH", "receipt host does not own this publication")
    if str(row.dle_id) != str(dle_id):
        raise EvidencePublicationError("EVIDENCE_DLE_MISMATCH", "receipt dle does not match publication")
    if row.destination_rel_path != destination_rel_path:
        raise EvidencePublicationError("EVIDENCE_PATH_MISMATCH", "receipt path does not match publication")
    if row.source_kind != facts.PUBLICATION_SOURCE_LOCAL:
        raise EvidencePublicationError("EVIDENCE_SOURCE_MISMATCH", "this publication is copied by the control plane")
    if not _is_dedup_first(db, plan_run_id=row.plan_run_id, platform=row.platform):
        raise EvidencePublicationError("EVIDENCE_PARTITION_NOT_DEDUP_FIRST", "partition is not DEDUP_FIRST")
    selections = facts.list_selections_for_dle(
        db, plan_run_id=row.plan_run_id, platform=row.platform, dle_id=row.dle_id,
    )
    if not selections:
        raise EvidencePublicationError("EVIDENCE_NOT_REPRESENTATIVE", "dle is no longer a representative")
    dle = facts.get_device_log_event(db, dle_id=row.dle_id)
    if dle is None:
        raise EvidencePublicationError("EVIDENCE_DLE_MISSING", "representative dle not found", status_code=404)

    now = _utcnow()
    if row.state == facts.PUBLICATION_STATE_VERIFIED:
        if state == facts.PUBLICATION_STATE_VERIFIED and checksum == row.checksum:
            return {
                "publication_id": row.id, "state": row.state,
                "attempts": row.attempts, "checksum": row.checksum,
                "size_bytes": row.size_bytes,
            }
        if state == facts.PUBLICATION_STATE_VERIFIED:
            raise EvidencePublicationError(
                "EVIDENCE_HASH_MISMATCH", "verified publication has a different checksum",
            )
        raise EvidencePublicationError(
            "EVIDENCE_TERMINAL_VERIFIED", "publication is already verified",
        )
    if row.state == facts.PUBLICATION_STATE_FAILED:
        if state == facts.PUBLICATION_STATE_FAILED:
            return {
                "publication_id": row.id, "state": row.state,
                "attempts": row.attempts, "failure_reason": row.failure_reason,
            }
        raise EvidencePublicationError(
            "EVIDENCE_TERMINAL_FAILED", "publication is already failed",
        )

    if state == facts.PUBLICATION_STATE_RETRYING:
        facts.update_evidence_publication_state(
            db, row=row, state=facts.PUBLICATION_STATE_RETRYING,
            attempts=max(int(attempts), 0),
        )
        return {"publication_id": row.id, "state": row.state, "attempts": row.attempts}
    if state == facts.PUBLICATION_STATE_FAILED:
        if int(attempts) < MAX_ATTEMPTS and (reason or "") not in _TERMINAL_REASONS:
            raise EvidencePublicationError(
                "EVIDENCE_TERMINAL_TOO_EARLY", "terminal failure requires exhausted attempts",
            )
        facts.update_evidence_publication_state(
            db, row=row, state=facts.PUBLICATION_STATE_FAILED,
            attempts=max(int(attempts), 1), failure_reason=(reason or "agent_failed")[:64],
        )
        return {"publication_id": row.id, "state": row.state, "attempts": row.attempts}
    if state != facts.PUBLICATION_STATE_VERIFIED:
        raise EvidencePublicationError("EVIDENCE_STATE_INVALID", f"invalid receipt state: {state!r}")

    event_dir = Path(dle.local_path or "").name
    try:
        destination = resolve_evidence_event_path(
            row.destination_rel_path,
            root_plan_run_id=row.root_plan_run_id,
            serial=dle.serial,
            event_dir=event_dir,
            must_exist=True,
        )
    except ArtifactPathNotFoundError as exc:
        raise EvidencePublicationError("EVIDENCE_PATH_MISSING", str(exc)) from exc
    except ArtifactPathError as exc:
        raise EvidencePublicationError("EVIDENCE_PATH_INVALID", str(exc)) from exc
    actual_checksum, actual_size = dir_sha256_and_size(destination)
    if checksum != actual_checksum or size_bytes is None or int(size_bytes) != actual_size:
        next_attempts = max(int(row.attempts or 0), int(attempts or 0), 1)
        if next_attempts >= MAX_ATTEMPTS:
            facts.update_evidence_publication_state(
                db, row=row, state=facts.PUBLICATION_STATE_FAILED,
                attempts=next_attempts, failure_reason=_REASON_HASH_MISMATCH,
            )
        else:
            facts.update_evidence_publication_state(
                db, row=row, state=facts.PUBLICATION_STATE_RETRYING,
                attempts=next_attempts,
            )
        # 已写入尝试计数：由路由提交后返回 409，不能在这里 raise 把计数回滚掉。
        return {
            "publication_id": row.id, "state": row.state,
            "attempts": row.attempts, "error": "EVIDENCE_HASH_MISMATCH",
            "message": f"destination hash mismatch: claimed={checksum} actual={actual_checksum}",
        }
    facts.update_evidence_publication_state(
        db, row=row, state=facts.PUBLICATION_STATE_VERIFIED,
        attempts=max(int(attempts or 0), 1),
        checksum=actual_checksum, size_bytes=actual_size,
        now=now,
    )
    return {
        "publication_id": row.id,
        "state": row.state,
        "attempts": row.attempts,
        "checksum": row.checksum,
        "size_bytes": row.size_bytes,
    }


def _copy_stamp(row: EvidencePublication) -> tuple[int, str]:
    now = _utcnow()
    row.last_attempt_at = now
    row.attempts = int(row.attempts or 0) + 1
    row.next_attempt_at = now + timedelta(seconds=_COPY_LEASE_SECONDS)
    row.copy_claim_token = uuid.uuid4().hex
    return int(row.attempts), str(row.copy_claim_token)


def _finish_copy_failure(
    publication_id: int, reason: str, *, terminal: bool, attempt: int, claim_token: str,
) -> None:
    with SessionLocal() as db:
        row = facts.get_evidence_publication(db, publication_id=publication_id, for_update=True)
        if (
            row is None
            or int(row.attempts or 0) != int(attempt)
            or str(row.copy_claim_token or "") != str(claim_token)
        ):
            logger.info(
                "evidence_copy_stale_failure_ignored publication=%d attempt=%d current=%s",
                publication_id, attempt, None if row is None else row.attempts,
            )
            return
        if row.state not in {
            facts.PUBLICATION_STATE_PENDING, facts.PUBLICATION_STATE_RETRYING,
        }:
            return
        if terminal or int(row.attempts or 0) >= MAX_ATTEMPTS:
            facts.update_evidence_publication_state(
                db, row=row, state=facts.PUBLICATION_STATE_FAILED,
                attempts=row.attempts, failure_reason=reason[:64],
            )
        else:
            facts.update_evidence_publication_state(
                db, row=row, state=facts.PUBLICATION_STATE_RETRYING,
                attempts=row.attempts,
                next_attempt_at=_retry_at(_utcnow(), int(row.attempts or 0)),
            )
        attempts = int(row.attempts or 0)
        db.commit()
    logger.warning(
        "evidence_copy_failed publication=%d reason=%s attempts=%d",
        publication_id, reason, attempts,
    )


def _finish_copy_verified(
    publication_id: int, checksum: str, size_bytes: int, *, attempt: int, claim_token: str,
) -> bool:
    with SessionLocal() as db:
        row = facts.get_evidence_publication(db, publication_id=publication_id, for_update=True)
        if (
            row is None
            or int(row.attempts or 0) != int(attempt)
            or str(row.copy_claim_token or "") != str(claim_token)
        ):
            logger.info(
                "evidence_copy_stale_success_ignored publication=%d attempt=%d current=%s",
                publication_id, attempt, None if row is None else row.attempts,
            )
            return False
        if row.state == facts.PUBLICATION_STATE_VERIFIED:
            return True
        if row.state not in {
            facts.PUBLICATION_STATE_PENDING, facts.PUBLICATION_STATE_RETRYING,
        }:
            return False
        facts.update_evidence_publication_state(
            db, row=row, state=facts.PUBLICATION_STATE_VERIFIED,
            attempts=row.attempts, checksum=checksum, size_bytes=size_bytes,
        )
        db.commit()
    logger.info("evidence_copy_verified publication=%d bytes=%d", publication_id, size_bytes)
    return True


def _destination_present(dest: Path) -> Optional[tuple[str, int]]:
    if dest.is_symlink():
        return None
    if not dest.exists():
        return None
    if not dest.is_dir():
        return ("", 0)
    try:
        assert_no_symlinks(dest)
    except EvidenceLayoutError:
        return ("", 0)
    return dir_sha256_and_size(dest)


def copy_spill_publication(publication_id: int) -> None:
    """中心侧复制一条 SPILL 代表的已校验溢出副本（D8 第 2 条）。

    与 Agent 上送共用同一目的路径规则、哈希算法与五次尝试上限；源始终保留
    （I4 / D11，溢出补扫继续读 DLE.remote_path）。
    """
    with SessionLocal() as db:
        row = facts.get_evidence_publication(db, publication_id=publication_id, for_update=True)
        if row is None or row.source_kind != facts.PUBLICATION_SOURCE_SPILL:
            return
        if row.state not in {
            facts.PUBLICATION_STATE_PENDING, facts.PUBLICATION_STATE_RETRYING,
        }:
            return
        if not _is_dedup_first(db, plan_run_id=row.plan_run_id, platform=row.platform):
            return
        if row.next_attempt_at is not None and row.next_attempt_at > _utcnow():
            return
        attempts = int(row.attempts or 0)
        recovery_only = attempts >= MAX_ATTEMPTS
        if recovery_only:
            # 预算已耗尽：接管记录只做既有目标核验，绝不再复制或覆盖目标。
            claim_token = uuid.uuid4().hex
            row.copy_claim_token = claim_token
            row.next_attempt_at = _utcnow() + timedelta(seconds=_COPY_LEASE_SECONDS)
        else:
            attempts, claim_token = _copy_stamp(row)
        db.commit()
        plan_run_id = row.plan_run_id
        source_path = row.source_path or ""
        rel_path = row.destination_rel_path or ""
        root_plan_run_id = row.root_plan_run_id
        dle_id = row.dle_id

    dle = None
    with SessionLocal() as db:
        dle = facts.get_device_log_event(db, dle_id=dle_id)
        serial = dle.serial if dle is not None else None
        local_path = dle.local_path if dle is not None else ""
    try:
        event_dir = Path(local_path).name
        storage_root = resolve_shared_storage_root()
        if not storage_root:
            _finish_copy_failure(publication_id, _REASON_SOURCE_UNAVAILABLE, terminal=False, attempt=attempts, claim_token=claim_token)
            return
        src = resolve_device_event_remote_path(
            source_path, plan_run_id=plan_run_id, must_exist=False,
        )
        if src.is_symlink() or not src.is_dir():
            raise ArtifactPathNotFoundError(str(src))
        dest = resolve_evidence_event_path(
            rel_path,
            root_plan_run_id=root_plan_run_id,
            serial=serial,
            event_dir=event_dir,
        )
        if Path(src).name != event_dir:
            _finish_copy_failure(publication_id, _REASON_SOURCE_MISMATCH, terminal=True, attempt=attempts, claim_token=claim_token)
            return
        assert_no_symlinks(src)
        existing = _destination_present(dest)
        if existing is not None:
            src_checksum, src_size = dir_sha256_and_size(src)
            if existing == (src_checksum, src_size):
                if _finish_copy_verified(publication_id, src_checksum, src_size, attempt=attempts, claim_token=claim_token):
                    refresh_delivery_and_archive(plan_run_id, PLATFORM_MTK)
                return
            _finish_copy_failure(publication_id, _REASON_DESTINATION_CONFLICT, terminal=True, attempt=attempts, claim_token=claim_token)
            return

        if recovery_only:
            _finish_copy_failure(publication_id, _REASON_COPY_FAILED, terminal=True, attempt=attempts, claim_token=claim_token)
            return

        ensure_dir_under_root(storage_root, list(Path(rel_path).parts[:-1]))
        temp = dest.parent / f".stp-evidence-tmp-{publication_id}-{uuid.uuid4().hex}"
        try:
            copytree_no_symlinks(src, temp, dest_root=storage_root)
            src_checksum, src_size = dir_sha256_and_size(src)
            temp_checksum, temp_size = dir_sha256_and_size(temp)
            if (src_checksum, src_size) != (temp_checksum, temp_size):
                raise EvidenceLayoutError("copy verification failed")
            if os.path.lexists(dest):
                existing = _destination_present(dest)
                if existing == (src_checksum, src_size):
                    if _finish_copy_verified(publication_id, src_checksum, src_size, attempt=attempts, claim_token=claim_token):
                        refresh_delivery_and_archive(plan_run_id, PLATFORM_MTK)
                    return
                _finish_copy_failure(publication_id, _REASON_DESTINATION_CONFLICT, terminal=True, attempt=attempts, claim_token=claim_token)
                return
            os.rename(temp, dest)
        finally:
            if temp.exists():
                shutil.rmtree(temp, ignore_errors=True)
    except EvidenceLayoutError as exc:
        _finish_copy_failure(publication_id, _REASON_COPY_FAILED, terminal=False, attempt=attempts, claim_token=claim_token)
        logger.warning("evidence_copy_invalid publication=%d err=%s", publication_id, exc)
        return
    except (ArtifactPathError, OSError):
        _finish_copy_failure(publication_id, _REASON_COPY_FAILED, terminal=False, attempt=attempts, claim_token=claim_token)
        logger.exception("evidence_copy_error publication=%d", publication_id)
        return

    if _finish_copy_verified(publication_id, src_checksum, src_size, attempt=attempts, claim_token=claim_token):
        refresh_delivery_and_archive(plan_run_id, PLATFORM_MTK)


def archive_verified_representatives(
    db: Session, *, plan_run_id: int, platform: str,
) -> int:
    """证据已校验且已登记进交付物后，把符合条件的代表 DLE 转为 ARCHIVED。

    PRUNED 是终态且已是已校验溢出的来源，保持原状；证据不全或发布失败对应
    的签名视为缺口，不归档其事件（D9 第 9 条）。
    """
    if platform.lower() != PLATFORM_MTK or not _is_dedup_first(
        db, plan_run_id=plan_run_id, platform=platform,
    ):
        return 0
    latest = facts.get_latest_delivery_version(
        db, plan_run_id=plan_run_id, platform=platform,
    )
    if latest is None:
        return 0
    event_ids = {str(value) for value in (latest.event_ids or [])}
    blocked = {
        int(gap["signature_id"])
        for gap in (latest.gaps or [])
        if gap.get("kind") in ("evidence_incomplete", "publish_failed")
        and gap.get("signature_id") is not None
    }
    now = _utcnow()
    archived = 0
    for publication in facts.list_partition_evidence_publications(
        db, plan_run_id=plan_run_id, platform=platform,
    ):
        if publication.state != facts.PUBLICATION_STATE_VERIFIED:
            continue
        if str(publication.dle_id) not in event_ids:
            continue
        selections = facts.list_selections_for_dle(
            db, plan_run_id=plan_run_id, platform=platform, dle_id=publication.dle_id,
        )
        if not selections or any(int(row.signature_id) in blocked for row in selections):
            continue
        dle = facts.get_device_log_event(db, dle_id=publication.dle_id)
        if dle is None or dle.state not in ("LOCAL", "REMOTE"):
            continue
        dle.state = "ARCHIVED"
        dle.updated_at = now
        archived += 1
    db.flush()
    return archived


def refresh_delivery_and_archive(
    plan_run_id: int, platform: str = PLATFORM_MTK,
) -> Optional[int]:
    """回执/复制完成后重算交付版本，再按登记结果归档代表 DLE。

    登记失败（None）时不归档：宁可留给下一轮补偿，也不在未登记新版本时
    提前写 ARCHIVED。异常只留专用日志，不影响已完成的回执。
    """
    if platform.lower() != PLATFORM_MTK:
        return None
    try:
        version_no = shadow_delivery.try_register_delivery_version(
            plan_run_id, platform=platform,
        )
    except Exception:
        logger.exception("evidence_delivery_refresh_failed plan_run=%d", plan_run_id)
        return None
    if version_no is None:
        return None
    try:
        with SessionLocal() as db:
            archived = archive_verified_representatives(
                db, plan_run_id=plan_run_id, platform=platform,
            )
            db.commit()
            if archived:
                logger.info(
                    "evidence_archived plan_run=%d platform=%s dles=%d",
                    plan_run_id, platform, archived,
                )
    except Exception:
        logger.exception("evidence_archive_failed plan_run=%d", plan_run_id)
    return version_no


def sweep_evidence_publications(*, now: Optional[datetime] = None) -> None:
    """周期补偿：中心复制重试、登记失败重试、已校验未归档补归档。

    只处理 MTK 的 DEDUP_FIRST 分区；与影子轮共用阶段开关。任何单分区失败
    只记日志，不影响其他分区与本轮现行归档。
    """
    settings = get_scheduler_settings()
    if not settings.stp_scan_round_shadow_enabled:
        return
    now = now or _utcnow()
    try:
        with SessionLocal() as db:
            due = facts.list_due_spill_publication_ids(db, now=now, limit=20)
            delivery_runs = facts.list_evidence_publication_delivery_due_run_ids(
                db, platform=PLATFORM_MTK,
            )
            archive_runs = facts.list_evidence_publication_archive_due_run_ids(
                db, platform=PLATFORM_MTK,
            )
    except Exception:
        logger.exception("evidence_sweep_discovery_failed")
        return

    tick = int(now.timestamp()) // max(1, settings.auto_archive_poll_interval_seconds)
    for publication_id in due:
        try:
            enqueue_sync(
                "evidence_copy_task",
                key=f"evidence:copy:{publication_id}:{tick}",
                timeout=1800,
                retries=1,
                publication_id=publication_id,
            )
        except Exception:
            logger.exception("evidence_copy_enqueue_failed publication=%d", publication_id)

    candidates = sorted(set(delivery_runs) | set(archive_runs))[:_SWEEP_LIMIT]
    for run_id in candidates:
        try:
            with SessionLocal() as db:
                if not _is_dedup_first(db, plan_run_id=run_id, platform=PLATFORM_MTK):
                    continue
            refresh_delivery_and_archive(run_id, PLATFORM_MTK)
        except Exception:
            logger.exception("evidence_sweep_run_failed plan_run=%d", run_id)