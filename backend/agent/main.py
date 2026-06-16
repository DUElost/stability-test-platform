import logging
import os
import signal
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Set

# 自动加载 .env 文件（支持手动运行时读取配置）
# 优先加载当前工作目录的 .env，不覆盖已有环境变量
try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:
    pass  # python-dotenv 未安装时跳过，由 systemd EnvironmentFile 提供变量

# 支持直接运行和作为包运行
if __name__ == "__main__" and __package__ is None:
    # 直接运行时的导入路径处理
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from agent.adb_wrapper import AdbWrapper
    from agent.api_client import complete_run, fetch_pending_jobs, sync_recovery
    from agent.aee.state_migration import migrate_legacy_aee_state_keys
    from agent.artifact_uploader import ArtifactUploader
    from agent.config import BASE_DIR, ensure_dirs
    from agent.log_archiver import LogArchiver, collect_archive_heartbeat_metrics
    from agent.local_disk_monitor import LocalDiskMonitor
    from agent.heartbeat_thread import HeartbeatThread
    from agent.host_registry import auto_register_host, get_host_info, load_required_host_id
    from agent.job_runner import JobRunnerState, run_task_wrapper
    from agent.lease_renewer import LeaseRenewer
    from agent.mq.producer import StepTraceWriter
    from agent.outbox_drainer import OutboxDrainThread
    from agent.registry.local_db import LocalDB
    from agent.registry.script_registry import ScriptRegistry
    from agent.step_trace_uploader import StepTraceUploader
    from agent.watcher import LogWatcherManager, OutboxDrainer
    from agent.watcher.enable import watcher_subsystem_enabled
    from agent.socketio_client import AgentSocketIOClient
else:
    from .adb_wrapper import AdbWrapper
    from .api_client import complete_run, fetch_pending_jobs, sync_recovery
    from .aee.state_migration import migrate_legacy_aee_state_keys
    from .artifact_uploader import ArtifactUploader
    from .config import BASE_DIR, ensure_dirs
    from .log_archiver import LogArchiver, collect_archive_heartbeat_metrics
    from .local_disk_monitor import LocalDiskMonitor
    from .heartbeat_thread import HeartbeatThread
    from .host_registry import auto_register_host, get_host_info, load_required_host_id
    from .job_runner import JobRunnerState, run_task_wrapper
    from .lease_renewer import LeaseRenewer
    from .mq.producer import StepTraceWriter
    from .outbox_drainer import OutboxDrainThread
    from .registry.local_db import LocalDB
    from .registry.script_registry import ScriptRegistry
    from .step_trace_uploader import StepTraceUploader
    from .watcher import LogWatcherManager, OutboxDrainer
    from .watcher.enable import watcher_subsystem_enabled
    from .socketio_client import AgentSocketIOClient

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# Device Log Watcher feature flag —— 全局 STP_WATCHER_ENABLED 或 Plan 默认开启。
# Plan 执行默认开启 watcher（STP_WATCHER_PLAN_DEFAULT=true）时，即使全局 env=false
# 也会 configure 子系统，并在 claim Plan job 时启动 JobSession。
STP_WATCHER_ENABLED = os.getenv("STP_WATCHER_ENABLED", "true").lower() == "true"
STP_WATCHER_PLAN_DEFAULT = os.getenv("STP_WATCHER_PLAN_DEFAULT", "true").lower() == "true"

# 全局活跃 Job 追踪（语义上存的就是 job_instance.id）
# 命名约定：_active_job_ids / _active_jobs_lock
_active_job_ids: Set[int] = set()
_active_device_ids: Set[int] = set()  # per-device concurrency guard
_active_job_tokens: Dict[int, str] = {}
_active_jobs_lock = threading.Lock()
_lock_renewal_stop_event = threading.Event()


def _make_local_worker_token(job_id: int, prefix: str) -> str:
    """Create a per-submit worker ownership token scoped to this agent process."""
    return f"{prefix}-{job_id}-{uuid.uuid4().hex[:12]}"


def _migrate_legacy_aee_state_on_startup(db_path: str) -> Dict[str, Any]:
    """Promote legacy scan_aee state into watcher:aee namespace during agent startup."""
    summary = migrate_legacy_aee_state_keys(db_path)
    if (
        int(summary["processed_entries_migrated"]) > 0
        or int(summary["pending_pull_migrated"]) > 0
    ):
        logger.info(
            "startup_aee_state_namespace_migrated db_path=%s summary=%s",
            db_path,
            summary,
        )
    if summary.get("errors"):
        logger.warning(
            "startup_aee_state_namespace_migration_errors db_path=%s errors=%s",
            db_path,
            summary["errors"],
        )
    return summary


