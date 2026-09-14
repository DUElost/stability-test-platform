# ADR-0042 D6 落地：env_inventory 解析 Settings 字段（防可见性回退，#737）

Status: implemented
Class: architecture

## Decision

[ADR-0042](../../adr/ADR-0042-settings-convergence-and-bare-read-boundary.md) 的 **D6**：
`tools/dev/env_inventory.py` 增加 **AST 解析 Settings 类字段**，让「收敛到 Settings」
不会变成新的不可见面——Settings 化的读取名仍进同一份清单与门禁。

识别与取值口径（写入工具 docstring）：

| 项 | 规则 |
|---|---|
| Settings 类判据 | 类基名以 `Settings` 结尾（含 `BaseSettings` 及 `*Settings` 子类命名惯例），**或** 类体含 `model_config = SettingsConfigDict(...)` |
| env 名来源 | `validation_alias` / `alias`（字符串）；`AliasChoices("A","B")` 取全部字符串参数；缺省回落 `env_prefix + 字段名.upper()`（`env_prefix` 取 `SettingsConfigDict(env_prefix=...)`，默认空） |
| 默认值 | `Field(...)` 的 `default=` 或位置第一参数字面量；无则 `-`（与行扫描口径一致） |

与行扫描（`os.getenv` 等）**合并进同一 `reads` 结构**——因此 Gate 的二选一裁决、
语义比较（名称/默认值/登记状态/类别）与附录渲染全部自动适用，无需第二套逻辑。

## Alternatives

- **正则解析 Settings 类**：`Field(...)` 常跨多行、`AliasChoices` 嵌套，正则易碎；AST 一次解析
  确定性更强、可单测——采用 AST；
- **把 Settings 字段排除在清单外（认为「有类型就不用管」）**：否决——清单的价值是
  「新增读取必须二选一」，排除即制造盲区，且运维仍需知道这些 env 名存在；
- **只认 `BaseSettings` 字面基名**：否决——本仓将有 `*Settings` 子类（如 `_SchedulerSettings`），
  以及基类间接继承的形态，按后缀判定成本低、覆盖好；
- **首版判据「类体有 `model_config = <任意 Call>` 即 Settings」**：**实测红**——
  本仓 130+ 个 pydantic `BaseModel` 都用 `model_config = ConfigDict(...)`，首版把它们的
  字段名（`DEVICE_ID`/`STATUS`/…）全当 env 名混入（清单 210 → 340+）。已收紧为
  **只认 `SettingsConfigDict`**——这正是 D6「先让门禁看见，再迁移」的次序价值：
  误判在门禁上立刻可见，而不是等试点时才发现。

## Verification

- `pytest tests/test_env_inventory.py` → **9 passed**（新增 3 例：Settings 字段含
  `validation_alias`/`alias`/`AliasChoices`/`env_prefix` 的取值、`scan_reads` 合并、
  **负例**「普通 `BaseModel` + `ConfigDict` 不得被扫」）；
- **端到端变异**（在 `backend/core/` 放真实 Settings 类）：
  ① 未登记 → 门禁红并列出 `ZZT_WINDOW_SECONDS` / `ZZT_LEGACY_A` / `ZZT_LEGACY_B`（`exit=1`）；
  ② `--write` → 附录可见（前缀名 42、别名共享默认 `x`）；
  ③ 删除探针 → 回到 210 名一致（`exit=0`）；
- `pytest tests/` → **334 passed**；`run_gates.py check:quick` → **10 gates 全绿**；
- `ruff check`（工具+测试）→ All checks passed；
- 文档附录口径已同步（覆盖范围补 Settings 字段与四类来源）。

## Revisit

- **P1 试点**落地后：若真实 Settings 类使用 `SettingsConfigDict(env_nested_delimiter=...)`、
  `case_sensitive` 等影响名字枚举的配置，按实际写法扩展判据（名字枚举只关心
  「会绑定哪些 env 名」，嵌套分隔符会影响嵌套字段名，需要示例驱动）；
- **误判面**：目前只认 `SettingsConfigDict`；若将来出现自定义 BaseSettings 封装
  （如 `class _Base(SettingsConfigDict…)` 间接写法），按试点实况补判据；
- **`_INTERNAL_ONLY`/示例登记**：Settings 字段同样受二选一约束——P1 迁移时按域登记或声明。
