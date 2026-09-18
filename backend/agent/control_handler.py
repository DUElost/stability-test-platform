"""SocketIO control command handler extracted from ``main`` (#736).

``main`` still creates late-bound runtime objects (coordinator / heartbeat /
scheduler / job_runner_state) and fills ``ControlHandlerDeps`` before
registering the handler. Reload / abort / scan_now logic lives here so it can
be unit-tested without importing the process entrypoint.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import BASE_DIR
from .event_uploader import EventUploader
from .log_archiver import LogArchiver
from .scan_runner import ScanRunner
from .settings import reset_agent_settings_caches
from .unisoc_scan_runner import UnisocScanRunner
from .upload_manager import UploadManager

logger = logging.getLogger(__name__)


@dataclass
class ControlHandlerDeps:
    """Late-bound runtime objects filled by ``main`` after construction order."""

    job_runner_state: Any = None
    coordinator: Any = None
    operation_scheduler: Any = None
    heartbeat_thread: Any = None


def parse_abort_job_ids(payload: Dict[str, Any]) -> List[int]:
    """Parse abort command payload into job ids (#805-2).

    A malformed id must not abort the whole control handler — otherwise other
    jobs in the same batch never get their abort signal. Skip invalid entries
    and keep the valid ones.
    """
    raw_job_ids = payload.get("job_ids")
    candidates: List[Any] = []
    if isinstance(raw_job_ids, list):
        candidates = list(raw_job_ids)
    elif payload.get("job_id") is not None:
        candidates = [payload["job_id"]]
    job_ids: List[int] = []
    for raw in candidates:
        try:
            job_ids.append(int(raw))
        except (TypeError, ValueError):
            logger.warning("control_abort_invalid_job_id raw=%r", raw)
    return job_ids


# Back-compat alias for tests that imported the private name from ``main``.
_parse_abort_job_ids = parse_abort_job_ids


def reload_runtime_env(env_file: Path | None = None) -> bool:
    """Reload the Agent EnvironmentFile for runtime-reconfigurable settings."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.warning("control_reload_config_dotenv_unavailable")
        return False

    path = env_file or (BASE_DIR / ".env")
    if not path.is_file():
        logger.warning("control_reload_config_env_missing path=%s", path)
        return False
    loaded = bool(load_dotenv(path, override=True))
    logger.info("control_reload_config_env_loaded path=%s loaded=%s", path, loaded)
    return loaded


# Back-compat alias.
_reload_runtime_env = reload_runtime_env


def build_control_handler(
    *,
    deps: ControlHandlerDeps,
    mq_producer: Any,
    host_id: str,
    api_url: str,
    agent_secret: str,
    adb_path: str,
    local_db: Any,
    active_jobs_lock: Any,
    active_job_ids: Any,
    ensure_adb_server: Callable[[str], bool],
) -> Callable[[dict], Optional[dict]]:
    """Return the SocketIO control handler closed over startup wiring."""

    def handle_control(data: dict) -> Optional[dict]:
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
            # #805-2：坏 id 不得中断整个 handler（否则同批其它 job 的 abort 丢失）。
            job_ids = parse_abort_job_ids(payload)
            for job_id in job_ids:
                # ADR-0026 Step 5b: signal abort FIRST so _is_aborted()
                # returns True, THEN cancel the permit waiter. If cancel
                # fires first, the waiter sees PermitDenied before the
                # abort flag is set, retries, and re-acquires the permit.
                job_runner_state = deps.job_runner_state
                if job_runner_state is not None:
                    requested = job_runner_state.request_abort(job_id)
                else:
                    requested = False
                deps.coordinator.cancel_waiting_job(job_id)
                logger.info(
                    "control_abort job_id=%s requested=%s",
                    job_id,
                    requested,
                )
        elif command == "archive_now":
            arch = LogArchiver.instance()
            if arch.is_configured():
                threading.Thread(
                    target=lambda: arch.scan_once(grace_seconds=0.0),
                    name="archive-now",
                    daemon=True,
                ).start()
                logger.info(
                    "control_archive_now triggered by backend — scan_once(grace=0) "
                    "clamped to min grace floor",
                )
            else:
                logger.warning("control_archive_now_skipped: archiver not configured")
        elif command == "scan_now":
            plan_run_id = payload.get("plan_run_id")
            is_final = bool(payload.get("is_final", False))
            if not plan_run_id:
                logger.warning("control_scan_now_missing_plan_run_id")
                return {"ok": False, "error": "missing plan_run_id"}

            ScanRunner.enqueue_scan_now(
                int(plan_run_id),
                host_id,
                is_final=is_final,
                device_serials=payload.get("device_serials") or [],
                run_date_stamps=payload.get("run_date_stamps") or [],
            )
            logger.info(
                "control_scan_now_triggered plan_run=%d final=%s serials=%s stamps=%s",
                plan_run_id,
                is_final,
                payload.get("device_serials") or [],
                payload.get("run_date_stamps") or [],
            )
        elif command == "reload_config":
            env_reloaded = reload_runtime_env()
            # ADR-0042 P1：`.env` 重读后必须清 Settings 缓存，否则新值被旧缓存吞掉。
            reset_agent_settings_caches()
            with active_jobs_lock:
                active_count = len(active_job_ids)
            if active_count == 0:
                adb_reconciled = ensure_adb_server(adb_path)
            else:
                # 对齐心跳自动修复语义（heartbeat_thread.py 要求 active_count==0）：
                # 收敛 ADB 会重启目标端口 server、全量重注册 USB，运行中 job 的
                # adb 会话会被打断。活跃期间跳过，留待无 job 窗口或 Agent 重启生效。
                adb_reconciled = False
                logger.warning(
                    "control_reload_config_skip_adb_reconcile active=%d "
                    "— 活跃作业期间跳过 ADB 收敛",
                    active_count,
                )
            ScanRunner.instance().configure(force=True)
            UnisocScanRunner.instance().configure(force=True)
            UploadManager.instance().configure(force=True)
            reloaded_api_url = (os.getenv("API_URL") or api_url).rstrip("/")
            reloaded_agent_secret = os.getenv("AGENT_SECRET") or agent_secret
            EventUploader.instance().configure(
                api_url=reloaded_api_url,
                agent_secret=reloaded_agent_secret,
                host_id=str(host_id),
                force=True,
            )
            EventUploader.instance().start()
            operation_cap = deps.operation_scheduler.reload_from_env()
            # #2086：心跳/协调域的节奏旋钮原先只在构造时取值——reload 打印 done
            # 但运行中的值不变。实例级 re-apply 补上（缓存已在上面清过）。
            deps.heartbeat_thread.reload_from_settings()
            deps.coordinator.reload_from_settings()
            runner_ok = ScanRunner.instance().is_configured()
            uploader_ok = UploadManager.instance().is_configured()
            logger.info(
                "control_reload_config_done env_reloaded=%s adb_reconciled=%s "
                "scan_runner=%s upload_manager=%s "
                "max_concurrent_operations=%d",
                env_reloaded,
                adb_reconciled,
                runner_ok,
                uploader_ok,
                operation_cap,
            )
        elif command == "list_log_signal_dead_letters":
            # #302: RPC 回传最近死信清单（经 SocketIO ack）。
            try:
                limit = int(payload.get("limit") or 100)
            except (TypeError, ValueError):
                limit = 100
            return {
                "dead_letters": local_db.get_log_signal_dead_letters(limit=limit),
            }
        elif command == "replay_log_signal_dead_letter":
            # #302: 重置死信行重新入队（next drain tick 拉取重发）。
            row_id = payload.get("row_id")
            if not isinstance(row_id, int) or row_id <= 0:
                return {"ok": False, "error": "invalid row_id"}
            replayed = local_db.replay_log_signal_dead_letter(row_id)
            logger.info(
                "control_replay_log_signal_dead_letter row_id=%d replayed=%s",
                row_id,
                replayed,
            )
            return {"ok": replayed, "row_id": row_id}
        elif command == "replay_dle_register_dead_letter":
            # #1204: DLE create 意图死信回放——重置 dead_letter/attempts，
            # 下一 drain tick 重新补建（中心升级/漂移窗口过后恢复）。
            event_id = payload.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                return {"ok": False, "error": "invalid event_id"}
            replayed = local_db.replay_dle_register_dead_letter(event_id)
            logger.info(
                "control_replay_dle_register_dead_letter event_id=%s replayed=%s",
                event_id,
                replayed,
            )
            return {"ok": replayed, "event_id": event_id}
        else:
            logger.warning("unknown_control_command: %s", command)
            return {"ok": False, "error": f"unknown command: {command}"}
        # P2-4 / #298: 单向 control（scan_now 等）须回 {"ok": true}，
        # call_agent_control 据此判定已送达；RPC 分支已在上方显式 return。
        return {"ok": True}

    return handle_control