# 全局活跃 Job 追踪辅助函数（仅 per-device guard）
def _register_active_device(did: int) -> None:
    with _active_jobs_lock:
        _active_device_ids.add(did)


def _deregister_active_device(did: int) -> None:
    with _active_jobs_lock:
        _active_device_ids.discard(did)


def _cleanup_after_lease_lost(
    *,
    job_id: int,
    device_id: Optional[int],
    active_jobs_lock: Any,
    active_job_ids: Set[int],
    active_device_ids: Set[int],
    active_job_tokens: Dict[int, str],
    local_db: Any,
) -> None:
    with active_jobs_lock:
        active_job_ids.discard(job_id)
        active_job_tokens.pop(job_id, None)
        if device_id is not None:
            active_device_ids.discard(device_id)
    # 保留本地 active_job 记录，等待设备重连或 agent 重启时走 recovery/sync 恢复。


def _cleanup_after_job_exit(
    *,
    job_id: int,
    fencing_token: str,
    local_worker_token: str = "",
    active_jobs_lock: Any,
    active_job_ids: Set[int],
    active_device_ids: Set[int],
    active_job_tokens: Dict[int, str],
    lease_renewer: Any,
    local_db: Any,
) -> None:
    """Worker/JobSession 退出后的统一清理。

    正常完成时删除本地 active_job；若该 job 已先因 lease_lost 从活跃集合移除，
    则仅清 runtime 占位，保留本地记录等待 recovery/sync。
    """
    effective_worker_token = local_worker_token or fencing_token
    device_id = lease_renewer.clear_fencing_token_if_current(
        job_id,
        fencing_token,
        effective_worker_token,
    )
    with active_jobs_lock:
        current_token = active_job_tokens.get(job_id, "")
        job_was_active = job_id in active_job_ids and (
            not effective_worker_token or current_token == effective_worker_token
        )
        if job_was_active:
            active_job_ids.discard(job_id)
            active_job_tokens.pop(job_id, None)
            if device_id is not None:
                active_device_ids.discard(device_id)
    if job_was_active:
        local_db.delete_active_job(job_id)


def trigger_recovery_sync_on_device_reconnect(
    *,
    reconnected_serials: List[str],
    local_db: Any,
    api_url: str,
    host_id: str,
    agent_instance_id: str,
    boot_id: str,
    execute_actions: Any,
) -> bool:
    """Device reconnect hook: re-run recovery sync when local active jobs still exist."""
    if not reconnected_serials:
        return False

    persisted_jobs = local_db.get_active_jobs()
    if not persisted_jobs:
        logger.info(
            "recovery_skip_reconnect_no_local_jobs serials=%s",
            ",".join(reconnected_serials),
        )
        return False

    matched_jobs = [
        job
        for job in persisted_jobs
        if job.get("device_serial") and job["device_serial"] in reconnected_serials
    ]
    if not matched_jobs:
        logger.info(
            "recovery_skip_reconnect_no_serial_match serials=%s active_jobs=%d",
            ",".join(reconnected_serials),
            len(persisted_jobs),
        )
        return False

    logger.info(
        "recovery_reconnect_triggered serials=%s matched_jobs=%d active_jobs=%d",
        ",".join(reconnected_serials),
        len(matched_jobs),
        len(persisted_jobs),
    )
    run_recovery_sync_if_needed(
        local_db=local_db,
        api_url=api_url,
        host_id=host_id,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        execute_actions=execute_actions,
        active_jobs=matched_jobs,
    )
    return True


