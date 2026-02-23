import logging
import os
import socket
import sys
import threading
import time
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
    from agent.adb_wrapper import AdbWrapper
    from agent.heartbeat import send_heartbeat
    from agent.task_executor import TaskExecutor, ExecutionContext
    from agent import device_discovery
    from agent.config import ensure_dirs
    from agent.ws_client import AgentWSClient
else:
    from .adb_wrapper import AdbWrapper
    from .heartbeat import send_heartbeat
    from .task_executor import TaskExecutor, ExecutionContext
    from . import device_discovery
    from .config import ensure_dirs
    from .ws_client import AgentWSClient

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

POST_RETRIES = int(os.getenv("AGENT_POST_RETRIES", "3"))
POST_RETRY_BASE_DELAY = float(os.getenv("AGENT_POST_RETRY_BASE_DELAY", "1"))
LOCK_RENEWAL_INTERVAL = int(os.getenv("AGENT_LOCK_RENEWAL_INTERVAL", "60"))  # 默认60秒续期一次

# 全局锁续期管理
_active_run_ids: Set[int] = set()
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
        url = f"{self.api_url}/api/v1/agent/runs/{run_id}/extend_lock"

        for attempt in range(1, POST_RETRIES + 1):
            try:
                resp = requests.post(url, timeout=10)
                resp.raise_for_status()
                result = resp.json()
                logger.debug(
                    f"lock_extended for run {run_id}, expires_at={result.get('expires_at')}"
                )
                return
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 409:
                    # 锁丢失，从活跃列表移除
                    logger.error(f"lock_lost for run {run_id}, removing from active runs")
                    _active_run_ids.discard(run_id)
                    raise RuntimeError(f"Lock lost for run {run_id}")
                logger.warning(f"lock_extension_attempt_{attempt}_failed for run {run_id}: {e}")
            except requests.RequestException as e:
                logger.warning(f"lock_extension_attempt_{attempt}_failed for run {run_id}: {e}")

            if attempt < POST_RETRIES:
                delay = POST_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                time.sleep(delay)

        raise RuntimeError(f"Failed to extend lock for run {run_id} after {POST_RETRIES} attempts")


RUN_TERMINAL_STATUS_MAP = {
    "COMPLETED": "FINISHED",
    "FINISHED": "FINISHED",
    "FAILED": "FAILED",
    "CANCELED": "CANCELED",
    "CANCELLED": "CANCELED",
}


