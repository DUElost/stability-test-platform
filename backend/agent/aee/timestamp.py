"""Timestamp parsing/formatting — ported from MonkeyAEEinfo."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

# #88:db_history 的时间戳带设备本地时区名(`Fri Jul 17 13:41:47 CST 2026`)。
# 原实现用 `(?:\s+\S+)?` 把该 token 匹配掉但**不解析**,调用方再直接盖上
# UTC,导致恒定 +8 小时偏差。这里显式识别常见时区名。
#
# 注意 CST 有歧义(China Standard Time +8 / US Central Standard Time -6)。
# 本项目设备均为国内市场 MTK/展锐机型,按 +8 解析;真要支持多地域需改为
# 由设备 `persist.sys.timezone` 显式下发,而不是猜时区缩写。
_TZ_OFFSET_HOURS: dict[str, int] = {
    "UTC": 0,
    "GMT": 0,
    "Z": 0,
    "CST": 8,
    "CT": 8,
    "HKT": 8,
    "SGT": 8,
    "JST": 9,
    "KST": 9,
}

_TS_RE = re.compile(
    r"^(?:\w{3}\s+)?(?P<mon>\w{3})\s+(?P<day>\d{1,2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})(?:\s+(?P<tz>\S+))?\s+(?P<year>\d{4})$"
)


def _tzinfo_for(token: Optional[str]) -> Optional[timezone]:
    """把时区缩写解析为 tzinfo;无法识别返回 None(调用方保持 naive)。"""
    if not token:
        return None
    hours = _TZ_OFFSET_HOURS.get(token.strip().upper())
    if hours is None:
        return None
    return timezone(timedelta(hours=hours))


def parse_timestamp(timestamp_field_str: str) -> Optional[datetime]:
    """解析 db_history 时间戳。

    识别出时区缩写时返回 **aware** datetime(墙钟字段保持设备本地值,仅附加
    tzinfo);无时区信息时返回 naive datetime(与历史行为一致)。

    墙钟字段不做换算 —— `format_timestamp_for_filename` 依赖它生成与设备
    侧一致的事件目录名,换算会导致路径漂移。需要真 UTC 的调用方请用
    `to_utc()`。
    """
    try:
        cleaned_str = timestamp_field_str.strip().split("@", 1)[-1].strip()
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(cleaned_str, fmt)
            except ValueError:
                continue
        match = _TS_RE.match(cleaned_str)
        if match:
            dt_str = (
                f"{match.group('mon')} {match.group('day')} "
                f"{match.group('time')} {match.group('year')}"
            )
            parsed = datetime.strptime(dt_str, "%b %d %H:%M:%S %Y")
            tzinfo = _tzinfo_for(match.group("tz"))
            return parsed.replace(tzinfo=tzinfo) if tzinfo else parsed
    except (ValueError, AttributeError):
        pass
    return None


def to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """把 `parse_timestamp` 的产物换算为**真实** UTC。

    只处理带时区的输入。naive(设备没给时区 / 时区缩写不认识)时返回 None —
    此时无从得知真实 UTC,直接盖上 `+00:00` 只是把「未知」伪装成「已知」,
    会让 `aee_ts_utc` 与 detected_at 的差值不再等于设备时钟漂移。

    调用方要区分「无时区信息」和「换算结果」时,判 None 即可。
    """
    if dt is None or dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def as_device_local_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """取设备本地墙钟(丢弃 tzinfo),用于与设备侧文件名时间戳比较。"""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def format_timestamp_for_filename(timestamp_field_str: str) -> str:
    dt_obj = parse_timestamp(timestamp_field_str)
    if not dt_obj:
        return datetime.now().strftime("%Y_%m%d_%H%M%S_%f")[:21]
    ms_part = "000"
    if "." in timestamp_field_str:
        ms_match = re.search(r"\.(\d+)", timestamp_field_str)
        if ms_match:
            ms_part = ms_match.group(1)[:3].ljust(3, "0")
    return dt_obj.strftime("%Y_%m%d_%H%M%S") + f"_{ms_part}"


def parse_mobilelog_filename_to_datetime(filename: str) -> Optional[datetime]:
    pattern = r".*?(\d{4})_(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})(?:[\._].*)?$"
    match = re.search(pattern, filename)
    if not match:
        return None
    try:
        y, mo, d, h, mi, s = map(int, match.groups())
        return datetime(y, mo, d, h, mi, s)
    except (ValueError, IndexError):
        return None
