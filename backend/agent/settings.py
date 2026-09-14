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

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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


def reset_agent_settings_caches() -> None:
    """清 Agent 侧 Settings 缓存。

    调用点：`main.py` 的 `reload_config` 分支在 `_reload_runtime_env()` 之后
    （hot-update 改写 `.env` → 重读 → 清缓存 → 新值对续租器生效）。
    """
    get_lease_settings.cache_clear()
