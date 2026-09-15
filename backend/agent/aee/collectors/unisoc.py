"""UNISOC PlatformCollector — uniview event metadata (ADR-0032 B5).

事件真源（**三重依据**：toolkit `modules/analyse/platform_sources.py:250` +
ADR-0032 §119「对齐 toolkit 检测逻辑」+ 真机 Z2581/Z2582 实测，2026-09-14）:

    /data/ylog/uniview_exception/{Type}.{event_id}/unievent_info   ← JSONL

> 修正前的实现指向 `/data/uniview` 且要求 `unievent_info.json`。两者都不对：
> `/data/uniview` 是**框架侧**目录（其 `logs/tmp` 与展锐异常无关），真机从未在
> 其中出现事件目录；真源在 `/data/ylog/uniview_exception`，元数据文件名为
> `unievent_info`（无 `.json`），内容为 **JSONL**。旧实现因此**恒采不到事件**。

`unievent_info` 的行分工（真机原文，`JE.103000004`）:

    {"sn":"…","software_version":"…","soc_model":"UMS9230E"}                       # A 设备头
    {"event_id":"103000004","event_type":"FAULT","event_level":"GENERAL",
     "event_name":"Java Crash"}                                                    # B 事件元数据
    {"kick_datetime":"2026-09-08_06:59:12.031","pid":"23847",
     "proc":"com.android.camera2","tag":"system_app_crash"}                        # C 事件发生

`Reboot` 类型的发生行带 `reboot_reason`；**normalboot 必须丢弃**（uniview 每次开机
都记，真机 `Reboot.103000002` 全部为 normalboot），否则每次正常重启都会被当成异常。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..collector import CollectorError, EventMetadata
from ..timestamp import parse_timestamp, to_utc

logger = logging.getLogger(__name__)

#: 权威事件根（真机确认；toolkit `_handle_uniview` 的 `type_dir` 前缀）
UNIVIEW_ROOT = "/data/ylog/uniview_exception"
#: 事件根下每个目录即一个事件：`{Type}.{event_id}`
UNIVIEW_INFO_FILENAME = "unievent_info"
#: 兼容读取的旧文件名（**真机从未观测到**，仅为不静默失败保留；见 Revisit）
UNIVIEW_INFO_LEGACY_FILENAME = "unievent_info.json"

_NORMALBOOT = "normalboot"
#: B 类行（事件元数据）的判别键
_EVENT_META_KEYS = ("event_id", "event_name", "event_type", "event_level")
#: C 类行（事件发生）的判别键；Reboot 类只有 reboot_reason + kick_datetime
_OCCURRENCE_KEYS = ("kick_datetime", "event_time", "timestamp", "reboot_reason")
#: 认定为「事件行」的键集合：含元数据键**或**发生键。
#: 设备头行（sn / software_version / soc_model）两者皆无 → 天然被排除，
#: 这正是 toolkit 过滤的**目的**（跳过非事件行）。
_EVENT_LINE_KEYS = _EVENT_META_KEYS + _OCCURRENCE_KEYS


def event_type_prefix(event_dir: Path) -> Optional[str]:
    """目录名前缀即事件类型（`ANR.103000005` → `ANR`），与 toolkit 的 tag 语义一致。"""
    name = event_dir.name
    prefix = name.split(".", 1)[0].strip()
    return prefix or None


def fold_unievent_info(text: str) -> dict[str, Any]:
    """把 ``unievent_info``（JSONL）折叠成**一条事件**。

    返回 ``{}`` 表示"没有可上报的事件"：无有效行，或发生行**全部**是 normalboot。

    行分类见模块 docstring；非 JSON 行与非 dict 行静默跳过（设备文件可能被截断，
    半个 JSON 行不应让整条采集失败）。
    """
    meta: dict[str, Any] = {}
    occurrences: list[dict[str, Any]] = []

    def _absorb(obj: dict[str, Any]) -> None:
        for key in _EVENT_META_KEYS:
            if key in obj and obj[key] not in (None, ""):
                meta[key] = obj[key]
        if not any(k in obj for k in _EVENT_LINE_KEYS):
            return  # 设备头行等非事件行（toolkit 同口径：只取事件行）
        if obj.get("reboot_reason") == _NORMALBOOT:
            return  # 正常开机：不是异常
        occurrences.append(obj)

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            _absorb(obj)

    if not occurrences:
        # 单对象（非 JSONL）的兼容形态：整文解析一次
        try:
            whole = json.loads(text)
        except ValueError:
            whole = None
        if isinstance(whole, dict):
            _absorb(whole)
            if not occurrences:
                whole = None

    if not occurrences:
        return {}

    last = occurrences[-1]
    folded: dict[str, Any] = dict(meta)
    folded.update({k: v for k, v in last.items() if v not in (None, "")})
    return folded


def _device_timestamp(folded: dict[str, Any]) -> tuple[Optional[datetime], Optional[str]]:
    """返回 (UTC 时间, 原文)。

    ``event_time`` 是 epoch **毫秒**（真机：``1788343422044``）→ 无歧义，优先；
    ``kick_datetime`` 是设备本地时区的裸字符串（``2026-09-08_06:59:12.031``，
    无 tz 信息）→ 只作为**原文**保留，不做时区假设（见 Revisit）。
    """
    raw = (
        folded.get("kick_datetime")
        or folded.get("event_time")
        or folded.get("timestamp")
    )
    raw_str = str(raw).strip() if raw not in (None, "") else None

    event_time = folded.get("event_time")
    if isinstance(event_time, (int, float)) and event_time > 0:
        seconds = float(event_time) / 1000.0 if event_time > 1e11 else float(event_time)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc), raw_str
        except (OverflowError, OSError, ValueError):
            logger.debug("unisoc_collector_event_time_out_of_range value=%s", event_time)

    if isinstance(raw, str) and raw:
        try:
            parsed = parse_timestamp(raw)
            return (to_utc(parsed) if parsed else None), raw_str
        except Exception:  # noqa: BLE001 - 解析失败不影响采集（只丢 UTC 换算）
            logger.debug("unisoc_collector_ts_parse_failed raw=%s", raw, exc_info=True)
    return None, raw_str


class UnisocPlatformCollector:
    platform = "UNISOC"

    def parse_metadata(self, event_dir: Path) -> EventMetadata:
        info_path = event_dir / UNIVIEW_INFO_FILENAME
        if not info_path.is_file():
            legacy = event_dir / UNIVIEW_INFO_LEGACY_FILENAME
            if legacy.is_file():
                info_path = legacy
            else:
                raise CollectorError(
                    f"missing {UNIVIEW_INFO_FILENAME} under {event_dir}"
                )
        try:
            text = info_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise CollectorError(f"unreadable {info_path.name}: {exc}") from exc

        folded = fold_unievent_info(text)
        if not folded:
            # normalboot-only / 空文件 / 截断：都不是可上报事件（不抛到上层告警）
            raise CollectorError(
                f"no reportable event under {event_dir} (normalboot-only or empty)"
            )

        device_ts, device_ts_raw = _device_timestamp(folded)
        event_name = folded.get("event_name")
        subtype = str(event_name).strip() if event_name else event_type_prefix(event_dir)
        package = folded.get("package_name") or folded.get("proc") or folded.get("package")

        return EventMetadata(
            event_type="UNIVIEW",
            event_subtype=subtype or None,
            package_name=str(package).strip() if package else None,
            device_timestamp=device_ts,
            device_timestamp_raw=device_ts_raw,
        )
