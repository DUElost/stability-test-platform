"""B8 G8：受控兼容补跑服务（ADR-0062 D15 第 5–7 条，§11 场景 10）。

设计不变量（#4031「第 2 轮复核后的返修判据」）：

1. 仅限 Owner/admin 显式发起，目标必须为单个 ``DEDUP_FIRST`` 分区；
2. 只消费同一发生仍受 I4 保护的 Agent 本地原件或中心已校验副本；已 ``PRUNED``
   的事件不得回头读本地原件，且永远不把 ``DeviceLogEvent.state`` 改回 ``LOCAL``；
3. 同 key 的重试由一次尝试独占执行：领取走 ``FOR UPDATE`` 行锁，锁内重读最新
   已提交状态；迟到的失败登记走守卫式写回，绝不改写已完成的登记或把次数回退；
4. 同 key 绑定第一次接受的交付版本：重试省略版本沿用绑定版本，显式另一版本
   在复制前 409；实际复制、计数、台账与 manifest 使用同一版本；
5. 现行 15 列报表的列值取自已登记轮次的工具原生 org 报表（经伴随映射绑定到
   本版本的发生），原生为空的字段保留为空；原生值无法复原时登记可定位到
   发生与字段的缺口，存在缺口即禁止登记为完成（FAILED），不因其他发生的
   缺源而放宽；D15 允许的源缺失仍按部分成果登记（is_partial 只反映源覆盖）
   并列出缺源范围。

源选择顺序（同为已校验/仍可用的材料）：中心证据区已校验代表 → DLE
``remote_path`` 指向的中心事件目录 → 中心溢出副本 → I4 保护期内的 Agent 本地
原件。所有源都按实际 PlanRun、平台与所选交付版本的 issue/event 集合限定。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import xlrd
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.agent.contracts.evidence_layout import (
    EvidenceLayoutError,
    assert_no_symlinks,
    collection_folder_from_event_path,
    copytree_no_symlinks,
)
from backend.core.artifact_paths import (
    ArtifactPathError,
    resolve_evidence_event_path,
    resolve_extract_event_src,
)
from backend.core.database import SessionLocal
from backend.core.storage_root import resolve_shared_storage_root
from backend.models.device_log_event import DeviceLogEvent
from backend.models.issue_fact import CompatRerun, DeliveryVersion
from backend.models.plan_run import PlanRun
from backend.services import issue_fact_store as facts
from backend.services.dedup_scan import write_dedup_xls

logger = logging.getLogger(__name__)

_ADMIN_ROLES = frozenset({"admin", "owner"})
_COMPAT_ROOT_DIR = "compat_reruns"

#: 兼容报表里由工具原生 org 报表承载的列（经伴随映射绑定到发生）；
#: 原生行不可用时这些列的值无法复原，按缺口登记。
NATIVE_SOURCED_FIELDS: tuple[str, ...] = (
    "ExpTime",
    "ExpClass",
    "ExpType",
    "CurProcess",
    "Package",
    "Detail",
    "CausedBy",
    "extraTag",
    "Activity",
    "Rom_Ram",
    "Version",
)


@dataclass
class CompatRerunError(Exception):
    """受控兼容补跑校验或执行错误；由路由层转换为 HTTP 错误响应。"""

    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True)
class CompatRerunOutcome:
    rerun: CompatRerun
    replayed: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp = Path(handle.name)
    try:
        with handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _safe_under_root(candidate: Path, root: Path) -> Optional[Path]:
    try:
        resolved_root = root.resolve(strict=False)
        if candidate.is_symlink():
            return None
        resolved_cand = candidate.resolve(strict=False)
    except OSError:
        return None
    if not resolved_cand.is_relative_to(resolved_root) or not resolved_cand.exists():
        return None
    try:
        assert_no_symlinks(resolved_cand)
    except EvidenceLayoutError:
        return None
    return resolved_cand


def _tree_sha256(root_path: Path) -> str:
    digest = hashlib.sha256()
    if root_path.is_file():
        digest.update(root_path.read_bytes())
        return digest.hexdigest()
    for path in sorted(root_path.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(root_path).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _resolve_dle_compat_source(
    *,
    dle: DeviceLogEvent,
    root_plan_run_id: int,
    nfs_root: str,
    publication: Any,
    spill_record: Any,
    local_source_roots: Mapping[str, str],
) -> tuple[Optional[Path], Optional[str], str]:
    """按 I4 与中心校验优先级定位单个 DLE 的兼容补跑源材料。"""
    # 1. 中心证据区已校验代表（G6 发布台账）。
    if (
        publication is not None
        and publication.state == facts.PUBLICATION_STATE_VERIFIED
        and publication.destination_rel_path
    ):
        try:
            event_path = resolve_evidence_event_path(
                publication.destination_rel_path,
                root_plan_run_id=root_plan_run_id,
                serial=dle.serial,
                must_exist=True,
            )
            return event_path, "center_evidence_verified", "i4_center_verified"
        except ArtifactPathError:
            pass

    # 2. DLE.remote_path 指向的中心事件目录（上送校验成功后才写入）。
    if dle.remote_path:
        located = resolve_extract_event_src(
            dle.remote_path, nfs_root=nfs_root,
            plan_run_id=int(dle.plan_run_id or 0),
        )
        if located is not None:
            return located[0], "center_remote_verified", "i4_center_verified"

    # 3. 中心溢出副本（HddSpill 台账 + 发布记录源路径）。
    if (
        spill_record is not None
        and publication is not None
        and publication.source_kind == facts.PUBLICATION_SOURCE_SPILL
        and publication.source_path
    ):
        spill_root = Path(nfs_root) / "spill"
        candidate = _safe_under_root(Path(publication.source_path), spill_root)
        if candidate is not None:
            return candidate, "center_spill_verified", "i4_spill_protected"

    # 4. Agent 本地原件：只在未被 PRUNED 且仍在可读根下时使用。
    if str(dle.state or "").upper() != "PRUNED" and dle.local_path:
        host_root_raw = local_source_roots.get(str(dle.host_id))
        if host_root_raw:
            host_root = Path(host_root_raw)
            local_path = Path(dle.local_path)
            if not local_path.is_absolute():
                local_path = host_root / local_path
            candidate = _safe_under_root(local_path, host_root)
            if candidate is not None:
                return candidate, "agent_local_original", "i4_local_protected"

    return None, None, "missing_after_prune_or_unavailable"


def _scoped_issue_stats(pairs: list[tuple[Any, DeviceLogEvent]]) -> dict[int, dict]:
    """按交付版本范围计算每个问题的发生/机器数，避免跨版本串读。"""
    stats: dict[int, dict] = {}
    occurrences: dict[int, dict[int, Any]] = {}
    for occurrence, _dle in pairs:
        sig_id = int(occurrence.signature_id)
        occurrences.setdefault(sig_id, {})[int(occurrence.id)] = occurrence
    for sig_id, by_id in occurrences.items():
        values = list(by_id.values())
        stats[sig_id] = {
            "occurrences": len(values),
            "machines": len({occ.serial for occ in values if occ.serial}),
            "build_versions": sorted({
                occ.build_version for occ in values if occ.build_version
            }),
        }
    return stats


def _load_native_index(
    db: Session, *, delivery: Optional[DeliveryVersion], plan_run_id: int, platform: str,
) -> dict[str, dict]:
    """从已登记轮次的覆盖记录收集 (org 报表, 伴随映射行) → 发生绑定。

    ``delivery_version.round_ids`` 指向本版本的依据轮次；轮次 expected_set 的
    reports 记录 org 报表与其 mapping（伴随映射）路径。映射行携带
    ``dle_id``/``row``（1 基 xls 行号），把工具原生 org 行绑定到发生。
    """
    native: dict[str, dict] = {}
    if delivery is None:
        return native
    round_ids = {int(value) for value in (delivery.round_ids or [])}
    if not round_ids:
        return native
    for round_row in facts.list_scan_rounds_for_read(
        db, plan_run_id=plan_run_id, platform=platform,
    ):
        if round_row.id not in round_ids:
            continue
        for unit in round_row.expected_set or []:
            for report in unit.get("reports") or []:
                if report.get("empty_receipt"):
                    continue
                org_uri = report.get("storage_uri")
                mapping_uri = report.get("mapping_uri")
                if not org_uri or not mapping_uri:
                    continue
                try:
                    mapping_data = json.loads(Path(mapping_uri).read_text("utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(mapping_data, dict):
                    continue
                for mapping_row in mapping_data.get("rows") or []:
                    dle_id = mapping_row.get("dle_id")
                    row_no = mapping_row.get("row")
                    if not dle_id or not isinstance(row_no, int) or row_no < 1:
                        continue
                    native.setdefault(str(dle_id), {
                        "org": str(org_uri),
                        "xls_row": int(row_no),
                        "round_id": round_row.id,
                    })
    return native


def _read_org_row(
    org_path: str, xls_row: int, cache: dict[tuple[str, int], Optional[dict]],
) -> Optional[dict]:
    """读取 org 报表一行（1 基行号）的原生列值；不可读时缓存 None。

    缓存按 (文件, 行号) 键：同一 org 报表承载多个发生，跨行不得串值。
    """
    cache_key = (org_path, xls_row)
    if cache_key in cache:
        return cache[cache_key]
    try:
        book = xlrd.open_workbook(org_path, on_demand=True)
        try:
            sheet = book.sheet_by_index(0)
            if sheet.nrows < xls_row:
                cache[cache_key] = None
                return None
            headers = [
                str(sheet.cell_value(0, column)).strip()
                for column in range(sheet.ncols)
            ]
            cache[cache_key] = {
                headers[column]: sheet.cell_value(xls_row - 1, column)
                for column in range(sheet.ncols)
            }
        finally:
            book.release_resources()
    except Exception:
        logger.warning(
            "compat_rerun_org_row_unreadable path=%s row=%d", org_path, xls_row,
        )
        cache[cache_key] = None
    return cache[cache_key]


def trigger_compat_rerun(
    db: Session,
    *,
    plan_run_id: int,
    platform: str = "mtk",
    requested_by: str,
    requester_role: str,
    request_key: Optional[str] = None,
    delivery_version_no: Optional[int] = None,
    local_source_roots: Optional[Mapping[str, str]] = None,
    copy_fn: Optional[Callable[[Path, Path], None]] = None,
) -> CompatRerunOutcome:
    """Owner 显式发起单个 ``DEDUP_FIRST`` 分区的受控兼容补跑。"""
    if (requester_role or "").strip().lower() not in _ADMIN_ROLES:
        raise CompatRerunError(403, "owner/admin role required to trigger compat rerun")

    plat = (platform or "").strip().lower()
    if not plat:
        raise CompatRerunError(400, "platform is required")

    plan_run = db.get(PlanRun, plan_run_id)
    if plan_run is None:
        raise CompatRerunError(404, f"plan run {plan_run_id} not found")

    partition = facts.get_partition_mode(db, plan_run_id=plan_run_id, platform=plat)
    if partition is None or partition.mode != facts.PARTITION_MODE_DEDUP_FIRST:
        raise CompatRerunError(409, "compat rerun requires a DEDUP_FIRST partition")

    nfs_root = resolve_shared_storage_root()
    if not nfs_root:
        raise CompatRerunError(503, "shared storage root is not configured")

    resolved_key = (request_key or "").strip() or ""
    # ── 交付版本解析（B3）：同 key 绑定第一次接受的版本 ──────────────────
    # 预读登记行确定绑定；行锁领取后再做权威复核。
    preexisting = (
        facts.get_compat_rerun_by_request(
            db, plan_run_id=plan_run_id, platform=plat, request_key=resolved_key,
        )
        if resolved_key else None
    )
    bound_no = (
        preexisting.source_delivery_version_no
        if preexisting is not None else None
    )
    if (
        preexisting is not None
        and delivery_version_no is not None
        and bound_no is not None
        and int(delivery_version_no) != int(bound_no)
    ):
        raise CompatRerunError(
            409,
            f"request_key {resolved_key!r} is bound to delivery version "
            f"v{bound_no}; refusing explicit v{delivery_version_no} "
            "(use a new request_key for another version)",
        )
    requested_no = (
        bound_no if (preexisting is not None and bound_no is not None)
        else delivery_version_no
    )
    if requested_no is not None:
        delivery = facts.get_delivery_version(
            db, plan_run_id=plan_run_id, platform=plat, version_no=int(requested_no),
        )
        if delivery is None:
            if preexisting is not None and bound_no is not None:
                raise CompatRerunError(
                    409,
                    f"request_key {resolved_key!r} is bound to delivery version "
                    f"v{bound_no}, which is no longer available",
                )
            raise CompatRerunError(
                404,
                f"delivery version v{requested_no} not found for plan_run={plan_run_id}",
            )
    else:
        delivery = facts.get_latest_delivery_version(
            db, plan_run_id=plan_run_id, platform=plat,
        )
    # 失败登记要写到独立行：主事务会被路由层回滚，先把标识取成本地值。
    source_dv_id = delivery.id if delivery is not None else None
    source_dv_no = delivery.version_no if delivery is not None else None
    if not resolved_key:
        resolved_key = (
            f"compat-{plan_run_id}-{plat}-v{source_dv_no if source_dv_no else 0}"
        )

    rerun_id: Optional[int] = None
    attempt_no = 0
    try:
        # ── 行锁领取（B1）：并发同 key 在此串行化，锁内重读最新已提交状态 ──
        locked = facts.get_compat_rerun_for_update(
            db, plan_run_id=plan_run_id, platform=plat, request_key=resolved_key,
        )
        if locked is not None:
            rerun = locked
            rerun_id = int(locked.id)
            locked_bound = rerun.source_delivery_version_no
            # B3：锁内先校验显式版本，再决定是否重放。并发交错下预读可能查
            # 不到未提交的登记（另一请求尚在复制），若先按 COMPLETED 重放，
            # 会把 v1 登记当作 v2 请求的结果返回；版本校验必须以锁内已提交
            # 状态为准，拒绝发生在任何复制与重放之前。
            if (
                locked_bound is not None
                and delivery_version_no is not None
                and int(delivery_version_no) != int(locked_bound)
            ):
                raise CompatRerunError(
                    409,
                    f"request_key {resolved_key!r} is bound to delivery version "
                    f"v{locked_bound}; refusing explicit v{delivery_version_no}",
                )
            if rerun.status == facts.COMPAT_RERUN_STATUS_COMPLETED:
                return CompatRerunOutcome(rerun=rerun, replayed=True)
            if rerun.status in {
                facts.COMPAT_RERUN_STATUS_PENDING,
                facts.COMPAT_RERUN_STATUS_RUNNING,
            }:
                raise CompatRerunError(
                    409,
                    f"compat rerun {rerun.id} is currently {rerun.status.lower()}",
                )
            if locked_bound is not None and int(locked_bound) != int(source_dv_no or 0):
                # 预读与锁内绑定不同（并发改写）：以锁内绑定为准重新解析。
                delivery = facts.get_delivery_version(
                    db, plan_run_id=plan_run_id, platform=plat,
                    version_no=int(locked_bound),
                )
                if delivery is None:
                    raise CompatRerunError(
                        409,
                        f"request_key {resolved_key!r} is bound to delivery version "
                        f"v{locked_bound}, which is no longer available",
                    )
                source_dv_id = delivery.id
                source_dv_no = delivery.version_no
        else:
            rerun = facts.record_compat_rerun(
                db,
                plan_run_id=plan_run_id,
                platform=plat,
                request_key=resolved_key,
                requested_by=requested_by,
                source_delivery_version_id=source_dv_id,
                source_delivery_version_no=source_dv_no,
            )
            rerun_id = int(rerun.id)

        attempt_no = int(rerun.attempts or 0) + 1
        facts.update_compat_rerun(
            db, row=rerun,
            status=facts.COMPAT_RERUN_STATUS_PENDING,
            attempts=attempt_no,
            error=None,
            source_delivery_version_id=source_dv_id,
            source_delivery_version_no=source_dv_no,
        )
        db.flush()

        return _execute_compat_rerun(
            db,
            rerun=rerun,
            rerun_id=rerun_id,
            plan_run=plan_run,
            plan_run_id=plan_run_id,
            platform=plat,
            resolved_key=resolved_key,
            requested_by=requested_by,
            delivery=delivery,
            source_dv_id=source_dv_id,
            source_dv_no=source_dv_no,
            attempt_no=attempt_no,
            nfs_root=nfs_root,
            local_source_roots=local_source_roots or {},
            copy_fn=copy_fn,
        )
    except IntegrityError:
        # 并发同 key 触发：唯一约束仲裁失败。回滚本事务后让调用方重查，
        # 命中已登记行时按重放/重试语义继续，不产生第二行。
        db.rollback()
        raise CompatRerunError(
            409,
            "compat rerun request_key already exists; re-issue the request "
            "to reuse the registered attempt",
        ) from None
    except Exception as exc:
        logger.exception(
            "compat_rerun_failed rerun_id=%s plan_run=%d platform=%s",
            rerun_id, plan_run_id, plat,
        )
        # 路由层对 CompatRerunError 回滚主事务（get_db 不 commit）；失败登记
        # 必须独立提交，否则同 key 重试会新建行，D15 的幂等重试只在服务级
        # 测试里成立。先回滚主事务丢弃半途状态，再走守卫式写回。
        try:
            db.rollback()
        except Exception:
            logger.exception(
                "compat_rerun_rollback_failed plan_run=%d platform=%s",
                plan_run_id, plat,
            )
        _record_failed_registration(
            rerun_id=rerun_id,
            plan_run_id=plan_run_id,
            platform=plat,
            request_key=resolved_key,
            requested_by=requested_by,
            source_delivery_version_id=source_dv_id,
            source_delivery_version_no=source_dv_no,
            attempts=attempt_no,
            error=exc,
        )
        if isinstance(exc, CompatRerunError):
            raise
        raise CompatRerunError(500, f"compat rerun failed: {exc}") from exc


def _execute_compat_rerun(
    db: Session,
    *,
    rerun: CompatRerun,
    rerun_id: int,
    plan_run: PlanRun,
    plan_run_id: int,
    platform: str,
    resolved_key: str,
    requested_by: str,
    delivery: Optional[DeliveryVersion],
    source_dv_id: Optional[int],
    source_dv_no: Optional[int],
    attempt_no: int,
    nfs_root: str,
    local_source_roots: Mapping[str, str],
    copy_fn: Optional[Callable[[Path, Path], None]],
) -> CompatRerunOutcome:
    """领取成功后的复制、原生列值装配与登记推进；调用方持有行锁。"""
    root_plan_run_id = int(plan_run.root_plan_run_id or plan_run.id)
    issue_ids = [int(i) for i in (delivery.issue_ids or [])] if delivery else None
    event_ids = [str(e) for e in (delivery.event_ids or [])] if delivery else None
    pairs = facts.list_partition_occurrences_for_scope(
        db,
        plan_run_id=plan_run_id,
        platform=platform,
        signature_ids=issue_ids,
        event_ids=event_ids,
    )
    all_issues = facts.list_partition_delivery_issues(
        db, plan_run_id=plan_run_id, platform=platform,
    )
    if issue_ids is not None:
        included = set(issue_ids)
        all_issues = [
            issue for issue in all_issues
            if int(issue["signature_id"]) in included
        ]
    stats = _scoped_issue_stats(pairs)
    native_index = _load_native_index(
        db, delivery=delivery, plan_run_id=plan_run_id, platform=platform,
    )

    publications = {
        str(row.dle_id): row
        for row in facts.list_partition_evidence_publications(
            db, plan_run_id=plan_run_id, platform=platform,
        )
    }

    dle_by_id: dict[str, DeviceLogEvent] = {}
    sigs_by_dle: dict[str, set[int]] = {}
    occ_by_dle: dict[str, Any] = {}
    for occurrence, dle in pairs:
        key = str(dle.id)
        dle_by_id[key] = dle
        sigs_by_dle.setdefault(key, set()).add(int(occurrence.signature_id))
        occ_by_dle.setdefault(key, occurrence)

    artifact_dir = (
        Path(nfs_root).resolve(strict=False)
        / _COMPAT_ROOT_DIR
        / str(plan_run_id)
        / platform
        / f"r{rerun_id}"
    )
    events_dir = artifact_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    copier = copy_fn or (
        lambda src, dst: copytree_no_symlinks(src, dst, dest_root=events_dir)
    )

    source_records: list[dict[str, Any]] = []
    missing_items: list[dict[str, Any]] = []
    copied_by_dle: dict[str, str] = {}

    for dle_id_str in sorted(dle_by_id):
        dle = dle_by_id[dle_id_str]
        publication = publications.get(dle_id_str)
        spill = facts.get_spill_ledger(db, dle_id=dle.id)
        src_path, source_kind, i4_status = _resolve_dle_compat_source(
            dle=dle,
            root_plan_run_id=root_plan_run_id,
            nfs_root=nfs_root,
            publication=publication,
            spill_record=spill,
            local_source_roots=local_source_roots,
        )
        if src_path is None or source_kind is None:
            missing_items.append({
                "dle_id": dle_id_str,
                "serial": dle.serial,
                "host_id": dle.host_id,
                "dle_state": dle.state,
                "signature_ids": sorted(sigs_by_dle.get(dle_id_str, ())),
                "reason": i4_status,
            })
            continue

        dest_name = src_path.name if src_path.is_dir() else dle_id_str
        # 副本保留 aee 采集布局（{采集目录}/{serial}/aee_exp/{事件目录}），
        # 让报表 Path 与现行产物一样可被消费方按目录结构解析
        # （handoff/Jira 工具从 Path 反解 serial/entry）；解析失败回落平铺。
        collection_folder = None
        aee_subdir = "aee_exp"
        try:
            collection_folder, _entry = collection_folder_from_event_path(
                dle.local_path or "", serial=dle.serial,
            )
            aee_subdir = Path(dle.local_path).parts[-2]
        except EvidenceLayoutError:
            collection_folder = None
        if collection_folder:
            dest_event_dir = (
                events_dir / collection_folder / dle.serial / aee_subdir / dest_name
            )
        else:
            dest_event_dir = events_dir / dle.serial / dest_name
        if dest_event_dir.exists():
            shutil.rmtree(dest_event_dir)
        if src_path.is_dir():
            copier(src_path, dest_event_dir)
        else:
            dest_event_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_event_dir / src_path.name)

        copied_by_dle[dle_id_str] = str(dest_event_dir)
        source_records.append({
            "dle_id": dle_id_str,
            "serial": dle.serial,
            "host_id": dle.host_id,
            "original_dle_state": dle.state,
            "source_kind": source_kind,
            "i4_status": i4_status,
            "source_path": str(src_path),
            "copied_path": str(dest_event_dir),
            "digest_sha256": _tree_sha256(dest_event_dir),
        })

    is_partial = bool(missing_items)
    # ── 现行 15 列装配（B2）：列值取自已登记轮次的工具原生 org 行 ──────────
    # 行发生的确定：代表发生已复制 → 用代表；否则取同问题内已复制的替补
    # 发生；无任何已复制材料但代表有原生记录时仍用代表（Path 留空）。
    # Path/ExpTime 及其余发生相关字段始终来自同一个实际发生。
    org_row_cache: dict[str, Optional[dict]] = {}
    field_gaps: list[dict[str, Any]] = []
    binding_rows: list[dict[str, Any]] = []
    run_build_version = str(plan_run.build_version or "")
    xls_rows: list[dict[str, Any]] = []
    for row_order, issue in enumerate(all_issues):
        sig_id = int(issue["signature_id"])
        representative_dle = str(issue.get("representative_dle_id") or "")
        row_dle = None
        if representative_dle and representative_dle in copied_by_dle:
            row_dle = representative_dle
        else:
            for dle_id_str, signature_ids in sorted(sigs_by_dle.items()):
                if sig_id in signature_ids and dle_id_str in copied_by_dle:
                    row_dle = dle_id_str
                    break
        row_occurrence = occ_by_dle.get(row_dle) if row_dle else None
        native = native_index.get(row_dle) if row_dle else None
        native_row = (
            _read_org_row(native["org"], native["xls_row"], org_row_cache)
            if native else None
        )
        if row_dle is None and native_index.get(representative_dle):
            # 无可复制材料，但代表发生有原生记录：沿用代表（Path 留空）。
            row_dle = representative_dle
            native = native_index.get(row_dle)
            native_row = _read_org_row(
                native["org"], native["xls_row"], org_row_cache,
            )
        if row_dle is None:
            row_dle = representative_dle or None
        if row_dle and native_row is None:
            field_gaps.append({
                "signature_id": sig_id,
                "dle_id": row_dle,
                "org_report": native["org"] if native else None,
                "org_row": native["xls_row"] if native else None,
                "reason": (
                    "org_row_unreadable" if native
                    else "org_report_or_mapping_unavailable"
                ),
                "fields": list(NATIVE_SOURCED_FIELDS),
            })

        copied_path = copied_by_dle.get(row_dle, "") if row_dle else ""
        issue_stats = stats.get(sig_id, {})
        if native_row is not None:
            # 原生行在：逐字取工具值；原生为空的字段保留为空。
            exp_time = str(native_row.get("ExpTime") or "")
            exp_class = str(native_row.get("ExpClass") or "")
            exp_type = str(native_row.get("ExpType") or "")
            cur_process = str(native_row.get("CurProcess") or "")
            package = str(native_row.get("Package") or "")
            detail = str(native_row.get("Detail") or "")
            caused_by = str(native_row.get("CausedBy") or "")
            extra_tag = str(native_row.get("extraTag") or "")
            activity = str(native_row.get("Activity") or "")
            rom_ram = str(native_row.get("Rom_Ram") or "")
            version = str(native_row.get("Version") or "")
        else:
            # 原生行不可用：登记缺口；已注册的工具签名事实仍可填充，
            # 其余发生相关列留空（时区无法从 DB 事实复原，不再近似）。
            try:
                signature_fields = json.loads(issue.get("signature_raw") or "{}")
                if not isinstance(signature_fields, dict):
                    signature_fields = {}
            except ValueError:
                signature_fields = {}
            exp_time = ""
            exp_class = str(
                issue.get("event_type") or signature_fields.get("exp_class") or ""
            )
            exp_type = ""
            cur_process = ""
            package = str(signature_fields.get("package") or "")
            detail = ""
            caused_by = str(signature_fields.get("caused_by") or "")
            extra_tag = ""
            activity = ""
            rom_ram = ""
            version = run_build_version or ";".join(
                sorted(issue_stats.get("build_versions")
                       or list(issue.get("build_versions") or []))
            )
        binding_rows.append({
            "id": row_order,
            "signature_id": sig_id,
            "dle_id": row_dle,
            "occurrence_id": (
                str(row_occurrence.id) if row_occurrence is not None else None
            ),
            "is_substitute": bool(
                row_dle and representative_dle and row_dle != representative_dle
            ),
            "org_report": native["org"] if native else None,
            "org_row": native["xls_row"] if native else None,
        })
        xls_rows.append({
            # Id 是 0 起行序（现行 merge 同），下游按行序建映射。
            "Id": row_order,
            "Path": copied_path,
            "Version": version,
            "ExpTime": exp_time,
            "ExpClass": exp_class,
            "ExpType": exp_type,
            "CurProcess": cur_process,
            "Package": package,
            "Detail": detail,
            "CausedBy": caused_by,
            "extraTag": extra_tag,
            "Count": int(issue_stats.get("occurrences", 0)),
            "Activity": activity,
            "DeviceCount": int(issue_stats.get("machines", 0)),
            "Rom_Ram": rom_ram,
        })

    merge_xls_path = write_dedup_xls(
        artifact_dir
        / f"Result_MergeFiles_Compat_{plan_run_id}_{platform}_r{rerun_id}.xls",
        xls_rows,
    )
    coverage_scope = {
        "total_events": len(dle_by_id),
        "covered_events": len(source_records),
        "missing_events": len(missing_items),
        "issue_ids": sorted(int(issue["signature_id"]) for issue in all_issues),
        "covered_dle_ids": sorted(copied_by_dle),
        "missing_dle_ids": sorted(item["dle_id"] for item in missing_items),
        "is_partial": is_partial,
        "field_gaps": field_gaps,
        "delivery_version_no": source_dv_no,
    }
    manifest_path = artifact_dir / "compat_manifest.json"
    # 原生非空值无法复原时禁止登记为完成（#4031 判据 3）。缺口与缺源是两个
    # 独立判定：已复制行的字段缺口不因其他发生缺源（is_partial）而被掩盖成
    # 部分成果；D15 允许的源缺失仍按部分成果登记（is_partial 只反映源覆盖）。
    if field_gaps:
        registered_status = facts.COMPAT_RERUN_STATUS_FAILED
        error_summary = (
            "native values unreconstructable for "
            f"{len(field_gaps)} occurrence(s); fields: "
            + ",".join(sorted({f for gap in field_gaps for f in gap["fields"]}))
        )
    else:
        registered_status = facts.COMPAT_RERUN_STATUS_COMPLETED
        error_summary = None
    _atomic_write_json(manifest_path, {
        "schema": "stp.compat_rerun_manifest/v1",
        "compat_rerun_id": int(rerun_id),
        "plan_run_id": int(plan_run_id),
        "platform": platform,
        "request_key": resolved_key,
        "requested_by": requested_by,
        "source_delivery_version_id": source_dv_id,
        "source_delivery_version_no": source_dv_no,
        "registered_status": registered_status,
        "is_partial": is_partial,
        "coverage_scope": coverage_scope,
        "source_records": source_records,
        "missing_items": missing_items,
        "field_gaps": field_gaps,
        "binding": {
            "delivery_version_no": source_dv_no,
            "issue_ids": sorted(int(issue["signature_id"]) for issue in all_issues),
            "event_ids": [str(e) for e in (delivery.event_ids or [])] if delivery else [],
            "rows": binding_rows,
        },
        "merge_xls_path": str(merge_xls_path),
    })

    facts.update_compat_rerun(
        db, row=rerun,
        status=registered_status,
        attempts=attempt_no,
        coverage_scope=coverage_scope,
        source_records=source_records,
        missing_items=missing_items,
        is_partial=is_partial,
        artifact_dir=str(artifact_dir),
        merge_xls_path=str(merge_xls_path),
        manifest_path=str(manifest_path),
        error=error_summary,
        completed_at=(
            _utcnow()
            if registered_status == facts.COMPAT_RERUN_STATUS_COMPLETED else None
        ),
    )
    db.flush()
    return CompatRerunOutcome(rerun=rerun, replayed=False)


def _record_failed_registration(
    *,
    rerun_id: Optional[int],
    plan_run_id: int,
    platform: str,
    request_key: str,
    requested_by: str,
    source_delivery_version_id: Optional[int],
    source_delivery_version_no: Optional[int],
    attempts: int,
    error: Exception,
) -> Optional[CompatRerun]:
    """独立会话登记失败终态；主事务已被回滚，登记行以同 key 幂等落库。

    写回走守卫式更新（B1 迟到写回）：绝不改写已完成的登记，也不把次数写回退。
    """
    try:
        with SessionLocal() as failure_db:
            row = None
            if rerun_id is not None:
                row = facts.guarded_compat_rerun_failure(
                    failure_db, rerun_id=rerun_id,
                    attempts=attempts, error=str(error),
                )
            if row is not None:
                failure_db.commit()
                return row
            # 首次尝试：主事务回滚吞掉了登记行；独立补插失败行，与并发领取
            # 的插入由唯一约束仲裁，命中后仍走守卫式写回。
            try:
                new_row = facts.record_compat_rerun(
                    failure_db,
                    plan_run_id=plan_run_id,
                    platform=platform,
                    request_key=request_key,
                    requested_by=requested_by,
                    source_delivery_version_id=source_delivery_version_id,
                    source_delivery_version_no=source_delivery_version_no,
                )
                facts.update_compat_rerun(
                    failure_db, row=new_row,
                    status=facts.COMPAT_RERUN_STATUS_FAILED,
                    attempts=attempts,
                    error=str(error),
                )
                failure_db.commit()
                return new_row
            except IntegrityError:
                failure_db.rollback()
                winner = facts.get_compat_rerun_by_request(
                    failure_db, plan_run_id=plan_run_id, platform=platform,
                    request_key=request_key,
                )
                if winner is not None:
                    row = facts.guarded_compat_rerun_failure(
                        failure_db, rerun_id=int(winner.id),
                        attempts=attempts, error=str(error),
                    )
                    failure_db.commit()
                    return row
                return None
    except Exception:
        logger.exception(
            "compat_rerun_failure_register_failed plan_run=%d platform=%s "
            "request_key=%s", plan_run_id, platform, request_key,
        )
        return None


def list_compat_reruns_for_partition(
    db: Session, *, plan_run_id: int, platform: Optional[str] = None,
) -> list[CompatRerun]:
    return facts.list_compat_reruns(db, plan_run_id=plan_run_id, platform=platform)


__all__ = [
    "CompatRerunError",
    "CompatRerunOutcome",
    "list_compat_reruns_for_partition",
    "trigger_compat_rerun",
]
