"""设备 SoC 平台判定 (#73)。

Watcher 的 AEE 子系统(`/data/aee_exp`、`/data/vendor/aee_exp`)是联发科
平台专有机制,展锐/高通机型上这些目录根本不存在。判定结果用于:

  1. `device.platform` 入库(心跳上报) — 让控制面能按平台筛选/统计
  2. AEE Reconciler 平台门禁(`job_session`) — 非 MTK 机型直接跳过,
     日志给出「平台不支持」而不是含糊的 no_active_watcher

判定优先级:
  `ro.soc.manufacturer`(Android 12 / API 31 起为强制属性)> `ro.board.platform`
  前缀 > `ro.hardware` 前缀。取不到时返回 UNKNOWN — **不返回 None**,
  否则无法区分「没采集」和「采集了但判不出来」。
"""
from __future__ import annotations

import logging
import subprocess
from typing import Dict, Optional

logger = logging.getLogger(__name__)

PLATFORM_MTK = "MTK"
PLATFORM_UNISOC = "UNISOC"
PLATFORM_QCOM = "QCOM"
PLATFORM_UNKNOWN = "UNKNOWN"

# ro.soc.manufacturer 取值(小写包含匹配)
_SOC_MANUFACTURER_MAP = (
    ("mediatek", PLATFORM_MTK),
    ("mtk", PLATFORM_MTK),
    ("spreadtrum", PLATFORM_UNISOC),
    ("unisoc", PLATFORM_UNISOC),
    ("qualcomm", PLATFORM_QCOM),
    ("qti", PLATFORM_QCOM),
)

# ro.board.platform / ro.hardware 前缀回退(soc.manufacturer 缺失的老设备)
_BOARD_PREFIX_MAP = (
    ("mt", PLATFORM_MTK),
    ("ums", PLATFORM_UNISOC),
    ("sc", PLATFORM_UNISOC),
    ("sp", PLATFORM_UNISOC),
    ("msm", PLATFORM_QCOM),
    ("sdm", PLATFORM_QCOM),
    ("sm", PLATFORM_QCOM),
    ("qcom", PLATFORM_QCOM),
    ("kona", PLATFORM_QCOM),
    ("lahaina", PLATFORM_QCOM),
    ("taro", PLATFORM_QCOM),
    ("kalama", PLATFORM_QCOM),
    ("pineapple", PLATFORM_QCOM),
)

# 平台判定结果按 serial 缓存 — SoC 不会在设备生命周期内变化,
# 每次心跳/每个 Job 重新探测纯属浪费 adb 往返。
_PLATFORM_CACHE: Dict[str, str] = {}


def normalize_platform(
    soc_manufacturer: Optional[str] = None,
    board_platform: Optional[str] = None,
    hardware: Optional[str] = None,
) -> str:
    """把 getprop 原始值归一化为 MTK / UNISOC / QCOM / UNKNOWN。"""
    soc = (soc_manufacturer or "").strip().lower()
    for needle, platform in _SOC_MANUFACTURER_MAP:
        if needle in soc:
            return platform

    for raw in (board_platform, hardware):
        value = (raw or "").strip().lower()
        if not value:
            continue
        for prefix, platform in _BOARD_PREFIX_MAP:
            if value.startswith(prefix):
                return platform

    return PLATFORM_UNKNOWN


def parse_platform_props(getprop_output: str) -> str:
    """解析一次批量 getprop 的输出(每行一个值,顺序见 PLATFORM_PROPS)。"""
    lines = [ln.strip() for ln in (getprop_output or "").splitlines()]
    # 设备侧偶发在首行输出 shell banner,只取末尾 3 行保证对齐
    values = lines[-3:] if len(lines) >= 3 else lines + [""] * (3 - len(lines))
    return normalize_platform(
        soc_manufacturer=values[0],
        board_platform=values[1],
        hardware=values[2],
    )


# 与 parse_platform_props 的解析顺序严格对应
PLATFORM_PROPS = ("ro.soc.manufacturer", "ro.board.platform", "ro.hardware")


def detect_device_platform(
    adb_path: str,
    serial: str,
    *,
    timeout: int = 5,
    use_cache: bool = True,
) -> str:
    """探测单台设备的 SoC 平台。失败返回 UNKNOWN(不抛异常)。

    一次 `adb shell` 取全部 3 个属性,避免多次往返。
    """
    if use_cache:
        cached = _PLATFORM_CACHE.get(serial)
        if cached:
            return cached

    shell_cmd = "; ".join(f"getprop {prop}" for prop in PLATFORM_PROPS)
    try:
        result = subprocess.run(
            [adb_path, "-s", serial, "shell", shell_cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — 探测失败不应影响调用方
        logger.warning("device_platform_probe_failed serial=%s error=%s", serial, exc)
        return PLATFORM_UNKNOWN

    if result.returncode != 0:
        logger.warning(
            "device_platform_probe_nonzero serial=%s rc=%s", serial, result.returncode
        )
        return PLATFORM_UNKNOWN

    platform = parse_platform_props(result.stdout)
    if platform != PLATFORM_UNKNOWN and use_cache:
        _PLATFORM_CACHE[serial] = platform
    return platform


def clear_platform_cache(serial: Optional[str] = None) -> None:
    """清除缓存(换机、测试用)。不传 serial 则全清。"""
    if serial is None:
        _PLATFORM_CACHE.clear()
    else:
        _PLATFORM_CACHE.pop(serial, None)