class HeartbeatThread:
    """Daemon thread that sends host heartbeat independently of the task execution loop.

    Runs device discovery + system monitor + heartbeat POST every poll_interval seconds.
    If a WebSocket client is provided and connected, sends heartbeat via WS;
    otherwise falls back to HTTP POST.
    """

    def __init__(
        self,
        api_url: str,
        host_id: int,
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
        self._thread = threading.Thread(target=self._loop, daemon=True, name="heartbeat")
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
        """Single heartbeat cycle: discover devices, collect stats, send heartbeat."""
        devices_list = []
        try:
            discovered = device_discovery.discover_devices(self._adb_path)
            for dev in discovered:
                info = device_discovery.collect_device_info(self._adb_path, dev["serial"])
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

        # Try WS heartbeat first, fall back to HTTP
        if self._ws_client and self._ws_client.connected:
            try:
                from .system_monitor import collect_system_stats
                from .heartbeat import check_mounts
                stats = collect_system_stats()
                stats["devices"] = devices_list
                stats["mount_status"] = check_mounts(self._mount_points)
                self._ws_client.send_heartbeat(stats)
                logger.debug("heartbeat_sent_via_ws")
                return
            except Exception as e:
                logger.warning(f"ws_heartbeat_failed, falling back to HTTP: {e}")

        # HTTP fallback
        send_heartbeat(
            self._api_url, self._host_id, self._mount_points,
            host_info=self._host_info, devices=devices_list,
        )


def fetch_pending_runs(api_url: str, host_id: int) -> List[Dict[str, Any]]:
    resp = requests.get(
        f"{api_url}/api/v1/agent/runs/pending",
        params={"host_id": host_id, "limit": 10},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _post_with_retry(url: str, payload: Dict[str, Any], context: str, timeout: int = 10) -> None:
    last_error: Optional[Exception] = None
    for attempt in range(1, POST_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
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
        f"{api_url}/api/v1/agent/runs/{run_id}/heartbeat",
        payload,
        context=f"run_heartbeat:{run_id}",
    )


def complete_run(api_url: str, run_id: int, payload: Dict[str, Any]) -> None:
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

    _post_with_retry(
        f"{api_url}/api/v1/agent/runs/{run_id}/complete",
        complete_payload,
        context=f"run_complete:{run_id}",
    )


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


def _load_required_host_id() -> int:
    raw_value = os.getenv("HOST_ID", "").strip()
    if not raw_value:
        raise ValueError("HOST_ID is required and cannot be empty")

    # 支持 AUTO 模式：自动注册主机
    if raw_value.upper() == "AUTO":
        return None  # 表示需要自动注册

    try:
        host_id = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"HOST_ID must be an integer, got: {raw_value}") from exc
    if host_id <= 0:
        raise ValueError(f"HOST_ID must be > 0, got: {host_id}")
    return host_id


def _auto_register_host(api_url: str, host_info: Dict) -> int:
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
        logger.info(f"auto_register_host: url={heartbeat_url}, ip={host_info.get('ip')}")
        response = requests.post(heartbeat_url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        host_id = data.get("host_id")
        if not host_id:
            raise ValueError(f"Heartbeat response missing host_id: {data}")
        logger.info(f"auto_register_host_success: host_id={host_id}, ip={host_info.get('ip')}")
        return host_id
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response else None
        body = exc.response.text[:500] if exc.response else None
        logger.error(f"auto_register_host_failed: status={status_code}, body={body}, error={exc}")
        raise
    except Exception as exc:
        logger.error(f"auto_register_host_failed: error={exc}")
        raise


def _execute_run_with_lock_renewal(
    task_type: str,
    task_params: Dict[str, Any],
    executor: TaskExecutor,
    context: ExecutionContext,
) -> Any:
    """执行任务并管理锁续期"""
    global _active_run_ids

    _active_run_ids.add(context.run_id)

    try:
        result = executor.execute_task(task_type, task_params, context)
        return result
    finally:
        _active_run_ids.discard(context.run_id)


def _execute_pipeline_run(pipeline_def, run_id, device_serial, adb, api_url, host_id, ws_client=None, tool_snapshot=None):
    """Execute a task using the pipeline engine instead of the legacy executor."""
    from backend.agent.pipeline_engine import PipelineEngine, StepResult

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
        url = f"{api_url}/api/v1/agent/runs/{rid}/steps/{sid}/status"
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

    engine = PipelineEngine(
        adb=adb,
        serial=device_serial,
        run_id=run_id,
        ws_client=ws_client,
        http_fallback=http_step_fallback,
    )

    # Inject tool_snapshot into engine's shared context for tool:<id> resolution
    if tool_snapshot and isinstance(tool_snapshot, dict):
        tool_id = tool_snapshot.get("id")
        if tool_id:
            engine._shared["_tool_snapshots"] = {str(tool_id): tool_snapshot}

    # Inject DB step IDs into pipeline_def for status reporting
    # Fetch step IDs from the backend
    try:
        import requests
        headers = {}
        if agent_secret:
            headers["X-Agent-Secret"] = agent_secret
        resp = requests.get(
            f"{api_url}/api/v1/runs/{run_id}/steps",
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            db_steps = resp.json()
            # Map (phase, step_order) -> db_step_id
            step_id_map = {}
            for s in db_steps:
                key = (s["phase"], s["step_order"])
                step_id_map[key] = s["id"]
            # Inject into pipeline_def
            for phase in pipeline_def.get("phases", []):
                phase_name = phase.get("name", "")
                for idx, step in enumerate(phase.get("steps", [])):
                    step["_db_step_id"] = step_id_map.get((phase_name, idx), 0)
    except Exception as e:
        logger.warning(f"Failed to fetch RunStep IDs: {e}")

    try:
        result = engine.execute(pipeline_def)
    finally:
        if own_ws:
            ws_client.disconnect()

    # Map PipelineEngine result to TaskResult-compatible dict
    from backend.agent.task_executor import TaskResult
    return TaskResult(
        status="FINISHED" if result.success else "FAILED",
        exit_code=result.exit_code,
        error_message=result.error_message,
    )


def main() -> None:
    api_url = os.getenv("API_URL", "http://127.0.0.1:8000")

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
            try:
                host_id = _auto_register_host(api_url, host_info)
                logger.info("auto_register_host_completed", extra={"host_id": host_id})
            except Exception as reg_exc:
                logger.error("auto_register_host_failed", extra={"error": str(reg_exc)})
                logger.error("Set HOST_ID manually or enable AUTO_REGISTER_HOST=true")
                raise SystemExit(2)
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

    # 如果 host_id 为 None（自动注册模式），再次尝试
    if host_id is None:
        try:
            host_id = _auto_register_host(api_url, host_info)
        except Exception as exc:
            logger.error(f"auto_register_failed: {exc}")
            raise SystemExit(2)
    poll_interval = float(os.getenv("POLL_INTERVAL", "5"))
    mount_points = [p for p in os.getenv("MOUNT_POINTS", "").split(",") if p]
    adb_path = os.getenv("ADB_PATH", "adb")

    logger.info("agent_started", extra={"host_id": host_id, "api_url": api_url, "ip": host_info["ip"]})

    adb = AdbWrapper(adb_path=adb_path)
    executor = TaskExecutor(adb)

    # 启动 WebSocket 客户端（best-effort，失败时降级到 HTTP）
    agent_secret = os.getenv("AGENT_SECRET", "")
    ws_client = AgentWSClient(api_url, host_id, agent_secret)
    ws_client.connect()
    # Start background reconnect loop for auto-recovery on disconnect
    ws_client.start_reconnect_loop()

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

    try:
        while True:
            try:
                runs = fetch_pending_runs(api_url, host_id)
                logger.info("pending_runs_fetched", extra={"host_id": host_id, "count": len(runs)})
                for run in runs:
                    run_id = run["id"]
                    logger.info(
                        "run_start",
                        extra={"run_id": run_id, "task_id": run.get("task_id"), "device_id": run.get("device_id")},
                    )
                    logger.info(
                        f"run_detail: run_id={run_id}, task_type={run.get('task_type')}, "
                        f"device_serial={run.get('device_serial')}, "
                        f"tool_id={run.get('tool_id')}, "
                        f"task_params_keys={list(run.get('task_params', {}).keys())}"
                    )
                    update_run(
                        api_url,
                        run_id,
                        {"status": "RUNNING", "started_at": datetime.utcnow().isoformat()},
                    )
                    task_params = dict(run.get("task_params", {}))
                    device_serial = run.get("device_serial", "")

                    # 合并 tool_id 和 tool_snapshot 到 task_params
                    # RunAgentOut 将这些作为顶层字段返回，executor 需要在 params 中读取
                    if run.get("tool_id"):
                        task_params["tool_id"] = run["tool_id"]
                    tool_snapshot = run.get("tool_snapshot")
                    if isinstance(tool_snapshot, dict):
                        for key in ("script_path", "script_class", "default_params", "timeout"):
                            if key in tool_snapshot and key not in task_params:
                                task_params[key] = tool_snapshot[key]

                    context = ExecutionContext(
                        api_url=api_url,
                        run_id=run_id,
                        host_id=host_id,
                        device_serial=device_serial,
                    )

                    # Route: pipeline engine vs legacy executor
                    pipeline_def = run.get("pipeline_def")
                    if pipeline_def and isinstance(pipeline_def, dict) and pipeline_def.get("phases"):
                        # Register for lock renewal (fix: pipeline bypasses lock renewal)
                        _active_run_ids.add(run_id)
                        try:
                            result = _execute_pipeline_run(
                                pipeline_def, run_id, device_serial, adb, api_url,
                                host_id=host_id, ws_client=ws_client,
                                tool_snapshot=run.get("tool_snapshot"),
                            )
                        finally:
                            _active_run_ids.discard(run_id)
                    else:
                        result = _execute_run_with_lock_renewal(
                            run["task_type"],
                            task_params,
                            executor,
                            context,
                        )

                    complete_run(
                        api_url,
                        run_id,
                        {
                            "status": result.status,
                            "exit_code": result.exit_code,
                            "error_code": result.error_code,
                            "error_message": result.error_message,
                            "log_summary": result.log_summary,
                            "artifact": result.artifact,
                        },
                    )
                    logger.info("run_complete", extra={"run_id": run_id, "status": result.status})
                    if result.status == "FAILED":
                        logger.warning(
                            f"run_failed_detail: run_id={run_id}, "
                            f"error_code={result.error_code}, "
                            f"error_message={result.error_message}, "
                            f"log_summary={result.log_summary}"
                        )
            except Exception:
                logger.exception("agent_loop_failed", extra={"host_id": host_id})
            time.sleep(poll_interval)
    finally:
        # 确保后台线程停止
        heartbeat_thread.stop()
        lock_manager.stop()
        ws_client.disconnect()


if __name__ == "__main__":
    main()
