import logging
import os
import signal
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import requests

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
    from agent import device_discovery
    from agent.adb_wrapper import AdbWrapper
    from agent.config import BASE_DIR, ensure_dirs, get_run_log_dir
    from agent.heartbeat import send_heartbeat
    from agent.mq.producer import MQProducer
    from agent.registry.local_db import LocalDB
    from agent.registry.tool_registry import ToolRegistry
    from agent.step_trace_uploader import StepTraceUploader
    from agent.ws_client import AgentWSClient
else:
    from . import device_discovery
    from .adb_wrapper import AdbWrapper
    from .config import BASE_DIR, ensure_dirs, get_run_log_dir
    from .heartbeat import send_heartbeat
    from .mq.producer import MQProducer
    from .registry.local_db import LocalDB
    from .registry.tool_registry import ToolRegistry
    from .step_trace_uploader import StepTraceUploader
    from .ws_client import AgentWSClient

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

POST_RETRIES = int(os.getenv("AGENT_POST_RETRIES", "3"))
POST_RETRY_BASE_DELAY = float(os.getenv("AGENT_POST_RETRY_BASE_DELAY", "1"))
LOCK_RENEWAL_INTERVAL = int(
    os.getenv("AGENT_LOCK_RENEWAL_INTERVAL", "60")
)  # 默认60秒续期一次
_AGENT_SECRET = os.getenv("AGENT_SECRET", "")

# 全局锁续期管理
_active_run_ids: Set[int] = set()
_active_device_ids: Set[int] = set()  # per-device concurrency guard
_active_runs_lock = threading.Lock()
_lock_renewal_stop_event = threading.Event()


class LockRenewalManager:
    """设备锁续期管理器 - 后台线程定期续期活跃任务的设备锁"""

    def __init__(self, api_url: str):
        self.api_url = api_url
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """启动续期线程"""
        if self._thread is not None and self._thread.is_alive():
            return

        _lock_renewal_stop_event.clear()
        self._thread = threading.Thread(target=self._renewal_loop, daemon=True)
        self._thread.start()
        logger.info("lock_renewal_thread_started")

    def stop(self) -> None:
        """停止续期线程"""
        _lock_renewal_stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("lock_renewal_thread_stopped")

    def _renewal_loop(self) -> None:
        """续期主循环"""
        while not _lock_renewal_stop_event.is_set():
            # 复制当前活跃任务列表避免并发修改
            with _active_runs_lock:
                current_runs = list(_active_run_ids)

            for run_id in current_runs:
                if _lock_renewal_stop_event.is_set():
                    break

                try:
                    self._extend_lock(run_id)
                except Exception as e:
                    logger.warning(f"lock_renewal_failed for run {run_id}: {e}")

            # 等待下一次续期周期
            _lock_renewal_stop_event.wait(LOCK_RENEWAL_INTERVAL)

    def _extend_lock(self, run_id: int) -> None:
        """向服务器请求延长设备锁"""
        url = f"{self.api_url}/api/v1/agent/jobs/{run_id}/extend_lock"
        headers = {"X-Agent-Secret": _AGENT_SECRET} if _AGENT_SECRET else {}

        for attempt in range(1, POST_RETRIES + 1):
            try:
                resp = requests.post(url, headers=headers, timeout=10)
                resp.raise_for_status()
                result = resp.json()
                logger.debug(
                    f"lock_extended for run {run_id}, expires_at={result.get('expires_at')}"
                )
                return
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 409:
                    # 锁丢失，从活跃列表移除
                    logger.error(
                        f"lock_lost for run {run_id}, removing from active runs"
                    )
                    with _active_runs_lock:
                        _active_run_ids.discard(run_id)
                    raise RuntimeError(f"Lock lost for run {run_id}")
                logger.warning(
                    f"lock_extension_attempt_{attempt}_failed for run {run_id}: {e}"
                )
            except requests.RequestException as e:
                logger.warning(
                    f"lock_extension_attempt_{attempt}_failed for run {run_id}: {e}"
                )

            if attempt < POST_RETRIES:
                delay = POST_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                time.sleep(delay)

        raise RuntimeError(
            f"Failed to extend lock for run {run_id} after {POST_RETRIES} attempts"
        )


