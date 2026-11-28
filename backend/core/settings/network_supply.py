"""统一网络供给域 Settings（ADR-0042 / ADR-0066 D2 & D6.3 / B13 U1）。

承载控制面签发 Ed25519 ``usbnet_grant`` 所需私钥与 TTL 边界，以及管理员手动
调试供网会话的默认与上限时长。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator

from backend.core.settings.base import DomainSettings


class NetworkSupplySettings(DomainSettings):
    """网络供给控制面旋钮（env 名 = 字段名大写）。"""

    stp_usbnet_grant_signing_key: str = Field(default="")
    stp_usbnet_grant_ttl_seconds: int = Field(default=900, ge=300, le=1800)
    stp_manual_network_default_ttl_seconds: int = Field(default=14400, ge=300, le=86400)
    stp_manual_network_max_ttl_seconds: int = Field(default=86400, ge=300, le=604800)

    @model_validator(mode="after")
    def _validate_manual_ttl_order(self) -> NetworkSupplySettings:
        if self.stp_manual_network_default_ttl_seconds > self.stp_manual_network_max_ttl_seconds:
            raise ValueError(
                "STP_MANUAL_NETWORK_DEFAULT_TTL_SECONDS must not exceed "
                "STP_MANUAL_NETWORK_MAX_TTL_SECONDS"
            )
        return self


@lru_cache(maxsize=1)
def get_network_supply_settings() -> NetworkSupplySettings:
    return NetworkSupplySettings()


def reset_network_supply_settings_cache() -> None:
    get_network_supply_settings.cache_clear()
