import logging
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Set

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
    from agent.api_client import complete_run, fetch_pending_jobs
    from agent.artifact_uploader import ArtifactUploader
    from agent.config import BASE_DIR, ensure_dirs
    from agent.heartbeat_thread import HeartbeatThread
    from agent.host_registry import auto_register_host, get_host_info, load_required_host_id
    from agent.job_runner import JobRunnerState, run_task_wrapper
    from agent.lock_manager import LockRenewalManager
    from agent.mq.producer import MQProducer
    from agent.outbox_drainer import OutboxDrainThread
    from agent.registry.local_db import LocalDB
    from agent.registry.script_registry import ScriptRegistry
    from agent.registry.tool_registry import ToolRegistry
    from agent.step_trace_uploader import StepTraceUploader
    from agent.watcher import LogWatcherManager, OutboxDrainer
    from agent.ws_client import AgentWSClient
else:
    from .adb_wrapper import AdbWrapper
    from .api_client import complete_run, fetch_pending_jobs
    from .artifact_uploader import ArtifactUploader
    from .config import BASE_DIR, ensure_dirs
    from .heartbeat_thread import HeartbeatThread
    from .host_registry import auto_register_host, get_host_info, load_required_host_id
    from .job_runner import JobRunnerState, run_task_wrapper
    from .lock_manager import LockRenewalManager
    from .mq.producer import MQProducer
    from .outbox_drainer import OutboxDrainThread
    from .registry.local_db import LocalDB
    from .registry.script_registry import ScriptRegistry
    from .registry.tool_registry import ToolRegistry
    from .step_trace_uploader import StepTraceUploader
    from .watcher import LogWatcherManager, OutboxDrainer
    from .ws_client import AgentWSClient

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# Device Log Watcher feature flag —— 默认 false，保持与治理前行为 100% 一致。
# 置 true 时：main() 会 configure LogWatcherManager；run_task_wrapper 用 JobSession
# 包裹 pipeline 执行，并在 complete payload 中回传 watcher_summary。
STP_WATCHER_ENABLED = os.getenv("STP_WATCHER_ENABLED", "false").lower() == "true"

# 全局活跃 Job 追踪（语义上存的就是 job_instance.id）
# 命名约定：_active_job_ids / _active_jobs_lock
_active_job_ids: Set[int] = set()
_active_device_ids: Set[int] = set()  # per-device concurrency guard
_active_jobs_lock = threading.Lock()
_lock_renewal_stop_event = threading.Event()


# 全局活跃 Job 追踪辅助函数（JobSession 回调与现有主循环共用）
def _register_active_job(jid: int) -> None:
    with _active_jobs_lock:
        _active_job_ids.add(jid)


def _deregister_active_job(jid: int) -> None:
    with _active_jobs_lock:
        _active_job_ids.discard(jid)


def _register_active_device(did: int) -> None:
    with _active_jobs_lock:
        _active_device_ids.add(did)


def _deregister_active_device(did: int) -> None:
    with _active_jobs_lock:
        _active_device_ids.discard(did)


