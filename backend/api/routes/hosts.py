from datetime import datetime, timedelta, timezone
import logging
import os
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import Any, List, Union

from backend.core.audit import record_audit
from backend.core.database import get_db
from backend.core.legacy_aee import hidden_legacy_plan_ids
from backend.core.ssh_security import (
    SshSecurityConfigError,
    encrypt_ssh_password,
    resolve_host_ssh_credentials,
    trust_host_key,
)
from backend.models.enums import JobStatus
from backend.models.host import Host
from backend.models.job import JobInstance
from backend.api.schemas import (
    HostActiveJob,
    HostCreate,
    HostOut,
    HostWatcherAdminStatePatch,
    PaginatedResponse,
)
from backend.api.routes.auth import get_current_active_user, require_admin, User
from backend.services.host_updater import execute_hot_update, _resolve_ssh_creds, get_agent_code_version
from backend.services.plan_run_abort import abort_jobs_for_host
from backend.tasks.saq_worker import enqueue_sync, EnqueueSyncError, get_saq_job_state_sync

logger = logging.getLogger(__name__)

# Host heartbeat timeout config (default 5 minutes)
HOST_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("HOST_HEARTBEAT_TIMEOUT_SECONDS", "300"))


def _ensure_host_status_up_to_date(host: Host) -> bool:
    """Update host status to OFFLINE if heartbeat has expired.
    Returns True if status was changed, False otherwise.
    """
    if host.status != "ONLINE":
        return False

    now = datetime.now(timezone.utc)
    offline_deadline = now - timedelta(seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS)
    last_heartbeat = host.last_heartbeat
    if last_heartbeat and last_heartbeat.tzinfo is None:
        # 兼容历史/测试数据中的 naive 时间
        last_heartbeat = last_heartbeat.replace(tzinfo=timezone.utc)

    if last_heartbeat is None or last_heartbeat < offline_deadline:
        host.status = "OFFLINE"
        logger.info(
            "host_status_marked_offline",
            extra={
                "host_id": host.id,
                "host_name": host.name,
                "last_heartbeat": last_heartbeat.isoformat() if last_heartbeat else None,
            },
        )
        return True
    return False

_ACTIVE_JOB_STATUSES = (JobStatus.PENDING.value, JobStatus.RUNNING.value)
_AGENT_SECRET_PLACEHOLDER = "change-me-in-production"


def _get_syncable_agent_secret() -> str:
    secret = os.getenv("AGENT_SECRET", "").strip()
    if not secret or secret == _AGENT_SECRET_PLACEHOLDER:
        raise HTTPException(
            status_code=409,
            detail=(
                "Local AGENT_SECRET is not configured or still using a placeholder value."
            ),
        )
    return secret


def _visible_plan_id(plan_id: int | None, hidden_plan_ids_set: set[int]) -> int | None:
    if plan_id is None:
        return None
    return None if int(plan_id) in hidden_plan_ids_set else int(plan_id)


