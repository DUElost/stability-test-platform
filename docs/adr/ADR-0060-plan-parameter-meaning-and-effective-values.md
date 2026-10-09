# ADR-0060: 计划参数的含义与生效值——分层投影与说明登记表
- 状态：Proposed v0.2（2026-10-10 按独立复核 R1-1～R1-4 修订；方向「方案乙」不变，正文待复核与 Owner 裁决）
- 版本记录：
  - v0.1 2026-10-09 起草（[#3637](https://github.com/DUElost/stability-test-platform/issues/3637)）。
  - v0.2 2026-10-10 按 #3638 独立复核修订：
    - R1-1：D1 把「计划基线 / 冻结设计 / 逐 Job 下发事实 / 运行期解析」分为四层，watcher 改为独立策略值，不再算作参数注入；
    - R1-2：D3 定义显示状态五态，主机环境回落不再显示为确定值；
    - R1-3：D4 要求服务端安全投影覆盖全部展示路径与嵌套键，并界定原始载荷的信任边界；
    - R1-4：新增 D5，执行前确认携带配置指纹；
    - R2：不改 D2 语义，记入 §5 的批次测试边界。
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
用 API 的自动化 agent 能拿到完整的参数事实，人在 UI 上拿不到。Owner 据此确定 UI 优先级：
「确认与修改执行步骤中的参数与设置」「执行测试前给出测试计划的各类参数及其含义」。

**参数展示：4 个视图，4 种口径。** 基线 `main@10289498`：

| 位置 | 显示什么 | 问题 |
|---|---|---|
| 编辑器检查器 `components/pipeline/PlanStepInspector.tsx`（`ParamFormCard`） | `step.params > default_params > schema.default` | 与后端合并同口径；不提示派发期才确定的值 |
| 执行页步骤列表 `components/execution/plan-execute/PlanStepList.tsx` | 只有脚本 `default_params` | 漏掉步骤级覆盖与 schema.default |
| 执行前确认 `components/execution/plan-execute/DispatchCockpit.tsx` | 只用了预览返回的设备数 | 预览已算出合并后的 lifecycle，前端丢弃 |
| 运行快照 `components/plan-run/PlanSnapshotDrawer.tsx` | `default_params` 与 `param_schema` 两段原始 JSON | 快照存了步骤 `params`，不显示；原样渲染，没有脱敏 |

**一个参数从配置到执行要经过四个时点，每个时点能确定的事实不同：**

1. **读取时刻**：后端用 `backend/services/script_params.py` 的 `merge_effective_params` 合并
   `schema.default` → `default_params` → `step.params`。运行预览（`preview_plan_dispatch_sync`）只做到这一步。
2. **prepare**（`prepare_plan_run`）：再次读取 Plan 与 PlanStep，冻结为 `plan_snapshot`。
   同时把本次派发的决定冻结进 `run_context`：`wifi_pool_id`、`dispatch_suite`、`dispatch_host_watcher_admin_states`。
3. **物化**（`materialize_jobs_and_allocations`，在准入时，可能远晚于 prepare）：
   - 逐设备分配 WiFi 池（按负载与 `host_group`，`_sync_allocate_devices`），同一 Run 的不同设备可能拿到不同池；
   - `inject_wifi_params` / `inject_suite_params` 只填缺失或空值，已有显式值优先；
   - 生成逐设备不同的 `JobInstance.pipeline_def`，分配事实写入 `ResourceAllocation`。
4. **claim 与运行**：
   - `agent_claim.enrich_job_metadata` 按 host，把冻结的主机 watcher 管控覆盖到 `JobOut.watcher_policy`。
     这是独立下发的策略，不在 `pipeline_def` 里；
   - Agent 运行时按主机环境解析未配置项：
     - 步骤墙钟：`STP_STEP_WALL_CLOCK_SECONDS`，未设则 300s（`pipeline_engine._resolve_step_wall_clock`）；
     - barrier：`STP_BARRIER_TIMEOUT_SECONDS`，未设则 600s，并有进度感知续期（#117，`_resolve_barrier_timeout`）；
     - watcher 策略：代码默认 → `WATCHER_*` 环境 → 下发策略依次合并（`agent/watcher/policy.py`）。

因此不存在一个能同时代表「计划」「冻结设计」「每台设备实际下发」「运行期取值」的单一值。
ADR-0023 的 2026-09-25 裁决已撤销 `run_context.wifi_assignments` 冗余副本：分配事实归 `ResourceAllocation`，
`plan_snapshot` 是派发前设计，不是派发后的每设备凭据。

**含义：没有可维护的事实源。**

- 参数说明只存在于各脚本版本的 `param_schema.label / description`。种子迁移中只有 flash_firmware、oobe_skip、
  flash_preflight、fill_storage、connect_wifi 等少数族写了说明。
- 已发布版本的 `param_schema` 不可原地改：种子迁移对被 `plan_step` 引用的版本直接失败
  （`docs/development/script-versioning.md`「种子迁移治理」）。

**Plan 级设置：只能经 API 写，UI 不可见。**

- 字段：`barrier_timeout_seconds`、`barrier_max_wait_seconds`、`watcher_policy`、`auto_archive_interval_seconds`，
  以及步骤 `stall_seconds`。
- barrier 预算的误配有误杀前科（#872 / #174 / #2948）。
- Plan 级字段的说明散落在各页手写文案里。#3635 之前，`timeout_seconds` 被三处叫作「超时」，
  驾驶舱还解释成「整个 PlanRun 超时后中止」。

**执行前确认与真实派发分两次读取 Plan。** 预览与 prepare 各自读取 Plan / PlanStep。
`PlanRunTrigger` 不携带任何版本标识，所以两次读取之间 Plan 被修改时，确认页看到的与实际派发的可能不同。

### 1.2 约束

- **ADR-0029 D1 / D4**：参数值的分层，即脚本默认 → 步骤覆盖（项目变量层挂起）。本 ADR 不改值的分层。
- **ADR-0051 D1**：发布单元不可原地修改；脚本族树（`backend/agent/scripts/<name>/`）改动必须登记新版本。
- **ADR-0023**：
  - D2 / D4：观测面与快照抽屉从 `plan_snapshot` 派生，不回读当前 PlanStep；
  - D5 撤销：WiFi 分配事实归 `ResourceAllocation`；
  - 原 D5 写明「`JobInstance.pipeline_def` 持有运行所需的实际值（仅 Agent 可读）」。
- **#955**：面向普通用户的资源池列表剥除 password 等机密（`public_pool_config`）。
- **硬不变量**：
  - Plan 不存 lifecycle，dispatcher 组装；
  - 前端 API 类型以 `frontend/src/utils/api/types.ts` 为入口，与后端 schema 同步。
- **ADR-0041**：站点独立交付。说明若放在各站点数据库，会随站点漂移。

## 2. 决策

### D1 生效值分四层，后端是唯一计算者

1. 后端必须按以下四层提供投影。每个值必须标注所在层，跨层的值不得写进同一个字段：

   | 层 | 内容 | 权威来源 | 主要消费面 |
   |---|---|---|---|
   | L1 计划基线 | 按读取时刻的 Plan、PlanStep 与引用脚本版本元数据推导的合并值；派发期才确定的值写成「待派发确定」 | Plan / PlanStep / Script 行 | 编辑器、执行页、执行前确认 |
   | L2 冻结设计 | prepare 冻结的设计 | `plan_snapshot` 与 `run_context` 中的冻结决定（`wifi_pool_id`、`dispatch_suite`、`dispatch_host_watcher_admin_states`） | 运行快照的「设计」视图 |
   | L3 下发事实 | 物化与 claim 后，按 job / device / host 的实际下发值 | `JobInstance.pipeline_def`、`ResourceAllocation`、claim 下发的 `JobOut.watcher_policy` 口径 | 设备维度的「实际下发」视图 |
   | L4 运行期解析 | Agent 按主机环境解析的值 | 只有存在权威回报通路时才可得（D3-1 第 5 态） | 按 D3 的状态展示 |

2. 投影必须由 `merge_effective_params` 与现有组装函数（`build_lifecycle_from_steps` / `build_lifecycle_from_snapshot`）产出。
   实现者不得另写一套合并逻辑。
3. L1 中，派发期才确定的值不得给出具体值，必须写明由什么决定。包括：
   - WiFi：由所选 WiFi 池或自动选池在物化时逐设备分配；
   - 套件参数：由绑定套件在派发时冻结；
   - watcher：由主机管控在 claim 时覆盖。
4. L2 必须只从 `plan_snapshot` 与 `run_context` 冻结决定重建，不得回读当前 PlanStep / Script 行（ADR-0023 D2 口径）。
   L2 不得包含逐设备的注入值；这些值属于 L3。
5. L3 必须从 `JobInstance.pipeline_def`、`ResourceAllocation` 与 claim 口径派生，不得写回或伪装为 `plan_snapshot` 的固有值。
   参数来源取以下四值之一：
   - `schema_default` / `script_default` / `step_override`；
   - `dispatch_injection`：只在注入实际填入了值时使用。显式值优先而未被覆盖的键，保留原来源。
6. watcher 是独立的「采集策略」值，不是步骤参数：
   - L1 / L2 展示 Plan 的 `watcher_policy` 与主机管控说明；
   - L3 按 host 展示下发策略，口径同 `agent_claim.enrich_job_metadata`；
   - 实施者不得把 watcher 计入参数注入，不得用 `pipeline_def` 比对 watcher。
7. 已保存的 Plan、执行前确认与运行快照，前端必须读投影，不得自行合并参数。
8. 编辑中未保存的草稿是例外：编辑器可以在本地合并 L1 以即时回显。
   前后端必须共用同一份一致性测试夹具（输入 schema / default_params / step.params，期望合并值），
   由 pytest 与 vitest 同时消费；夹具不一致时以后端为准。

### D2 参数说明登记表：与脚本发布单元解耦

1. 参数「含义」的事实源是平台侧「参数说明登记表」。
   它以代码文件形式放在仓库内、脚本族树与 `tool_manifest.json` 之外，按脚本族存放，
   例如 `backend/schemas/param_docs/<script_name>.json`。路径由实施批次确定。
2. 登记表的键是 `(script_name, 参数键路径)`。条目内容：
   - 必须有中文标签与含义（白话，写明对设备做什么）；
   - 可以有单位、注意事项、`sensitive` 标记，以及适用版本范围。
3. 投影解析说明时必须按以下顺序回落：登记表 → 该版本 `param_schema` 的 label / description → 参数键名。
4. 说明是文档，不是执行语义：
   - 不得参与派发、校验与参数合并；
   - 不得写进 `plan_snapshot` 或数据库；
   - 历史运行展示当前说明，条目有版本范围时按运行时的版本取说明。
5. 维护者修改说明必须走普通 PR，不得要求发脚本新版本或做数据迁移。
6. 实施者必须为登记表提供结构校验（单元测试）。维护者不得为此新增 CI 门禁（ADR-0059 D5）。

### D3 显示状态与 Plan 级设置登记表

1. 投影中每个可能「未配置」的值，必须带以下五种状态之一：

   | 状态 | 含义 | 展示 |
   |---|---|---|
   | `explicit` | Plan / 步骤显式配置了值 | 显示该值 |
   | `unset_definite` | 未配置，平台语义确定，与运行环境无关 | 显示语义本身。例：巡检时长未设 = 不限；`barrier_max_wait_seconds` 未设 = 无硬顶；`auto_archive_interval_seconds` 未设 = 不自动归档 |
   | `env_fallback` | 未配置，运行期由 Agent 按主机环境回落 | 必须显示回落链，不得显示为确定数值。例：步骤墙钟「由主机 `STP_STEP_WALL_CLOCK_SECONDS` 决定，未设则 300s」；barrier「由主机 `STP_BARRIER_TIMEOUT_SECONDS` 决定，未设则 600s，并按进度续期」；watcher 未配置的子键 |
   | `pending_dispatch` | 派发或物化时才确定 | 显示决定因素，见 D1-3 |
   | `actual` | 已确认的实际值 | 只在存在权威取值通路时使用，必须注明通路、取值时点与所属 job / host |

2. 投影不得把不同字段的回落混为一谈。例如 `barrier_timeout_seconds` 未设是 `env_fallback`，
   `barrier_max_wait_seconds` 未设是 `unset_definite`（无硬顶）。
3. 以下字段必须在后端「Plan 级设置登记表」登记：`patrol_interval_seconds`、`timeout_seconds`（展示名「巡检时长」）、
   `barrier_timeout_seconds`、`barrier_max_wait_seconds`、`auto_archive_interval_seconds`、`watcher_policy`
   （含 `enabled` / `paths` / `required_categories` / `on_unavailable` 等子键），以及步骤级的
   `timeout_seconds` / `stall_seconds` / `retry` / `enabled`。
4. 每项必须写明以下内容，含义必须指向源码语义所在位置：
   - 标签、含义、单位；
   - 未配置时的状态，按第 1 项五态之一，`env_fallback` 必须给出回落链；
   - 写入边界（与 `PlanCreate` / `PlanUpdate` 一致）；
   - 是否允许 UI 编辑。
5. 前端不得再硬编码这些说明。`frontend/src/components/pipeline/planTiming.ts`（#3635）是过渡实现：
   登记表上线后，实施者必须改为读投影，并删除该模块中的说明文案。这是它的终态出口。

### D4 安全投影与信任边界

1. 掩码必须在服务端生成投影时完成。前端不得以「收到明文后隐藏或折叠」的方式实现掩码。
2. 面向人的展示路径必须只渲染服务端安全投影。展示路径包括：
   - 参数清单；
   - 折叠的原始 / JSON 排障视图；
   - schema / default_params / 步骤参数各段；
   - 新的只读投影与导出。

   折叠的 JSON 视图只能渲染已掩码的投影对象，不得渲染原始载荷。
3. 敏感判定按「参数键路径」进行，任意嵌套深度，命中任一项即掩码：
   - 内置注入路径：`connect_wifi` 的 `password`，`monkey_setup` 的 `wifi.password`；
   - D2 登记表标记为 `sensitive` 的键路径；
   - 名称兜底：键名与 `backend/core/redaction.py` 的敏感键集合一致（大小写不敏感，精确匹配）。
4. 掩码只保留「已设置 / 未设置」与来源，不得暴露值或长度。
5. 信任边界：现有 API 的原始载荷是原始事实面，不是 UI 安全投影。原始载荷包括：
   - `GET /plan-runs/{id}` 返回的 `plan_snapshot`；
   - `GET /runs/{job_id}/steps` 在 Job 尚无步骤轨迹时，从 `pipeline_def` 回落返回的 `params`。

   规则：
   - 本 ADR 的起草 PR 不修改旧 API；UI 不得再直接渲染这些载荷；
   - 「前端不展示」不得被记为暴露已解决。后一条路径会向任意登录用户返回注入后的 WiFi 凭据，
     与 #955 的剥密意图及 ADR-0023 原 D5「仅 Agent 可读」相冲突，必须另立单处置。

### D5 执行前确认与派发的一致性

1. 执行前确认展示的 L1 基线必须携带配置指纹。指纹由后端根据确认所依据的配置计算：
   Plan 直列字段、PlanStep 行，以及引用的脚本 `(name, version)` 身份。
2. 提交派发时，后端必须用 prepare 实际读取的配置重新计算指纹并比较。
   - 不一致时，后端必须拒绝本次派发并返回可识别的错误；
   - 前端必须提示配置已变化，并要求重新预览、重新确认；
   - 后端不得静默用更新后的配置派发。
3. 指纹只覆盖 L1 的确认对象。WiFi 逐设备分配、套件内容与主机 watcher 管控属于派发期决定，
   确认页按 D1-3 展示为「待派发确定」，不计入指纹。
4. prepare 之后，准入、物化与 claim 以 `plan_snapshot` 和冻结决定为准，不再回读 Plan，因此不再存在「看 A、执行 B」。
5. 指纹的具体形态与比较机制由实施批次确定，可以是内容摘要，或由同一次后端冻结对象保证。
   实施者不得以前端自行比对替代后端比较。

### D6 UI 消费规则

1. 编辑器、执行前确认、运行快照必须用同一个「参数清单」组件展示投影。
   每个参数显示四样：值或状态（D3）、来源、所在层（D1）、含义（D2）。
2. 执行前确认必须展示将要执行的步骤与 L1 生效参数，派发期决定按 D1-3 标注；它替代现有「只显示设备数」的确认。
3. 运行快照必须展示 L2 设计。按设备展示 L3 下发事实时，必须标明是哪台设备、来自哪个权威来源。
4. Plan 级设置在编辑器中必须先以只读形式展示，包括当前值、D3 状态与含义。
5. UI 开放编辑 Plan 级设置时，必须做到：
   - 校验与后端写入边界一致；
   - 该编辑单元按 ADR-0058 走独立复核；
   - `watcher_policy` 只开放登记表定义的子键，不得开放自由 JSON。

### D7 不做的事

- 本 ADR 不改参数值的分层（ADR-0029），不改派发与 Agent 执行，不改脚本版本不可变规则（ADR-0051）。
- 说明不进数据库，不建 UI 内的说明编辑页。
- 本 ADR 的起草 PR 不新增接口、不改数据库、不改业务代码。

## 3. 备选方案与权衡

| 方案 | 做法 | 不选的原因 |
|---|---|---|
| 甲 | 说明写进各脚本版本的 `param_schema` | 被引用版本不可原地改：补一句说明要发新版本、改指 Plan、走激活闸门，并加重版本膨胀（#735） |
| 丙 | 说明存数据库，由管理页编辑 | 需要迁移、权限、审计；各站点独立（ADR-0041），说明会随站点漂移。复议触发见 §5 |
| 丁 | 维持前端各页手写说明（现状） | 已证明会分叉：「超时」三处同名、一处解释错误 |
| 单一「生效值」（v0.1） | 一个投影代表全部时点 | 物化逐设备注入、claim 按 host 覆盖、Agent 按主机环境回落，单值必然失真（复核 R1-1 / R1-2） |
| 前端合并生效值 | 各页在前端重算 | 第二计算者必然漂移；派发期决定只有后端知道。只给编辑中的草稿留例外，并用共享夹具约束（D1-8） |
| 确认后加锁 Plan | 预览到派发之间禁止修改 Plan | 锁的持有与释放复杂，多会话编辑时易误伤。指纹比较只在真正冲突时要求重新确认（D5） |

## 4. 影响

- **后端**：新增分层投影函数与只读出口（形态由批次确定）；派发入口增加确认指纹比较（D5）；
  不改物化、不改 Agent、无数据迁移。
- **前端**：
  - 参数清单组件三处复用；
  - 删除执行页 `default_params` 视图；快照抽屉的原始 JSON 改为渲染已掩码投影；
  - `types.ts` 同步。
- **内容**：登记表首批覆盖按 `plan_step` 引用量排序的高频脚本族。含义必须由熟悉脚本的人复核，范围与人选由批次确定。
- **回退**：投影与展示可以逐单元回退。指纹比较回退后，派发恢复为现状（不比较）。

## 5. 落地与后续动作

- **批次**（ADR-0058）。单元草案，以批次方案为准：
  - U1：后端分层投影、显示状态与 Plan 级设置登记表，含一致性夹具与掩码；
  - U2：参数清单组件与执行前确认，含 D5 指纹；
  - U3：编辑器（参数清单 + Plan 级设置只读区）；
  - U4：运行快照（L2 设计 + 按设备 L3）；
  - U5：高频脚本族说明内容；
  - U6：Plan 级设置开放编辑（必须复核）。
- **验收对等关系**（按层，替代 v0.1 的「单一投影等于 `pipeline_def`」）：
  - L1 → L2：由已确认预览发起的 PlanRun，在指纹一致时，其 L2 合并值必须等于确认时的 L1 合并值。
  - L2 → L3：
    - 对每个 Job，`pipeline_def` 中的参数必须等于 L2 合并值，叠加注入实际填入的键；
    - 显式值不得被注入覆盖；
    - watcher 单独验收：每个 Job 的下发策略必须等于 L2 的 `watcher_policy` 叠加该 host 的冻结管控，读 claim 口径。
  - L4：只在存在权威回报通路时验收，按 job / host 比对。
- **必须覆盖的边界反例**：
  - 同一 Run 多设备分配到不同 WiFi 池；
  - 步骤显式填了 `ssid`，注入不覆盖，来源保持 `step_override`；
  - 某 host 的 watcher 管控为 inactive，该 host 的 Job 下发 `enabled=false`，其他 host 不变；
  - 步骤墙钟未设：L1 / L2 显示 `env_fallback` 回落链，不显示 300；
  - `barrier_max_wait_seconds` 未设显示「无硬顶」（`unset_definite`）；
  - 预览后、派发前 Plan 被修改：派发被拒，要求重新确认；
  - `monkey_setup` 的 `wifi.password` 在参数清单与折叠 JSON 中都被掩码。
- **R2（非阻塞，交批次）**：嵌套参数的路径表达、跨版本同名参数的版本范围匹配与冲突兜底，
  由批次在首批内容验收中约定，并给出测试反例（嵌套键查说明、同键跨版本含义变化、版本范围重叠）。
- **另立单**：`GET /runs/{job_id}/steps` 回落路径返回注入后 WiFi 凭据的暴露面（D4-5）。
- **复议触发**：
  - 说明需要非开发人员高频编辑时，重议方案丙；
  - 出现新的派发期决定类型时，扩展 D1-3 与来源枚举；
  - 新增 Agent 回报运行期取值的通路时，扩展 D3 `actual` 的适用范围；
  - 登记表与脚本参数键持续漂移时，增加键存在性校验。

## 6. 关联实现与文档

- 审查稿：`docs/reviews/UI_HUMAN_OBSERVABILITY_REVIEW_2026-10-09_89b9dc8_claude.md`（A3 / A4 / A5 / A6 / A7、§8）
- 载体 issue：#3637；独立复核：#3638 评论（v0.1 → v0.2）
- 已完成的单点修复：
  - #3630（脚本库死链）；
  - #3634（参数说明常显）；
  - #3635（巡检时长改名，`planTiming.ts` 为 D3-5 所述过渡实现）；
  - #3636（异常采集降级提示）
- 相关 ADR：
  - ADR-0020（Plan-Step 模型）；
  - ADR-0023（脚本溯源：D2–D4 观测面与快照抽屉，D5 撤销）；
  - ADR-0029（参数分层）；
  - ADR-0051（发布单元不可变）；
  - ADR-0058（批次交付）；
  - ADR-0059（写作约定）
- 代码锚点：
  - `backend/services/script_params.py`；
  - `backend/services/plan_dispatcher_core.py`；
  - `backend/services/plan_dispatcher_sync.py`（`prepare_plan_run`、`materialize_jobs_and_allocations`、`_sync_allocate_devices`）；
  - `backend/services/agent_claim.py`（`enrich_job_metadata`）；
  - `backend/agent/pipeline_engine.py`（`_resolve_step_wall_clock`、`_resolve_barrier_timeout`）；
  - `backend/agent/watcher/policy.py`；
  - `backend/core/redaction.py`；
  - `backend/api/routes/runs.py`（`list_run_steps`）；
  - `frontend/src/components/plan-run/PlanSnapshotDrawer.tsx`
