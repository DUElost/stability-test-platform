"""
设备发现和采集模块 - 用于测试
"""
import logging
import subprocess
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)


def discover_devices(adb_path: str = "adb") -> List[Dict[str, Any]]:
    """
    发现所有 ADB 设备

    Returns:
        设备列表，每个设备包含 serial, adb_state, model
    """
    try:
        result = subprocess.run(
            [adb_path, "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=10
        )
        lines = result.stdout.splitlines()
    except Exception as e:
        logger.error(f"adb_devices_failed: {e}")
        return []

    devices = []
    for line in lines[1:]:  # 跳过第一行标题
        if not line.strip():
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        serial = parts[0]
        adb_state = parts[1] if len(parts) > 1 else "unknown"

        # 解析 model
        model = None
        for part in parts:
            if part.startswith("model:"):
                model = part.split(":", 1)[1]

        devices.append({
            "serial": serial,
            "adb_state": adb_state,
            "model": model,
        })

    logger.info(f"discovered_devices: {len(devices)} devices")
    return devices


def collect_device_info(adb_path: str, serial: str) -> Dict[str, Any]:
    """
    采集单台设备的基础信息

    Args:
        adb_path: adb 命令路径
        serial: 设备序列号

    Returns:
        设备信息字典
    """
    info = {
        "serial": serial,
        "adb_state": "unknown",
        "adb_connected": False,
        "model": None,
        "battery_level": None,
        "temperature": None,
        "network_latency": None,
    }

    # 检查 ADB 连接状态
    try:
        result = subprocess.run(
            [adb_path, "-s", serial, "shell", "echo", "test"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            info["adb_state"] = "device"
            info["adb_connected"] = True
            logger.info(f"adb_check_success: {serial}, adb_connected=True")
        else:
            info["adb_state"] = "offline"
            info["adb_connected"] = False
            logger.warning(f"adb_check_failed: {serial}, returncode={result.returncode}, adb_connected=False")
            return info
    except Exception as e:
        logger.warning(f"adb_check_exception: {serial}, error={e}, adb_connected=False")
        info["adb_state"] = "offline"
        info["adb_connected"] = False
        return info

    # 采集电池信息
    try:
        result = subprocess.run(
            [adb_path, "-s", serial, "shell", "dumpsys", "battery"],
            capture_output=True,
            text=True,
            timeout=10
        )
        battery_text = result.stdout
        info["battery_level"] = _parse_battery_level(battery_text)
        info["temperature"] = _parse_battery_temp(battery_text)
    except Exception as e:
        logger.warning(f"battery_parse_failed: {serial}, error={e}")

    # 采集网络延迟 (主目标 223.5.5.5, 备用 8.8.8.8)
    latency = _ping_with_fallback(adb_path, serial, "223.5.5.5", fallback="8.8.8.8")
    if latency is not None:
        info["network_latency"] = latency

    return info


def _parse_battery_level(text: str) -> int:
    """从 dumpsys battery 输出中解析电量"""
    for line in text.splitlines():
        if "level:" in line:
            try:
                return int(line.split(":")[1].strip())
            except (ValueError, IndexError):
                pass
    return 0


def _parse_battery_temp(text: str) -> int:
    """从 dumpsys battery 输出中解析温度"""
    for line in text.splitlines():
        if "temperature:" in line:
            try:
                # 温度通常是 0.1摄氏度为单位
                temp = int(line.split(":")[1].strip()) / 10
                return int(temp)
            except (ValueError, IndexError):
                pass
    return 0


def _parse_ping_time(text: str) -> Optional[float]:
    """
    从 ping 输出中解析平均延迟时间

    优先解析 rtt 汇总行获取平均值，如果没有则使用单个 time= 值

    Args:
        text: ping 命令输出文本

    Returns:
        平均延迟时间（毫秒），解析失败返回 None
    """
    try:
        lines = text.splitlines()

        # 第一遍：查找 rtt min/avg/max/mdev 行 (Linux 格式，包含平均值)
        for line in lines:
            if "rtt min/avg/max/mdev" in line or "round-trip" in line:
                # 格式: rtt min/avg/max/mdev = 1.234/5.678/9.012/1.234 ms
                # 或: round-trip min/avg/max = 1.234/5.678/9.012 ms
                parts = line.split("=")[1].strip().split("/")
                if len(parts) >= 2:
                    # parts[1] 是 avg 值，可能包含 "ms" 后缀
                    avg_str = parts[1].strip().replace("ms", "").strip()
                    return float(avg_str)

        # 第二遍：查找 time=XXms 格式 (每行输出，返回最后一个值)
        last_time = None
        for line in lines:
            if "time=" in line and "bytes from" in line:
                # 提取 time=XXms 或 time=XX.Xms
                for part in line.split():
                    if part.startswith("time="):
                        time_str = part.split("=")[1].replace("ms", "").strip()
                        try:
                            last_time = float(time_str)
                        except (ValueError, TypeError):
                            pass

        if last_time is not None:
            return last_time

    except Exception as e:
        logger.debug(f"parse_ping_exception: {e}")

    return None


def _ping_with_fallback(adb_path: str, serial: str, target: str, fallback: Optional[str] = None) -> Optional[float]:
    """
    使用 ping 检测网络延迟，支持备用目标切换

    Args:
        adb_path: adb 命令路径
        serial: 设备序列号
        target: 主 ping 目标
        fallback: 备用 ping 目标（可选）

    Returns:
        平均延迟时间（毫秒），失败返回 None
    """
    def _ping(host: str) -> Tuple[Optional[float], bool]:
        """执行 ping 并返回 (延迟, 是否成功)"""
        try:
            result = subprocess.run(
                [adb_path, "-s", serial, "shell", "ping", "-c", "3", host],
                capture_output=True,
                text=True,
                timeout=15
            )
            # 记录原始输出用于调试
            logger.info(f"ping_raw_output: {serial}, target={host}, returncode={result.returncode}")
            logger.info(f"ping_stdout: {serial}, stdout={result.stdout[:500] if result.stdout else 'empty'}")
            if result.stderr:
                logger.warning(f"ping_stderr: {serial}, stderr={result.stderr[:200]}")

            # 检查是否成功
            if result.returncode != 0:
                logger.warning(f"ping_returncode_failed: {serial}, target={host}, returncode={result.returncode}")
                return None, False
            # 检查是否 100% 丢包
            if "100% packet loss" in result.stdout or "100.0% packet loss" in result.stdout:
                logger.warning(f"ping_packet_loss: {serial}, target={host}, 100% packet loss")
                return None, False
            # 解析延迟
            latency = _parse_ping_time(result.stdout)
            if latency is not None:
                logger.info(f"ping_parse_success: {serial}, target={host}, latency={latency}ms")
            else:
                logger.warning(f"ping_parse_failed: {serial}, target={host}, could not parse latency from output")
            return latency, latency is not None
        except Exception as e:
            logger.error(f"ping_exception: {serial}, target={host}, error={e}")
            return None, False

    # 尝试主目标
    latency, success = _ping(target)
    if success:
        return latency

    # 尝试备用目标
    if fallback:
        latency, success = _ping(fallback)
        if success:
            return latency

    return None
