# Plan 双必填 + GENERIC 哨兵 + ops 词条（ADR-0029 P1-B2）

Status: implemented
Class: feature

P1-B 第二段：Plan 的 project + specialty 从可空手选变为**双必填**——PlanListPage
那个「永远筛不出东西」的 specialty 筛选器接上线（新增 ops 词条）；NULL 不再
存在（GENERIC 是显式「不限」哨兵）；存量 9 行 migration 回填。

## Decision

**1. Migration `e6f7g8h9i0j1`**（down_revision = d5e6f7g8h9i0）

- specialty 新增 `ops`（运维）词条（ON CONFLICT DO NOTHING）
- 新建 GENERIC 项目（「通用（不限项目）」，USER）——运维型 Plan（刷机/装
  APK）的显式哨兵，「不归属」必须显式表达，NULL 不再存在
- 存量回填：`plan.project_id IS NULL → GENERIC`；specialty 按名称关键词
  推断（%MTBF%→mtbf、%Monkey%→monkey、%power%→power-cycle、否则 ops）
- `plan.project_id / specialty_id` 收 **NOT NULL**（生产强制）

**2. API 双必填（schema 层）**

- `PlanCreate` / `PlanUpdate` / `PlanChainTailCreate` 的 `project_key` /
  `specialty_key` 均为必填 str——归属**不可清除**（GENERIC 是显式不限）；
  更新必含两者（fields_set 语义保留：未变化不发、变化才审计）
- append-chain-tail 端点创建链尾 Plan 同步接 `_resolve_project_id/_specialty_id`

**3. ORM 保持可空（注释说明）**：生产 NOT NULL 由 migration 强制，测试
create_all 的宽松 schema 避免重写几十个内联 `Plan(...)` fixture——API 层
已双必填，行为一致性由 schema 保证。

**4. 前端**：Plan 编辑下拉 option 文案改「请选择」（必填语义）+ title 提示
「影响后续派发，不影响已有历史 Run（快照语义）」「运维型 Plan 选『通用
（不限项目）』」；`usePlanEditForm` 提交时双必填校验（toast 引导）；update
payload 归属字段不再有 `|| null`（不可清除）。

## Alternatives

- **PlanUpdate 归属可清除（保留 null 语义）**：与「NULL 只剩迁移瞬态」的
  不变量冲突——清除 = 静默回到不可归属态。GENERIC 显式表达更健康。
- **ORM 也 NOT NULL**：测试破坏面大（几十处内联 Plan 构造）；schema 层
  必填已覆盖所有写入路径，migration 覆盖生产。次要不一致注释说明。
- **不新增 ops 词条**：刷机/装 APK 类 Plan 无处归类，specialty 筛选器继续
  摆设。词条是字典表一行，成本最低的接线。

## Verification

- `test_plans_api.py` 54 passed：双必填 422、归属变更 + GENERIC 哨兵语义
  （改写原「清除」测试）、append 链尾双必填、乐观锁/权限/legacy 拦截在
  必填校验后照常（404/403/409 顺序不变）
- 全套 161 passed（project_routes / devices / heartbeat / attribution）+
  ruff 全过 + 前端 tsc / 98 vitest / eslint 全绿
- 生产预期：9 行存量 Plan 回填为 GENERIC/ops（或按名推断），plan_run
  派发推断（P0-1）在其上继续工作

## Revisit

P1 剩余：match_models 列 drop（读侧已全切规则表，P1-A 起写侧停写）；
夜间 sweep 评估。P2：项目详情页 JIRA 块改提单记录、风险趋势聚合。