class OutboxDrainThread:
    """Background thread that retries un-acked terminal-state payloads."""

    def __init__(self, api_url: str, local_db, interval: float = 15.0):
        self._api_url = api_url
        self._local_db = local_db
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="outbox-drain",
        )
        self._thread.start()
        logger.info("outbox_drain_thread_started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("outbox_drain_thread_stopped")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(self._interval)
            if self._stop_event.is_set():
                break
            try:
                self._drain_once()
            except Exception:
                logger.exception("outbox_drain_error")

    def drain_sync(self) -> int:
        """Blocking drain for shutdown — returns number of successfully sent items."""
        return self._drain_once()

    _TERMINAL_STATUSES = {"COMPLETED", "FAILED", "ABORTED", "UNKNOWN"}

    def _drain_once(self) -> int:
        pending = self._local_db.get_pending_terminals(limit=20)
        if not pending:
            self._local_db.prune_acked_terminals()
            return 0

        sent = 0
        headers = {"X-Agent-Secret": _AGENT_SECRET} if _AGENT_SECRET else {}
        for entry in pending:
            job_id = entry["job_id"]
            payload = entry["payload"]
            try:
                resp = requests.post(
                    f"{self._api_url}/api/v1/agent/jobs/{job_id}/complete",
                    json=payload,
                    headers=headers,
                    timeout=15,
                )
                resp.raise_for_status()
                self._local_db.ack_terminal(job_id)
                sent += 1
                logger.info("outbox_drain_acked job=%d", job_id)
            except requests.HTTPError as e:
                status_code = e.response.status_code if e.response else None
                if status_code == 409:
                    # Parse server response to distinguish "already terminal"
                    # from "genuine conflict on non-terminal state"
                    current = self._parse_current_status(e.response)
                    if current and current in self._TERMINAL_STATUSES:
                        self._local_db.ack_terminal(job_id)
                        sent += 1
                        logger.info(
                            "outbox_drain_conflict_ack job=%d current=%s (job is terminal)",
                            job_id, current,
                        )
                    else:
                        self._local_db.bump_terminal_attempt(job_id, str(e))
                        logger.warning(
                            "outbox_drain_conflict_retry job=%d current=%s",
                            job_id, current,
                        )
                elif status_code == 404:
                    # Job doesn't exist on server — nothing to do, ACK to stop retrying
                    self._local_db.ack_terminal(job_id)
                    logger.warning("outbox_drain_job_gone job=%d", job_id)
                else:
                    self._local_db.bump_terminal_attempt(job_id, str(e))
            except Exception as e:
                self._local_db.bump_terminal_attempt(job_id, str(e))
                logger.warning("outbox_drain_retry job=%d error=%s", job_id, e)

        self._local_db.prune_acked_terminals()
        return sent

    @staticmethod
    def _parse_current_status(response) -> Optional[str]:
        """Extract current_status from a 409 response body."""
        try:
            body = response.json()
            detail = body.get("detail", {})
            if isinstance(detail, dict):
                return detail.get("current_status")
            # Fallback: wrapped ApiResponse format
            err = body.get("error", {})
            if isinstance(err, dict):
                return err.get("current_status")
        except Exception:
            pass
        return None


RUN_TERMINAL_STATUS_MAP = {
    "COMPLETED": "FINISHED",
    "FINISHED": "FINISHED",
    "FAILED": "FAILED",
    "CANCELED": "CANCELED",
    "CANCELLED": "CANCELED",
    "ABORTED": "CANCELED",
}


class HeartbeatThread:
    """Daemon thread: device discovery + heartbeat every poll_interval seconds.

    Channel design (Phase 0 state-closure):
      - HTTP POST /api/v1/heartbeat is the SOLE authority for persisting
        host and device state to the DB (last_heartbeat, device.last_seen,
        battery, temperature, etc.).
      - WS heartbeat is supplementary: pushes real-time device metrics to
        dashboard subscribers for instant UI refresh.  The server-side WS
        handler does NOT write to the DB.
    """

    def __init__(
        self,
        api_url: str,
        host_id: str,
        adb_path: str,
        mount_points: List[str],
        host_info: Dict[str, Any],
        poll_interval: float,
        ws_client: Optional[AgentWSClient] = None,
    ):
        self._api_url = api_url
        self._host_id = host_id
        self._adb_path = adb_path
        self._mount_points = mount_points
        self._host_info = host_info
        self._poll_interval = poll_interval
        self._ws_client = ws_client
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest_devices: List[Dict[str, Any]] = []
        self._devices_lock = threading.Lock()

    @property
    def latest_devices(self) -> List[Dict[str, Any]]:
        """Return the most recent device list (thread-safe)."""
        with self._devices_lock:
            return list(self._latest_devices)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="heartbeat"
        )
        self._thread.start()
        logger.info("heartbeat_thread_started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("heartbeat_thread_stopped")

    def _loop(self) -> None:
        # Send first heartbeat immediately on startup
        self._tick()

        while not self._stop_event.is_set():
            self._stop_event.wait(self._poll_interval)
            if self._stop_event.is_set():
                break
            self._tick()

    def _tick(self) -> None:
        """Single heartbeat cycle: discover → HTTP POST (authoritative) → WS push (display-only)."""
        devices_list = []
        try:
            discovered = device_discovery.discover_devices(self._adb_path)
            for dev in discovered:
                info = device_discovery.collect_device_info(
                    self._adb_path, dev["serial"]
                )
                device_data = {
                    "serial": dev["serial"],
                    "model": dev.get("model"),
                    "state": dev["adb_state"],
                    "adb_state": info.get("adb_state", dev.get("adb_state", "unknown")),
                    "adb_connected": info.get("adb_connected", False),
                    "battery_level": info.get("battery_level"),
                    "temperature": info.get("temperature"),
                    "network_latency": info.get("network_latency"),
                }
                devices_list.append(device_data)
                logger.info(
                    f"device_collected: {dev['serial']}, "
                    f"adb_connected={info.get('adb_connected')}, "
                    f"network_latency={info.get('network_latency')}, "
                    f"battery={info.get('battery_level')}, temp={info.get('temperature')}"
                )
            logger.debug(f"discovered_{len(devices_list)}_devices")
        except Exception as e:
            logger.warning(f"device_discovery_failed: {e}")

        # Update latest device cache
        with self._devices_lock:
            self._latest_devices = devices_list

        # HTTP heartbeat is the authority for host + device state in the DB.
        # Always send HTTP so device.last_seen, battery, temperature etc. are updated.
        send_heartbeat(
            self._api_url,
            self._host_id,
            self._mount_points,
            host_info=self._host_info,
            devices=devices_list,
        )

        # WS heartbeat is supplementary: pushes real-time device metrics to
        # dashboard subscribers for instant UI refresh (no DB write on server).
        if self._ws_client and self._ws_client.connected:
            try:
                from .heartbeat import check_mounts
                from .system_monitor import collect_system_stats

                stats = collect_system_stats()
                stats["devices"] = devices_list
                stats["mount_status"] = check_mounts(self._mount_points)
                self._ws_client.send_heartbeat(stats)
                logger.debug("heartbeat_ws_push_sent")
            except Exception as e:
                logger.debug("heartbeat_ws_push_failed: %s", e)


def fetch_pending_runs(api_url: str, host_id: str) -> List[Dict[str, Any]]:
    headers = {"X-Agent-Secret": _AGENT_SECRET} if _AGENT_SECRET else {}
    resp = requests.get(
        f"{api_url}/api/v1/agent/jobs/pending",
        params={"host_id": host_id, "limit": 10},
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    # Backend wraps response in ApiResponse[T] = {"data": [...], "error": null}
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"] or []
    return payload


def _post_with_retry(
    url: str, payload: Dict[str, Any], context: str, timeout: int = 10
) -> None:
    headers = {"X-Agent-Secret": _AGENT_SECRET} if _AGENT_SECRET else {}
    last_error: Optional[Exception] = None
    for attempt in range(1, POST_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= POST_RETRIES:
                logger.warning(
                    "agent_post_failed",
                    extra={"context": context, "attempts": attempt, "error": str(exc)},
                )
                raise

            delay = POST_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "agent_post_retry",
                extra={
                    "context": context,
                    "attempt": attempt,
                    "next_delay_seconds": delay,
                    "error": str(exc),
                },
            )
            time.sleep(delay)
    if last_error:
        raise last_error


def update_run(api_url: str, run_id: int, payload: Dict[str, Any]) -> None:
    _post_with_retry(
        f"{api_url}/api/v1/agent/jobs/{run_id}/heartbeat",
        payload,
        context=f"run_heartbeat:{run_id}",
    )


def _build_complete_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build the normalized payload for the /complete endpoint."""
    raw_status = str(payload.get("status", "FAILED")).upper()
    normalized_status = RUN_TERMINAL_STATUS_MAP.get(raw_status, "FAILED")
    complete_payload: Dict[str, Any] = {
        "update": {
            "status": normalized_status,
            "exit_code": payload.get("exit_code"),
            "error_code": payload.get("error_code"),
            "error_message": payload.get("error_message"),
            "log_summary": payload.get("log_summary"),
        }
    }
    artifact = payload.get("artifact")
    if isinstance(artifact, dict):
        complete_payload["artifact"] = artifact
    return complete_payload


def complete_run(
    api_url: str,
    run_id: int,
    payload: Dict[str, Any],
    local_db=None,
) -> None:
    """Report job terminal state. Writes to local outbox first for durability."""
    complete_payload = _build_complete_payload(payload)

    # Outbox-first: persist locally before attempting HTTP
    if local_db is not None:
        try:
            local_db.enqueue_terminal(run_id, complete_payload)
        except Exception as e:
            logger.warning("outbox_enqueue_failed job=%d: %s", run_id, e)

    try:
        _post_with_retry(
            f"{api_url}/api/v1/agent/jobs/{run_id}/complete",
            complete_payload,
            context=f"run_complete:{run_id}",
        )
        # ACK on success
        if local_db is not None:
            try:
                local_db.ack_terminal(run_id)
            except Exception:
                pass
    except Exception:
        # HTTP failed after retries — outbox drain thread will pick it up
        if local_db is not None:
            logger.warning(
                "complete_run_deferred_to_outbox job=%d", run_id,
            )
        else:
            raise


def get_host_info() -> Dict[str, Any]:
    """获取本机信息"""
    try:
        # 获取本机 IP（连接到服务器的 IP）
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(("8.8.8.8", 80))  # Google DNS，仅用于获取本地 IP
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "127.0.0.1"

    return {
        "ip": ip,
    }


def _load_required_host_id() -> Optional[str]:
    raw_value = os.getenv("HOST_ID", "").strip()
    if not raw_value:
        raise ValueError("HOST_ID is required and cannot be empty")

    # 支持 AUTO 模式：自动注册主机
    if raw_value.upper() == "AUTO":
        return None  # 表示需要自动注册

    return raw_value


def _auto_register_host(api_url: str, host_info: Dict) -> str:
    """通过心跳端点自动注册主机到后端

    心跳端点 /api/v1/heartbeat 已内置主机自动创建逻辑：
    当 host_id 对应的主机不存在时，会根据 IP 查找或自动创建，
    并返回实际分配的 host_id。
    """
    # 使用 host_id=0 触发心跳端点的自动创建逻辑
    # (PostgreSQL 自增 ID 从 1 开始，所以 id=0 不存在 → 触发 auto-create)
    heartbeat_url = f"{api_url.rstrip('/')}/api/v1/heartbeat"

    payload = {
        "host_id": 0,
        "status": "ONLINE",
        "mount_status": {},
        "extra": {},
        "host": host_info,  # 包含 ip，供心跳端点按 IP 查找/创建
        "devices": [],
    }

    agent_secret = os.getenv("AGENT_SECRET", "")
    headers = {}
    if agent_secret:
        headers["x-agent-secret"] = agent_secret

    try:
        logger.info(
            f"auto_register_host: url={heartbeat_url}, ip={host_info.get('ip')}"
        )
        response = requests.post(
            heartbeat_url, json=payload, headers=headers, timeout=10
        )
        response.raise_for_status()
        data = response.json()
        host_id = data.get("host_id")
        if not host_id:
            raise ValueError(f"Heartbeat response missing host_id: {data}")
        logger.info(
            f"auto_register_host_success: host_id={host_id}, ip={host_info.get('ip')}"
        )
        return host_id
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response else None
        body = exc.response.text[:500] if exc.response else None
        logger.error(
            f"auto_register_host_failed: status={status_code}, body={body}, error={exc}"
        )
        raise
    except Exception as exc:
        logger.error(f"auto_register_host_failed: error={exc}")
        raise


def _execute_pipeline_run(
    pipeline_def,
    run_id,
    device_serial,
    adb,
    api_url,
    host_id,
    ws_client=None,
    mq_producer=None,
    tool_registry=None,
    local_db=None,
):
    """Execute a task using the pipeline engine instead of the legacy executor."""
    from .pipeline_engine import PipelineEngine, StepResult

    # Get log directory for this run
    log_dir = get_run_log_dir(run_id)
    os.makedirs(log_dir, exist_ok=True)

    # Use existing WS client if provided, otherwise create a new one
    own_ws = False
    if ws_client is None:
        agent_secret = os.getenv("AGENT_SECRET", "")
        ws_client = AgentWSClient(api_url, host_id, agent_secret)
        ws_client.connect()  # Best-effort; falls back to HTTP if fails
        own_ws = True

    # HTTP fallback for step status updates
    agent_secret = os.getenv("AGENT_SECRET", "")

    def http_step_fallback(rid, sid, status, **kwargs):
        import requests

        url = f"{api_url}/api/v1/agent/jobs/{rid}/steps/{sid}/status"
        payload = {"status": status}
        for k in ("started_at", "finished_at", "exit_code", "error_message"):
            if k in kwargs and kwargs[k] is not None:
                val = kwargs[k]
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                payload[k] = val
        headers = {}
        if agent_secret:
            headers["X-Agent-Secret"] = agent_secret
        try:
            requests.post(url, json=payload, headers=headers, timeout=10)
        except Exception as e:
            logger.warning(f"HTTP step status fallback failed: {e}")

    # Abort callback: checks if LockRenewalManager removed this run (409 received)
    def _check_aborted():
        with _active_runs_lock:
            return run_id not in _active_run_ids

    engine = PipelineEngine(
        adb=adb,
        serial=device_serial,
        run_id=run_id,
        log_dir=log_dir,
        ws_client=ws_client,
        http_fallback=http_step_fallback,
        mq_producer=mq_producer,
        tool_registry=tool_registry,
        local_db=local_db,
        api_url=api_url,
        agent_secret=agent_secret,
        is_aborted=_check_aborted,
    )

    try:
        result = engine.execute(pipeline_def)
    finally:
        if own_ws:
            ws_client.disconnect()

    # Map lifecycle termination_reason to terminal status
    status = "FINISHED" if result.success else "FAILED"
    if not result.success and hasattr(result, "metadata") and isinstance(result.metadata, dict):
        reason = result.metadata.get("termination_reason", "")
        if reason == "abort":
            status = "CANCELED"

    return {
        "status": status,
        "exit_code": result.exit_code,
        "error_code": None,
        "error_message": result.error_message,
        "log_summary": None,
        "artifact": result.artifact,
    }


def _run_task_wrapper(
    run, adb, api_url, host_id, ws_client, mq_producer=None, tool_registry=None, local_db=None
):
    """Wrapper to run a single task in a thread and report completion."""
    run_id = run["id"]
    task_id = run.get("task_id")
    device_id = run.get("device_id")
    device_serial = run.get("device_serial", "")
    pipeline_def = run.get("pipeline_def")

    logger.info(
        "run_start run_id=%d task_id=%s device_id=%s device_serial=%s",
        run_id, task_id, device_id, device_serial,
    )

    # Server already transitioned job to RUNNING during claim (get_pending_jobs).
    # Send heartbeat as confirmation (idempotent, won't fail on already-RUNNING).
    try:
        update_run(
            api_url,
            run_id,
            {"status": "RUNNING", "started_at": datetime.utcnow().isoformat()},
        )
    except Exception as e:
        logger.warning(f"Heartbeat confirmation for run {run_id} failed (non-fatal): {e}")

    # run_id and device_id are already registered in _active_run_ids / _active_device_ids
    # by the main loop before submitting to the thread pool. The finally block below
    # MUST execute on every exit path to release the slot.
    try:
        # Validate pipeline_def: must be a dict with either 'stages' or 'lifecycle' key
        if not (pipeline_def and isinstance(pipeline_def, dict)):
            complete_run(
                api_url, run_id,
                {"status": "FAILED", "exit_code": 1,
                 "error_code": "PIPELINE_REQUIRED",
                 "error_message": "pipeline_def is required"},
                local_db=local_db,
            )
            return

        is_lifecycle = isinstance(pipeline_def.get("lifecycle"), dict)
        is_stages = isinstance(pipeline_def.get("stages"), dict)

        if not is_lifecycle and not is_stages:
            complete_run(
                api_url, run_id,
                {"status": "FAILED", "exit_code": 1,
                 "error_code": "PIPELINE_REQUIRED",
                 "error_message": "pipeline_def must contain 'stages' or 'lifecycle'"},
                local_db=local_db,
            )
            return

        if is_stages and not is_lifecycle:
            stages = pipeline_def.get("stages", {})
            if not any(isinstance(stages.get(k), list) and len(stages.get(k) or []) > 0 for k in ("prepare", "execute", "post_process")):
                complete_run(
                    api_url, run_id,
                    {"status": "FAILED", "exit_code": 1,
                     "error_code": "PIPELINE_REQUIRED",
                     "error_message": "pipeline_def.stages must contain at least one step"},
                    local_db=local_db,
                )
                return

        result = _execute_pipeline_run(
            pipeline_def,
            run_id,
            device_serial,
            adb,
            api_url,
            host_id=host_id,
            ws_client=ws_client,
            mq_producer=mq_producer,
            tool_registry=tool_registry,
            local_db=local_db,
        )

        complete_run(
            api_url, run_id,
            {"status": result["status"], "exit_code": result["exit_code"],
             "error_code": result.get("error_code"),
             "error_message": result.get("error_message"),
             "log_summary": result.get("log_summary"),
             "artifact": result.get("artifact")},
            local_db=local_db,
        )
        logger.info(
            "run_complete", extra={"run_id": run_id, "status": result["status"]}
        )
    except Exception as e:
        logger.exception("run_failed job=%d: %s", run_id, e)
        # Outbox guarantees delivery even if this call fails
        complete_run(
            api_url, run_id,
            {"status": "FAILED", "exit_code": 1,
             "error_code": "AGENT_ERROR", "error_message": str(e)},
            local_db=local_db,
        )
    finally:
        with _active_runs_lock:
            _active_run_ids.discard(run_id)
            if device_id:
                _active_device_ids.discard(device_id)


def main() -> None:
    api_url = os.getenv("API_URL", "http://127.0.0.1:8000")
    max_concurrent_tasks = int(os.getenv("MAX_CONCURRENT_TASKS", "2"))

    # 确保运行时目录存在
    ensure_dirs()

    # 获取本机信息（需要在验证 HOST_ID 之前）
    host_info = get_host_info()

    # 加载 HOST_ID，支持自动注册
    try:
        host_id = _load_required_host_id()
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
                host_id = _auto_register_host(api_url, host_info)
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
                with _active_runs_lock:
                    _active_run_ids.discard(int(job_id))
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
    )
    heartbeat_thread.start()

    # 启动锁续期管理器
    lock_manager = LockRenewalManager(api_url)
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
                with _active_runs_lock:
                    active_count = len(_active_run_ids)

                available_slots = max(0, max_concurrent_tasks - active_count)

                if available_slots > 0:
                    runs = fetch_pending_runs(api_url, host_id)
                    runs = runs[:available_slots]

                    if runs:
                        logger.info(
                            "pending_runs_fetched host_id=%s count=%d slots=%d run_ids=%s",
                            host_id, len(runs), available_slots,
                            [r.get("id") for r in runs],
                        )
                    else:
                        logger.debug(
                            "no_pending_runs host_id=%s active=%d slots=%d",
                            host_id, active_count, available_slots,
                        )

                    for run in runs:
                        device_id = run.get("device_id")

                        with _active_runs_lock:
                            if device_id and device_id in _active_device_ids:
                                logger.debug(
                                    "skip_device_busy run=%d device=%d",
                                    run["id"], device_id,
                                )
                                continue
                            _active_run_ids.add(run["id"])
                            if device_id:
                                _active_device_ids.add(device_id)

                        try:
                            executor.submit(
                                _run_task_wrapper,
                                run,
                                adb,
                                api_url,
                                host_id,
                                ws_client,
                                mq_producer,
                                tool_registry,
                                local_db,
                            )
                        except Exception:
                            logger.exception("submit_failed run=%d device=%s", run["id"], device_id)
                            with _active_runs_lock:
                                _active_run_ids.discard(run["id"])
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
        heartbeat_thread.stop()
        lock_manager.stop()
        mq_producer.close()
        local_db.close()
        ws_client.disconnect()
        logger.info("agent_shutdown_complete")


if __name__ == "__main__":
    main()