def _host_to_out(h: Host, *, db: Session | None = None, host_key_trust: str | None = None) -> HostOut:
    """从 ORM 对象构造 HostOut，从 host.extra 中提取 capacity/health。

    不能仅靠 HostOut.model_validate(h) —— Pydantic 不会自动从 JSON 列
    的嵌套 key 映射到顶层字段。此 helper 在 validate 后补充。

    若提供 ``db``，会一并查询 host 上的活跃 Job 列表（ADR-0021 hot-update gate）。
    """
    out = HostOut.model_validate(h)
    extra = dict(h.extra or {})
    for key in ("ssh_password", "ssh_key_path", "password", "secret", "token", "private_key"):
        extra.pop(key, None)
    out.extra = extra
    out.capacity = extra.get("capacity")
    out.health = extra.get("health")
    out.host_key_trust = host_key_trust

    if db is not None:
        from backend.models.plan_run import PlanRun

        hidden_plan_ids_set = hidden_legacy_plan_ids(db)
        active_jobs = (
            db.query(JobInstance)
            .filter(
                JobInstance.host_id == h.id,
                JobInstance.status.in_(_ACTIVE_JOB_STATUSES),
            )
            .order_by(JobInstance.id)
            .all()
        )
        # v3: preload PlanRun 以检测 abort_requested
        pr_ids = {j.plan_run_id for j in active_jobs}
        pr_map: dict[int, Any] = {}
        if pr_ids:
            pr_rows = (
                db.query(PlanRun)
                .filter(PlanRun.id.in_(pr_ids))
                .all()
            )
            pr_map = {pr.id: pr for pr in pr_rows}

        def _abort_pending(j: JobInstance) -> bool:
            pr = pr_map.get(j.plan_run_id)
            if pr is None or pr.run_context is None:
                return False
            return isinstance(pr.run_context, dict) and "abort_requested" in pr.run_context

        out.active_jobs = [
            HostActiveJob(
                id=j.id,
                plan_run_id=j.plan_run_id,
                plan_id=_visible_plan_id(j.plan_id, hidden_plan_ids_set),
                device_id=j.device_id,
                status=j.status,
                started_at=j.started_at,
                abort_pending=_abort_pending(j),
            )
            for j in active_jobs
        ]
        out.active_job_count = len(active_jobs)
    return out


router = APIRouter(prefix="/api/v1/hosts", tags=["hosts"])