def main() -> None:
    api_url = os.getenv("API_URL", "http://127.0.0.1:8000")
    max_concurrent_tasks = int(os.getenv("MAX_CONCURRENT_TASKS", "2"))

    # 确保运行时目录存在
    ensure_dirs()

    # 获取本机信息（需要在验证 HOST_ID 之前）
    host_info = get_host_info()

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
    ws_client = AgentWSClient(api_url, host_id, agent_secret)
    ws_client.connect()
    # Start background reconnect loop for auto-recovery on disconnect
    ws_client.start_reconnect_loop()

    # 初始化本地 SQLite WAL 缓存
    local_db = LocalDB()
    db_path = str(BASE_DIR / "agent_state.db")
    local_db.initialize(db_path)

    # 初始化工具注册表
    tool_registry = ToolRegistry(local_db, api_url, agent_secret)
    tool_registry.initialize()
    script_registry = ScriptRegistry(local_db, api_url, agent_secret)
    script_registry.initialize()

    # Device Log Watcher 子系统（feature flag 控制，默认关闭）
    log_signal_drainer: Optional[OutboxDrainer] = None
    if STP_WATCHER_ENABLED:
        # 5B1：LogPuller NFS 根目录（空串 = 禁用 puller，仅记元数据）
        nfs_base_dir = os.getenv("STP_WATCHER_NFS_BASE_DIR", "")
        LogWatcherManager.instance().configure(
            adb=adb,
            adb_path=adb_path,          # InotifydSource.Popen 需要 adb 二进制路径
            local_db=local_db,
            ws_client=ws_client,
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
    else:
        logger.info("watcher_subsystem_disabled (STP_WATCHER_ENABLED=false)")

    # Step trace local writer (Redis XADD removed in Phase 4; HTTP upload via StepTraceUploader)
    mq_producer = MQProducer("", host_id, local_db=local_db)

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
        elif command == "tool_update":
            try:
                tool_id = int(payload.get("tool_id", 0))
                version = payload.get("version", "")
            except (TypeError, ValueError):
                tool_id, version = 0, ""
            if tool_id and tool_registry:
                threading.Thread(
                    target=tool_registry.pull_tool_sync,
                    args=(tool_id, version),
                    daemon=True,
                    name=f"tool-pull-{tool_id}",
                ).start()
        elif command == "abort":
            job_id = payload.get("job_id")
            if job_id:
                with _active_jobs_lock:
                    _active_job_ids.discard(int(job_id))
                logger.info("control_abort job_id=%s", job_id)
        else:
            logger.warning("unknown_control_command: %s", command)

    ws_client.set_control_handler(_handle_control)

    # 启动心跳守护线程（独立于任务执行循环）
    heartbeat_thread = HeartbeatThread(
        api_url=api_url,
        host_id=host_id,
        adb_path=adb_path,
        mount_points=mount_points,
        host_info=host_info,
        poll_interval=poll_interval,
        ws_client=ws_client,
        catalog_versions=lambda: {
            "tool_catalog_version": tool_registry.version,
            "script_catalog_version": script_registry.version,
        },
        on_scripts_outdated=script_registry.initialize,
    )
    heartbeat_thread.start()

    # 启动锁续期管理器
    lock_manager = LockRenewalManager(
        api_url,
        active_jobs_lock=_active_jobs_lock,
        active_job_ids=_active_job_ids,
        lock_renewal_stop_event=_lock_renewal_stop_event,
    )
    lock_manager.start()

    # 启动终态 Outbox Drain 线程
    outbox_drain = OutboxDrainThread(api_url, local_db, interval=15.0)
    outbox_drain.start()

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
        watcher_enabled=STP_WATCHER_ENABLED,
        lock_register=_register_active_job,
        lock_deregister=_deregister_active_job,
        device_id_register=_register_active_device,
        device_id_deregister=_deregister_active_device,
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

                if available_slots > 0:
                    jobs = fetch_pending_jobs(api_url, host_id)
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

                    for job in jobs:
                        device_id = job.get("device_id")

                        with _active_jobs_lock:
                            if device_id and device_id in _active_device_ids:
                                logger.debug(
                                    "skip_device_busy job=%d device=%d",
                                    job["id"], device_id,
                                )
                                continue
                            _active_job_ids.add(job["id"])
                            if device_id:
                                _active_device_ids.add(device_id)

                        try:
                            executor.submit(
                                run_task_wrapper,
                                job,
                                adb,
                                api_url,
                                host_id,
                                ws_client,
                                job_runner_state,
                                mq_producer,
                                tool_registry,
                                script_registry,
                                local_db,
                            )
                        except Exception:
                            logger.exception("submit_failed job=%d device=%s", job["id"], device_id)
                            with _active_jobs_lock:
                                _active_job_ids.discard(job["id"])
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
        heartbeat_thread.stop()
        lock_manager.stop()
        mq_producer.close()
        local_db.close()
        ws_client.disconnect()
        logger.info("agent_shutdown_complete")


if __name__ == "__main__":
    main()
