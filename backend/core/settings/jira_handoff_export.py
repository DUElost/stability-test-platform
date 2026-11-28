"""Jira handoff B 段导出域 Settings（ADR-0042 / B11-U3 #4199 v1.1）。

覆盖 15.4 本机导出根、厂商 Path 前缀、过渡期代表补解全局并发上限与锁目录。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator

from backend.core.settings.base import DomainSettings


class JiraHandoffExportSettings(DomainSettings):
    """控制面 B 段导出旋钮（env 名 = 字段名大写）。"""

    stp_jira_handoff_export_root: str = Field(default="/mnt/jxtinno-sonic")
    stp_jira_handoff_vendor_path_prefix: str = Field(default="/home/jxtinno/sonic_tinno")
    # 硬上限 = 1（#4199 v1.1 §3 B11-U3 / #4174 §5.3）：任何 >1 在 Settings 层即拒。
    stp_jira_handoff_dec_extract_max_concurrent: int = Field(default=1, ge=1, le=1)
    stp_jira_handoff_dec_extract_lock_dir: str = Field(
        default="/tmp/stp-jira-handoff-dec-extract"
    )

    @field_validator(
        "stp_jira_handoff_export_root",
        "stp_jira_handoff_vendor_path_prefix",
        "stp_jira_handoff_dec_extract_lock_dir",
        mode="before",
    )
    @classmethod
    def _strip_paths(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


@lru_cache(maxsize=1)
def get_jira_handoff_export_settings() -> JiraHandoffExportSettings:
    return JiraHandoffExportSettings()


def reset_jira_handoff_export_settings_cache() -> None:
    get_jira_handoff_export_settings.cache_clear()
