"""
系统监控模块 - 采集 CPU、内存、磁盘使用率
"""
import logging
import shutil
from typing import Dict, Any

logger = logging.getLogger(__name__)


def get_cpu_usage() -> float:
    """
    获取 CPU 使用率（百分比）

    Returns:
        CPU 使用率 (0-100)
    """
    try:
        # 读取 /proc/stat 第一行
        with open('/proc/stat', 'r') as f:
            line = f.readline()

        # 解析字段
        # 格式: cpu user nice system idle iowait irq softirq
        fields = line.split()
        if len(fields) < 5 or fields[0] != 'cpu':
            raise ValueError("Invalid /proc/stat format")

        # 提取数值
        user = int(fields[1])    # user mode
        nice = int(fields[2])    # user mode with low priority
        system = int(fields[3])  # system mode
        idle = int(fields[4])    # idle task

        # 计算 CPU 使用率
        # usage = (user + system) / (user + nice + system + idle) * 100
        total = user + nice + system + idle
        if total == 0:
            return 0.0

        usage = ((user + system) / total) * 100
        return round(usage, 2)
    except Exception as e:
        logger.warning(f"get_cpu_usage_failed: {e}")
        return 0.0


def get_memory_usage() -> float:
    """
    获取内存使用率（百分比）

    Returns:
        内存使用率 (0-100)
    """
    try:
        with open('/proc/meminfo', 'r') as f:
            meminfo = dict((i.split()[0].rstrip(':'), int(i.split()[1]))
                          for i in f.readlines()[:5])

        total = meminfo.get('MemTotal', 1)
        available = meminfo.get('MemAvailable', meminfo.get('MemFree', 0))
        used = total - available

        usage = 100.0 * used / total if total > 0 else 0.0
        return round(usage, 2)
    except Exception as e:
        logger.warning(f"get_memory_usage_failed: {e}")
        return 0.0


def get_disk_usage(path: str = '/') -> Dict[str, Any]:
    """读取 ``path`` 所在盘使用率。

    读盘失败时 ``usage_percent`` 为 ``None``（不要填 ``0.0`` —— HddSpill 会
    把 0% 当成磁盘健康而跳过溢出）。
    """
    try:
        usage = shutil.disk_usage(path)
        total_gb = usage.total / (1024 ** 3)
        used_gb = usage.used / (1024 ** 3)
        free_gb = usage.free / (1024 ** 3)
        usage_percent = 100.0 * usage.used / usage.total if usage.total > 0 else 0.0

        return {
            "total_gb": round(total_gb, 2),
            "used_gb": round(used_gb, 2),
            "free_gb": round(free_gb, 2),
            "usage_percent": round(usage_percent, 2),
        }
    except Exception as e:
        logger.warning("get_disk_usage_failed path=%s error=%s", path, e)
        return {
            "total_gb": None,
            "used_gb": None,
            "free_gb": None,
            "usage_percent": None,
        }


def get_aee_disk_usage() -> Dict[str, Any]:
    """设备日志盘（``STP_AEE_LOCAL_ROOT`` 所在文件系统）使用率。

    与系统盘 ``disk_usage`` 分开上报：``/storage`` 健康页按 host 展示这块盘
    （#273），阈值口径与 HddSpill（95%）对齐。返回里带 ``path``，因为各 host
    的本地根落点不同（``/data/hdd`` / ``/mnt/hdd`` / 系统盘），控制面需要知道
    每台上报的是哪个路径。
    """
    from .aee.paths import get_aee_local_root

    root = str(get_aee_local_root())
    info = get_disk_usage(root)
    info["path"] = root
    return info


def get_network_connections() -> Dict[str, int]:
    """
    获取网络连接统计

    Returns:
        连接统计字典
    """
    try:
        # 简单统计 TCP 连接数
        connections = 0
        try:
            with open('/proc/net/tcp', 'r') as f:
                connections = len(f.readlines()) - 1  # 减去标题行
        except:
            pass

        return {
            "tcp_connections": connections,
        }
    except Exception as e:
        logger.warning(f"get_network_connections_failed: {e}")
        return {"tcp_connections": 0}


def collect_system_stats() -> Dict[str, Any]:
    """
    采集完整的系统统计信息

    Returns:
        系统统计字典
    """
    return {
        "cpu_load": get_cpu_usage(),
        "ram_usage": get_memory_usage(),
        "disk_usage": get_disk_usage('/'),
        "disk_usage_aee": get_aee_disk_usage(),
        "network": get_network_connections(),
    }
