"""
系统监控模块 - 采集 CPU、内存、磁盘使用率
"""
import logging
import shutil
import threading
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# #1259：CPU 使用率必须两次采样差分——单次累计计数求比例等于「开机以来
# 平均值」，长时间低负载后突然满载仍接近历史均值。心跳线程与手动心跳可能
# 并发采集，状态更新加锁。
_cpu_lock = threading.Lock()
_cpu_prev: Optional[Dict[str, int]] = None

_CPU_FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")


def _parse_cpu_line(line: str) -> Dict[str, int]:
    """解析 ``/proc/stat`` 首行；缺列按 0 补齐（旧内核字段子集）。"""
    fields = line.split()
    if len(fields) < 5 or fields[0] != 'cpu':
        raise ValueError("Invalid /proc/stat format")
    values = [int(v) for v in fields[1:1 + len(_CPU_FIELDS)]]
    values += [0] * (len(_CPU_FIELDS) - len(values))
    # #1558：不用 ``zip(..., strict=)``——它要 Python 3.10+，而
    # backend/agent/DEPLOY.md 声明「Python 3.8+」、heartbeat_thread.py 也明确
    # 注释「不用 zip(strict=)：Agent 运行环境兼容旧 python3」。在 3.8/3.9 上它抛
    # TypeError，被 get_cpu_usage 的 except 吞掉 → CPU 恒 0.0 →
    # capacity_reporter 的 cpu>90 限流判据永不触发（主机可持续超领 slot），
    # 且只剩一条 warning 日志。
    # 也不能写成 ``zip(..., strict=False)``：``strict`` 形参本身就是 3.10 才有的。
    # 故直接按索引建字典——既不带该形参，也满足 ruff B905（不能用 noqa 掩盖，
    # 那会把「本文件刻意避开 zip」这条约束藏起来）。
    return {field: values[index] for index, field in enumerate(_CPU_FIELDS)}


def get_cpu_usage() -> float:
    """当前 CPU 使用率（百分比），基于 ``/proc/stat`` 两次采样的区间差分。

    首采无基线返回 ``0.0``（仅 priming，不报开机以来均值）；窗口内
    ``busy = total − idle − iowait``（iowait 是等 IO 的空闲，不算 CPU 忙）。
    计数器回绕（重启/热插拔）时重置基线并返回 ``0.0``。

    Returns:
        CPU 使用率 (0-100)
    """
    global _cpu_prev
    try:
        with open('/proc/stat', 'r') as f:
            current = _parse_cpu_line(f.readline())

        with _cpu_lock:
            previous, _cpu_prev = _cpu_prev, current

        if previous is None:
            return 0.0

        deltas = {key: current[key] - previous[key] for key in _CPU_FIELDS}
        if any(delta < 0 for delta in deltas.values()):
            return 0.0     # 计数器回绕：新样本已设为基线，下一窗口重算

        total = sum(deltas.values())
        if total <= 0:
            return 0.0

        busy = total - deltas["idle"] - deltas["iowait"]
        return round(max(0.0, min(100.0, busy / total * 100)), 2)
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
