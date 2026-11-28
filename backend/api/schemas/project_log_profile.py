"""ADR-0067 D1 — 项目级日志预设（TestProject.log_profile）白名单校验与规范化。

单一真源：供 API schema（``backend/api/schemas/project.py``）、写路径服务
（``backend/services/project_registry.py``）及后续 U2 五层合并/指纹计算共用，
避免控制面多处维护各自的白名单与边界常量。
"""

from __future__ import annotations

import re
from typing import Any, List, Mapping, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    SerializerFunctionWrapHandler,
    ValidationError,
    field_validator,
    model_serializer,
)

MOBILELOG_SIZE_MB_MIN = 256
MOBILELOG_SIZE_MB_MAX = 20480
YLOG_SIZE_MB_MIN = 64
YLOG_SIZE_MB_MAX = 10240

# Phase 1 实机取证（#4182 / #7211180563）：A57/P525AA ylogctl query 核验子流。
# 基础流 android / kernel 不得被项目预设移除；仅允许追加已实证子流（排除 sysinfo 等假阳性流）。
UNISOC_MANDATORY_YLOG_STREAMS = frozenset({"android", "kernel"})
UNISOC_VERIFIED_YLOG_STREAMS = frozenset({"android", "kernel", "uboot", "lastlog"})

_MTK_SUBLOG_CMD_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_AEE_MODE_RE = re.compile(r"^[0-9]{1,2}$")

LOG_PROFILE_ALLOWLIST_KEYS = frozenset({
    "mobilelog_size_mb",
    "mtk_auto_start",
    "mtk_sublog_cmd",
    "aee_mode",
    "ylog_size_mb",
    "ylog_rotate",
    "ylog_required_streams",
})


class ProjectLogProfile(BaseModel):
    """ADR-0067 D1.2 强校验 7 键白名单模型（extra='forbid'）。"""

    model_config = ConfigDict(extra="forbid")

    mobilelog_size_mb: Optional[int] = None
    mtk_auto_start: Optional[bool] = None
    mtk_sublog_cmd: Optional[str] = None
    aee_mode: Optional[str] = None
    ylog_size_mb: Optional[int] = None
    ylog_rotate: Optional[bool] = None
    ylog_required_streams: Optional[List[str]] = None

    @field_validator("mobilelog_size_mb", mode="before")
    @classmethod
    def validate_mobilelog_size_mb(cls, value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("mobilelog_size_mb must be an integer")
        if not (MOBILELOG_SIZE_MB_MIN <= value <= MOBILELOG_SIZE_MB_MAX):
            raise ValueError(
                f"mobilelog_size_mb must be between {MOBILELOG_SIZE_MB_MIN} "
                f"and {MOBILELOG_SIZE_MB_MAX}"
            )
        return value

    @field_validator("mtk_auto_start", "ylog_rotate", mode="before")
    @classmethod
    def validate_strict_bool(cls, value: Any) -> Optional[bool]:
        if value is None:
            return None
        if not isinstance(value, bool):
            raise ValueError("field must be a boolean")
        return value

    @field_validator("mtk_sublog_cmd", mode="before")
    @classmethod
    def validate_mtk_sublog_cmd(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("mtk_sublog_cmd must be a string")
        cmd = value.strip()
        if not _MTK_SUBLOG_CMD_RE.fullmatch(cmd):
            raise ValueError(
                "mtk_sublog_cmd must match ^[A-Za-z0-9_.-]{1,64}$"
            )
        return cmd

    @field_validator("aee_mode", mode="before")
    @classmethod
    def validate_aee_mode(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("aee_mode must be a string")
        mode = value.strip()
        if not _AEE_MODE_RE.fullmatch(mode):
            raise ValueError("aee_mode must match ^[0-9]{1,2}$")
        return mode

    @field_validator("ylog_size_mb", mode="before")
    @classmethod
    def validate_ylog_size_mb(cls, value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("ylog_size_mb must be an integer")
        if not (YLOG_SIZE_MB_MIN <= value <= YLOG_SIZE_MB_MAX):
            raise ValueError(
                f"ylog_size_mb must be between {YLOG_SIZE_MB_MIN} "
                f"and {YLOG_SIZE_MB_MAX}"
            )
        return value

    @field_validator("ylog_required_streams", mode="before")
    @classmethod
    def validate_ylog_required_streams(cls, value: Any) -> Optional[List[str]]:
        if value is None:
            return None
        if not isinstance(value, list) or not value:
            raise ValueError("ylog_required_streams must be a non-empty list of strings")
        deduped: List[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("ylog_required_streams items must be strings")
            stream = item.strip().lower()
            if stream not in UNISOC_VERIFIED_YLOG_STREAMS:
                allowed = ", ".join(sorted(UNISOC_VERIFIED_YLOG_STREAMS))
                raise ValueError(
                    f"unsupported ylog stream {item!r}; allowed: {allowed}"
                )
            if stream not in seen:
                seen.add(stream)
                deduped.append(stream)
        missing_base = sorted(UNISOC_MANDATORY_YLOG_STREAMS - seen)
        if missing_base:
            raise ValueError(
                "ylog_required_streams must include mandatory base streams: "
                + ", ".join(missing_base)
            )
        return deduped

    @model_serializer(mode="wrap")
    def _serialize_sparse(
        self, handler: SerializerFunctionWrapHandler,
    ) -> dict[str, Any]:
        data = handler(self)
        return {k: v for k, v in data.items() if v is not None}


def normalize_log_profile(
    raw: Optional[ProjectLogProfile | Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    """校验并规范化为非 null 键的 dict；全空/全 null 统一归一为 None。

    保证 ``None``、``{}`` 与 ``{"aee_mode": None}`` 等价（ADR-0067 D1.2 / D1.6），
    避免无实质变更时误推进 ``log_profile_version``。
    """
    if raw is None:
        return None
    if isinstance(raw, ProjectLogProfile):
        model = raw
    elif isinstance(raw, Mapping):
        model = ProjectLogProfile.model_validate(dict(raw))
    else:
        raise ValueError("log_profile must be a JSON object or null")
    cleaned = model.model_dump(exclude_none=True)
    return cleaned if cleaned else None


def format_log_profile_validation_error(exc: ValidationError | ValueError) -> str:
    """将 Pydantic 校验错误收敛为可读单行文本（供服务层 422 抛出）。"""
    if isinstance(exc, ValidationError):
        parts: list[str] = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc") or ())
            msg = str(err.get("msg") or "invalid value")
            parts.append(f"{loc}: {msg}" if loc else msg)
        return "; ".join(parts) or "invalid log_profile"
    return str(exc) or "invalid log_profile"
