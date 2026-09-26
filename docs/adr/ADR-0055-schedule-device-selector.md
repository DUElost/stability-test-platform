# ADR-0055：定时回归链的设备选择——固定名单与条件现算两种模式

- 状态：**Accepted** v1.0（2026-09-26 owner 裁决：采纳「条件现算 + 保留固定名单」方向，实施未开始）
- 优先级：P2
- 目标里程碑：M7
- 日期：2026-09-26
- 决策者：owner（2026-09-26）；起草：平台研发组
- 归属域：n/a（定时触发的设备选择面，语义归属表尚无对应概念；实施 PR 首次引用时补登）
- 标签：schedule, plan-chain, device-selection, regression
- 关联：[#2909](https://github.com/DUElost/stability-test-platform/issues/2909)（触发：周期回归链静态快照无刷新）、
  [ADR-0020](./ADR-0020-plan-step-one-shot-migration.md)（`task_schedules` 只触发 Plan）、
  [ADR-0026](./ADR-0026-plan-execution-scaling.md)（prepare 期 `plan_run_target_device` 不可变派发快照）、
  [ADR-0038](./ADR-0038-host-retirement-semantics.md)（主机退役 / 空置意图）、
  [ADR-0048](./ADR-0048-execution-status-semantics-v2.md)（链续跑判定）、
  [#2962](https://github.com/DUElost/stability-test-platform/issues/2962)（设备陈旧度口径）、#107（周期链验收）
- 版本记录：v1.0（2026-09-26）首次提出即裁决（选项对照见 #2909 裁决评论）

## 背景

周期回归链由 `TaskSchedule` 按 cron 触发链头 Plan，后续环由链触发逐环派生。事实（读 `main@9503a58`）：

| 事实 | 出处 |
|---|---|
| 设备集合是 schedule 上存的一份 id 名单 | `backend/models/schedule.py` `device_ids = Column(JSON)` |
| 每个周期原样读取该名单 | `backend/scheduler/cron_scheduler.py:125` |
| 后续环只从父段名单里剔除，从不补进新设备 | `backend/services/plan_chain_trigger.py` `_select_chain_devices` |
| 名单没有任何再生成路径；09-20 实测在线但不在链 79/629（约 12.6%），含放量时被排除、放量后新注册、每主机缺最早 3 台三种形态 | #2909 正文与 09-20 评论 |
| 覆盖差已可观测 | `StabilityChainCoverageGap`（#2909 第 3 问） |

任何一次性名单都会过期；问题的本质是 schedule 保存的是「某一时刻的结果」，而不是「选择的规则」。

## 决策

### D1：schedule 设备选择分两种模式

- **`fixed`（固定名单）**：即现行 `device_ids` 语义，逐字不变。用于需要固定对照组的回归（跨 run 样本一致、趋势可比）。
- **`selector`（条件现算）**：schedule 保存选择条件，每次 cron 触发链头时从全机队现算设备集合。
- 模式是 schedule 的显式字段；**存量 schedule 全部迁移为 `fixed`**，不做自动转换。改为 `selector` 由人显式操作。

### D2：`selector` 的条件面与固定健康门

- **可配置条件**（AND 组合，至少一项）：主机集合、`project_key`、设备标签（tags）。需要「新设备先确认再入链」时，用标签条件做准入（人给设备打标签即确认）。
- **固定健康门**（不可关闭）：设备 `ONLINE`；所属主机未退役（ADR-0038 D1）、不在维护窗、未置「空置」意图（ADR-0038 D9，#3159 落地后接入）；设备不在陈旧期（#2962 的陈旧度口径落地后接入）。
- 现算只发生在**链头触发**时刻；链内后续环仍按 `_select_chain_devices` 从父段派生，本 ADR 不改链内语义，也不改续链判定（ADR-0048）。

### D3：现算结果仍是不可变派发快照

- `selector` 现算出的设备集合在 prepare 期照常固化为 `plan_run_target_device`（ADR-0026），QUEUED 期间不随机队漂移。
- 链头 run 的 `run_context` 记录：所用条件、现算命中数、被健康门排除的设备与原因。
- 相对上一周期的增减（新入、移出）在 run 详情可见，使「样本变了」这件事可查，不影响趋势解读时的归因。

### D4：不做什么

- 不做「名单 + 后台定期重算写回」（#2909 选项 B）：它仍要定义现算判据，又多一个无人确认就改名单的写回任务。
- 不改变 `fixed` 模式下任何现行行为；不改动 `StabilityChainCoverageGap` 口径（`selector` 模式下覆盖差预期趋近 0，告警照常作为回归信号）。

## 备选方案与权衡

| 方案 | 结论 | 理由 |
|---|---|---|
| A 维持名单，靠覆盖差告警人工更新 | 不采纳为终态 | 名单必然再次过期；漏入链要等告警才发现；保留为 `fixed` 模式 |
| B 名单 + 定期自动重算 | 否决 | 等于做了 C 的判据却多出写回与审计面；两次重算间仍漂移 |
| C 按条件现算，保留固定名单 | **采纳** | 从规则层消除过期；固定对照组需求由 `fixed` 模式承接 |

代价（已接受）：`selector` 模式下被测集合随机队变化，跨 run 趋势对比不再是同一批样本——由 D3 的增减记录承担可解释性；需要严格对照的回归继续用 `fixed`。

## 影响

- 数据模型：`task_schedules` 增模式字段与条件字段（Alembic 迁移，存量回填 `fixed`）。
- API / 前端：schedule 创建与编辑界面增加模式选择与条件编辑；`frontend/src/utils/api/types.ts` 同步。
- 调度：`cron_scheduler._fire_schedule` 按模式取设备集合；健康门复用现有主机退役 / 维护 / 空置判据，不另立第二套。

## 落地与后续动作

1. ⏳ 实施（#2909 承接）：迁移 + 模式字段 + 现算函数（纯函数，单测覆盖健康门每一项单独不满足即排除）+ API/前端 + `run_context` 记录。
2. ⏳ #3159（空置意图）与 #2962（陈旧度口径）落地后，各自接入 D2 健康门。
3. 复议触发器：`selector` 模式上线后出现「现算集合在两周期间大幅波动导致回归结论不可解释」的实例，或需要按条件以外的维度（如型号配额）选样时，修订本 ADR。

## 关联实现/文档

- `backend/models/schedule.py`、`backend/scheduler/cron_scheduler.py`、`backend/services/plan_chain_trigger.py`
- 裁决记录：`docs/notes/architecture/2026-09-26-tier-b-issue-decisions.md`
