# ADR-0060: 计划参数的含义与生效值——单一投影与说明登记表
- 状态：Proposed v0.1（2026-10-09 起草；Owner 同日同意方向「方案乙」，正文待裁决）
- 版本记录：v0.1 2026-10-09 起草（[#3637](https://github.com/DUElost/stability-test-platform/issues/3637)）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-10-09
- 决策者：Owner（方向已同意，正文待裁决）；起草：Claude Code（本地工作面）
- 标签：Plan, 参数, 前端, 可理解性, 投影
- 归属域：n/a（新概念「计划参数的说明与生效值投影」；Accepted 后在语义归属索引新增 key。参数值的分层仍归 ADR-0029 D1 / D4）

> 规则句按 ADR-0059 D3 只用「必须 / 不得 / 建议 / 可以」，并写出执行者。

## 1. 背景

### 1.1 问题

UI 人类可达性审查（2026-10-09，未跟踪稿
`docs/reviews/UI_HUMAN_OBSERVABILITY_REVIEW_2026-10-09_89b9dc8_claude.md`）发现：
用 API 的自动化 agent 能拿到完整的参数事实，人在 UI 上拿不到。Owner 据此确定 UI 的优先级：
「确认与修改执行步骤中的参数与设置」「执行测试前给出测试计划的各类参数及其含义」。

**生效值：4 个视图，4 种口径。** 基线 `main@10289498`：

| 位置 | 显示什么 | 与实际执行的差异 |
|---|---|---|
| 编辑器检查器 `components/pipeline/PlanStepInspector.tsx`（`ParamFormCard`） | `step.params > default_params > schema.default` | 与派发一致；不含派发注入 |
| 执行页步骤列表 `components/execution/plan-execute/PlanStepList.tsx` | 只有脚本 `default_params` | 漏掉步骤级覆盖与 schema.default |
| 执行前确认 `components/execution/plan-execute/DispatchCockpit.tsx` | 只用了预览返回的设备数 | 预览已算出合并后的 lifecycle，前端丢弃 |
| 运行快照 `components/plan-run/PlanSnapshotDrawer.tsx` | `default_params` 与 `param_schema` 两段原始 JSON | 快照存了步骤 `params`，不显示 |

**实际执行值的组成：**

- 后端用 `backend/services/script_params.py` 的 `merge_effective_params` 合并
  （`schema.default` → `default_params` → `step.params`）；
- 物化时再做两处注入（`backend/services/plan_dispatcher_sync.py:1142-1144`）：
  - `inject_wifi_params`：本次所选 WiFi 池写进 `connect_wifi` / `monkey_setup`；
  - `inject_suite_params`：绑定套件写进 `mtbf_*`；
- 主机级 watcher 管控还会在派发时把 `watcher_policy.enabled` 改成 false
  （`plan_dispatcher_core.py` 的 `apply_dispatch_host_watcher_admin_state_to_policy`）。

运行预览（`preview_plan_dispatch_sync`）不做以上注入。

**含义：没有可维护的事实源。**

- 参数说明只存在于各脚本版本的 `param_schema.label / description`。
  种子迁移中只有 flash_firmware、oobe_skip、flash_preflight、fill_storage、connect_wifi 等少数族写了说明，
  monkey / mtbf / gpu / sleep / powercycle 等族基本没有。
- 已发布版本的 `param_schema` 不可原地改：种子迁移对被 `plan_step` 引用的版本直接失败
  （`docs/development/script-versioning.md`「种子迁移治理」）。补一句说明，就要发新版本、再重指 Plan。

**Plan 级设置：只能经 API 写，UI 不可见。**

- 字段：`barrier_timeout_seconds`、`barrier_max_wait_seconds`、`watcher_policy`、`auto_archive_interval_seconds`，
  以及步骤 `stall_seconds`。`PlanCreate` / `PlanUpdate` 接受这些字段，编辑器不展示。
- barrier 预算的误配有误杀前科（#872 / #174 / #2948）：
  按 `backend/models/plan.py` 的注释，含长耗时前置步骤（自动刷机等）的 Plan 必须抬高该预算。
- Plan 级字段的说明散落在各页手写文案里。#3635 之前，`timeout_seconds` 被三处叫作「超时」，
  驾驶舱还解释成「整个 PlanRun 超时后中止」；它实际是自各设备 init 完成起计的巡检时长，到点算成功。

### 1.2 约束

- **ADR-0029 D1 / D4**：参数值的分层，即脚本默认 → 步骤覆盖（项目变量层挂起）。本 ADR 不改值的分层。
- **ADR-0051 D1**：发布单元不可原地修改。脚本族树（`backend/agent/scripts/<name>/`）改动必须登记新版本。
- **硬不变量**：
  - Plan 不存 lifecycle，dispatcher 从 PlanStep 与 Plan 时间字段组装；
  - 前端 API 类型以 `frontend/src/utils/api/types.ts` 为入口，与后端 schema 同步。
- **ADR-0023 D4**：运行快照抽屉的数据源是 `plan_snapshot`。
- **ADR-0041**：站点独立交付。说明若放在各站点数据库里，会随站点各自漂移。

## 2. 决策

### D1 生效值只由后端计算

1. 后端必须提供「生效计划视图」投影。投影由 `merge_effective_params` 与现有组装函数
   （`build_lifecycle_from_steps` / `build_lifecycle_from_snapshot`）产出；实现者不得另写一套合并逻辑。
2. 投影必须对每个步骤给出：
   - 阶段、step_key、脚本 @ 版本、启用状态，以及步骤级时限（`timeout_seconds` / `stall_seconds` / `retry`）；
   - 对每个参数键：生效值、来源与含义（按 D2 解析）。来源取四值之一：
     `schema_default` / `script_default` / `step_override` / `dispatch_injection`。
3. 派发注入在投影中必须标为 `dispatch_injection`，并写明注入来源（WiFi 池 / 绑定套件 / 主机 watcher 管控）。
   在派发前（编辑、预览）无法确定的注入值，投影必须写明「派发时注入」，不得留空不提。
4. 投影必须对敏感参数掩码（WiFi 密码及 D2 登记为 `sensitive` 的键）。前端不得展示这些键的明文。
5. 已保存的 Plan、执行前确认与运行快照这三个消费面，前端必须读投影，不得自行合并参数。
6. 编辑中未保存的草稿是例外：编辑器可以在本地合并以即时回显。但前后端必须共用同一份一致性测试夹具
   （输入 schema / default_params / step.params，期望生效值），由 pytest 与 vitest 同时消费。
   夹具不一致时以后端为准。
7. 运行快照的投影必须从 `plan_snapshot` 重建，不得回读当前 PlanStep 或 Script 行。这与 ADR-0023 D2 的口径一致。

### D2 参数说明登记表：与脚本发布单元解耦

1. 参数「含义」的事实源是平台侧「参数说明登记表」。它以代码文件形式放在仓库内、脚本族树与
   `tool_manifest.json` 之外，按脚本族存放，例如 `backend/schemas/param_docs/<script_name>.json`。
   路径由实施批次确定。
2. 登记表的键是 `(script_name, param_key)`。条目内容：
   - 必须有中文标签与含义（白话，写明对设备做什么）；
   - 可以有单位、注意事项、`sensitive` 标记，以及适用版本范围（同一键在不同版本含义不同时使用）。
3. 投影解析说明时必须按以下顺序回落：登记表 → 该版本 `param_schema` 的 label / description → 参数键名。
4. 说明是文档，不是执行语义。说明不得参与派发、校验与参数合并，不得写进 `plan_snapshot` 或数据库。
   历史运行展示当前说明；条目有版本范围时按运行时的版本取说明。
5. 维护者修改说明必须走普通 PR。修改说明不得要求发脚本新版本或做数据迁移。
6. 实施者必须为登记表提供结构校验（单元测试）。键是否仍存在于对应脚本参数中，校验口径由实施批次确定。
   维护者不得为此新增 CI 门禁（ADR-0059 D5）。

### D3 Plan 级设置登记表

1. 以下字段必须在后端登记：`patrol_interval_seconds`、`timeout_seconds`（展示名「巡检时长」）、
   `barrier_timeout_seconds`、`barrier_max_wait_seconds`、`auto_archive_interval_seconds`、`watcher_policy`
   （含 `enabled` / `paths` / `required_categories` / `on_unavailable` 等子键），以及步骤级的
   `timeout_seconds` / `stall_seconds` / `retry` / `enabled`。
2. 每项必须写明以下内容，含义必须指向源码语义所在位置：
   - 标签与含义；
   - 单位；
   - 未设置时的实际行为（回落链）。例如步骤墙钟回落 `STP_STEP_WALL_CLOCK_SECONDS`，再回落 300s；
     barrier 回落 `STP_BARRIER_TIMEOUT_SECONDS`，再回落 600s；
   - 写入边界（与 `PlanCreate` / `PlanUpdate` 一致）；
   - 是否允许在 UI 编辑。
3. 投影必须输出 Plan 级设置的当前值、未设置时的生效值与含义。
4. 前端不得再硬编码这些说明。`frontend/src/components/pipeline/planTiming.ts`（#3635）是过渡实现：
   登记表上线后，实施者必须改为读投影，并删除该模块中的说明文案。这是它的终态出口。

### D4 UI 消费规则

1. 编辑器、执行前确认、运行快照必须用同一个「参数清单」组件展示投影。每个参数显示三样：生效值、来源、含义。
2. 执行前确认必须展示将要执行的步骤与生效参数，派发注入按 D1-3 标注。这一步替代现有「只显示设备数」的预览确认。
3. 运行快照必须展示生效参数，不得只展示 `default_params`。原始 JSON 可以保留为折叠的排障视图。
4. Plan 级设置在编辑器中必须先以只读形式展示，包括当前值、未设置时的行为与含义。
5. UI 开放编辑 Plan 级设置时，必须做到：
   - 校验与后端写入边界一致；
   - 该编辑单元按 ADR-0058 走独立复核；
   - `watcher_policy` 只开放登记表定义的子键，不得开放自由 JSON。

### D5 不做的事

- 本 ADR 不改参数值的分层（ADR-0029），不改派发与 Agent 执行，不改脚本版本不可变规则（ADR-0051）。
- 说明不进数据库，不建 UI 内的说明编辑页。

## 3. 备选方案与权衡

| 方案 | 做法 | 不选的原因 |
|---|---|---|
| 甲 | 说明写进各脚本版本的 `param_schema` | 被引用版本不可原地改：补一句说明要发新版本、改指 Plan、走激活闸门，并加重版本膨胀（#735）。说明是文档，不应绑定执行发布单元 |
| 丙 | 说明存数据库，由管理页编辑 | 需要迁移、权限、审计；按 ADR-0041 各站点独立，说明会随站点漂移；说明与参数代码脱节。复议触发见 §5 |
| 丁 | 维持前端各页手写说明（现状） | 已证明会分叉：「超时」三处同名、一处解释错误；四个参数视图四种口径 |
| 前端合并生效值 | 各页在前端重算 | 第二计算者必然漂移；派发注入只有后端知道。只给编辑中的草稿留例外，并用共享夹具约束（D1-6） |

## 4. 影响

- **后端**：新增投影函数与只读出口（形态由批次确定，例如已保存 Plan 的解析视图、扩展预览、运行快照解析视图）。
  不改派发、不改 Agent、无数据迁移。
- **前端**：参数清单组件三处复用；删除执行页 `default_params` 视图与快照抽屉的原始 JSON 主视图；`types.ts` 同步。
- **内容**：登记表首批覆盖按 `plan_step` 引用量排序的高频脚本族。含义必须由熟悉脚本的人复核，
  范围与人选由批次确定。
- **回退**：全部改动在展示层与只读出口，可以逐单元回退；说明登记表回退不影响执行。

## 5. 落地与后续动作

- **批次**（ADR-0058）。单元草案，以批次方案为准：
  - U1：后端投影与 Plan 级设置登记表，含一致性夹具；
  - U2：参数清单组件与执行前确认；
  - U3：编辑器（参数清单 + Plan 级设置只读区）；
  - U4：运行快照；
  - U5：高频脚本族说明内容；
  - U6：Plan 级设置开放编辑（必须复核）。
- **核心验收（所见即所跑）**：对同一 PlanRun，投影的生效值必须等于物化后各 Job `pipeline_def` 中的对应参数值。
  派发注入键按 D1-3 单独比对。
- **复议触发**：
  - 说明需要非开发人员高频编辑时，重议方案丙；
  - 出现第三类派发注入时，扩展 D1 的来源枚举；
  - 登记表与脚本参数键持续漂移时，收紧 D2-6 的键存在性校验。

## 6. 关联实现与文档

- 审查稿：`docs/reviews/UI_HUMAN_OBSERVABILITY_REVIEW_2026-10-09_89b9dc8_claude.md`（A3 / A4 / A5 / A6 / A7、§8）
- 载体 issue：#3637
- 已完成的单点修复：
  - #3630（脚本库死链）；
  - #3634（参数说明常显）；
  - #3635（巡检时长改名，`planTiming.ts` 为 D3-4 所述过渡实现）；
  - #3636（异常采集降级提示）
- 相关 ADR：
  - ADR-0020（Plan-Step 模型）；
  - ADR-0023（脚本溯源，D2–D4 观测面与快照抽屉）；
  - ADR-0029（参数分层）；
  - ADR-0051（发布单元不可变）；
  - ADR-0058（批次交付）；
  - ADR-0059（写作约定）
- 代码锚点：
  - `backend/services/script_params.py`；
  - `backend/services/plan_dispatcher_core.py`；
  - `backend/services/plan_dispatcher_sync.py`；
  - `frontend/src/components/pipeline/PlanStepInspector.tsx`；
  - `frontend/src/components/execution/plan-execute/{PlanStepList,DispatchCockpit}.tsx`；
  - `frontend/src/components/plan-run/PlanSnapshotDrawer.tsx`
