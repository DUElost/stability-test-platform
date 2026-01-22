#!/usr/bin/env python3
"""
简化版 Agent 测试脚本 - 直接在 Linux 主机上运行
"""
import subprocess
import requests
import json
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def discover_devices(adb_path="adb"):
    """发现 ADB 设备"""
    try:
        result = subprocess.run(
            [adb_path, "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=10
        )
        lines = result.stdout.splitlines()
    except Exception as e:
        logger.error(f"ADB 命令失败: {e}")
        return []

    devices = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 2:
            serial = parts[0]
            state = parts[1]
            model = None
            for p in parts:
                if p.startswith("model:"):
                    model = p.split(":", 1)[1]
            devices.append({"serial": serial, "state": state, "model": model})

    logger.info(f"发现 {len(devices)} 台设备")
    return devices


def get_device_info(adb_path, serial):
    """获取设备信息"""
    info = {"serial": serial, "state": "offline", "battery": None, "temp": None}

    # 检查连接
    try:
        r = subprocess.run([adb_path, "-s", serial, "shell", "echo", "test"],
                          capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            info["state"] = "device"
        else:
            return info
    except:
        return info

    # 获取电池信息
    try:
        r = subprocess.run([adb_path, "-s", serial, "shell", "dumpsys", "battery"],
                          capture_output=True, text=True, timeout=10)
        for line in r.stdout.splitlines():
            if line.strip().startswith("level:"):
                info["battery"] = int(line.split(":")[1].strip())
    except:
        pass

    return info


def send_heartbeat(api_url, host_id, devices):
    """发送心跳"""
    payload = {
        "host_id": host_id,
        "status": "ONLINE",
        "host": {
            "hostname": os.uname().nodename,
            "ip": "172.21.15.1"  # 替换为实际 IP
        },
        "devices": devices
    }

    logger.info(f"发送心跳到 {api_url}/api/v1/heartbeat")
    try:
        r = requests.post(f"{api_url}/api/v1/heartbeat", json=payload, timeout=10)
        r.raise_for_status()
        logger.info(f"心跳成功: {r.json()}")
        return True
    except Exception as e:
        logger.error(f"心跳失败: {e}")
        return False


def main():
    API_URL = os.getenv("API_URL", "http://172.21.10.21:8000")
    HOST_ID = int(os.getenv("HOST_ID", "1"))
    ADB_PATH = os.getenv("ADB_PATH", "adb")

    logger.info(f"API_URL: {API_URL}")
    logger.info(f"HOST_ID: {HOST_ID}")
    logger.info(f"ADB_PATH: {ADB_PATH}")

    # 测试后端连接
    try:
        r = requests.get(f"{API_URL}/", timeout=5)
        logger.info(f"后端连接成功: {r.json()}")
    except Exception as e:
        logger.error(f"后端连接失败: {e}")
        return

    # 发现设备
    devices = discover_devices(ADB_PATH)

    # 采集设备信息
    device_infos = []
    for d in devices:
        info = get_device_info(ADB_PATH, d["serial"])
        info["model"] = d["model"]
        device_infos.append(info)
        logger.info(f"设备: {d['serial'][:16]} | {d['model'] or 'Unknown':20} | {info['state']:10} | 电量: {info.get('battery', 'N/A')}%")

    # 发送心跳
    if device_infos:
        send_heartbeat(API_URL, HOST_ID, device_infos)
    else:
        logger.warning("没有发现设备")


if __name__ == "__main__":
    main()
