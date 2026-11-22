"""固件传输并发许可域 Settings（ADR-0064 D3 / B10 U3）。

控制面 Redis 全局传输闸门旋钮：池并发上限与单次许可 TTL。
Agent 侧等待/不可达超时由 `TransferPermitClient` 构造参数承接（本批不改
`backend/agent/settings.py`，避免与 U2 冲突）。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field

from backend.core.settings.base import DomainSettings


class TransferPermitSettings(DomainSettings):
    """控制面传输许可旋钮（env 名 = 字段名大写）。"""

    stp_firmware_transfer_max_hosts: int = Field(default=2, ge=1)
    stp_firmware_transfer_permit_ttl_seconds: int = Field(default=60, ge=5)


@lru_cache(maxsize=1)
def get_transfer_permit_settings() -> TransferPermitSettings:
    return TransferPermitSettings()


def reset_transfer_permit_settings_cache() -> None:
    get_transfer_permit_settings.cache_clear()
