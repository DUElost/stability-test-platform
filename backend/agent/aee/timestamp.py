"""Timestamp parsing/formatting — ported from MonkeyAEEinfo."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional


def parse_timestamp(timestamp_field_str: str) -> Optional[datetime]:
    try:
        cleaned_str = timestamp_field_str.strip().split("@", 1)[-1].strip()
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(cleaned_str, fmt)
            except ValueError:
                continue
        match = re.match(
            r"^(?:\w{3}\s+)?(?P<mon>\w{3})\s+(?P<day>\d{1,2})\s+(?P<time>\d{2}:\d{2}:\d{2})(?:\s+\S+)?\s+(?P<year>\d{4})$",
            cleaned_str,
        )
        if match:
            dt_str = (
                f"{match.group('mon')} {match.group('day')} "
                f"{match.group('time')} {match.group('year')}"
            )
            return datetime.strptime(dt_str, "%b %d %H:%M:%S %Y")
    except (ValueError, AttributeError):
        pass
    return None


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
