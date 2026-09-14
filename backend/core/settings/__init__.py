"""分域 Settings 包（ADR-0042）。

约定（v1.0 裁决的硬约束，各域必须遵守）：

1. **只读 `os.environ`**：`env_file=None`——`.env` 的来源与优先级仍由
   :mod:`backend.core.env_source` 独家决定，禁止出现第二个来源解析器；
2. **名字不变**：字段名 snake_case 与既有 env 名（大写）一一对应，不改名、不加前缀；
   别名（如兼容旧名）用 `validation_alias` = `AliasChoices(...)` 显式声明；
3. **惰性访问**：各域提供 `get_<domain>_settings()`（`lru_cache` 包裹）与
   `reset_<domain>_settings_cache()`；不得在 import 时固化取值（D4）；
4. **迁移判据**（D2）：域内 ≥3 旋钮 / 默认值与类型转换重复 ≥2 处 / 需跨字段校验
   才迁移；不满足判据的域保持裸读（继续受 env_inventory 门禁约束）；
5. **可见性不退化**（D6）：Settings 字段会被 `tools/dev/env_inventory.py` 解析进
   环境变量清单，新增字段同样受「登记 ∪ 内部声明」二选一约束。
"""

from backend.core.settings.base import DomainSettings

__all__ = ["DomainSettings"]
