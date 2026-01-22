import logging
import os
import socket
import time
from typing import Any, Dict, List

import requests

from .adb_wrapper import AdbWrapper
from .heartbeat import send_heartbeat
from .task_executor import TaskExecutor
from . import device_discovery

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


def fetch_pending_runs(api_url: str, host_id: int) -> List[Dict[str, Any]]:
    resp = requests.get(
        f"{api_url}/api/v1/agent/runs/pending",
        params={"host_id": host_id, "limit": 10},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def update_run(api_url: str, run_id: int, payload: Dict[str, Any]) -> None:
    requests.post(f"{api_url}/api/v1/agent/runs/{run_id}/heartbeat", json=payload, timeout=10)


def complete_run(api_url: str, run_id: int, payload: Dict[str, Any]) -> None:
    requests.post(f"{api_url}/api/v1/agent/runs/{run_id}/complete", json=payload, timeout=10)


def get_host_info() -> Dict[str, Any]:
    """获取本机信息"""
    hostname = socket.gethostname()
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
        "hostname": hostname,
        "ip": ip,
    }


def main() -> None:
    api_url = os.getenv("API_URL", "http://127.0.0.1:8000")
    host_id = int(os.getenv("HOST_ID", "0"))
    poll_interval = float(os.getenv("POLL_INTERVAL", "5"))
    mount_points = [p for p in os.getenv("MOUNT_POINTS", "").split(",") if p]
    adb_path = os.getenv("ADB_PATH", "adb")

    # 获取本机信息
    host_info = get_host_info()
    logger.info("agent_started", extra={"host_id": host_id, "api_url": api_url, "hostname": host_info["hostname"], "ip": host_info["ip"]})

    adb = AdbWrapper(adb_path=adb_path)
    executor = TaskExecutor(adb)

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
                    "battery_level": info.get("battery_level"),
                    "temperature": info.get("temperature"),
                    "network_latency": info.get("network_latency"),
                }
                devices_list.append(device_data)
                # 记录设备数据用于调试
                logger.info(f"device_collected: {dev['serial']}, network_latency={info.get('network_latency')}, battery={info.get('battery_level')}, temp={info.get('temperature')}")
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
                update_run(api_url, run_id, {"status": "RUNNING", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
                result = executor.execute_task(run["task_type"], run.get("task_params", {}), run.get("device_serial", ""))
                complete_run(
                    api_url,
                    run_id,
                    {
                        "status": result.status,
                        "exit_code": result.exit_code,
                        "error_code": result.error_code,
                        "error_message": result.error_message,
                        "log_summary": result.log_summary,
                    },
                )
                logger.info("run_complete", extra={"run_id": run_id, "status": result.status})
        except Exception:
            logger.exception("agent_loop_failed", extra={"host_id": host_id})
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