@router.post("", response_model=HostOut)
def create_host(payload: HostCreate, db: Session = Depends(get_db), current_user: User = Depends(require_admin), request: Request = None):
    host_id = str(uuid.uuid4())
    try:
        encrypted_password = encrypt_ssh_password(payload.ssh_password) or None
    except SshSecurityConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    host = Host(
        id=host_id,
        hostname=payload.name,
        name=payload.name,
        ip=payload.ip,
        ip_address=payload.ip,
        ssh_port=payload.ssh_port,
        ssh_user=payload.ssh_user,
        ssh_auth_type=payload.ssh_auth_type,
        ssh_key_path=payload.ssh_key_path,
        ssh_password_enc=encrypted_password,
        ssh_known_hosts_path=payload.ssh_known_hosts_path,
    )
    db.add(host)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"主机已存在（名称或 IP 重复）：{payload.name} / {payload.ip}",
        ) from exc
    record_audit(
        db,
        action="create",
        resource_type="host",
        resource_id=host.id,
        details={"name": host.name, "ip": host.ip,
                 "ssh_port": host.ssh_port, "ssh_auth_type": host.ssh_auth_type},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(host)

    # Best-effort ssh-keyscan so subsequent hot-update/install won't trip
    # paramiko.RejectPolicy. Failure is non-fatal — surfaced as a warning.
    key_ok, key_reason = trust_host_key(
        host.ip or "", host.ssh_port or 22, host.ssh_known_hosts_path or "",
    )
    host_key_trust = "ok" if key_ok else f"failed: {key_reason}"
    if not key_ok:
        logger.warning("host_key_trust_failed host=%s ip=%s reason=%s", host.id, host.ip, key_reason)

    return _host_to_out(host, host_key_trust=host_key_trust)


@router.get("", response_model=Union[List[HostOut], PaginatedResponse])
def list_hosts(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    query = db.query(Host).order_by(Host.id)
    total = query.count()
    hosts = query.offset(skip).limit(limit).all()
    # Update status for hosts with expired heartbeat
    needs_commit = False
    for host in hosts:
        if _ensure_host_status_up_to_date(host):
            needs_commit = True
    if needs_commit:
        db.commit()
    items = [_host_to_out(h) for h in hosts]
    # 兼容旧接口：未显式传分页参数时返回数组
    if "skip" not in request.query_params and "limit" not in request.query_params:
        return items
    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/{host_id}", response_model=HostOut)
def get_host(host_id: str, db: Session = Depends(get_db), _current_user: User = Depends(get_current_active_user)):
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="host not found")
    if _ensure_host_status_up_to_date(host):
        db.commit()
    return _host_to_out(host, db=db)


@router.put("/{host_id}", response_model=HostOut)
def update_host(
    host_id: str,
    payload: HostCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    """更新主机信息"""
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="host not found")

    old_ip = host.ip
    old_port = host.ssh_port
    host.name = payload.name
    host.hostname = payload.name
    host.ip = payload.ip
    host.ip_address = payload.ip
    host.ssh_port = payload.ssh_port
    host.ssh_user = payload.ssh_user
    host.ssh_auth_type = payload.ssh_auth_type
    host.ssh_key_path = payload.ssh_key_path
    host.ssh_known_hosts_path = payload.ssh_known_hosts_path
    if payload.ssh_password is not None:
        try:
            host.ssh_password_enc = encrypt_ssh_password(payload.ssh_password) or None
        except SshSecurityConfigError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    record_audit(
        db,
        action="update",
        resource_type="host",
        resource_id=host.id,
        details={"name": host.name, "ip": host.ip},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"主机名或 IP 与其他主机冲突：{payload.name} / {payload.ip}",
        ) from exc
    db.refresh(host)

    # Re-scan host key only when IP or port changed.
    host_key_trust = None
    if payload.ip != old_ip or payload.ssh_port != old_port:
        key_ok, key_reason = trust_host_key(
            host.ip or "", host.ssh_port or 22, host.ssh_known_hosts_path or "",
        )
        host_key_trust = "ok" if key_ok else f"failed: {key_reason}"
        if not key_ok:
            logger.warning("host_key_trust_failed host=%s ip=%s reason=%s", host.id, host.ip, key_reason)

    return _host_to_out(host, host_key_trust=host_key_trust)


@router.delete("/{host_id}")
def delete_host(
    host_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    """删除主机记录。仅 admin。若主机仍 ONLINE 或有活跃 Job 拒绝删除。"""
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="host not found")

    if host.status == "ONLINE":
        raise HTTPException(
            status_code=409,
            detail="主机当前 ONLINE，请先停止 Agent 服务再删除",
        )

    # 检查活跃 Job
    active_jobs = (
        db.query(JobInstance)
        .filter(
            JobInstance.host_id == host_id,
            JobInstance.status.in_(_ACTIVE_JOB_STATUSES),
        )
        .count()
    )
    if active_jobs > 0:
        raise HTTPException(
            status_code=409,
            detail=f"主机有 {active_jobs} 个活跃 Job，请先 abort 再删除",
        )

    record_audit(
        db,
        action="delete",
        resource_type="host",
        resource_id=host.id,
        details={"name": host.name, "ip": host.ip},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.delete(host)
    db.commit()
    return {"ok": True, "host_id": host_id, "message": "host deleted"}


@router.patch("/{host_id}/watcher-admin-state", response_model=HostOut)
def update_host_watcher_admin_state(
    host_id: str,
    payload: HostWatcherAdminStatePatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="host not found")

    host.watcher_admin_active = payload.watcher_admin_active
    record_audit(
        db,
        action="update_watcher_admin_state",
        resource_type="host",
        resource_id=host.id,
        details={"watcher_admin_active": payload.watcher_admin_active},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(host)
    return _host_to_out(host)


HOT_UPDATE_ABORT_POLL_TIMEOUT_SECONDS = float(
    os.getenv("HOT_UPDATE_ABORT_POLL_TIMEOUT_SECONDS", "45")
)
HOT_UPDATE_ABORT_POLL_INTERVAL_SECONDS = float(
    os.getenv("HOT_UPDATE_ABORT_POLL_INTERVAL_SECONDS", "1.0")
)


def _wait_until_no_active_jobs(
    db: Session, host_id: str, *, timeout_seconds: float
) -> tuple[bool, list[int]]:
    """Poll until ``host_id`` has zero PENDING/RUNNING jobs or the timeout
    elapses.  Returns (ok, lingering_job_ids)."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        db.expire_all()
        rows = (
            db.query(JobInstance.id)
            .filter(
                JobInstance.host_id == host_id,
                JobInstance.status.in_(_ACTIVE_JOB_STATUSES),
            )
            .all()
        )
        ids = [r[0] for r in rows]
        if not ids:
            return True, []
        if time.monotonic() >= deadline:
            return False, ids
        time.sleep(HOT_UPDATE_ABORT_POLL_INTERVAL_SECONDS)


@router.post("/{host_id}/hot-update")
def host_hot_update(
    host_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    abort_running_jobs: bool = Query(
        False,
        description=(
            "ADR-0021: 当 host 上有 RUNNING/PENDING Job 时, 默认拒绝热更新 (409). "
            "传 ?abort_running_jobs=true 串联 abort 流程: "
            "释放租约 → 等 Agent 自然退出 (≤45s) → 执行热更新."
        ),
    ),
    sync_agent_secret: bool = Query(
        False,
        description="可选: 同步本机后端 AGENT_SECRET 到远端 Agent .env。",
    ),
):
    """热更新：同步 Agent 代码到目标主机并重启服务。

    ADR-0021 D8 — soft-lock policy:
        active_job_count == 0 → 直接 hot-update
        active_job_count > 0  → 默认 409, 列出 active_jobs;
                                ?abort_running_jobs=true 走 abort 串联.
    """
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="host not found")

    if host.status != "ONLINE":
        raise HTTPException(
            status_code=409,
            detail=f"Host is {host.status}, hot-update requires ONLINE status",
        )

    if not host.ip:
        raise HTTPException(
            status_code=400,
            detail="Host has no IP address configured",
        )

    # ── ADR-0021 D8 active-job gate ────────────────────────────────────────
    from backend.models.plan_run import PlanRun
    from backend.scheduler.device_lease_reconciler import _ABORT_REAPER_GRACE_SECONDS
    from backend.scheduler.app_scheduler import RECONCILER_INTERVAL

    hidden_plan_ids_set = hidden_legacy_plan_ids(db)
    active_jobs_rows = (
        db.query(JobInstance)
        .filter(
            JobInstance.host_id == host_id,
            JobInstance.status.in_(_ACTIVE_JOB_STATUSES),
        )
        .order_by(JobInstance.id)
        .all()
    )

    # v3: preload PlanRun to detect abort_requested
    pr_ids = {j.plan_run_id for j in active_jobs_rows}
    pr_map: dict[int, Any] = {}
    if pr_ids:
        pr_rows = db.query(PlanRun).filter(PlanRun.id.in_(pr_ids)).all()
        pr_map = {pr.id: pr for pr in pr_rows}

    def _job_abort_pending(j: JobInstance) -> bool:
        pr = pr_map.get(j.plan_run_id)
        if pr is None or pr.run_context is None:
            return False
        return (
            isinstance(pr.run_context, dict)
            and "abort_requested" in pr.run_context
        )

    active_summary = [
        {
            "id": j.id,
            "plan_run_id": j.plan_run_id,
            "plan_id": _visible_plan_id(j.plan_id, hidden_plan_ids_set),
            "device_id": j.device_id,
            "status": j.status,
            "abort_pending": _job_abort_pending(j),
        }
        for j in active_jobs_rows
    ]
    aborted_summary: dict[str, Any] | None = None

    if active_summary:
        if not abort_running_jobs:
            all_abort_pending = all(item["abort_pending"] for item in active_summary)
            if all_abort_pending:
                # 所有 active job 都在 abort 收口中 → 返回 HOST_ABORT_PENDING
                # 计算 retry_after_seconds: 取最晚 abort 的 Job 剩余 grace，
                # 确保用户按此时间重试时所有 Job 都已被 reaper 收割
                max_remaining = 0
                now_ts = datetime.now(timezone.utc)
                for item in active_summary:
                    pr = pr_map.get(item["plan_run_id"])
                    if pr is None:
                        continue
                    rc = pr.run_context or {}
                    at_str = rc.get("abort_requested", {}).get("at", "")
                    if at_str:
                        try:
                            at_dt = datetime.fromisoformat(at_str.replace("Z", "+00:00"))
                            elapsed = (now_ts - at_dt).total_seconds()
                            remaining = max(0, _ABORT_REAPER_GRACE_SECONDS - elapsed)
                            if remaining > max_remaining:
                                max_remaining = remaining
                        except (ValueError, TypeError):
                            pass
                retry_after = int(max_remaining + RECONCILER_INTERVAL)
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "HOST_ABORT_PENDING",
                        "message": (
                            f"Abort is still draining for {len(active_summary)} job(s) "
                            f"on host {host_id}. Retry in approximately {retry_after}s."
                        ),
                        "active_jobs": active_summary,
                        "retry_after_seconds": retry_after,
                    },
                )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "HOST_HAS_ACTIVE_JOBS",
                    "message": (
                        f"Host {host_id} has {len(active_summary)} active job(s). "
                        "Pass ?abort_running_jobs=true to abort them then hot-update."
                    ),
                    "active_jobs": active_summary,
                },
            )

        # Compound path: abort then wait then update.
        aborted_summary = abort_jobs_for_host(
            host_id,
            db=db,
            reason="aborted_for_host_update",
            triggered_by=current_user.username if current_user else "api",
            audit_user_id=current_user.id if current_user else None,
            audit_username=current_user.username if current_user else None,
        )
        logger.info(
            "hot_update_abort_initiated host=%s plan_runs=%s aborted_jobs=%s",
            host_id,
            aborted_summary["plan_runs"],
            aborted_summary["aborted_jobs"],
        )

        ok_drained, lingering = _wait_until_no_active_jobs(
            db, host_id,
            timeout_seconds=HOT_UPDATE_ABORT_POLL_TIMEOUT_SECONDS,
        )
        if not ok_drained:
            raise HTTPException(
                status_code=504,
                detail={
                    "code": "ABORT_DRAIN_TIMEOUT",
                    "message": (
                        f"Aborted jobs but {len(lingering)} job(s) on host {host_id} "
                        f"did not reach a terminal state within "
                        f"{HOT_UPDATE_ABORT_POLL_TIMEOUT_SECONDS}s. "
                        "Investigate the agent or retry."
                    ),
                    "lingering_jobs": lingering,
                    "abort_summary": aborted_summary,
                },
            )

    # ── SSH credentials ────────────────────────────────────────────────────
    try:
        creds, _migrated = resolve_host_ssh_credentials(
            host, inventory_lookup=_resolve_ssh_creds,
        )
    except SshSecurityConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not creds.password and not creds.key_path:
        raise HTTPException(
            status_code=400,
            detail=(
                "Host has no SSH credentials configured and is not found "
                "in Ansible inventory. Set ssh_password or ssh_key_path via "
                "PUT /api/v1/hosts/{host_id}."
            ),
        )

    agent_secret = _get_syncable_agent_secret() if sync_agent_secret else ""
    code_version = get_agent_code_version()

    record_audit(
        db,
        action="hot_update",
        resource_type="host",
        resource_id=None,
        details={
            "host_id": host_id,
            "ip": host.ip,
            "abort_running_jobs": abort_running_jobs,
            "sync_agent_secret": sync_agent_secret,
            "aborted_jobs": (
                aborted_summary["aborted_jobs"] if aborted_summary else []
            ),
            "code_version": code_version,
        },
        user_id=current_user.id if current_user else None,
        username=current_user.username if current_user else None,
    )
    db.commit()

    result = execute_hot_update(
        host_ip=host.ip or "",
        ssh_port=host.ssh_port or 22,
        ssh_user=creds.user,
        ssh_password=creds.password,
        ssh_key_path=creds.key_path,
        known_hosts_path=creds.known_hosts_path,
        sync_agent_secret=sync_agent_secret,
        agent_secret=agent_secret,
        code_version=code_version,
    )

    record_audit(
        db,
        action="hot_update_result",
        resource_type="host",
        resource_id=None,
        details={
            "host_id": host_id,
            "ip": host.ip,
            "ok": bool(result.get("ok")),
            "deps_refreshed": bool(result.get("deps_refreshed")),
            "code_version": result.get("code_version", ""),
            "duration_ms": result.get("duration_ms"),
            "message": result.get("message", ""),
        },
        user_id=current_user.id if current_user else None,
        username=current_user.username if current_user else None,
    )
    db.commit()

    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result["message"])

    return {
        "ok": True,
        "host_id": host_id,
        "message": result["message"],
        "duration_ms": result.get("duration_ms"),
        "deps_refreshed": result.get("deps_refreshed", False),
        "code_version": result.get("code_version", ""),
        "abort_summary": aborted_summary,
    }


@router.post("/{host_id}/install")
def host_install_agent(
    host_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """触发 Agent 首次安装（ansible-playbook install_agent.yml，SAQ 异步执行）。

    自动检测控制平面 ansible-playbook + sshpass 是否可用；缺失则 501。
    装完 install_agent.sh 自动落 sudoers.d NOPASSWD，解锁后续免密热更新。
    """
    import shutil

    missing = [
        cmd for cmd in ("ansible-playbook", "sshpass")
        if not shutil.which(cmd)
    ]
    if missing:
        raise HTTPException(
            status_code=501,
            detail=(
                f"Agent 首次安装缺少依赖：{', '.join(missing)}。"
                f"控制平面请执行：apt install ansible-core sshpass。"
                f"或改用 CLI: ansible-playbook tools/ansible/playbooks/install_agent.yml"
            ),
        )

    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="host not found")
    if not host.ip:
        raise HTTPException(status_code=400, detail="Host has no IP address configured")

    record_audit(
        db,
        action="install_agent_request",
        resource_type="host",
        resource_id=host_id,
        details={"host_id": host_id, "ip": host.ip},
        user_id=current_user.id if current_user else None,
        username=current_user.username if current_user else None,
    )
    db.commit()

    saq_key = f"install:{host_id}"
    try:
        enqueue_sync(
            "install_agent_task",
            key=saq_key,
            timeout=900,
            retries=0,
            required=True,
            host_id=host_id,
            initiated_by=current_user.username if current_user else None,
        )
    except EnqueueSyncError as exc:
        raise HTTPException(status_code=503, detail=f"SAQ enqueue failed: {exc}") from exc

    return {
        "ok": True,
        "host_id": host_id,
        "saq_key": saq_key,
        "status": "queued",
        "message": "Agent 安装任务已入队，轮询 GET /hosts/{id}/install/status 查看进度",
    }


@router.get("/{host_id}/install/status")
def host_install_status(
    host_id: str,
    _db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """查询 Agent 首次安装 SAQ 任务状态。返回 status + 已记录的 log_path（如有）。"""
    saq_key = f"install:{host_id}"
    state = get_saq_job_state_sync(saq_key)
    if state is None:
        return {"host_id": host_id, "saq_key": saq_key, "status": "unknown",
                "log_path": None, "result": None}
    result = None
    if isinstance(state.get("result"), dict):
        result = state["result"]
    return {
        "host_id": host_id,
        "saq_key": saq_key,
        "status": state.get("status", "unknown"),
        "log_path": (result or {}).get("log_path"),
        "result": result,
    }
