"""分域 Settings 基类（ADR-0042 v1.0）。

**来源约束**：`env_file=None` —— 只读 `os.environ`，不启用 pydantic-settings 自带的
dotenv 加载。生产 `.env.backend` / `backend/.env` 的加载与优先级仍由
:mod:`backend.core.env_source` 独家负责（进程 env 最优先、`TESTING=1` 跳过）；
本层只是「已进入 os.environ 的那份配置」的类型化视图。

**越界即忽略**：`extra="ignore"` —— os.environ 里与本域无关的键不参与校验，
也不会因为别的域新增变量而让本域报错。
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class DomainSettings(BaseSettings):
    """分域 Settings 的公共基类（约束见模块 docstring）。"""

    model_config = SettingsConfigDict(
        env_file=None,       # 硬约束：不引入第二个 dotenv 来源
        extra="ignore",      # 非本域变量不参与校验
        case_sensitive=False,
    )
