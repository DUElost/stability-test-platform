"""Agent 侧分域 Settings（ADR-0042 P1 试点：租约续期域）。

与后端 `backend/core/settings/` 的分域 Settings **同口径**，但**自包含**：
Agent 进程不一定携带/安装 `backend.core`（见 `backend/agent/aee/reconciler.py`
的说明），因此本模块只依赖 `pydantic-settings`，不 import 后端包。

口径（ADR-0042 v1.0）：

1. **只读 `os.environ`（`env_file=None`）**：Agent 侧 `.env` 的加载与优先级由
   安装布局 + `main._reload_runtime_env()` 决定，本层不引入第二个来源解析器；
2. **名字不变**：字段名 snake_case ↔ 既有 env 名大写（`AGENT_LEASE_TTL` →
   `agent_lease_ttl`），不改名、不加前缀；
3. **热更新**：`reload_config` 路径重读 `.env` 后必须调用
   :func:`reset_agent_settings_caches`，否则缓存吞掉新值；
4. **凭据不入表**：`AGENT_SECRET` 等凭据保持裸读（本层只承载可调旋钮）。
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def _tolerant_pct(raw: object, default: float, name: str) -> float:
    """宽容百分比解析（#1710 语义）：缺失/非法/非有限/越界 → 默认值 + WARNING。"""
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return default
    try:
        value = float(text)
    except (TypeError, ValueError):
        logger.warning("invalid %s=%r; using default %.1f", name, text, default)
        return default
    if not math.isfinite(value) or not 0.0 <= value <= 100.0:
        logger.warning("out-of-range %s=%r; using default %.1f", name, text, default)
        return default
    return value


def _tolerant_positive_int(raw: object, default: int, name: str) -> int:
    """宽容正整数解析：缺失/非法/非正 → 默认值 + WARNING。"""
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return default
    try:
        value = int(text)
    except (TypeError, ValueError):
        logger.warning("invalid %s=%r; using default %d", name, text, default)
        return default
    if value <= 0:
        logger.warning("non-positive %s=%r; using default %d", name, text, default)
        return default
    return value


def _tolerant_seconds(raw: object, default: float, name: str) -> float:
    """宽容秒数解析（只要求有限；0/负值由调用点语义决定，不在此拦截）。"""
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return default
    try:
        value = float(text)
    except (TypeError, ValueError):
        logger.warning("invalid %s=%r; using default %.1f", name, text, default)
        return default
    if not math.isfinite(value):
        logger.warning("non-finite %s=%r; using default %.1f", name, text, default)
        return default
    return value


class DiskArchiveSettings(BaseSettings):
    """磁盘监控与日志归档域（ADR-0042 P2 #2）。

    **失败形态与迁移前逐旋钮对齐**（等价性优先）：

    - `STP_HDD_SPILL_CRITICAL_PCT` / `_CRITICAL_BATCH` / `_CATCHUP_INTERVAL`：
      迁移前是**宽容解析**（#1710：非法值只告警并回落默认，不得拖垮 Agent 启动）
      → `mode="before"` validator 保留同一语义与告警文案；
    - 其余五个：迁移前 `float(os.getenv(..., "…"))` 直转（非法值启动即失败）
      → 保持严格类型（pydantic `ValidationError` 即等价的失败面）。
    """

    model_config = SettingsConfigDict(
        env_file=None,       # 硬约束：不引入第二个 dotenv 来源
        extra="ignore",      # 非本域变量不参与校验
        case_sensitive=False,
    )

    # ── 宽容组（#1710 / #741 / #1522）──
    stp_hdd_spill_critical_pct: float = 98.0
    stp_hdd_spill_critical_batch: int = 100
    stp_hdd_spill_catchup_interval: float = 30.0

    # ── 严格组（main.py 直转）──
    stp_local_disk_monitor_interval_seconds: float = 300.0
    stp_local_disk_spill_threshold: float = 80.0
    stp_local_disk_spill_target: float = 70.0
    stp_log_archive_interval_seconds: float = 3600.0
    stp_log_archive_grace_seconds: float = 1800.0

    @field_validator("stp_hdd_spill_critical_pct", mode="before")
    @classmethod
    def _v_critical_pct(cls, value: object) -> float:
        return _tolerant_pct(value, 98.0, "STP_HDD_SPILL_CRITICAL_PCT")

    @field_validator("stp_hdd_spill_critical_batch", mode="before")
    @classmethod
    def _v_critical_batch(cls, value: object) -> int:
        return _tolerant_positive_int(value, 100, "STP_HDD_SPILL_CRITICAL_BATCH")

    @field_validator("stp_hdd_spill_catchup_interval", mode="before")
    @classmethod
    def _v_catchup(cls, value: object) -> float:
        return _tolerant_seconds(value, 30.0, "STP_HDD_SPILL_CATCHUP_INTERVAL")


class LeaseSettings(BaseSettings):
    """租约续期域旋钮（env 名 = 字段名大写）。"""

    model_config = SettingsConfigDict(
        env_file=None,       # 硬约束：不引入第二个 dotenv 来源
        extra="ignore",      # 非本域变量不参与校验
        case_sensitive=False,
    )

    # 续期请求的 HTTP 重试（原实现 `max(int(...), 1)`，钳制留在调用点）
    agent_post_retries: int = 3
    agent_post_retry_base_delay: float = 1
    # 续租线程节奏（秒）
    agent_lock_renewal_interval: int = 60
    # 单次续期请求覆盖的 job 分块大小（原实现 `max(int(...), 1)`）
    agent_lease_extend_batch_chunk: int = 100
    # 租约 TTL（秒）：默认对齐后端 lease_manager.py:_DEFAULT_LEASE_SECONDS = 600
    agent_lease_ttl: int = 600


@lru_cache(maxsize=1)
def get_lease_settings() -> LeaseSettings:
    """取租约域 Settings（惰性 + 缓存；不读 `.env` 文件）。"""
    return LeaseSettings()


@lru_cache(maxsize=1)
def get_disk_archive_settings() -> DiskArchiveSettings:
    """取磁盘监控与日志归档域 Settings（惰性 + 缓存；不读 `.env` 文件）。"""
    return DiskArchiveSettings()


def reset_agent_settings_caches() -> None:
    """清 Agent 侧**全部** Settings 缓存（ADR-0042 P1 Note 预告的多域扩展）。

    调用点：`main.py` 的 `reload_config` 分支在 `_reload_runtime_env()` 之后
    （hot-update 改写 `.env` → 重读 → 清缓存 → 各域新值生效）。
    """
    get_lease_settings.cache_clear()
    get_disk_archive_settings.cache_clear()
