# 种子迁移治理语义裁决：脚本 default_params 覆写与无引用停用（#942）

- **状态**：Proposed——**待裁决**（三选一见 §3；裁决后按 §5 拆实现单，本 note 不关闭 #942）
- **日期**：2026-09-08
- **来源**：R03 台账 #945 的 R03-F10（设计风险）；台账标注「交接 R07」
- **关联不变量**：AGENTS.md 硬不变量「已存在脚本版本的 `default_params` 不可原地修改；参数变化通过新版本表达」

## 1. 问题本质

产品路径（API/UI）对脚本版本有引用保护与不可变约束：`deactivate` 走
`_ensure_script_can_be_deactivated` 引用检查、参数变化必须新建版本。但
**数据迁移里的种子逻辑以裸 SQL 绕过服务层**，同类的保护在迁移内不存在：

| 迁移 | 行为 | 与产品路径的差异 |
|---|---|---|
| `b8c9d0e1f2a3_seed_flash_firmware_v131_params` | `flash_firmware v1.3.1` 已存在时 **UPDATE `default_params`/`param_schema`/`is_active`**（原地覆写既有版本的参数定义） | 产品路径禁止原地改参数——变化须新建版本 |
| `k1l2m3n4o5p6_seed_gpu_check_v104_monitor_mode` | 新版 INSERT 后按 `deactivate` 列表把旧版 `is_active=false`，**无 plan_step 引用检查** | 产品路径 `_ensure_script_can_be_deactivated` 会拦被引用版本 |

危害是**条件性**的（取决于升级瞬间库中是否有人工改过的参数 / 是否有 Plan
引用旧版），因此台账归类为设计风险而非确定缺陷。但一旦成立，后果是
Plan 的派发参数静默变化（审计与重放失真）或被引用版本停用（无法派发）。

## 2. 约束

- 迁移在升级路径上运行，**不能依赖服务层代码**（alembic env 只连 DB）；
- 迁移必须确定性：同样的库状态必须同样的结果，不允许「有时跳过有时覆写」
  的隐式行为；
- AGENTS.md 不变量的立法意图是保护 **plan_step 的期望**（重放/审计/派发），
  而非把 DB 里的字节当圣物——`plan_snapshot` 在派发时刻冻结，历史 Run 不受
  `default_params` 变化影响。

## 3. 裁决选项

| 选项 | 语义 | 后果 |
|---|---|---|
| **A. 引用感知 + 遇引用即失败**（推荐） | 种子逻辑对「已存在版本」执行覆写/停用前，检查 `plan_step` 是否引用该 `(name, version)`；有引用 → 迁移**失败**并给出明确指引（重指 plan_step 或新建版本）；无引用 → 执行 | 与 API 层 `_ensure_script_can_be_deactivated` 的 409 语义一致；确定性最强；代价是阻塞升级直至人工消解引用（与 API 行为同构，操作者已有心智模型） |
| B. 引用感知 + 跳过并告警 | 有引用 → 跳过该子步骤，`RAISE NOTICE`/日志留痕，升级继续 | 不阻塞升级，但「种子声明」与「库中事实」静默分叉——正是 #942 要消灭的形态 |
| C. 维持现状 + 文档化前置条件 | 承认种子迁移是**受控例外**，在 script-versioning.md 写明升级前置（升级前核对引用与参数） | 零代码；防护靠人，新种子迁移会继续复制裸 SQL 模式 |

## 4. 推荐：A

- 与既有护栏**同构**（API 409 ↔ 迁移失败），不引入第二套语义；
- 迁移哲学上「声明未满足即失败」优于静默跳过（跳过正是 #942 指控的形态）；
- 阻塞升级的代价真实但可控：出现频率 = 「有 Plan 引用旧版且触发种子迁移」，
  而种子迁移只在新版本发布时出现一次；指引信息直接给出消解路径。

## 5. 实现计划（裁决后拆单）

1. 治理模板权威源落 `backend/services/script_seed_governance.py`
   （`raise_if_version_referenced` / `raise_if_any_version_referenced`）；
   未来的种子迁移按**迁移自包含原则**把模板当前实现**内嵌**进迁移文件
   （不 import 服务层——迁移是冻结的历史，服务层会演进），义务条文写入
   `docs/development/script-versioning.md`「种子迁移治理」节；
2. 两个既有迁移**不改**（已在生产执行过，重写历史迁移违反 ADR-0008；其
   已产生的影响属现状数据）；辅助模块仅约束**未来的**种子迁移；
3. `docs/development/script-versioning.md` 增「种子迁移治理」节：引用检查
   义务 + 模板（必须走辅助模块，禁止裸 UPDATE/UPDATE is_active）；
4. 带数据迁移测试（testcontainers 自起容器，同
   `tests/test_alembic_upgrade.py` 模式）：有引用 → 断言迁移失败且指引可
   读；无引用 → 断言覆写/停用执行成功。

## 6. 交接 R07

- R07 的迁移治理专项（ADR-0008 后续）应把「种子迁移引用检查义务」纳入
  迁移模板 checklist；本 note 为其提供裁决输入。
