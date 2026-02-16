import logging
import os
import socket
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import requests

# 支持直接运行和作为包运行
if __name__ == "__main__" and __package__ is None:
    # 直接运行时的导入路径处理
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from agent.adb_wrapper import AdbWrapper
    from agent.heartbeat import send_heartbeat
    from agent.task_executor import TaskExecutor, ExecutionContext
    from agent import device_discovery
else:
    from .adb_wrapper import AdbWrapper
    from .heartbeat import send_heartbeat
    from .task_executor import TaskExecutor, ExecutionContext
    from . import device_discovery

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
    try:
        host_id = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"HOST_ID must be an integer, got: {raw_value}") from exc
    if host_id <= 0:
        raise ValueError(f"HOST_ID must be > 0, got: {host_id}")
    return host_id


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


def main() -> None:
    api_url = os.getenv("API_URL", "http://127.0.0.1:8000")
    try:
        host_id = _load_required_host_id()
    except ValueError as exc:
        logger.error(
            "invalid_host_id_config",
            extra={
                "host_id_raw": os.getenv("HOST_ID"),
                "error": str(exc),
            },
        )
        logger.error(
            "Set HOST_ID to a positive integer matching backend hosts.id, then restart agent."
        )
        raise SystemExit(2)
    poll_interval = float(os.getenv("POLL_INTERVAL", "5"))
    mount_points = [p for p in os.getenv("MOUNT_POINTS", "").split(",") if p]
    adb_path = os.getenv("ADB_PATH", "adb")

    # 获取本机信息
    host_info = get_host_info()
    logger.info("agent_started", extra={"host_id": host_id, "api_url": api_url, "ip": host_info["ip"]})

    adb = AdbWrapper(adb_path=adb_path)
    executor = TaskExecutor(adb)

    # 启动锁续期管理器
    lock_manager = LockRenewalManager(api_url)
    lock_manager.start()

    try:
        while True:
            # 采集设备信息
            devices_list = []
            try:
                discovered = device_discovery.discover_devices(adb_path)
                for dev in discovered:
                    # 采集每台设备的详细监控数据
                    info = device_discovery.collect_device_info(adb_path, dev["serial"])
                    device_data = {
                        "serial": dev["serial"],
                        "model": dev.get("model"),
                        "state": dev["adb_state"],  # device, offline, unauthorized
                        "adb_state": info.get("adb_state", dev.get("adb_state", "unknown")),
                        "adb_connected": info.get("adb_connected", False),
                        "battery_level": info.get("battery_level"),
                        "temperature": info.get("temperature"),
                        "network_latency": info.get("network_latency"),
                    }
                    devices_list.append(device_data)
                    # 记录设备数据用于调试
                    logger.info(f"device_collected: {dev['serial']}, adb_connected={info.get('adb_connected')}, network_latency={info.get('network_latency')}, battery={info.get('battery_level')}, temp={info.get('temperature')}")
                logger.debug(f"discovered_{len(devices_list)}_devices")
            except Exception as e:
                logger.warning(f"device_discovery_failed: {e}")

            send_heartbeat(api_url, host_id, mount_points, host_info=host_info, devices=devices_list)
            try:
                runs = fetch_pending_runs(api_url, host_id)
                logger.info("pending_runs_fetched", extra={"host_id": host_id, "count": len(runs)})
                for run in runs:
                    run_id = run["id"]
                    logger.info(
                        "run_start",
                        extra={"run_id": run_id, "task_id": run.get("task_id"), "device_id": run.get("device_id")},
                    )
                    update_run(
                        api_url,
                        run_id,
                        {"status": "RUNNING", "started_at": datetime.utcnow().isoformat()},
                    )
                    task_params = run.get("task_params", {})
                    device_serial = run.get("device_serial", "")

                    context = ExecutionContext(
                        api_url=api_url,
                        run_id=run_id,
                        host_id=host_id,
                        device_serial=device_serial,
                    )

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
            except Exception:
                logger.exception("agent_loop_failed", extra={"host_id": host_id})
            time.sleep(poll_interval)
    finally:
        # 确保锁续期线程停止
        lock_manager.stop()


if __name__ == "__main__":
    main()