def execute_recovery_actions_impl(
    resp: dict,
    active_jobs_by_id: dict,
    lease_renewer: Any,
    local_db: Any,
    outbox_drain: Any,
    register_active_job: Any,
    resume_job: Any = None,
) -> None:
    """ADR-0019 Phase 3a: execute recovery actions (module-level for testability)."""
    job_actions = resp.get("actions", [])
    outbox_actions = resp.get("outbox_actions", [])
    resumed_job_ids: set[int] = set()

    for a in job_actions:
        jid = a["job_id"]
        action = a["action"]
        if action == "RESUME":
            token = a.get("fencing_token", "")
            # Defense-in-depth: a RESUME without a dict job_payload cannot re-enter
            # JobSession, so the watcher would never re-attach and the job would
            # become a zombie active record. Backend now guarantees RESUME carries
            # a payload (job-row-missing → ABORT_LOCAL); skip registration if it
            # somehow doesn't, and let the next recovery round reconcile.
            if not isinstance(a.get("job_payload"), dict):
                logger.warning(
                    "recovery_resume_missing_payload job=%d — skipping register (backend will reconcile)",
                    jid,
                )
                continue
            persisted_job = active_jobs_by_id.get(jid) or {}
            device_serial = (a.get("device_serial") or persisted_job.get("device_serial") or "").strip()
            local_worker_token = _make_local_worker_token(jid, "resume")
            register_active_job(
                jid,
                token,
                a.get("device_id"),
                device_serial,
                local_worker_token,
            )
            resumed_job_ids.add(jid)
            if resume_job is not None:
                resumed_payload = dict(a["job_payload"])
                resumed_payload["id"] = jid
                if a.get("device_id") is not None:
                    resumed_payload["device_id"] = a["device_id"]
                if device_serial:
                    resumed_payload["device_serial"] = device_serial
                if token:
                    resumed_payload["fencing_token"] = token
                resumed_payload["local_worker_token"] = local_worker_token
                # T3: mark as recovery-resumed so the watcher re-attach is observable
                resumed_payload["recovery_resumed"] = True
                try:
                    resume_job(resumed_payload)
                except Exception:
                    logger.exception("recovery_resume_submit_failed job=%d", jid)
            logger.info(
                "recovery_resume job=%d token=%s worker=%s",
                jid,
                token[:8] if token else "",
                local_worker_token,
            )
        elif action == "CLEANUP":
            local_db.delete_active_job(jid)
            lease_renewer.clear_fencing_token(jid)
            logger.warning("recovery_cleanup job=%d reason=%s", jid, a.get("reason"))
        elif action == "ABORT_LOCAL":
            local_db.delete_active_job(jid)
            lease_renewer.clear_fencing_token(jid)
            logger.warning("recovery_abort_local job=%d reason=%s", jid, a.get("reason"))

    if outbox_actions:
        has_upload = any(a["action"] == "UPLOAD_TERMINAL" for a in outbox_actions)
        if has_upload:
            try:
                flushed = outbox_drain.drain_sync()
                logger.info("recovery_outbox_flushed count=%d", flushed)
            except Exception:
                logger.exception("recovery_outbox_flush_failed")
                return

        still_pending = local_db.get_pending_outbox()
        pending_ids = {e["job_id"] for e in still_pending}
        for a in outbox_actions:
            jid = a["job_id"]
            action = a["action"]
            if jid in resumed_job_ids:
                logger.info("recovery_outbox_skip_active_job_cleanup job=%d action=%s", jid, action)
                continue
            if action == "UPLOAD_TERMINAL":
                if jid not in pending_ids:
                    local_db.delete_active_job(jid)
                else:
                    logger.warning("recovery_upload_terminal_still_pending job=%d", jid)
            elif action == "NOOP":
                local_db.delete_active_job(jid)


def _check_agent_version(api_url: str, host_id: str, mount_points, host_info) -> None:
    """Send a single heartbeat and verify agent version meets backend's minimum.

    Exits the process if the agent is too old.
    """
    from . import __version__ as agent_version
    from .heartbeat import send_heartbeat

    try:
        resp = send_heartbeat(api_url, host_id, mount_points, host_info=host_info)
    except Exception:
        logger.warning("version_check_heartbeat_failed — skipping version guard")
        return

    if resp is None:
        logger.warning("version_check_no_response — skipping version guard")
        return

    min_version = (resp.get("agent_min_version") or "").strip()
    if not min_version:
        return  # Backend doesn't enforce a minimum version yet

    if _version_lt(agent_version, min_version):
        logger.critical(
            "agent_version_too_old agent=%s required=%s — refusing to start",
            agent_version, min_version,
        )
        sys.exit(1)

    logger.info("version_check_ok agent=%s min=%s", agent_version, min_version)


