"""
Agent 测试脚本 - 用于验证设备发现和上报流程
"""
import os
import sys
import time
import logging
import requests

# 添加父目录到路径以导入本地模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.agent.device_discovery import discover_devices, collect_device_info

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def send_heartbeat_with_devices(
    api_url: str,
    host_id: int,
    adb_path: str,
    ip: str = None
):
    """
    发送带设备信息的心跳

    Args:
        api_url: 后端 API 地址
        host_id: 主机 ID
        adb_path: adb 命令路径
        ip: 主机 IP（可选）
    """
    logger.info(f"开始设备发现... (adb_path={adb_path})")

    # 1. 发现设备
    devices = discover_devices(adb_path)
    logger.info(f"发现 {len(devices)} 台设备")

    # 2. 采集设备信息
    device_infos = []
    for device in devices:
        serial = device["serial"]
        logger.info(f"采集设备信息: {serial} ({device.get('model', 'Unknown')})")
        info = collect_device_info(adb_path, serial)
        device_infos.append(info)
        logger.info(f"  - ADB: {info['adb_state']}, 电量: {info.get('battery_level', 'N/A')}%, 温度: {info.get('temperature', 'N/A')}°C")

    # 3. 构造心跳数据
    payload = {
        "host_id": host_id,
        "status": "ONLINE",
        "host": {
            "ip": ip or "unknown",
        },
        "devices": device_infos
    }

    # 4. 发送心跳
    logger.info(f"发送心跳到 {api_url}/api/v1/heartbeat")
    try:
        response = requests.post(
            f"{api_url}/api/v1/heartbeat",
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        logger.info(f"心跳成功: {result}")
        return True
    except Exception as e:
        logger.error(f"心跳失败: {e}")
        return False


def main():
    """主测试流程"""
    # 配置
    API_URL = os.getenv("API_URL", "http://172.21.10.5:8000")
    HOST_ID = int(os.getenv("HOST_ID", "1"))
    ADB_PATH = os.getenv("ADB_PATH", "adb")
    HOST_IP = os.getenv("HOST_IP", None)

    logger.info("=" * 50)
    logger.info("Agent 测试脚本")
    logger.info("=" * 50)
    logger.info(f"API_URL: {API_URL}")
    logger.info(f"HOST_ID: {HOST_ID}")
    logger.info(f"ADB_PATH: {ADB_PATH}")
    logger.info("")

    # 测试连接
    logger.info("测试后端连接...")
    try:
        response = requests.get(f"{API_URL}/", timeout=5)
        logger.info(f"后端连接成功: {response.json()}")
    except Exception as e:
        logger.error(f"后端连接失败: {e}")
        logger.error("请检查:")
        logger.error("  1. 后端是否正在运行")
        logger.error("  2. API_URL 是否正确")
        return

    # 执行设备发现和心跳
    logger.info("")
    logger.info("=" * 50)
    success = send_heartbeat_with_devices(
        api_url=API_URL,
        host_id=HOST_ID,
        adb_path=ADB_PATH,
        ip=HOST_IP
    )

    logger.info("=" * 50)
    if success:
        logger.info("✅ 测试成功！")
    else:
        logger.error("❌ 测试失败！")


if __name__ == "__main__":
    main()