def _version_lt(a: str, b: str) -> bool:
    """Compare two SemVer strings (no pre-release tags). Returns True if a < b."""
    try:
        parts_a = [int(x) for x in a.split(".")]
        parts_b = [int(x) for x in b.split(".")]
    except (ValueError, TypeError):
        return False  # Malformed versions → don't block
    # Pad shorter list with zeros
    max_len = max(len(parts_a), len(parts_b))
    parts_a += [0] * (max_len - len(parts_a))
    parts_b += [0] * (max_len - len(parts_b))
    return parts_a < parts_b


def run_recovery_sync_if_needed(
    local_db: Any,
    api_url: str,
    host_id: str,
    agent_instance_id: str,
    boot_id: str,
    execute_actions: Any,
    active_jobs: Optional[List[dict]] = None,
) -> None:
    """ADR-0019 Phase 3a: check local persisted state and sync with Backend if needed."""
    try:
        persisted_jobs = active_jobs if active_jobs is not None else local_db.get_active_jobs()
        pending_outbox = local_db.get_pending_outbox()
        if persisted_jobs or pending_outbox:
            resp = sync_recovery(
                api_url, host_id, agent_instance_id, boot_id,
                active_jobs=persisted_jobs,
                pending_outbox=pending_outbox,
            )
            if resp is not None:
                execute_actions(resp, {j["job_id"]: j for j in persisted_jobs})
                logger.info(
                    "recovery_sync_complete active_jobs=%d outbox=%d",
                    len(persisted_jobs), len(pending_outbox),
                )
        else:
            logger.info("recovery_skip_no_persisted_state")
    except Exception:
        logger.exception("recovery_sync_failed_continuing")


def main() -> None:
    api_url = os.getenv("API_URL", "http://127.0.0.1:8000")
    max_concurrent_tasks = int(os.getenv("MAX_CONCURRENT_TASKS", "2"))

    # 确保运行时目录存在
    ensure_dirs()

    # 获取本机信息（需要在验证 HOST_ID 之前）
    host_info = get_host_info()

    # ADR-0019 Phase 3a: generate agent identity
    from .identity import generate_agent_instance_id, read_boot_id

    agent_instance_id = generate_agent_instance_id()
    boot_id = read_boot_id()
    # ADR-0020: agent version for preflight consistency check
    from . import __version__ as _agent_pkg_version
    logger.info("agent_identity instance=%s boot=%s version=%s", agent_instance_id, boot_id, _agent_pkg_version)

    # 加载 HOST_ID，支持自动注册
    try:
        host_id = load_required_host_id()
    except ValueError as exc:
        # 检查是否启用自动注册
        if os.getenv("AUTO_REGISTER_HOST", "false").lower() == "true":
            host_id = None  # will be resolved in the retry loop below
        else:
            logger.error(
                "invalid_host_id_config",
                extra={
                    "host_id_raw": os.getenv("HOST_ID"),
                    "error": str(exc),
                },
            )
            logger.error(
                "Set HOST_ID to a positive integer, or set AUTO_REGISTER_HOST=true to auto-register"
            )
            raise SystemExit(2)

    # 如果 host_id 为 None（自动注册模式），带重试地注册
    if host_id is None:
        max_retries = int(os.getenv("AUTO_REGISTER_MAX_RETRIES", "0"))  # 0 = infinite
        retry_delay = float(os.getenv("AUTO_REGISTER_RETRY_DELAY", "10"))
        attempt = 0
        while True:
            attempt += 1
            try:
                host_id = auto_register_host(api_url, host_info)
                break
            except Exception as exc:
                if max_retries and attempt >= max_retries:
                    logger.error("auto_register_failed after %d attempts: %s", attempt, exc)
                    raise SystemExit(2)
                logger.warning(
                    "auto_register_retry attempt=%d delay=%.0fs error=%s",
                    attempt, retry_delay, exc,
                )
                time.sleep(retry_delay)
    poll_interval = float(os.getenv("POLL_INTERVAL", "5"))
    mount_points = [p for p in os.getenv("MOUNT_POINTS", "").split(",") if p]
    adb_path = os.getenv("ADB_PATH", "adb")

    logger.info(
        "agent_started",
        extra={"host_id": host_id, "api_url": api_url, "ip": host_info["ip"]},
    )

    adb = AdbWrapper(adb_path=adb_path)
    # 启动 WebSocket 客户端（best-effort，失败时降级到 HTTP）
    agent_secret = os.getenv("AGENT_SECRET", "")
    sio_client = AgentSocketIOClient(api_url, host_id, agent_secret)
    sio_client.connect()
    # Start background reconnect loop for auto-recovery on disconnect
    sio_client.start_reconnect_loop()

    # 初始化本地 SQLite WAL 缓存
    local_db = LocalDB()
    db_path = str(BASE_DIR / "agent_state.db")
    local_db.initialize(db_path)
    _migrate_legacy_aee_state_on_startup(db_path)

    script_registry = ScriptRegistry(local_db, api_url, agent_secret)
    script_registry.initialize()

    # Device Log Watcher 子系统（全局或 Plan 默认开启时 configure）
    log_signal_drainer: Optional[OutboxDrainer] = None
    if watcher_subsystem_enabled():
        # 5B1 + D1：LogPuller NFS 根（空串 = 禁用 puller，仅记元数据）
        nfs_base_dir = (
            os.getenv("STP_WATCHER_NFS_BASE_DIR", "")
            or os.getenv("STP_AEE_NFS_ROOT", "")
        )
        LogWatcherManager.instance().configure(
            adb=adb,
            adb_path=adb_path,          # InotifydSource.Popen 需要 adb 二进制路径
            local_db=local_db,
            sio_client=sio_client,
            api_url=api_url,
            agent_secret=agent_secret,
            nfs_base_dir=nfs_base_dir,
        )
        # log_signal_outbox 后台批量上送线程（watcher 写入 → drainer 推送到后端）
        log_signal_drainer = OutboxDrainer.instance().configure(
            local_db=local_db,
            api_url=api_url,
            agent_secret=agent_secret,
            interval_seconds=5.0,
            batch_size=50,
        )
        log_signal_drainer.start()
        # 5B2：artifact 上传单例（fire-and-forget；失败不影响 log_signal 主链路）
        ArtifactUploader.instance().configure(
            api_url=api_url,
            agent_secret=agent_secret,
        )
        ArtifactUploader.instance().start()
        logger.info("watcher_subsystem_enabled log_signal_drainer=started artifact_uploader=started")
        # ADR-0025 Sprint 2: 运行日志归档调度器 + 本地盘溢出监控（nfs_base_dir 为空时归档禁用）
        if nfs_base_dir:
            LogArchiver.instance().configure(
                local_db=local_db,
                host_id=host_id,
                nfs_base_dir=nfs_base_dir,
                run_log_dir=str(BASE_DIR / "logs" / "runs"),
                api_url=api_url,
                agent_secret=agent_secret,
                interval_seconds=float(os.getenv("STP_LOG_ARCHIVE_INTERVAL_SECONDS", "3600")),
                grace_seconds=float(os.getenv("STP_LOG_ARCHIVE_GRACE_SECONDS", "1800")),
            ).start()
            LocalDiskMonitor.instance().configure(
                archiver=LogArchiver.instance(),
                base_dir=str(BASE_DIR),
                interval_seconds=float(os.getenv("STP_LOCAL_DISK_MONITOR_INTERVAL_SECONDS", "300")),
                spill_threshold_pct=float(os.getenv("STP_LOCAL_DISK_SPILL_THRESHOLD", "80")),
                target_pct=float(os.getenv("STP_LOCAL_DISK_SPILL_TARGET", "70")),
            ).start()
            logger.info("log_archiver=started local_disk_monitor=started nfs_base=%s", nfs_base_dir)
        else:
            logger.info("log_archiver_skipped nfs_base_dir_empty")
        # M4/T4-4: 清理上次进程残留的 active watcher_state(崩溃/重启脏记录)。
        # 必须在 configure(注入 local_db)之后调用。
        try:
            stale_cleaned = LogWatcherManager.instance().reconcile_on_startup()
            if stale_cleaned:
                logger.info("watcher_reconcile_on_startup cleaned_stale=%d", stale_cleaned)
        except Exception:
            logger.exception("watcher_reconcile_on_startup failed")
        # D2: AeeDbHistoryReconciler 启动期参数(读 env;是否真正启动按 capability + host 白名单门控)
        logger.info(
            "aee_reconciler_env enabled=%s interval_seconds=%s burst_interval_seconds=%s "
            "burst_rounds=%s hosts=%s",
            os.getenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "true"),
            os.getenv("STP_WATCHER_AEE_RECONCILE_INTERVAL_SECONDS", "180"),
            os.getenv("STP_WATCHER_AEE_RECONCILE_BURST_INTERVAL_SECONDS", "60"),
            os.getenv("STP_WATCHER_AEE_RECONCILE_BURST_ROUNDS", "5"),
            os.getenv("STP_WATCHER_AEE_RECONCILE_HOSTS", "") or "(unset → 全 host 放行)",
        )
    else:
        logger.info(
            "watcher_subsystem_disabled (STP_WATCHER_ENABLED=false STP_WATCHER_PLAN_DEFAULT=false)"
        )

    # Step trace local writer (Redis XADD removed in Phase 4; HTTP upload via StepTraceUploader)
    mq_producer = StepTraceWriter("", host_id, local_db=local_db)

    # Control commands via SocketIO (replaces Redis ControlListener)
    def _handle_control(data):
        command = data.get("command", "")
        payload = data.get("payload", {})
        if command == "backpressure":
            limit_str = payload.get("log_rate_limit")
            limit = None
            if limit_str and str(limit_str) not in ("None", "null", ""):
                try:
                    limit = int(limit_str)
                except ValueError:
                    pass
            mq_producer.set_log_rate_limit(limit)
        elif command == "abort":
            job_id = payload.get("job_id")
            if job_id:
                _deregister_active_job(int(job_id))
                logger.info("control_abort job_id=%s", job_id)
        else:
            logger.warning("unknown_control_command: %s", command)

    # ADR-0019 Phase 1: capacity helper — thread-safe active job count
    def _get_active_job_count() -> int:
        with _active_jobs_lock:
            return len(_active_job_ids)

    # ADR-0019 Phase 3c: active device count for effective_slots
    def _get_active_device_count() -> int:
        with _active_jobs_lock:
            return len(_active_device_ids)

    _execute_recovery_actions = None

    # 启动心跳守护线程（独立于任务执行循环）
    heartbeat_thread = HeartbeatThread(
        api_url=api_url,
        host_id=host_id,
        adb_path=adb_path,
        mount_points=mount_points,
        host_info=host_info,
        poll_interval=poll_interval,
        sio_client=sio_client,
        catalog_versions=lambda: {
            "script_catalog_version": script_registry.version,
        },
        on_scripts_outdated=script_registry.initialize,
        get_active_job_count=_get_active_job_count,
        get_active_device_count=_get_active_device_count,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        agent_version=_agent_pkg_version,
        get_outbox_counts=lambda: {
            "terminal_outbox_pending": local_db.count_pending_terminals(),
            "log_signal_outbox_pending": local_db.count_pending_log_signals(),
        },
        # ADR-0025 Sprint 2: 上报归档指标到 extra['archive']（归档禁用时回调返回 None）
        get_archive_metrics=collect_archive_heartbeat_metrics,
        on_devices_reconnected=lambda serials: (
            trigger_recovery_sync_on_device_reconnect(
                reconnected_serials=serials,
                local_db=local_db,
                api_url=api_url,
                host_id=host_id,
                agent_instance_id=agent_instance_id,
                boot_id=boot_id,
                execute_actions=_execute_recovery_actions,
            )
            if _execute_recovery_actions is not None
            else False
        ),
    )
    heartbeat_thread.start()

    # ADR-0019 Phase 3b: lease 丢失回调（409 时 LeaseRenewer 内部已清理，此处清理外部状态）
    def _on_lease_lost(jid: int, device_id: Optional[int]) -> None:
        try:
            _cleanup_after_lease_lost(
                job_id=jid,
                device_id=device_id,
                active_jobs_lock=_active_jobs_lock,
                active_job_ids=_active_job_ids,
                active_device_ids=_active_device_ids,
                active_job_tokens=_active_job_tokens,
                local_db=local_db,
            )
        except Exception:
            logger.exception("on_lease_lost_cleanup_failed", extra={
                "job_id": jid,
                "reason": "external_cleanup_exception",
            })

    # 启动 lease 续租器
    lease_renewer = LeaseRenewer(
        api_url,
        active_jobs_lock=_active_jobs_lock,
        active_job_ids=_active_job_ids,
        lock_renewal_stop_event=_lock_renewal_stop_event,
        agent_instance_id=agent_instance_id,
        on_lease_lost=_on_lease_lost,
    )
    lease_renewer.start()

    # ADR-0019 Phase 2b + Phase 3a/3b: 活跃 job 注册/注销闭包（捕获 lease_renewer + local_db）
    def _register_active_job(
        jid: int,
        fencing_token: str = "",
        device_id: Optional[int] = None,
        device_serial: str = "",
        local_worker_token: str = "",
    ) -> None:
        effective_worker_token = local_worker_token or fencing_token
        with _active_jobs_lock:
            _active_job_ids.add(jid)
            _active_job_tokens[jid] = effective_worker_token
            if device_id is not None:
                _active_device_ids.add(device_id)  # Phase 3b: 注册时同步占位 device
        if fencing_token:
            lease_renewer.set_fencing_token(
                jid,
                fencing_token,
                device_id,
                effective_worker_token,
            )
        if device_id is not None:
            local_db.save_active_job(jid, device_id, fencing_token, device_serial)

    def _deregister_active_job(
        jid: int,
        fencing_token: str = "",
        local_worker_token: str = "",
    ) -> None:
        _cleanup_after_job_exit(
            job_id=jid,
            fencing_token=fencing_token,
            local_worker_token=local_worker_token,
            active_jobs_lock=_active_jobs_lock,
            active_job_ids=_active_job_ids,
            active_device_ids=_active_device_ids,
            active_job_tokens=_active_job_tokens,
            lease_renewer=lease_renewer,
            local_db=local_db,
        )

    # 必须在闭包定义之后注册，避免 _handle_control 中 _deregister_active_job 引用未绑定
    sio_client.set_control_handler(_handle_control)

    # 启动终态 Outbox Drain 线程
    outbox_drain = OutboxDrainThread(api_url, local_db, interval=15.0)
    outbox_drain.start()

    _resume_recovered_job = None

    # ── ADR-0019 Phase 3a: Recovery Sync ──
    def _execute_recovery_actions_impl_closure(
        resp: dict,
        active_jobs_by_id: dict,
    ) -> None:
        """Execute recovery actions returned by Backend (closure capturing dependencies)."""
        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id=active_jobs_by_id,
            lease_renewer=lease_renewer,
            local_db=local_db,
            outbox_drain=outbox_drain,
            register_active_job=_register_active_job,
            resume_job=_resume_recovered_job,
        )
    _execute_recovery_actions = _execute_recovery_actions_impl_closure

    from .patrol_recovery import build_patrol_job_not_running_handler

    patrol_job_not_running_recovery = build_patrol_job_not_running_handler(
        api_url=api_url,
        host_id=host_id,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        local_db=local_db,
        execute_actions=_execute_recovery_actions_impl_closure,
    )

    # Version compatibility guard: refuse to run if agent is below backend's min_version
    _check_agent_version(api_url, host_id, mount_points, host_info)

    # StepTrace HTTP 批量上报（Phase 3.7: acked=0 补传 → Phase 4: 唯一上报路径）
    step_trace_uploader = StepTraceUploader(
        api_url, local_db, agent_secret=agent_secret, interval=5.0,
    )
    step_trace_uploader.start()

    # Create thread pool for parallel task execution
    executor = ThreadPoolExecutor(
        max_workers=max_concurrent_tasks, thread_name_prefix="task-worker"
    )
    job_runner_state = JobRunnerState(
        active_jobs_lock=_active_jobs_lock,
        active_job_ids=_active_job_ids,
        active_device_ids=_active_device_ids,
        active_job_tokens=_active_job_tokens,
        running_worker_tokens={},
        watcher_globally_enabled=STP_WATCHER_ENABLED,
        watcher_plan_default=STP_WATCHER_PLAN_DEFAULT,
        lock_register=_register_active_job,
        lock_deregister=_deregister_active_job,
        device_id_register=_register_active_device,
        device_id_deregister=_deregister_active_device,
        on_job_not_running_recovery=patrol_job_not_running_recovery,
    )

    def _resume_recovered_job_impl(job_payload: dict) -> None:
        executor.submit(
            run_task_wrapper,
            job_payload,
            adb,
            api_url,
            host_id,
            job_runner_state,
            mq_producer,
            script_registry,
            local_db,
        )

    _resume_recovered_job = _resume_recovered_job_impl

    # Recovery sync execution
    run_recovery_sync_if_needed(
        local_db=local_db,
        api_url=api_url,
        host_id=host_id,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        execute_actions=_execute_recovery_actions_impl_closure,
    )
    # SIGTERM / SIGINT graceful shutdown
    _shutdown_event = threading.Event()

    def _signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("received_%s, initiating graceful shutdown", sig_name)
        _shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        while not _shutdown_event.is_set():
            try:
                with _active_jobs_lock:
                    active_count = len(_active_job_ids)

                available_slots = max(0, max_concurrent_tasks - active_count)

                logger.info("main_loop_tick active=%d slots=%d", active_count, available_slots)

                if available_slots > 0:
                    jobs = fetch_pending_jobs(api_url, host_id, agent_instance_id,
                                              capacity=available_slots)
                    jobs = jobs[:available_slots]

                    if jobs:
                        logger.info(
                            "pending_jobs_fetched host_id=%s count=%d slots=%d job_ids=%s",
                            host_id, len(jobs), available_slots,
                            [job.get("id") for job in jobs],
                        )
                    else:
                        logger.debug(
                            "no_pending_jobs host_id=%s active=%d slots=%d",
                            host_id, active_count, available_slots,
                        )

                    for claimed_job in jobs:
                        job = dict(claimed_job)
                        device_id = job.get("device_id")

                        with _active_jobs_lock:
                            if device_id and device_id in _active_device_ids:
                                logger.debug(
                                    "skip_device_busy job=%d device=%d",
                                    job["id"], device_id,
                                )
                                continue
                            if device_id:
                                _active_device_ids.add(device_id)

                        local_worker_token = _make_local_worker_token(job["id"], "claim")
                        job["local_worker_token"] = local_worker_token

                        # ADR-0019 Phase 2b + 3a: 注册 job + fencing_token + 持久化 active_job
                        _register_active_job(
                            job["id"],
                            job["fencing_token"],
                            device_id,
                            job.get("device_serial", ""),
                            local_worker_token,
                        )

                        try:
                            executor.submit(
                                run_task_wrapper,
                                job,
                                adb,
                                api_url,
                                host_id,
                                job_runner_state,
                                mq_producer,
                                script_registry,
                                local_db,
                            )
                        except Exception:
                            logger.exception("submit_failed job=%d device=%s", job["id"], device_id)
                            _deregister_active_job(
                                job["id"],
                                job.get("fencing_token", ""),
                                local_worker_token,
                            )
                            with _active_jobs_lock:
                                if device_id:
                                    _active_device_ids.discard(device_id)

            except Exception:
                logger.exception("agent_loop_failed", extra={"host_id": host_id})
            # Use event wait instead of sleep so SIGTERM wakes us immediately
            _shutdown_event.wait(poll_interval)
    finally:
        logger.info("agent_shutting_down, waiting for active tasks to finish...")
        executor.shutdown(wait=True, cancel_futures=False)
        # Flush step traces via HTTP before shutdown
        try:
            flushed = step_trace_uploader.drain_sync()
            if flushed:
                logger.info("shutdown_step_trace_flushed count=%d", flushed)
        except Exception:
            logger.exception("shutdown_step_trace_flush_failed")
        step_trace_uploader.stop()
        # Final outbox drain: flush any un-acked terminal states
        try:
            flushed = outbox_drain.drain_sync()
            if flushed:
                logger.info("shutdown_outbox_flushed count=%d", flushed)
        except Exception:
            logger.exception("shutdown_outbox_flush_failed")
        outbox_drain.stop()
        # log_signal_outbox drainer（watcher 子系统启用时）
        if log_signal_drainer is not None:
            try:
                flushed = log_signal_drainer.tick_once()
                if flushed:
                    logger.info("shutdown_log_signal_flushed count=%d", flushed)
            except Exception:
                logger.exception("shutdown_log_signal_flush_failed")
            log_signal_drainer.stop(timeout=5.0)
            # 5B2：artifact uploader 收尾
            try:
                ArtifactUploader.instance().stop(drain=True, timeout=5.0)
            except Exception:
                logger.exception("shutdown_artifact_uploader_stop_failed")
            # ADR-0025 Sprint 2: 停归档调度器 + 磁盘监控（未启动时为安全 no-op）
            try:
                LocalDiskMonitor.instance().stop(timeout=5.0)
                LogArchiver.instance().stop(timeout=5.0)
            except Exception:
                logger.exception("shutdown_log_archiver_stop_failed")
        heartbeat_thread.stop()
        lease_renewer.stop()
        mq_producer.close()
        local_db.close()
        sio_client.disconnect()
        logger.info("agent_shutdown_complete")


if __name__ == "__main__":
    main()
